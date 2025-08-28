# cogs/economy.py
from __future__ import annotations
import json, random
import discord
from discord.ext import commands
from discord import app_commands
from utils.db import fetchone, fetchall, execute, executemany
from utils.embeds import send_ok, send_err
from utils.constants import (
    BASE_DROP, NPC_RESOURCE_RATE, NPC_ITEM_RATE, NPC_ITEM_TAX
)
from utils.timezone import KST
from datetime import datetime, date

def today_kst() -> date:
    return datetime.now(KST).date()

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="길드", description="제작/정산/판매 등 경제 활동")

    async def _ensure_user(self, cid:int, uid:int):
        row = await fetchone("SELECT 1 FROM users WHERE country_id=$1 AND user_id=$2", (cid, uid))
        if not row:
            await execute("INSERT INTO users(country_id,user_id) VALUES ($1,$2)", (cid, uid))

    @group.command(name="정산", description="매일 0시(KST) 이후 1일 1회 자원을 수령합니다.")
    async def claim(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        land = await fetchone(
            "SELECT tier,resource_bias,base_yield FROM lands WHERE country_id=$1 AND channel_id=$2",
            (cid, inter.channel_id)
        )
        if not land:
            return await send_err(inter, "이 채널은 토지가 아닙니다. `/토지 지정` 후 이용하세요.")

        await self._ensure_user(cid, inter.user.id)
        user = await fetchone(
            "SELECT last_claim_date, streak FROM users WHERE country_id=$1 AND user_id=$2",
            (cid, inter.user.id)
        )
        today = today_kst()
        if user["last_claim_date"] is not None and user["last_claim_date"] >= today:
            return await send_err(inter, "오늘(0시 이후) 정산은 이미 완료되었습니다. 내일 다시 오세요!")

        # 편향 적용
        bias = land["resource_bias"]
        table = [(i, p + (10 if i == bias else 0)) for i, p in BASE_DROP]
        s = sum(p for _, p in table)
        table = [(i, round(p * 100 / s)) for i, p in table]
        diff = 100 - sum(p for _, p in table)
        if diff:
            i0, p0 = table[0]; table[0] = (i0, p0 + diff)

        # 산출
        base = land["base_yield"]
        results: dict[str,int] = {}
        for _ in range(base):
            r = random.randint(1,100)
            acc = 0
            for item,p in table:
                acc += p
                if r <= acc:
                    results[item] = results.get(item,0)+1
                    break

        # 반영
        await execute(
            "INSERT INTO users(country_id,user_id,last_claim_date,streak) VALUES ($1,$2,$3,1) "
            "ON CONFLICT (country_id,user_id) DO UPDATE SET "
            " last_claim_date = EXCLUDED.last_claim_date, "
            " streak = CASE WHEN users.last_claim_date = EXCLUDED.last_claim_date - INTERVAL '1 day' "
            "               THEN users.streak + 1 ELSE 1 END",
            (cid, inter.user.id, today)
        )
        rows = [(cid, inter.user.id, item_id, qty, qty) for item_id, qty in results.items()]
        if rows:
            await executemany(
                "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty = inventory.qty + $5",
                rows
            )

        lines = [f"• **{k}** × **{v}**" for k,v in results.items()] or ["오늘은 빈 손으로 돌아왔습니다…"]
        await send_ok(inter, "일일 정산 (KST 0시 리셋)",
            "왕국의 광산과 삼림에서 다음 자원을 회수했습니다:\n" + "\n".join(lines))

    @group.command(name="인벤", description="내 자원/아이템 보유량을 확인합니다.")
    async def inventory(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)
        rows = await fetchall(
            "SELECT i.item_id, i.name, i.typ, inv.qty "
            "FROM inventory inv JOIN items i ON i.item_id=inv.item_id "
            "WHERE inv.country_id=$1 AND inv.user_id=$2 "
            "ORDER BY i.typ DESC, i.item_id ASC",
            (cid, inter.user.id)
        )
        if not rows:
            return await send_ok(inter, "여신의 상자", "아직 아무것도 없습니다. `/길드 정산`으로 자원을 모아보세요.")
        res = [f"• {r['name']} × **{r['qty']}**" for r in rows]
        await send_ok(inter, "여신의 상자", "\n".join(res))

    @group.command(name="제작", description="자원으로 아이템을 제작합니다 (아이템은 NPC 전용 판매).")
    @app_commands.describe(아이템="iron_ingot/steel_ingot/toolkit/healing_potion", 수량="제작 수량")
    async def craft(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,100]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)

        rec = await fetchone("SELECT inputs_json,yield_qty,active_flag FROM recipes WHERE product_id=$1", (아이템,))
        if not rec or not rec["active_flag"]:
            return await send_err(inter, "금단의 조합서입니다. 다른 제련을 시도하십시오.")
        inputs = dict(rec["inputs_json"])
        # 재료 체크
        for item_id, need in inputs.items():
            row = await fetchone(
                "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
                (cid, inter.user.id, item_id)
            )
            if not row or row["qty"] < need*수량:
                return await send_err(inter, f"재료가 부족합니다: {item_id} x {need*수량}")
        # 차감/지급
        for item_id, need in inputs.items():
            await execute(
                "UPDATE inventory SET qty = qty - $1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                (need*수량, cid, inter.user.id, item_id)
            )
        out_qty = rec["yield_qty"] * 수량
        await execute(
            "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty = inventory.qty + $4",
            (cid, inter.user.id, 아이템, out_qty)
        )
        await send_ok(inter, "제작 완료",
            f"장인의 손길로 **{아이템} × {out_qty}** 를 단조했습니다.\n(아이템은 NPC에게만 판매할 수 있습니다)")

    @group.command(name="판매자원", description="자원을 왕국의 NPC에게 판매합니다(고정률 65%).")
    @app_commands.describe(아이템="iron/wood/stone/herb/water", 수량="판매 수량")
    async def sell_resource(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,1_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)

        item = await fetchone("SELECT typ, base_price FROM items WHERE item_id=$1", (아이템,))
        if not item or item["typ"] != "resource":
            return await send_err(inter, "그것은 자원이 아닙니다.")
        inv = await fetchone(
            "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
            (cid, inter.user.id, 아이템)
        )
        if not inv or inv["qty"] < 수량:
            return await send_err(inter, "수량이 부족합니다.")

        unit_price = round(item["base_price"] * NPC_RESOURCE_RATE)
        total = unit_price * 수량

        await execute("UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                      (수량, cid, inter.user.id, 아이템))
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",
                      (total, cid, inter.user.id))
        await execute("INSERT INTO treasury_ledger(country_id,typ,reason,amount) VALUES($1,'in','자원 NPC 매입(통화발행)',0)",
                      (cid,))
        await send_ok(inter, "자원 판매",
            f"상인 조합이 **{아이템} × {수량}** 을(를) **{total} LC**에 매입했습니다.\n(단가 {unit_price} LC)")

    @group.command(name="판매아이템", description="제작 아이템을 NPC에게 판매합니다(고정률 95%, 매입세 5%).")
    @app_commands.describe(아이템="iron_ingot/steel_ingot/toolkit/healing_potion", 수량="판매 수량")
    async def sell_item(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,1_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)

        item = await fetchone("SELECT typ, base_price FROM items WHERE item_id=$1", (아이템,))
        if not item or item["typ"] != "item":
            return await send_err(inter, "그것은 제작 아이템이 아닙니다.")
        inv = await fetchone(
            "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
            (cid, inter.user.id, 아이템)
        )
        if not inv or inv["qty"] < 수량:
            return await send_err(inter, "수량이 부족합니다.")

        unit_price = round(item["base_price"] * NPC_ITEM_RATE)
        gross = unit_price * 수량
        tax = int(gross * NPC_ITEM_TAX)      # 국고 귀속
        net = gross - tax

        await execute("UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                      (수량, cid, inter.user.id, 아이템))
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",
                      (net, cid, inter.user.id))
        await execute("UPDATE countries SET treasury=treasury+$1 WHERE country_id=$2",
                      (tax, cid))
        await execute("INSERT INTO treasury_ledger(country_id,typ,reason,amount) VALUES($1,'in','아이템 NPC 매입세',$2)",
                      (cid, tax))
        await send_ok(inter, "납품 완료",
            f"왕실 조달청이 **{아이템} × {수량}** 을(를) 매입했습니다.\n"
            f"단가 **{unit_price} LC**, 총액 **{gross} LC**, 공납세 **{tax} LC**\n"
            f"수령액 **{net} LC**.")

    @group.command(name="시세", description="자원 시세(최근일 기준)를 확인합니다.")
    @app_commands.describe(자원="iron/wood/stone/herb/water")
    async def price(self, inter: discord.Interaction, 자원: str):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        item = await fetchone("SELECT item_id, name, typ, base_price FROM items WHERE item_id=$1", (자원,))
        if not item or item["typ"] != "resource":
            return await send_err(inter, "자원만 시세 조회가 가능합니다.")

        r = await fetchone(
            "SELECT date, avg_price, volume, ema_price, price_index "
            "FROM price_indices_daily WHERE country_id=$1 AND item_id=$2 "
            "ORDER BY date DESC LIMIT 1",
            (cid, 자원)
        )
        if not r:
            return await send_ok(inter, "시세",
                f"**{item['name']}**\n최근 체결 데이터가 없어 **기준가 {item['base_price']} LC**를 참고하세요.")
        await send_ok(
            inter, "시세",
            f"**{item['name']}**\n"
            f"• 최근일 평균가: **{r['avg_price']} LC** (거래량 {r['volume']})\n"
            f"• EMA(평활가): **{float(r['ema_price']):.2f} LC**\n"
            f"• 가격지수: **{float(r['price_index']):.2f}** (1.00=기준)\n"
            f"• 기준가: {item['base_price']} LC"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))

from __future__ import annotations
import json
import random
from datetime import datetime
import discord
from discord.ext import commands
from discord import app_commands

from utils.db import fetchone, fetchall, execute, executemany
from utils.embeds import send_ok, send_err
from utils.constants import (
    BASE_DROP,
    NPC_RESOURCE_RATE,   # 예: 0.65  (자원 NPC 매입 단가 = base_price * 0.65)
    NPC_ITEM_RATE,       # 예: 1.00  (아이템 NPC 매입 단가 = base_price * 1.00)
    NPC_ITEM_TAX,        # 예: 0.05  (아이템 매각액의 5%를 국고 세금)
)
from utils.timezone import KST


# ---------- 티어 설정 (1~5) ----------
# 가격/유지비는 정부(토지 지정) 쪽에서 사용하고, 여기서는 수확량 범위만 사용.
LAND_TIERS = {
    1: {"price": 5_000,   "upkeep": 1_000,  "yield_min": 2,  "yield_max": 4},
    2: {"price": 15_000,  "upkeep": 3_000,  "yield_min": 4,  "yield_max": 6},
    3: {"price": 30_000,  "upkeep": 6_000,  "yield_min": 6,  "yield_max": 9},
    4: {"price": 60_000,  "upkeep": 12_000, "yield_min": 9,  "yield_max": 12},
    5: {"price": 120_000, "upkeep": 25_000, "yield_min": 12, "yield_max": 16},
}


# ---------- 유틸: recipes.inputs_json 안전 파서 ----------
def _json_obj(val) -> dict:
    """recipes.inputs_json이 dict가 아닐 수 있는 환경(드라이버/직렬화)에 대비한 파서."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    try:
        return dict(val)  # asyncpg.Record 등 대응
    except Exception:
        return {}


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="길드", description="경제 활동 명령어")

    # ---------- 공통 유틸 ----------
    async def _ensure_user(self, cid: int, uid: int):
        await execute(
            "INSERT INTO users(country_id,user_id) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING",
            (cid, uid)
        )

    async def _item_name(self, item_id: str) -> str:
        r = await fetchone("SELECT name FROM items WHERE item_id=$1", (item_id,))
        return r["name"] if r else item_id

    # ---------- 내부 자동완성 ----------
    async def ac_all_items_any(self, inter: discord.Interaction, current: str):
        rows = await fetchall(
            "SELECT item_id, name FROM items "
            "WHERE item_id ILIKE $1 OR name ILIKE $1 "
            "ORDER BY item_id LIMIT 25",
            (f"%{current}%",)
        )
        return [app_commands.Choice(name=f"{r['name']} ({r['item_id']})", value=r['item_id']) for r in rows]

    async def _ac_resource(self, inter: discord.Interaction, current: str, owned_only: bool):
        cid, uid = inter.guild.id, inter.user.id
        if owned_only:
            rows = await fetchall(
                "SELECT i.item_id, i.name FROM inventory inv "
                "JOIN items i ON i.item_id=inv.item_id "
                "WHERE inv.country_id=$1 AND inv.user_id=$2 AND i.typ='resource' AND inv.qty>0 "
                "AND (i.item_id ILIKE $3 OR i.name ILIKE $3) "
                "ORDER BY i.item_id LIMIT 25",
                (cid, uid, f"%{current}%")
            )
        else:
            rows = await fetchall(
                "SELECT item_id, name FROM items WHERE typ='resource' "
                "AND (item_id ILIKE $1 OR name ILIKE $1) "
                "ORDER BY item_id LIMIT 25",
                (f"%{current}%",)
            )
        return [app_commands.Choice(name=f"{r['name']} ({r['item_id']})", value=r["item_id"]) for r in rows]

    async def _ac_item(self, inter: discord.Interaction, current: str, owned_only: bool):
        cid, uid = inter.guild.id, inter.user.id
        if owned_only:
            rows = await fetchall(
                "SELECT i.item_id, i.name FROM inventory inv "
                "JOIN items i ON i.item_id=inv.item_id "
                "WHERE inv.country_id=$1 AND inv.user_id=$2 AND i.typ='item' AND inv.qty>0 "
                "AND (i.item_id ILIKE $3 OR i.name ILIKE $3) "
                "ORDER BY i.item_id LIMIT 25",
                (cid, uid, f"%{current}%")
            )
        else:
            rows = await fetchall(
                "SELECT item_id, name FROM items WHERE typ='item' "
                "AND (item_id ILIKE $1 OR name ILIKE $1) "
                "ORDER BY item_id LIMIT 25",
                (f"%{current}%",)
            )
        return [app_commands.Choice(name=f"{r['name']} ({r['item_id']})", value=r["item_id"]) for r in rows]

    # ---------- 자동완성 콜백(코루틴) ----------
    async def ac_resource_any(self, inter: discord.Interaction, current: str):
        return await self._ac_resource(inter, current, owned_only=False)

    async def ac_resource_owned(self, inter: discord.Interaction, current: str):
        return await self._ac_resource(inter, current, owned_only=True)

    async def ac_item_any(self, inter: discord.Interaction, current: str):
        return await self._ac_item(inter, current, owned_only=False)

    async def ac_item_owned(self, inter: discord.Interaction, current: str):
        return await self._ac_item(inter, current, owned_only=True)

    # ---------- 명령어들 ----------
    @group.command(name="인벤", description="내 인벤토리를 확인합니다.")
    async def inventory(self, inter: discord.Interaction):
        cid, uid = inter.guild.id, inter.user.id
        await self._ensure_user(cid, uid)
        rows = await fetchall(
            "SELECT i.name, i.typ, inv.qty FROM inventory inv "
            "JOIN items i ON i.item_id=inv.item_id "
            "WHERE inv.country_id=$1 AND inv.user_id=$2 "
            "ORDER BY i.typ DESC, i.item_id",
            (cid, uid)
        )
        if not rows:
            return await send_ok(inter, "인벤토리", "아무것도 없습니다. `/길드 정산`으로 자원을 모아보세요.")
        parts_res = [f"• {r['name']} × **{r['qty']}**" for r in rows if r["typ"] == "resource"]
        parts_itm = [f"• {r['name']} × **{r['qty']}**" for r in rows if r["typ"] == "item"]
        desc = []
        if parts_res:
            desc.append("### 자원\n" + "\n".join(parts_res))
        if parts_itm:
            desc.append("\n### 아이템\n" + "\n".join(parts_itm))
        await send_ok(inter, "인벤토리", "\n".join(desc) if desc else "아무것도 없습니다.")

    @group.command(name="시세", description="자원/아이템 시세를 확인합니다. (미지정 시 전체)")
    @app_commands.describe(아이템="시세를 볼 아이템(선택)")
    @app_commands.autocomplete(아이템=ac_all_items_any)
    async def price_view(self, inter: discord.Interaction, 아이템: str | None = None):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id

        # 단일 아이템 시세
        if 아이템:
            row = await fetchone(
                "SELECT i.item_id, i.name, i.typ, "
                "COALESCE(mp.ema_price, i.base_price) AS price, "
                "mp.last_updated "
                "FROM items i "
                "LEFT JOIN market_prices mp ON mp.country_id=$1 AND mp.item_id=i.item_id "
                "WHERE i.item_id=$2",
                (cid, 아이템)
            )
            if not row:
                return await send_err(inter, "해당 아이템을 찾을 수 없습니다.")
            typ = "자원" if row["typ"] == "resource" else "아이템"
            updated = row["last_updated"].strftime("%Y-%m-%d %H:%M") if row.get("last_updated") else "기록 없음"
            await send_ok(
                inter, "시세",
                f"분류: **{typ}**\n"
                f"이름: **{row['name']}** ({row['item_id']})\n"
                f"현재 시세: **{int(row['price'])} LC**\n"
                f"갱신: {updated}"
            )
            return

        # 전체 시세
        rows = await fetchall(
            "SELECT i.item_id, i.name, i.typ, COALESCE(mp.ema_price, i.base_price) AS price "
            "FROM items i "
            "LEFT JOIN market_prices mp ON mp.country_id=$1 AND mp.item_id=i.item_id "
            "ORDER BY i.typ DESC, i.item_id",
            (cid,)
        )
        if not rows:
            return await send_ok(inter, "시세", "등록된 아이템이 없습니다.")
        res_lines = []
        if any(r["typ"] == "resource" for r in rows):
            res_lines.append("### 자원")
            for r in rows:
                if r["typ"] == "resource":
                    res_lines.append(f"• {r['name']} ({r['item_id']}) — **{int(r['price'])} LC**")
        if any(r["typ"] == "item" for r in rows):
            res_lines.append("\n### 아이템")
            for r in rows:
                if r["typ"] == "item":
                    res_lines.append(f"• {r['name']} ({r['item_id']}) — **{int(r['price'])} LC**")
        await send_ok(inter, "전체 시세", "\n".join(res_lines))

    @group.command(name="정산", description="이 토지(채널)에서 오늘의 자원을 수령합니다. (채널마다 1회/일, KST 0시 리셋)")
    async def claim(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid, ch, uid = inter.guild.id, inter.channel_id, inter.user.id

        land = await fetchone(
            "SELECT tier,resource_bias,base_yield FROM lands WHERE country_id=$1 AND channel_id=$2",
            (cid, ch)
        )
        if not land:
            return await send_err(inter, "이 채널은 토지가 아닙니다. `/왕국 토지 지정`으로 설정하세요.")
        await self._ensure_user(cid, uid)

        today = datetime.now(KST).date()
        dup = await fetchone(
            "SELECT 1 FROM user_claims WHERE country_id=$1 AND user_id=$2 AND channel_id=$3 AND claim_date=$4",
            (cid, uid, ch, today)
        )
        if dup:
            return await send_err(inter, "오늘은 이미 이 토지에서 수확했습니다. 내일 다시 오세요!")

        # --------- 수확량: 티어 기반 랜덤 범위 ---------
        tier = int(land["tier"])
        bias = land["resource_bias"]
        tier_conf = LAND_TIERS.get(tier, LAND_TIERS[1])
        harvest_qty = random.randint(tier_conf["yield_min"], tier_conf["yield_max"])

        # 드랍 확률 테이블(편향 적용)
        table = [(i, p + (10 if i == bias else 0)) for i, p in BASE_DROP]
        s = sum(p for _, p in table)
        table = [(i, round(p * 100 / s)) for i, p in table]
        diff = 100 - sum(p for _, p in table)
        if diff:
            i0, p0 = table[0]
            table[0] = (i0, p0 + diff)

        results: dict[str, int] = {}
        for _ in range(harvest_qty):
            r = random.randint(1, 100)
            acc = 0
            for item, p in table:
                acc += p
                if r <= acc:
                    results[item] = results.get(item, 0) + 1
                    break

        # 지급
        if results:
            await executemany(
                "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty=inventory.qty+$4",
                [(cid, uid, k, v, v) for k, v in results.items()]
            )

        await execute(
            "INSERT INTO user_claims(country_id,user_id,channel_id,claim_date) VALUES ($1,$2,$3,$4)",
            (cid, uid, ch, today)
        )

        pretty = [f"• **{await self._item_name(k)}** × **{v}**" for k, v in results.items()]
        await send_ok(inter, "오늘의 수확", "\n".join(pretty) if pretty else "오늘은 빈 손입니다…")

    @group.command(name="레시피", description="제작 가능한 레시피 목록을 확인합니다.")
    async def recipes(self, inter: discord.Interaction):
        rows = await fetchall(
            "SELECT r.product_id, i.name, r.inputs_json, r.yield_qty "
            "FROM recipes r JOIN items i ON i.item_id=r.product_id "
            "WHERE r.active_flag=TRUE ORDER BY r.product_id"
        )
        if not rows:
            return await send_ok(inter, "레시피", "등록된 레시피가 없습니다.")
        lines = []
        for r in rows:
            inputs_obj = _json_obj(r["inputs_json"])
            inputs = ", ".join([f"{await self._item_name(k)}×{v}" for k, v in inputs_obj.items() ])
            lines.append(f"• **{r['name']}** ({r['product_id']}) = {inputs} → ×{r['yield_qty']}")
        await send_ok(inter, "제작 레시피", "\n".join(lines))

    @group.command(name="레시피상세", description="특정 아이템의 레시피를 확인합니다.")
    @app_commands.autocomplete(아이템=ac_item_any)
    async def recipe_detail(self, inter: discord.Interaction, 아이템: str):
        rec = await fetchone(
            "SELECT inputs_json, yield_qty, active_flag FROM recipes WHERE product_id=$1",
            (아이템,)
        )
        prod = await fetchone("SELECT name FROM items WHERE item_id=$1", (아이템,))
        if not rec or not rec["active_flag"]:
            return await send_err(inter, "해당 제작법이 없거나 비활성화되었습니다.")
        inputs_obj = _json_obj(rec["inputs_json"])
        inputs = ", ".join([f"{await self._item_name(k)}×{v}" for k, v in inputs_obj.items()])
        await send_ok(
            inter, "레시피",
            f"**{prod['name']}** ({아이템})\n재료: {inputs}\n산출: ×{rec['yield_qty']}"
        )

    @group.command(name="제작", description="자원으로 아이템을 제작합니다 (아이템은 NPC 전용 판매).")
    @app_commands.describe(아이템="제작 아이템", 수량="제작 수량")
    @app_commands.autocomplete(아이템=ac_item_any)
    async def craft(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,1_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid, uid = inter.guild.id, inter.user.id
        await self._ensure_user(cid, uid)

        rec = await fetchone("SELECT inputs_json,yield_qty,active_flag FROM recipes WHERE product_id=$1", (아이템,))
        if not rec or not rec["active_flag"]:
            return await send_err(inter, "금단의 조합서입니다. 다른 제련을 시도하십시오.")
        inputs_obj = _json_obj(rec["inputs_json"])
        out_qty = int(rec["yield_qty"]) * 수량

        # 재료 체크
        for item_id, need in inputs_obj.items():
            row = await fetchone(
                "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
                (cid, uid, item_id)
            )
            need_total = int(need) * 수량
            if not row or row["qty"] < need_total:
                name = await self._item_name(item_id)
                return await send_err(inter, f"재료가 부족합니다: {name} × {need_total}")

        # 차감
        for item_id, need in inputs_obj.items():
            await execute(
                "UPDATE inventory SET qty = qty - $1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                (int(need) * 수량, cid, uid, item_id)
            )
        # 산출
        await execute(
            "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty = inventory.qty + $4",
            (cid, uid, 아이템, out_qty)
        )
        prod_name = await self._item_name(아이템)
        await send_ok(inter, "제작 완료", f"**{prod_name} × {out_qty}** 제작을 마쳤습니다.\n(아이템은 NPC에게만 판매할 수 있습니다)")

    @group.command(name="판매자원", description="자원을 NPC에게 판매합니다 (고정률 65%).")
    @app_commands.describe(아이템="판매할 자원", 수량="판매 수량")
    @app_commands.autocomplete(아이템=ac_resource_owned)
    async def sell_res(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,1_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid, uid = inter.guild.id, inter.user.id
        await self._ensure_user(cid, uid)

        item = await fetchone("SELECT typ, base_price, name FROM items WHERE item_id=$1", (아이템,))
        if not item or item["typ"] != "resource":
            return await send_err(inter, "그것은 자원이 아닙니다.")
        inv = await fetchone(
            "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
            (cid, uid, 아이템)
        )
        if not inv or inv["qty"] < 수량:
            return await send_err(inter, "수량이 부족합니다.")

        # NPC 자원 매입: 고정 비율(세금 없음)
        unit_price = round(int(item["base_price"]) * float(NPC_RESOURCE_RATE))
        total = unit_price * 수량

        await execute("UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                      (수량, cid, uid, 아이템))
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",
                      (total, cid, uid))

        await send_ok(
            inter, "자원 판매",
            f"**{item['name']} × {수량}**\n단가 **{unit_price} LC** → 합계 **{total} LC**\n"
            f"지급 완료!"
        )

    @group.command(name="판매아이템", description="제작 아이템을 NPC에게 판매합니다 (고정률·세금 고정).")
    @app_commands.describe(아이템="판매할 아이템", 수량="판매 수량")
    @app_commands.autocomplete(아이템=ac_item_owned)
    async def sell_item(self, inter: discord.Interaction, 아이템: str, 수량: app_commands.Range[int,1,1_000_000]):
        """
        - 아이템은 NPC 전용 판매.
        - 단가 = base_price * NPC_ITEM_RATE (고정)
        - 세금 = 매각액 * NPC_ITEM_TAX → 국고로 귀속
        - 유저 수령액 = 매각액 - 세금
        """
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid, uid = inter.guild.id, inter.user.id
        await self._ensure_user(cid, uid)

        it = await fetchone("SELECT typ, base_price, name FROM items WHERE item_id=$1", (아이템,))
        if not it or it["typ"] != "item":
            return await send_err(inter, "그것은 제작 아이템이 아닙니다.")
        inv = await fetchone(
            "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
            (cid, uid, 아이템)
        )
        if not inv or inv["qty"] < 수량:
            return await send_err(inter, "수량이 부족합니다.")

        unit_price = round(int(it["base_price"]) * float(NPC_ITEM_RATE))
        gross = unit_price * 수량
        tax = round(gross * float(NPC_ITEM_TAX))
        net = gross - tax
        if net < 0:
            net = 0

        # 차감 / 지급 / 국고 세금 적립
        await execute("UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
                      (수량, cid, uid, 아이템))
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",
                      (net, cid, uid))
        await execute("UPDATE countries SET treasury=treasury+$1 WHERE country_id=$2",
                      (tax, cid))

        await send_ok(
            inter, "아이템 판매",
            f"**{it['name']} × {수량}**\n"
            f"단가 **{unit_price} LC** → 매각액 **{gross} LC**\n"
            f"세금 **{tax} LC** (국고 적립) → 수령액 **{net} LC**\n"
            f"지급 완료!"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))

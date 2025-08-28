# cogs/market.py
from __future__ import annotations
import discord
from discord.ext import commands
from discord import app_commands
from utils.db import fetchone, fetchall, execute
from utils.embeds import send_ok, send_err
from utils.constants import EMA_ALPHA, KST
from datetime import datetime


def today_kst_str() -> str:
    return datetime.now(KST).date().isoformat()


class Market(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="상점", description="왕국의 장터를 이용합니다")

    async def _ensure_user(self, cid: int, uid: int):
        row = await fetchone("SELECT 1 FROM users WHERE country_id=$1 AND user_id=$2", (cid, uid))
        if not row:
            await execute("INSERT INTO users(country_id,user_id) VALUES ($1,$2)", (cid, uid))

    @group.command(name="등록", description="자원을 장터에 등록합니다.")
    @app_commands.describe(자원="iron/wood/stone/herb/water", 수량="판매 수량", 단가="개당 LC")
    async def register(self, inter: discord.Interaction,
                       자원: str,
                       수량: app_commands.Range[int, 1, 1_000_000],
                       단가: app_commands.Range[int, 1, 1_000_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)

        item = await fetchone("SELECT item_id, name, typ FROM items WHERE item_id=$1", (자원,))
        if not item or item["typ"] != "resource":
            return await send_err(inter, "자원만 상점 등록이 가능합니다.")
        inv = await fetchone(
            "SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",
            (cid, inter.user.id, 자원)
        )
        if not inv or inv["qty"] < 수량:
            return await send_err(inter, "수량이 부족합니다.")

        # 인벤 차감 후 등록
        await execute(
            "UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",
            (수량, cid, inter.user.id, 자원)
        )
        row = await fetchone(
            "INSERT INTO listings(country_id,seller_id,resource_id,qty,unit_price) "
            "VALUES ($1,$2,$3,$4,$5) RETURNING listing_id",
            (cid, inter.user.id, 자원, 수량, 단가)
        )
        await send_ok(
            inter, "상점 등록",
            f"등록 완료! 고유코드 **#{row['listing_id']}**\n"
            f"• 품목: **{item['name']}**\n• 수량: **{수량}**\n• 단가: **{단가} LC**\n"
            f"유효기간: 72시간"
        )

    @group.command(name="목록", description="자원별 현재 매물을 확인합니다.")
    @app_commands.describe(자원="iron/wood/stone/herb/water", 상위="최저가 상위 N개 (기본 10)")
    async def list_open(self, inter: discord.Interaction, 자원: str, 상위: app_commands.Range[int, 1, 20] = 10):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        item = await fetchone("SELECT item_id, name, typ FROM items WHERE item_id=$1", (자원,))
        if not item or item["typ"] != "resource":
            return await send_err(inter, "자원만 조회할 수 있습니다.")
        rows = await fetchall(
            "SELECT listing_id, qty, unit_price, seller_id FROM listings "
            "WHERE country_id=$1 AND resource_id=$2 AND status='open' AND expires_at > NOW() "
            "ORDER BY unit_price ASC, created_at ASC LIMIT $3",
            (cid, 자원, 상위)
        )
        if not rows:
            return await send_ok(inter, "장터", f"**{item['name']}** 매물이 없습니다.")
        agg = await fetchone(
            "SELECT SUM(qty)::BIGINT AS total_qty, MIN(unit_price) AS min_price FROM listings "
            "WHERE country_id=$1 AND resource_id=$2 AND status='open' AND expires_at>NOW()",
            (cid, 자원)
        )
        lines = [f"#{r['listing_id']} • {r['qty']}개 • {r['unit_price']} LC • 판매자 <@{r['seller_id']}>"
                 for r in rows]
        await send_ok(
            inter, "장터(최저가 순)",
            f"품목: **{item['name']}**\n"
            f"• 최저가: **{agg['min_price']} LC**\n"
            f"• 총수량: **{agg['total_qty']}**\n\n" + "\n".join(lines)
        )

    class ConfirmBuy(discord.ui.View):
        def __init__(self, buyer_id: int, listing_id: int, qty: int):
            super().__init__(timeout=30)
            self.buyer_id = buyer_id
            self.listing_id = listing_id
            self.qty = qty
            self.value = None

        @discord.ui.button(label="구매 확정", style=discord.ButtonStyle.green)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.buyer_id:
                return await interaction.response.send_message("타인은 확인할 수 없습니다.", ephemeral=True)
            self.value = True
            self.stop()
            await interaction.response.defer()

        @discord.ui.button(label="취소", style=discord.ButtonStyle.gray)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.buyer_id:
                return await interaction.response.send_message("타인은 취소할 수 없습니다.", ephemeral=True)
            self.value = False
            self.stop()
            await interaction.response.defer()

    @group.command(name="구매", description="상점 고유코드로 매물을 구매합니다.")
    @app_commands.describe(코드="상점 고유코드 (listing_id)", 수량="구매 수량")
    async def buy(self, inter: discord.Interaction, 코드: int, 수량: app_commands.Range[int, 1, 1_000_000_000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        await self._ensure_user(cid, inter.user.id)

        li = await fetchone(
            "SELECT listing_id, seller_id, resource_id, qty, unit_price "
            "FROM listings WHERE listing_id=$1 AND country_id=$2 AND status='open' AND expires_at>NOW()",
            (코드, cid)
        )
        if not li:
            return await send_err(inter, "유효하지 않은 매물입니다.")
        if 수량 > li["qty"]:
            return await send_err(inter, f"구매 수량이 매물 수량을 초과합니다. (남은 {li['qty']})")
        if inter.user.id == li["seller_id"]:
            return await send_err(inter, "자신의 매물은 구매할 수 없습니다.")

        pol = await fetchone("SELECT market_tax_bp FROM countries WHERE country_id=$1", (cid,))
        fee_bp = pol["market_tax_bp"]

        unit_price = li["unit_price"]
        gross = unit_price * 수량
        fee = (gross * fee_bp) // 10000
        net_to_seller = gross - fee

        buyer = await fetchone("SELECT balance FROM users WHERE country_id=$1 AND user_id=$2", (cid, inter.user.id))
        if not buyer or buyer["balance"] < gross:
            return await send_err(inter, f"잔액이 부족합니다. 필요: {gross} LC")

        # 확인 UI
        view = Market.ConfirmBuy(inter.user.id, li["listing_id"], 수량)
        await inter.response.send_message(
            embed=discord.Embed(
                title="🛒 매입 확인",
                description=(
                    f"품목: **{li['resource_id']}**\n"
                    f"단가: **{unit_price} LC**\n"
                    f"수량: **{수량}**\n"
                    f"총액: **{gross} LC**\n"
                    f"시장세(판매자 부담): **{fee} LC**\n"
                    f"판매자 수령액: **{net_to_seller} LC**"
                ),
                color=discord.Color.gold()
            ),
            view=view,
            ephemeral=True
        )
        await view.wait()
        if view.value is not True:
            return  # 취소/타임아웃

        # 체결 처리
        # 1) 구매자 차감
        await execute("UPDATE users SET balance=balance-$1 WHERE country_id=$2 AND user_id=$3",
                      (gross, cid, inter.user.id))
        # 2) 판매자 수령
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",
                      (net_to_seller, cid, li["seller_id"]))
        # 3) 국고(시장세)
        await execute("UPDATE countries SET treasury=treasury+$1 WHERE country_id=$2", (fee, cid))
        await execute(
            "INSERT INTO treasury_ledger(country_id,typ,reason,amount) VALUES($1,'in','시장세 징수',$2)",
            (cid, fee)
        )
        # 4) 구매자 인벤 증가
        await execute(
            "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty=inventory.qty+$4",
            (cid, inter.user.id, li["resource_id"], 수량)
        )
        # 5) 매물 감소/완판
        remain = li["qty"] - 수량
        if remain > 0:
            await execute("UPDATE listings SET qty=$1 WHERE listing_id=$2", (remain, li["listing_id"]))
        else:
            await execute("UPDATE listings SET status='sold' WHERE listing_id=$1", (li["listing_id"],))
        # 6) 거래 기록
        await execute(
            "INSERT INTO trades(country_id,listing_id,buyer_id,seller_id,resource_id,qty,unit_price,fee_paid) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            (cid, li["listing_id"], inter.user.id, li["seller_id"], li["resource_id"], 수량, unit_price, fee)
        )
        # 7) 시세 롤업 + EMA
        d = today_kst_str()
        base = await fetchone("SELECT base_price FROM items WHERE item_id=$1", (li["resource_id"],))
        # 기존 레코드
        old = await fetchone(
            "SELECT avg_price, volume, ema_price FROM price_indices_daily "
            "WHERE country_id=$1 AND item_id=$2 AND date=$3",
            (cid, li["resource_id"], d)
        )
        if old:
            new_vol = old["volume"] + 수량
            new_avg = (old["avg_price"] * old["volume"] + unit_price * 수량) // new_vol
            new_ema = float(EMA_ALPHA) * unit_price + float(1 - EMA_ALPHA) * float(old["ema_price"])
            idx = max(0.5, min(1.5, new_ema / max(1, base["base_price"])))
            await execute(
                "UPDATE price_indices_daily SET avg_price=$1, volume=$2, ema_price=$3, price_index=$4 "
                "WHERE country_id=$5 AND item_id=$6 AND date=$7",
                (new_avg, new_vol, new_ema, idx, cid, li["resource_id"], d)
            )
        else:
            new_avg = unit_price
            new_vol = 수량
            new_ema = float(unit_price)  # 첫 값은 거래가로 초기화
            idx = max(0.5, min(1.5, new_ema / max(1, base["base_price"])))
            await execute(
                "INSERT INTO price_indices_daily(country_id,item_id,date,avg_price,volume,ema_price,price_index) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                (cid, li["resource_id"], d, new_avg, new_vol, new_ema, idx)
            )

        await send_ok(
            inter, "체결 완료",
            f"**#{li['listing_id']}** {li['resource_id']} **{수량}개** 구매 완료!\n"
            f"총액 **{gross} LC**가 지출되었습니다.",
            ephemeral=True
        )

    @group.command(name="취소", description="내가 등록한 매물을 취소하고 남은 수량을 회수합니다.")
    @app_commands.describe(코드="상점 고유코드 (listing_id)")
    async def cancel(self, inter: discord.Interaction, 코드: int):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id

        li = await fetchone(
            "SELECT listing_id, seller_id, resource_id, qty, status FROM listings "
            "WHERE listing_id=$1 AND country_id=$2",
            (코드, cid)
        )
        if not li:
            return await send_err(inter, "해당 매물이 존재하지 않습니다.")
        if li["seller_id"] != inter.user.id:
            return await send_err(inter, "자신의 매물만 취소할 수 있습니다.")
        if li["status"] != "open":
            return await send_err(inter, f"이 매물은 이미 {li['status']} 상태입니다.")

        # 인벤 반환 + 상태 변경
        if li["qty"] > 0:
            await execute(
                "INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty=inventory.qty+$4",
                (cid, inter.user.id, li["resource_id"], li["qty"])
            )
        await execute("UPDATE listings SET status='cancelled' WHERE listing_id=$1", (li["listing_id"],))
        await send_ok(inter, "상점 취소", f"**#{li['listing_id']}** 매물을 취소하고 남은 수량을 회수했습니다.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Market(bot))

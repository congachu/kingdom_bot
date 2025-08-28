import discord
from discord.ext import commands
from discord import app_commands

from utils.db import fetchone, fetchall, execute
from utils.embeds import send_ok, send_err


class Market(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot=bot

    group=app_commands.Group(name="상점", description="유저 간 자원 거래소")

    # ---------- 자동완성 ----------
    async def ac_inv_any(self, inter: discord.Interaction, current: str):
        """보유 중인 모든 아이템/자원 자동완성"""
        cid, uid = inter.guild.id, inter.user.id
        rows = await fetchall(
            "SELECT i.item_id,i.name FROM inventory inv "
            "JOIN items i ON i.item_id=inv.item_id "
            "WHERE inv.country_id=$1 AND inv.user_id=$2 AND inv.qty>0 "
            "AND (i.item_id ILIKE $3 OR i.name ILIKE $3) "
            "ORDER BY i.item_id LIMIT 25",
            (cid, uid, f"%{current}%")
        )
        return [app_commands.Choice(name=f"{r['name']} ({r['item_id']})", value=r["item_id"]) for r in rows]

    async def ac_item_any(self, inter: discord.Interaction, current: str):
        """DB 등록된 모든 아이템 자동완성 (매물 조회용)"""
        rows = await fetchall(
            "SELECT item_id,name FROM items "
            "WHERE item_id ILIKE $1 OR name ILIKE $1 "
            "ORDER BY item_id LIMIT 25",
            (f"%{current}%",)
        )
        return [app_commands.Choice(name=f"{r['name']} ({r['item_id']})", value=r["item_id"]) for r in rows]

    # ---------- 명령어 ----------
    @group.command(name="등록", description="자원을 상점에 등록합니다.")
    @app_commands.autocomplete(아이템=ac_inv_any)
    async def register(self, inter:discord.Interaction, 아이템:str, 수량:int, 단가:int):
        cid,uid=inter.guild.id,inter.user.id
        inv=await fetchone("SELECT qty FROM inventory WHERE country_id=$1 AND user_id=$2 AND item_id=$3",(cid,uid,아이템))
        if not inv or inv["qty"]<수량:
            return await send_err(inter,"재고 부족")
        await execute("UPDATE inventory SET qty=qty-$1 WHERE country_id=$2 AND user_id=$3 AND item_id=$4",(수량,cid,uid,아이템))
        row=await fetchone("INSERT INTO listings(country_id,seller_id,resource_id,qty,unit_price) VALUES ($1,$2,$3,$4,$5) RETURNING listing_id",(cid,uid,아이템,수량,단가))
        prod=await fetchone("SELECT name FROM items WHERE item_id=$1",(아이템,))
        nm=prod["name"] if prod else 아이템
        await send_ok(inter,"상점 등록",f"등록ID {row['listing_id']}\n품목: {nm}\n수량 {수량} 단가 {단가}LC")

    @group.command(name="목록", description="특정 아이템의 매물을 확인합니다.")
    @app_commands.autocomplete(아이템=ac_item_any)
    async def list_open(self, inter:discord.Interaction, 아이템:str):
        cid=inter.guild.id
        rows=await fetchall("SELECT listing_id,seller_id,qty,unit_price FROM listings WHERE country_id=$1 AND resource_id=$2 AND status='open' ORDER BY unit_price ASC",(cid,아이템))
        prod=await fetchone("SELECT name FROM items WHERE item_id=$1",(아이템,))
        nm=prod["name"] if prod else 아이템
        if not rows: return await send_ok(inter,"상점",f"{nm} 매물이 없습니다.")
        lines=[f"• ID {r['listing_id']} | 판매자 <@{r['seller_id']}> | {nm}×{r['qty']} | {r['unit_price']}LC" for r in rows]
        await send_ok(inter,"상점 매물","\n".join(lines))

    @group.command(name="구매", description="상점에서 매물을 구매합니다.")
    async def buy(self, inter:discord.Interaction, 코드:int, 수량:int):
        cid,uid=inter.guild.id,inter.user.id
        li=await fetchone("SELECT * FROM listings WHERE listing_id=$1 AND country_id=$2 AND status='open'",(코드,cid))
        if not li: return await send_err(inter,"해당 매물이 없습니다.")
        if 수량>li["qty"]: return await send_err(inter,"수량 부족")

        prod=await fetchone("SELECT name FROM items WHERE item_id=$1",(li["resource_id"],))
        nm=prod["name"] if prod else li["resource_id"]

        # 금액 계산
        cost = li["unit_price"]*수량
        buyer = await fetchone("SELECT balance FROM users WHERE country_id=$1 AND user_id=$2",(cid,uid))
        if not buyer or buyer["balance"]<cost:
            return await send_err(inter,"잔액 부족")

        # 차감/증가 처리
        await execute("UPDATE users SET balance=balance-$1 WHERE country_id=$2 AND user_id=$3",(cost,cid,uid))
        await execute("UPDATE users SET balance=balance+$1 WHERE country_id=$2 AND user_id=$3",(cost,cid,li["seller_id"]))
        # 재고 이동
        await execute("INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                      "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty=inventory.qty+$4",
                      (cid,uid,li["resource_id"],수량))
        await execute("UPDATE listings SET qty=qty-$1 WHERE listing_id=$2",(수량,li["listing_id"]))
        await execute("DELETE FROM listings WHERE listing_id=$1 AND qty<=0",(li["listing_id"],))

        await send_ok(inter,"구매",f"{nm}×{수량} 구매 완료 (ID {코드})\n지불: {cost}LC")

    @group.command(name="취소", description="내 상점 매물을 취소합니다.")
    async def cancel(self, inter:discord.Interaction, 코드:int):
        cid,uid=inter.guild.id,inter.user.id
        li=await fetchone("SELECT * FROM listings WHERE listing_id=$1 AND country_id=$2",(코드,cid))
        if not li: return await send_err(inter,"없음")
        if li["seller_id"]!=uid: return await send_err(inter,"본인 매물만 취소 가능")

        await execute("UPDATE listings SET status='cancelled' WHERE listing_id=$1",(코드,))
        await execute("INSERT INTO inventory(country_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                      "ON CONFLICT (country_id,user_id,item_id) DO UPDATE SET qty=inventory.qty+$4",
                      (cid,uid,li["resource_id"],li["qty"]))

        prod=await fetchone("SELECT name FROM items WHERE item_id=$1",(li["resource_id"],))
        nm=prod["name"] if prod else li["resource_id"]
        await send_ok(inter,"상점 취소",f"{nm}×{li['qty']} 취소 완료 (ID {코드})")


async def setup(bot:commands.Bot):
    await bot.add_cog(Market(bot))

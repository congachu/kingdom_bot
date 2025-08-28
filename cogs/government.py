# cogs/government.py
from __future__ import annotations
import random
import discord
from discord.ext import commands
from discord import app_commands

from utils.db import fetchone, execute
from utils.embeds import send_ok, send_err
from utils.constants import INITIAL_TREASURY, RESOURCE_TYPES  # land_defaults는 더 이상 사용하지 않음

# -----------------------------
# 토지 티어 정의 (1 ~ 5)
# price: 지정 비용(국고 지출), upkeep: 주간 유지비, base_yield: 일일 최소 수확량(정보 표시용)
# 실제 수확은 economy.py의 LAND_TIERS(yield_min~yield_max)로 결정됨. 여기선 일관성 위해 동일 수치 사용.
LAND_TIERS = {
    1: {"price": 5_000,   "upkeep": 1_000,  "base_yield": 2,  "yield_max": 4},
    2: {"price": 15_000,  "upkeep": 3_000,  "base_yield": 4,  "yield_max": 6},
    3: {"price": 30_000,  "upkeep": 6_000,  "base_yield": 6,  "yield_max": 9},
    4: {"price": 60_000,  "upkeep": 12_000, "base_yield": 9,  "yield_max": 12},
    5: {"price": 120_000, "upkeep": 25_000, "base_yield": 12, "yield_max": 16},
}
# -----------------------------

def pick_resource_for_tier(tier: int) -> str:
    """
    티어가 높을수록 희귀 자원(herb, water)의 가중치를 올려서 배정.
    RESOURCE_TYPES는 ["iron","wood","stone","herb","water"] 형태를 가정.
    """
    base_weight = {"iron": 4, "wood": 4, "stone": 4, "herb": 2, "water": 2}
    # 혹시 constants에 다른/추가 자원이 있어도 최소 1 가중치
    weights = {r: base_weight.get(r, 1) for r in RESOURCE_TYPES}
    weights["herb"] = weights.get("herb", 1) + tier
    weights["water"] = weights.get("water", 1) + tier

    items, w = zip(*weights.items())
    return random.choices(items, weights=w, k=1)[0]


class Government(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="왕국", description="국가와 국고를 다룹니다")

    def _country_id(self, inter: discord.Interaction) -> int:
        if inter.guild is None:
            raise RuntimeError("서버에서만 사용 가능합니다.")
        return inter.guild.id

    @group.command(name="국가생성", description="이 서버를 하나의 왕국으로 창건합니다.")
    async def create_country(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "왕국은 서버에서만 창건할 수 있습니다.")
        cid = inter.guild.id
        row = await fetchone("SELECT 1 FROM countries WHERE country_id=$1", (cid,))
        if row:
            return await send_err(inter, "이미 이 왕국은 세워져 있습니다.")
        await execute(
            "INSERT INTO countries(country_id,name,treasury) VALUES ($1,$2,$3)",
            (cid, inter.guild.name, INITIAL_TREASURY),
        )
        await execute(
            "INSERT INTO treasury_ledger(country_id,typ,reason,amount) VALUES ($1,'in','초기 자본',$2)",
            (cid, INITIAL_TREASURY),
        )
        await send_ok(
            inter,
            "왕국 창건",
            f"국가 **{inter.guild.name}** 창건! 국고에 **{INITIAL_TREASURY:,} LC**가 적립되었습니다. 🏦",
        )

    @group.command(name="국고", description="국고 현황을 확인합니다.")
    async def treasury(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        row = await fetchone(
            "SELECT name, treasury, market_tax_bp FROM countries WHERE country_id=$1", (cid,)
        )
        if not row:
            return await send_err(inter, "아직 왕국이 없습니다. `/왕국 국가생성`으로 시작하세요.")
        await send_ok(
            inter,
            "국고 현황",
            f"왕국: **{row['name']}**\n"
            f"금고: **{row['treasury']:,} LC**\n\n"
            f"정책치(변경 가능):\n"
            f"• 시장세: **{row['market_tax_bp']/100:.2f}%**\n\n"
            f"NPC(고정):\n"
            f"• 자원 매입률 65% • 아이템 매입률 95% • 아이템 매입세 5%",
        )

    @group.command(name="정책설정", description="왕국의 시장세를 조정합니다. (관리자 전용)")
    @app_commands.describe(시장세bp="0~10000 사이 (500=5.00%)")
    async def set_policy(self, inter: discord.Interaction, 시장세bp: app_commands.Range[int, 0, 10000]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        if not inter.user.guild_permissions.administrator:
            return await send_err(inter, "왕국의 조세법은 대관의 서명이 필요합니다. (관리자만 가능)")
        cid = inter.guild.id
        await execute("UPDATE countries SET market_tax_bp=$1 WHERE country_id=$2", (시장세bp, cid))
        await send_ok(inter, "정책 갱신", f"시장세 → **{시장세bp/100:.2f}%** 로 개정되었습니다.")

    # -----------------------------
    # 토지 하위 그룹
    lands = app_commands.Group(name="토지", description="토지 지정과 정보를 다룹니다")

    @lands.command(name="지정", description="이 채널을 자원 생산지로 지정합니다(국고 지출).")
    @app_commands.describe(티어="1~5 등급 선택")
    async def land_assign(self, inter: discord.Interaction, 티어: app_commands.Range[int, 1, 5]):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id

        country = await fetchone("SELECT treasury FROM countries WHERE country_id=$1", (cid,))
        if not country:
            return await send_err(inter, "왕국이 존재하지 않습니다. `/왕국 국가생성` 후 이용하세요.")
        exist = await fetchone(
            "SELECT 1 FROM lands WHERE country_id=$1 AND channel_id=$2", (cid, inter.channel_id)
        )
        if exist:
            return await send_err(inter, "이미 이 채널은 토지로 지정되어 있습니다.")

        # 티어 설정
        conf = LAND_TIERS[int(티어)]
        cost = conf["price"]
        upkeep = conf["upkeep"]
        base_yield = conf["base_yield"]

        if country["treasury"] < cost:
            return await send_err(inter, f"국고가 부족합니다. (필요: {cost:,} LC)")

        # 자원 편향 배정 (티어 가중)
        bias = pick_resource_for_tier(int(티어))

        # 국고 차감 + 장부 기록 + 토지 생성
        await execute("UPDATE countries SET treasury=treasury-$1 WHERE country_id=$2", (cost, cid))
        await execute(
            "INSERT INTO treasury_ledger(country_id,typ,reason,amount) VALUES($1,'out','토지 지정 비용',$2)",
            (cid, cost),
        )
        await execute(
            "INSERT INTO lands(country_id,channel_id,tier,resource_bias,base_yield,upkeep_weekly) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            (cid, inter.channel_id, int(티어), bias, base_yield, upkeep),
        )

        yield_max = conf["yield_max"]
        await send_ok(
            inter,
            "토지 지정",
            f"이 채널이 **티어 {int(티어)}** 토지로 지정되었습니다.\n"
            f"주 생산 편향: **{bias}**\n"
            f"일일 생산량: **{base_yield} ~ {yield_max}개**\n"
            f"주간 유지비: **{upkeep:,} LC**\n"
            f"국고 지출: **{cost:,} LC**",
        )

    @lands.command(name="정보", description="이 채널 토지 정보를 확인합니다.")
    async def land_info(self, inter: discord.Interaction):
        if inter.guild is None:
            return await send_err(inter, "서버에서만 사용 가능합니다.")
        cid = inter.guild.id
        row = await fetchone(
            "SELECT tier,resource_bias,base_yield,upkeep_weekly,created_at "
            "FROM lands WHERE country_id=$1 AND channel_id=$2",
            (cid, inter.channel_id),
        )
        if not row:
            return await send_err(inter, "이 채널은 토지가 아닙니다. `/왕국 토지 지정`으로 지정할 수 있습니다.")

        tier = int(row["tier"])
        conf = LAND_TIERS.get(tier, LAND_TIERS[1])
        yield_min = conf["base_yield"]
        yield_max = conf["yield_max"]

        await send_ok(
            inter,
            "토지 정보",
            f"티어: **{tier}**\n"
            f"주 생산 편향: **{row['resource_bias']}**\n"
            f"일일 생산량: **{yield_min} ~ {yield_max}개**\n"
            f"주간 유지비: **{row['upkeep_weekly']:,} LC**\n"
            f"개설일: {row['created_at']}",
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Government(bot))

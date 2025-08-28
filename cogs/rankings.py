# cogs/rankings.py
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List, Tuple

from utils.db import fetchall

RANK_COLOR = 0xC9A227  # 중세 금색 톤

def fmt_lc(n: int) -> str:
    return f"{n:,} LC"

class Rankings(commands.Cog):
    """국가/개인/서버 순위"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.guild_only()
    @app_commands.default_permissions(view_channel=True)
    class RankGroup(app_commands.Group):
        """순위 명령 그룹"""
        pass

    rank = RankGroup(name="순위", description="국가/개인/서버 자산 순위를 확인합니다.")

    # --- 국가 순위: 국가(서버) 국고 기준 ---
    @rank.command(name="국가", description="국가(서버) 국고 순위")
    @app_commands.describe(개수="가져올 순위 개수 (기본 10, 1~25)")
    async def rank_countries(self, interaction: discord.Interaction, 개수: Optional[int] = 10):
        limit = max(1, min(개수 or 10, 25))
        rows = await fetchall(
            """
            SELECT name, country_id, treasury
            FROM countries
            ORDER BY treasury DESC, country_id ASC
            LIMIT $1
            """,
            (limit,),
        )
        e = discord.Embed(
            title="🏰 국가 순위 (국고)",
            description=f"국고 잔액이 많은 국가 순입니다. (상위 {limit}개)",
            color=RANK_COLOR,
        )
        if not rows:
            e.description = "등록된 국가가 없습니다. `/국가생성`으로 국가를 만들 수 있습니다."
            await interaction.response.send_message(embed=e)
            return

        lines: List[str] = []
        for i, r in enumerate(rows, start=1):
            name = r["name"]
            amount = r["treasury"]
            lines.append(f"**{i}.** `{name}` — **{fmt_lc(amount)}**")
        e.add_field(name="순위", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=e)

    # --- 개인 순위: 전체 서버 통합, 개인 자산 기준 ---
    @rank.command(name="개인", description="개인 자산 글로벌 순위")
    @app_commands.describe(개수="가져올 순위 개수 (기본 10, 1~25)")
    async def rank_users_global(self, interaction: discord.Interaction, 개수: Optional[int] = 10):
        limit = max(1, min(개수 or 10, 25))
        rows = await fetchall(
            """
            SELECT u.country_id, u.user_id, u.balance, c.name AS country_name
            FROM users u
            JOIN countries c ON c.country_id = u.country_id
            ORDER BY u.balance DESC, u.user_id ASC
            LIMIT $1
            """,
            (limit,),
        )
        e = discord.Embed(
            title="👑 개인 순위 (글로벌)",
            description=f"개인 잔액이 많은 순입니다. (상위 {limit}명)",
            color=RANK_COLOR,
        )
        if not rows:
            e.description = "등록된 사용자가 없습니다. 서버에서 활동을 시작해 보세요!"
            await interaction.response.send_message(embed=e)
            return

        def mention_or_id(uid: int) -> str:
            # 캐시에 있으면 멘션, 없으면 ID 표시
            u = self.bot.get_user(int(uid))
            return u.mention if u else f"`{uid}`"

        lines: List[str] = []
        for i, r in enumerate(rows, start=1):
            user_disp = mention_or_id(r["user_id"])
            bal = r["balance"]
            cname = r["country_name"]
            lines.append(f"**{i}.** {user_disp} — **{fmt_lc(bal)}** · `{cname}`")
        e.add_field(name="순위", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=e)

    # --- 서버 순위: 현재 길드 내 개인 자산 기준 ---
    @rank.command(name="서버", description="현재 서버 내 개인 자산 순위")
    @app_commands.describe(개수="가져올 순위 개수 (기본 10, 1~25)")
    async def rank_server_local(self, interaction: discord.Interaction, 개수: Optional[int] = 10):
        limit = max(1, min(개수 or 10, 25))
        gid = interaction.guild_id
        rows = await fetchall(
            """
            SELECT user_id, balance
            FROM users
            WHERE country_id = $1
            ORDER BY balance DESC, user_id ASC
            LIMIT $2
            """,
            (gid, limit),
        )
        e = discord.Embed(
            title="🏹 서버 개인 순위",
            description=f"이 서버(국가) 내 개인 잔액 순입니다. (상위 {limit}명)",
            color=RANK_COLOR,
        )

        if not rows:
            e.description = "이 서버에 등록된 사용자가 없습니다. 활동을 시작해 보세요!"
            await interaction.response.send_message(embed=e)
            return

        lines: List[str] = []
        for i, r in enumerate(rows, start=1):
            member = interaction.guild.get_member(int(r["user_id"])) if interaction.guild else None
            name = member.mention if member else f"`{r['user_id']}`"
            lines.append(f"**{i}.** {name} — **{fmt_lc(r['balance'])}**")

        e.add_field(name="순위", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=e)

async def setup(bot: commands.Bot):
    await bot.add_cog(Rankings(bot))

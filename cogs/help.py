# cogs/help.py
import discord
from discord import app_commands
from discord.ext import commands
from typing import Dict, List, Optional

HELP_COLOR = 0xC9A227  # 중세 금색

# 간단한 도움말 데이터 (핵심 명령 위주)
HELP_INDEX: Dict[str, Dict[str, str]] = {
    "국가": {
        "국가생성": "이 서버를 국가로 등록하고 국고를 초기화합니다.",
        "토지지정": "채널을 토지로 설정합니다. 랜덤 자원이 배정됩니다.",
    },
    "경제": {
        "정산": "토지 채널에서 하루 1회 자원을 수령합니다.",
        "레시피목록": "제작 가능한 레시피 목록을 보여줍니다.",
        "레시피상세": "특정 레시피의 재료와 산출물을 보여줍니다.",
    },
    "상점": {
        "상점등록": "자원을 상점에 등록하여 판매합니다.",
        "상점목록": "등록된 매물과 시세를 확인합니다.",
        "상점구매": "고유코드로 상점 매물을 구매합니다.",
        "상점취소": "본인이 등록한 매물을 취소합니다.",
    },
    "순위": {
        "순위 국가": "국가(서버) 국고 순위",
        "순위 개인": "개인 자산 글로벌 순위",
        "순위 서버": "현재 서버 내 개인 자산 순위",
    },
    "기타": {
        "시세": "자원/아이템 시세(전체 또는 단일)를 확인합니다.",
        "국고": "국고 잔액 및 최근 내역을 확인합니다.",
    }
}

# 개별 상세 설명(있는 경우)
DETAILS: Dict[str, str] = {
    "국가생성": "서버를 국가로 등록합니다. 초기에 국고가 지급되며 세율 등 기본 설정이 적용됩니다.",
    "토지지정": "현재 채널을 '토지'로 지정합니다. 국가 국고에서 비용이 차감되며 자원은 랜덤으로 정해집니다.",
    "정산": "토지 채널에서 하루 1회 자원을 수령합니다. (채널별 1회)",
    "레시피목록": "제작 가능한 아이템 목록을 표시합니다.",
    "레시피상세": "`/레시피상세 <아이템>` 형태로 사용하세요. 입력은 자동완성을 지원합니다.",
    "상점등록": "보유 자원을 상점에 등록합니다. 수수료/세금이 부과되며 시세에 영향을 줍니다.",
    "상점목록": "현재 등록된 매물을 종류·가격순으로 보여줍니다.",
    "상점구매": "매물 고유코드로 구매합니다. 확인 메시지 후 결제됩니다.",
    "상점취소": "판매자가 자신의 매물을 취소합니다.",
    "순위 국가": "국가(서버)의 국고 잔액 기준 순위입니다.",
    "순위 개인": "모든 서버 통합 개인 잔액 기준 순위입니다.",
    "순위 서버": "현재 서버(국가) 내 개인 잔액 기준 순위입니다.",
    "시세": "자원/아이템의 시세(EMA 기반)를 보여줍니다. 지정 없으면 전체 시세.",
    "국고": "국고 잔액 및 최근 입출 내역을 임베드로 표시합니다.",
}

COMMAND_CHOICES: List[str] = [name for section in HELP_INDEX.values() for name in section.keys()]

class HelpCog(commands.Cog):
    """Kingdom Bot 도움말"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # 자동완성
    async def _autocomplete_commands(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        current_lower = (current or "").lower()
        opts = [c for c in COMMAND_CHOICES if current_lower in c.lower()]
        return [app_commands.Choice(name=o, value=o) for o in opts[:25]]

    @app_commands.command(name="도움말", description="명령어 도움말을 표시합니다.")
    @app_commands.describe(명령어="특정 명령어 이름(선택). 입력하면 해당 명령만 상세 표시")
    @app_commands.autocomplete(명령어=_autocomplete_commands)
    async def help_root(self, interaction: discord.Interaction, 명령어: Optional[str] = None):
        if 명령어:
            # 특정 명령 상세
            title = f"📜 명령어 도움말 — {명령어}"
            desc = DETAILS.get(명령어, "해당 명령에 대한 상세 설명이 준비되어 있지 않습니다.")
            embed = discord.Embed(title=title, description=desc, color=HELP_COLOR)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 인덱스(전체)
        embed = discord.Embed(
            title="🏰 Kingdom Bot 도움말",
            description="중세 왕국 경제 시스템 봇입니다. 아래 목차에서 명령을 확인하세요.\n"
                        "특정 명령만 보고 싶다면 `/도움말 명령어:`에 입력하면 자동완성이 떠요.",
            color=HELP_COLOR,
        )
        embed.set_thumbnail(url="https://em-content.zobj.net/thumbs/120/apple/354/classical-building_1f3db-fe0f.png")

        for section, cmds in HELP_INDEX.items():
            lines = [f"• **/{name}** — {desc}" for name, desc in cmds.items()]
            # 너무 길면 잘라서 여러 필드로 나눌 수도 있지만 기본은 한 필드에
            embed.add_field(name=f"【 {section} 】", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))

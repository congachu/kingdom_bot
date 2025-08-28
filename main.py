import os
import asyncio
import datetime
import discord
from discord.ext import commands
import psycopg2
from dotenv import load_dotenv
from utils.db import init_db

load_dotenv()

class AClient(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # Server Members Intent (필요 시 개발자 포털에서 활성화)

        super().__init__(command_prefix="!", intents=intents)
        self.synced = False
        self.start_time = datetime.datetime.utcnow()  # 업타임 기준(UTC)

    # --------- 유틸 ----------
    @staticmethod
    def format_uptime(delta: datetime.timedelta) -> str:
        total = int(delta.total_seconds())
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days:
            return f"{days}d {hours}h {minutes}m"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    async def setup_hook(self):
        # 코그 로드
        await init_db()
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")

        # 슬래시 동기화 1회
        if not self.synced:
            await self.tree.sync()
            self.synced = True

        # 상태 업데이트를 백그라운드 태스크로 시작
        self.loop.create_task(self.update_status())

        print("✅ 준비 완료")

    async def on_ready(self):
        print(f"✅ {self.user} 로그인 완료")
        # 로그인 직후 1회 즉시 상태 갱신
        await self.set_presence_once()

    async def on_guild_join(self, guild: discord.Guild):
        # 서버 추가 시 즉시 상태 갱신
        await self.set_presence_once()

    async def on_guild_remove(self, guild: discord.Guild):
        # 서버 제거 시 즉시 상태 갱신
        await self.set_presence_once()

    async def set_presence_once(self):
        """상태 메시지를 즉시 한 번 갱신"""
        try:
            uptime = datetime.datetime.utcnow() - self.start_time
            guild_count = len(self.guilds)
            text = f"🏰 {guild_count} kingdoms | ⏱ {self.format_uptime(uptime)}"
            # Game 상태(원하면 ActivityType.watching 등으로 변경 가능)
            await self.change_presence(activity=discord.Game(text))
        except Exception as e:
            print(f"❌ 상태 즉시 갱신 오류: {e}")

    # --------- 상태 메시지 루프(업타임 + 서버 수) ---------
    async def update_status(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                uptime = datetime.datetime.utcnow() - self.start_time
                guild_count = len(self.guilds)
                text = f"🏰 {guild_count} kingdoms | ⏱ {self.format_uptime(uptime)}"
                await self.change_presence(activity=discord.Game(text))
                await asyncio.sleep(60)  # 1분마다 업데이트
            except Exception as e:
                print(f"❌ 상태 업데이트 오류: {e}")
                await asyncio.sleep(5)

client = AClient()

try:
    client.run(os.getenv("DISCORD_TOKEN"))
except KeyboardInterrupt:
    print("🛑 봇이 중지되었습니다.")
except Exception as e:
    print(f"❌ 봇 실행 중 오류 발생: {e}")

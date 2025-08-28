# utils/embeds.py
import discord
from typing import Optional

MEDIEVAL_COLOR = discord.Color.gold()

def parchment(title: str, desc: str = "", *, footer: Optional[str] = None) -> discord.Embed:
    emb = discord.Embed(title=f"🏰 {title}", description=desc, color=MEDIEVAL_COLOR)
    emb.set_thumbnail(url="https://em-content.zobj.net/thumbs/240/apple/354/scroll_1f4dc.png")
    if footer:
        emb.set_footer(text=footer)
    return emb

async def send_ok(inter: discord.Interaction, title: str, desc: str = "", *, ephemeral: bool=False):
    if inter.response.is_done():
        await inter.followup.send(embed=parchment(title, desc), ephemeral=ephemeral)
    else:
        await inter.response.send_message(embed=parchment(title, desc), ephemeral=ephemeral)

async def send_err(inter: discord.Interaction, message: str):
    emb = discord.Embed(title="⚠️ 왕의 칙령", description=message, color=discord.Color.red())
    if inter.response.is_done():
        await inter.followup.send(embed=emb, ephemeral=True)
    else:
        await inter.response.send_message(embed=emb, ephemeral=True)

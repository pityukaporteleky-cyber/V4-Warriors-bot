import os
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
SCRIM_TIMEZONE = ZoneInfo("Europe/Budapest")
OPEN_HOUR = 10
OPEN_MINUTE = 0
CLOSE_HOUR = 13
CLOSE_MINUTE = 0

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

registered_teams = []
registration_open = False


def current_scrim_time() -> datetime:
    return datetime.now(SCRIM_TIMEZONE)


def is_within_open_window() -> bool:
    now = current_scrim_time()
    current_hm = (now.hour, now.minute)
    return (OPEN_HOUR, OPEN_MINUTE) <= current_hm < (CLOSE_HOUR, CLOSE_MINUTE)


@bot.event
async def on_ready():
    print(f"Bejelentkezve mint: {bot.user} (ID: {bot.user.id})")


@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send(f"Pong! ({round(bot.latency * 1000)} ms)")


@bot.command(name="scrimstatus")
async def scrim_status(ctx):
    now = current_scrim_time()
    status_text = "NYITVA" if registration_open else "ZÁRVA"
    window_text = "időablakon belül" if is_within_open_window() else "időablakon kívül"
    await ctx.send(
        f"Státusz: **{status_text}** ({window_text}) | "
        f"Idő: `{now.strftime('%Y-%m-%d %H:%M')}` | "
        f"Csapatok: `{len(registered_teams)}`"
    )


@bot.command(name="open")
@commands.has_permissions(manage_guild=True)
async def open_reg(ctx):
    global registration_open, registered_teams
    registration_open = True
    registered_teams = []
    await ctx.send("Scrim regisztráció **MEGNYITVA**!")


@bot.command(name="close")
@commands.has_permissions(manage_guild=True)
async def close_reg(ctx):
    global registration_open
    registration_open = False
    await ctx.send(f"Scrim regisztráció **LEZÁRVA**! Regisztrált csapatok: `{len(registered_teams)}`.")


@bot.command(name="reg")
async def register_team(ctx, *, team_name: str = None):
    if not registration_open:
        await ctx.send("A regisztráció jelenleg zárva van.")
        return
    if not team_name:
        await ctx.send("Add meg a csapatnevet! Példa: `!reg V4 | Warriors`")
        return
    registered_teams.append(team_name.strip())
    await ctx.send(f"Regisztrálva: **{team_name.strip()}** (#{len(registered_teams)})")


if not TOKEN:
    raise RuntimeError("Hiányzik a DISCORD_TOKEN környezeti változó!")

bot.run(TOKEN)

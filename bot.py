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
    print(f"Logged in as: {bot.user} (ID: {bot.user.id})")


@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send(f"Pong! ({round(bot.latency * 1000)} ms)")


@bot.command(name="scrimstatus")
async def scrim_status(ctx):
    now = current_scrim_time()
    status_text = "OPEN" if registration_open else "CLOSED"
    window_text = "inside window" if is_within_open_window() else "outside window"
    await ctx.send(
        f"Status: **{status_text}** ({window_text}) | "
        f"Time: `{now.strftime('%Y-%m-%d %H:%M')}` | "
        f"Teams: `{len(registered_teams)}`"
    )


@bot.command(name="open")
@commands.has_permissions(manage_guild=True)
async def open_reg(ctx):
    global registration_open, registered_teams
    registration_open = True
    registered_teams = []
    await ctx.send("Scrim registration is now **OPEN**!")


@bot.command(name="close")
@commands.has_permissions(manage_guild=True)
async def close_reg(ctx):
    global registration_open
    registration_open = False
    await ctx.send(f"Scrim registration is now **CLOSED**! Registered teams: `{len(registered_teams)}`.")


@bot.command(name="reg")
async def register_team(ctx, *, team_name: str = None):
    if not registration_open:
        await ctx.send("Registration is currently closed.")
        return
    if not team_name:
        await ctx.send("Please provide a team name! Example: `!reg V4 | Warriors`")
        return
    registered_teams.append(team_name.strip())
    await ctx.send(f"Registered: **{team_name.strip()}** (#{len(registered_teams)})")


if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable!")

bot.run(TOKEN)

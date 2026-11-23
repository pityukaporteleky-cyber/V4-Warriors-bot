import os
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
SCRIM_TIMEZONE = ZoneInfo("Europe/Budapest")  # CET / CEST (16:00 CEST nyár, 16:00 CET tél)
OPEN_HOUR = 16
OPEN_MINUTE = 0

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

registered_teams = []
registration_open = False


def current_scrim_time() -> datetime:
    return datetime.now(SCRIM_TIMEZONE)


def is_after_open_time() -> bool:
    now = current_scrim_time()
    return (now.hour, now.minute) >= (OPEN_HOUR, OPEN_MINUTE)


@bot.event
async def on_ready():
    print(f"Bejelentkezve mint: {bot.user} (ID: {bot.user.id})")


@bot.command(name="nyit")
@commands.has_permissions(manage_messages=True)
async def open_reg(ctx):
    global registration_open, registered_teams
    if not is_after_open_time():
        now_str = current_scrim_time().strftime("%H:%M")
        await ctx.send(
            f"A regisztráció csak **16:00** után nyitható meg (jelenlegi idő: **{now_str}**)."
        )
        return
    registered_teams = []
    registration_open = True
    await ctx.send(
        "🟢 **16:00-s SCRIM REGISZTRÁCIÓ MEGNYITVA!**\n"
        "Írd be: `!reg <Csapatnév>` a jelentkezéshez!"
    )


@bot.command(name="zar")
@commands.has_permissions(manage_messages=True)
async def close_reg(ctx):
    global registration_open
    registration_open = False
    await ctx.send("🔴 **A scrim regisztráció lezárult!**")


@bot.command(name="reg")
async def register_team(ctx, *, team_name: str):
    if not registration_open:
        await ctx.send(
            f"{ctx.author.mention}, a regisztráció jelenleg nincs megnyitva (16:00-kor indul)."
        )
        return
    if any(t["user_id"] == ctx.author.id for t in registered_teams):
        await ctx.send(f"{ctx.author.mention}, te már regisztráltál csapatot!")
        return

    slot = len(registered_teams) + 1
    registered_teams.append(
        {
            "slot": slot,
            "team_name": team_name.strip(),
            "user_id": ctx.author.id,
        }
    )
    await ctx.send(
        f"✅ **Slot #{slot}** rögzítve: **{team_name.strip()}** ({ctx.author.mention})"
    )


@bot.command(name="lista")
async def list_teams(ctx):
    if not registered_teams:
        await ctx.send("Még nincs regisztrált csapat.")
        return
    lines = [f"**#{t['slot']}** – {t['team_name']}" for t in registered_teams]
    await ctx.send("📋 **Regisztrált csapatok:**\n" + "\n".join(lines))


if not TOKEN:
    raise RuntimeError("Hiányzik a DISCORD_TOKEN környezeti változó!")

bot.run(TOKEN)

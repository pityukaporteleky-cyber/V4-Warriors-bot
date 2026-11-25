import asyncio
from datetime import datetime, time, timedelta, timezone
import json
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from discord.ext import commands

# =====================================================================
# BOT CONFIGURATION & CHANNEL SETTINGS (MATCHING YOUR DISCORD SERVER)
# =====================================================================

TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
PREFIX = "%"
VIP_ROLE_NAME = "Vip_role"
WELCOME_CHANNEL_NAME = "welcome"
TEAM_LOGO_CHANNEL = "team-logo"
DEFAULT_RESULTS_CHANNEL = "scrim-results"
RESULTS_LOG_FILE = "results_log.json"

# TDM channels and max slots
TDM_REG_CHANNEL = "tdm-registration"
TDM_SLOT_CHANNEL = "tdm-slot"
TDM_IDPW_CHANNEL = "tdm-id-pw"
TDM_RESULT_CHANNEL = "tdm-result-eredmèny"
TDM_MAX_SLOTS = 40

# 20:00 Tournament channels (manual open! / close!, no VIP, no waitlist)
TOURNAMENT_20_CFG = {
    "label": "20:00 TOURNAMENT",
    "slot_channel": "20-tournament-slot",
    "reg_channel": "20-tournament-registration",
    "wait_channel": None,
    "idpw_channel": "20-tournamnet-id-pw",
    "result_channel": "20-tournamnet-eredmémeny-result",
}

# Ban log channels and unban fee
SCRIMS_BAN_CHANNEL = "scrims-ban"
TOURNAMENT_BAN_CHANNEL = "tournament-ban"
PERMABAN_CHANNEL = "permaban"
UNBAN_FEE_EUR = 5
BAN_DATA_FILE = "bans_data.json"


def get_local_timezone():
    try:
        return ZoneInfo("Europe/Budapest")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=1))


TIMEZONE = get_local_timezone()

# Daily scrim schedules (16:00, 20:00, 23:00)
# 16:00 -> VIP: 08:00 - 15:30 (VIP only), Normal: 10:00 - 13:00
# 20:00 -> VIP: 08:00 - 18:00 (VIP only), Normal: 15:00 - 18:00
# 23:00 -> VIP: 08:00 - 18:00 (VIP only), Normal: 16:00 - 18:00
SCHEDULES = {
    16: {
        "label": "16:00 SCRIM",
        "slot_channel": "16-slots",
        "reg_channel": "16-registration",
        "wait_channel": "16-wait-list",
        "idpw_channel": "16-id-pw",
        "result_channel": "16-result-eredmény",
        "vip": (time(8, 0), time(15, 30)),
        "reg": (time(10, 0), time(13, 0)),
        "waitlist": (time(15, 20), time(15, 30)),
    },
    20: {
        "label": "20:00 SCRIM",
        "slot_channel": "20-slot",
        "reg_channel": "20-registration",
        "wait_channel": "20-wait-list",
        "idpw_channel": "20-id-pw",
        "result_channel": "20-result-eredmény",
        "vip": (time(8, 0), time(18, 0)),
        "reg": (time(15, 0), time(18, 0)),
        "waitlist": (time(19, 20), time(19, 30)),
    },
    23: {
        "label": "23:00 SCRIM",
        "slot_channel": "23-slot",
        "reg_channel": "23-registration",
        "wait_channel": "23-wait-list",
        "idpw_channel": "23-id-pw",
        "result_channel": "23-result-eredmény",
        "vip": (time(8, 0), time(18, 0)),
        "reg": (time(16, 0), time(18, 0)),
        "waitlist": (time(22, 20), time(22, 30)),
    },
}

# Placement points table (1 kill = 1 point)
PLACEMENT_POINTS = {
    1: 10,
    2: 6,
    3: 5,
    4: 4,
    5: 3,
    6: 2,
    7: 1,
    8: 1,
}
KILL_POINT_VALUE = 1


def get_placement_points(place: int) -> int:
    """Return placement points for positions 1..20 (9-20 = 0)."""
    if place in PLACEMENT_POINTS:
        return PLACEMENT_POINTS[place]
    if 9 <= place <= 20:
        return 0
    return 0


# =====================================================================
# INTENTS, BOT INSTANCE & STATE STORAGE
# =====================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

ALLOWED_MENTIONS = discord.AllowedMentions(everyone=True, users=True, roles=True)

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    allowed_mentions=ALLOWED_MENTIONS,
)

scrim_state = {}
tourney20_state = {
    "slots": {},
    "slots_msg_id": None,
    "last_day_reset": None,
    "manual_open": False,
    "room_id": "-",
    "room_pw": "-",
}
tdm_state = {
    "slots": {},
    "slots_msg_id": None,
    "is_open": False,
}
ban_records = {}
results_log = []

_view_registered = False
_schedule_task = None


# =====================================================================
# PERSISTENCE: BAN RECORDS & RESULTS LOG
# =====================================================================

def load_ban_records():
    global ban_records
    if os.path.exists(BAN_DATA_FILE):
        try:
            with open(BAN_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    ban_records = data
        except Exception as e:
            print(f"[ERROR] load_ban_records: {e}")


def save_ban_records():
    try:
        with open(BAN_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(ban_records, f, indent=2)
    except Exception as e:
        print(f"[ERROR] save_ban_records: {e}")


def load_results_log():
    global results_log
    if os.path.exists(RESULTS_LOG_FILE):
        try:
            with open(RESULTS_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    results_log = data
        except Exception as e:
            print(f"[ERROR] load_results_log: {e}")


def save_results_log():
    try:
        with open(RESULTS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(results_log, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] save_results_log: {e}")


def get_ban_info(user_id: int) -> dict:
    key = str(user_id)
    rec = ban_records.get(key)
    if not rec:
        return {
            "misses": 0,
            "ban_until": None,
            "permanent": False,
            "active": False,
            "label": "None",
        }
    if rec.get("permanent"):
        return {
            "misses": rec.get("misses", 4),
            "ban_until": None,
            "permanent": True,
            "active": True,
            "label": "PERMANENT BAN",
        }
    until_str = rec.get("ban_until")
    if until_str:
        try:
            until_dt = datetime.fromisoformat(until_str)
            if datetime.now(TIMEZONE) < until_dt:
                return {
                    "misses": rec.get("misses", 0),
                    "ban_until": until_str,
                    "permanent": False,
                    "active": True,
                    "label": f"Until {until_dt.strftime('%Y-%m-%d %H:%M')}",
                }
        except Exception:
            pass
    return {
        "misses": rec.get("misses", 0),
        "ban_until": None,
        "permanent": False,
        "active": False,
        "label": "Expired",
    }


def apply_missed_match_ban(user_id: int) -> dict:
    key = str(user_id)
    rec = ban_records.get(key, {"misses": 0, "ban_until": None, "permanent": False})
    misses = int(rec.get("misses", 0)) + 1
    now_dt = datetime.now(TIMEZONE)

    if misses == 1:
        rec = {
            "misses": 1,
            "ban_until": (now_dt + timedelta(days=2)).isoformat(),
            "permanent": False,
        }
        duration_text, is_perma = "2 days", False
    elif misses == 2:
        rec = {
            "misses": 2,
            "ban_until": (now_dt + timedelta(days=7)).isoformat(),
            "permanent": False,
        }
        duration_text, is_perma = "1 week (7 days)", False
    elif misses == 3:
        rec = {
            "misses": 3,
            "ban_until": (now_dt + timedelta(days=30)).isoformat(),
            "permanent": False,
        }
        duration_text, is_perma = "1 month (30 days)", False
    else:
        rec = {
            "misses": misses,
            "ban_until": None,
            "permanent": True,
        }
        duration_text, is_perma = "PERMANENT", True

    ban_records[key] = rec
    save_ban_records()
    return {
        "misses": misses,
        "duration_text": duration_text,
        "permanent": is_perma,
    }


def clear_user_ban(user_id: int):
    key = str(user_id)
    if key in ban_records:
        ban_records[key]["ban_until"] = None
        ban_records[key]["permanent"] = False
        ban_records[key]["misses"] = 0
        save_ban_records()


async def post_ban_log(
    guild: discord.Guild,
    mode: str,
    user_id: int,
    reason_line: str,
    permanent: bool,
):
    member = guild.get_member(user_id)
    mention = member.mention if member else f"<@{user_id}>"
    if permanent:
        ch_name = PERMABAN_CHANNEL
    elif mode == "scrim":
        ch_name = SCRIMS_BAN_CHANNEL
    else:
        ch_name = TOURNAMENT_BAN_CHANNEL

    ch = discord.utils.get(guild.text_channels, name=ch_name)
    if isinstance(ch, discord.TextChannel):
        await ch.send(f"🚫 {mention} (`{user_id}`) — {reason_line}")
# =====================================================================
# TIME WINDOWS & SLOT BOARDS
# =====================================================================

def init_hour_state(hour: int):
    if hour not in scrim_state:
        scrim_state[hour] = {
            "slots": {},
            "waitlist": [],
            "slots_msg_id": None,
            "last_day_reset": None,
            "manual_open": None,
            "last_announced_state": None,
        }
    return scrim_state[hour]


def is_in_window(now_t: time, start_t: time, end_t: time) -> bool:
    if start_t <= end_t:
        return start_t <= now_t <= end_t
    return now_t >= start_t or now_t <= end_t


def is_vip_member(member: discord.Member) -> bool:
    return any(r.name == VIP_ROLE_NAME for r in member.roles)


def get_scrim_access_phase(
    hour: int, member: discord.Member, now_t: time, manual_open: bool | None = None
) -> tuple[str | None, str]:
    cfg = SCHEDULES[hour]
    if manual_open is False:
        return None, f"{cfg['label']} registration is currently closed."

    in_vip_win = is_in_window(now_t, cfg["vip"][0], cfg["vip"][1])
    in_reg_win = is_in_window(now_t, cfg["reg"][0], cfg["reg"][1])
    in_wait_win = is_in_window(now_t, cfg["waitlist"][0], cfg["waitlist"][1])
    vip = is_vip_member(member)

    if manual_open is True:
        return ("vip" if vip else "reg"), ""

    if vip:
        if in_vip_win or in_reg_win:
            return "vip", ""
        if in_wait_win:
            return "waitlist", ""
        return None, f"{cfg['label']} VIP window is closed."

    if in_reg_win:
        return "reg", ""
    if in_wait_win:
        return "waitlist", ""
    if in_vip_win:
        return None, f"{cfg['label']} is VIP-only right now; regular reg opens at {cfg['reg'][0].strftime('%H:%M')}."
    return None, f"{cfg['label']} regular registration is closed."


async def announce_everyone(channel: discord.TextChannel | None, text: str):
    if isinstance(channel, discord.TextChannel):
        await channel.send(f"@everyone {text}", allowed_mentions=ALLOWED_MENTIONS)
def format_slot_board_from_state(title: str, st: dict, include_waitlist: bool = True) -> str:
    lines = [
        f"**{title} — SLOT LIST**",
        "",
        "🔒 **Slot 1:** Locked",
        "🔒 **Slot 2:** Locked",
        "",
        "**Regular Slots (3–20):**",
    ]
    for s in range(3, 21):
        entry = st["slots"].get(s)
        if entry:
            label = f"{entry['team']} [{entry['tag']}]"
            if entry.get("confirmed"):
                label = f"__{label}__"
            lines.append(f"**Slot {s}:** {label} — <@{entry['manager_id']}>")
        else:
            lines.append(f"**Slot {s}:** *Open*")

    lines.extend(["", f"**VIP Slots (24–21 — `{VIP_ROLE_NAME}`):**"])
    for s in [24, 23, 22, 21]:
        entry = st["slots"].get(s)
        if entry:
            label = f"{entry['team']} [{entry['tag']}]"
            if entry.get("confirmed"):
                label = f"__{label}__"
            lines.append(f"👑 **Slot {s}:** {label} — <@{entry['manager_id']}>")
        else:
            lines.append(f"👑 **Slot {s}:** *Open (VIP)*")

    if include_waitlist and st.get("waitlist"):
        lines.extend(["", "**Wait-list:**"])
        for idx, w in enumerate(st["waitlist"], start=1):
            lines.append(f"{idx}. {w['team']} [{w['tag']}] — <@{w['manager_id']}>")

    lines.extend(["", "React ✅ to confirm your slot | React ❌ to cancel your slot."])
    return "\n".join(lines)


async def sync_board_message(slot_ch: discord.TextChannel | None, st: dict, content: str):
    if slot_ch is None:
        return
    msg_id = st.get("slots_msg_id")
    try:
        if msg_id:
            try:
                msg = await slot_ch.fetch_message(msg_id)
                await msg.edit(content=content)
                await ensure_board_reactions(msg)
                return
            except discord.NotFound:
                pass
        msg = await slot_ch.send(content=content)
        st["slots_msg_id"] = msg.id
        await ensure_board_reactions(msg)
    except Exception as e:
        print(f"[ERROR] sync_board_message #{slot_ch.name}: {e}")


async def send_scrim_state_announcement(
    reg_ch: discord.TextChannel | None,
    wait_ch: discord.TextChannel | None,
    hour: int,
    new_state: str,
):
    label = SCHEDULES[hour]["label"]
    if new_state == "vip":
        await announce_everyone(
            reg_ch,
            f"👑 **VIP regisztráció MEGNYÍLT ({label})!** Csak a `{VIP_ROLE_NAME}` ranggal rendelkező csapatok regisztrálhatnak most.",
        )
    elif new_state == "reg":
        await announce_everyone(
            reg_ch,
            f"🟢 **A regisztráció MEGNYÍLT ({label})!** Most már mindenki jelentkezhet a szabad helyekre.",
        )
    elif new_state == "waitlist":
        await announce_everyone(
            wait_ch or reg_ch,
            f"⏳ **Waitlist MEGNYÍLT ({label})!** A várólistás jelentkezés most elindult.",
        )
    elif new_state == "closed":
        await announce_everyone(
            reg_ch,
            f"🔴 **A regisztráció LEZÁRULT ({label})!** A jelentkezés jelenleg zárva van.",
        )
def parse_result_rows(lines: list[str]) -> tuple[str | None, list[dict], str | None]:
    match_title = None
    rows = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.lower().startswith(("match:", "title:", "game:")):
            match_title = line.split(":", 1)[1].strip()
            continue

        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
        elif ";" in line:
            parts = [p.strip() for p in line.split(";")]
        else:
            parts = [p.strip() for p in line.split(",")]

        if len(parts) < 3:
            if match_title is None and not rows:
                match_title = line
                continue
            return None, [], f"Invalid row `{line}`."

        place_match = re.search(r"\d+", parts[-2])
        kills_match = re.search(r"\d+", parts[-1])
        team_name = " | ".join(parts[:-2]).strip()
        if not team_name or not place_match or not kills_match:
            return None, [], f"Could not parse `{line}`."

        place = int(place_match.group(0))
        kills = int(kills_match.group(0))
        place_pts = get_placement_points(place)
        kill_pts = kills * KILL_POINT_VALUE
        rows.append(
            {
                "team": team_name,
                "place": place,
                "kills": kills,
                "place_points": place_pts,
                "kill_points": kill_pts,
                "total_points": place_pts + kill_pts,
            }
        )

    if not rows:
        return match_title, [], "No valid rows."

    # Highest total points always first
    rows.sort(key=lambda r: (-r["total_points"], -r["kills"], r["place"]))
    return match_title, rows, None
@bot.command(name="result", aliases=["res"])
@commands.has_permissions(manage_channels=True)
async def result_cmd(ctx: commands.Context, *, content: str = ""):
    if ctx.guild is None:
        return
    image_url = get_attached_image_url(ctx.message)
    raw_lines = [line for line in content.splitlines() if line.strip()]
    first_token = raw_lines[0].strip() if raw_lines else None
    target_ch, consumed = resolve_result_target_channel(ctx.guild, first_token)
    if consumed:
        raw_lines = raw_lines[1:]
    match_title, rows, err = parse_result_rows(raw_lines)
    if err:
        await ctx.message.add_reaction("❌")
        await ctx.send(f"❌ {err}")
        return
    final_title = match_title or f"Match #{len(results_log) + 1}"
    embed = build_result_embed(final_title, rows, str(ctx.author), image_url)
    if isinstance(target_ch, discord.TextChannel):
        await target_ch.send(embed=embed)
    else:
        await ctx.send(embed=embed)
    results_log.append({
        "id": len(results_log) + 1,
        "match_title": final_title,
        "timestamp": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "submitted_by": str(ctx.author),
        "winner": rows[0]["team"],
        "winner_points": rows[0]["total_points"],
        "total_kills": sum(r["kills"] for r in rows),
        "image_url": image_url,
        "standings": rows,
    })
    save_results_log()
    await ctx.message.add_reaction("✅")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None or not isinstance(message.channel, discord.TextChannel):
        return
    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if lines and lines[0].lower() == "%logo":
        if message.channel.name != TEAM_LOGO_CHANNEL or len(lines) < 3 or not get_attached_image_url(message):
            await message.add_reaction("❌")
            return
        await message.add_reaction("✅")
        await message.reply(f"✅ Team logo received — **{lines[1]}** [{lines[2]}]")
        return
    await bot.process_commands(message)


if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("Missing bot token: set DISCORD_TOKEN.")

bot.run(TOKEN)
@bot.command(name="stats")
async def stats_cmd(ctx: commands.Context, *, team_query: str = ""):
    query = team_query.strip().lower()
    if not query:
        await ctx.send("❌ Usage: `%stats <Team Name>`")
        return

    matches = 0
    wins = 0
    kills_sum = 0
    points_sum = 0
    matched_name = team_query.strip()

    for match in results_log:
        for row in match.get("standings", []):
            if query in row["team"].lower():
                matches += 1
                matched_name = row["team"]
                kills_sum += int(row["kills"])
                points_sum += int(row["total_points"])
                if match.get("winner", "").lower() == row["team"].lower():
                    wins += 1

    if matches == 0:
        await ctx.send(f"No stats found for `{team_query}`.")
        return

    embed = discord.Embed(
        title=f"📊 Team Stats — {matched_name}",
        color=discord.Color.purple(),
    )
    embed.add_field(name="Matches", value=str(matches), inline=True)
    embed.add_field(name="Wins", value=str(wins), inline=True)
    embed.add_field(name="Total Kills", value=str(kills_sum), inline=True)
    embed.add_field(name="Total Points", value=str(points_sum), inline=True)
    await ctx.send(embed=embed)


@bot.event
async def on_message(message: discord.Message):
    if (
        message.author.bot
        or message.guild is None
        or not isinstance(message.channel, discord.TextChannel)
    ):
        return

    ch_name = message.channel.name
    raw = message.content.strip().lower()
    all_reg_channels = {cfg["reg_channel"] for cfg in SCHEDULES.values()} | {
        TDM_REG_CHANNEL,
        TOURNAMENT_20_CFG["reg_channel"],
    }

    if ch_name in all_reg_channels and raw in ("open!", "close!", "reset!"):
        if (
            not isinstance(message.author, discord.Member)
            or not message.author.guild_permissions.manage_channels
        ):
            await message.add_reaction("❌")
            return

        if raw == "reset!":
            if ch_name == TDM_REG_CHANNEL:
                await reset_tdm(message.guild)
            elif ch_name == TOURNAMENT_20_CFG["reg_channel"]:
                await reset_tourney20(message.guild)
            else:
                hour = parse_hour_from_reg_channel(message.channel)
                if hour is not None:
                    await reset_scrim_hour(message.guild, hour)
            await message.add_reaction("✅")
            await announce_everyone(
                message.channel, "🔄 **A regisztráció alaphelyzetbe állt (Reset).**"
            )
            return

        is_opening = raw == "open!"
        if ch_name == TDM_REG_CHANNEL:
            tdm_state["is_open"] = is_opening
            await set_channel_writable(message.channel, is_opening)
            await sync_tdm_board(message.guild)
            await message.add_reaction("✅")
            await announce_everyone(
                message.channel,
                "🟢 **TDM regisztráció MEGNYÍLT!**"
                if is_opening
                else "🔴 **TDM regisztráció LEZÁRULT!**",
            )
        elif ch_name == TOURNAMENT_20_CFG["reg_channel"]:
            tourney20_state["manual_open"] = is_opening
            await set_channel_writable(message.channel, is_opening)
            await sync_tourney20_board(message.guild)
            await message.add_reaction("✅")
            await announce_everyone(
                message.channel,
                "🟢 **20:00 Tournament regisztráció MEGNYÍLT!**"
                if is_opening
                else "🔴 **20:00 Tournament regisztráció LEZÁRULT!**",
            )
        else:
            hour = parse_hour_from_reg_channel(message.channel)
            if hour is not None:
                st = init_hour_state(hour)
                st["manual_open"] = is_opening
                new_st = "open" if is_opening else "closed"
                st["last_announced_state"] = new_st
                await set_channel_writable(message.channel, is_opening)
                await sync_slot_board(message.guild, hour)
                await message.add_reaction("✅")
                _, _, wait_ch, _ = get_channels_for_hour(message.guild, hour)
                await send_scrim_state_announcement(message.channel, wait_ch, hour, new_st)
        return

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if lines and lines[0].lower() == "%logo":
        if ch_name != TEAM_LOGO_CHANNEL:
            await message.add_reaction("❌")
            await message.reply(f"❌ Use `%logo` inside `#{TEAM_LOGO_CHANNEL}`.")
            return
        if len(lines) < 3 or not get_attached_image_url(message):
            await message.add_reaction("❌")
            await message.reply("❌ Format:\n`%logo`\n`Team Name`\n`Tag`\n`+ Photo`")
            return
        await message.add_reaction("✅")
        await message.reply(
            f"✅ Team logo received — **{lines[1]}** [{lines[2]}] | {message.author.mention}"
        )
        return

    if lines and lines[0].lower() == "%register":
        manager = message.mentions[0] if message.mentions else message.author
        ban_info = get_ban_info(manager.id)
        if ban_info["active"]:
            await message.add_reaction("❌")
            await message.reply(
                f"🚫 Registration blocked: {manager.mention} has an active ban ({ban_info['label']})."
            )
            return

        if ch_name == TDM_REG_CHANNEL:
            if not tdm_state["is_open"] or len(lines) < 2:
                await message.add_reaction("❌")
                return
            game_name = lines[1]
            if any(
                e["manager_id"] == manager.id or e["name"].lower() == game_name.lower()
                for e in tdm_state["slots"].values()
            ):
                await message.add_reaction("❌")
                return
            assigned_slot = next(
                (s for s in range(1, TDM_MAX_SLOTS + 1) if s not in tdm_state["slots"]),
                None,
            )
            if assigned_slot is None:
                await message.add_reaction("❌")
                return
            tdm_state["slots"][assigned_slot] = {
                "name": game_name,
                "manager_id": manager.id,
                "confirmed": False,
            }
            await sync_tdm_board(message.guild)
            await grant_view_access(
                discord.utils.get(message.guild.text_channels, name=TDM_SLOT_CHANNEL), manager
            )
            await grant_view_access(
                discord.utils.get(message.guild.text_channels, name=TDM_IDPW_CHANNEL), manager
            )
            await message.add_reaction("✅")
            await message.reply(
                f"✅ TDM Slot **#{assigned_slot}** — **{game_name}** | {manager.mention}"
            )
            return

        if ch_name == TOURNAMENT_20_CFG["reg_channel"]:
            if not is_tourney20_reg_open() or len(lines) < 2:
                await message.add_reaction("❌")
                return
            team_name = lines[1]
            team_tag = lines[2] if len(lines) >= 3 else ""
            if any(
                e["manager_id"] == manager.id or e["team"].lower() == team_name.lower()
                for e in tourney20_state["slots"].values()
            ):
                await message.add_reaction("❌")
                return
            used_slots = set(tourney20_state["slots"].keys())
            assigned_slot = 1
            while assigned_slot in used_slots:
                assigned_slot += 1
            tourney20_state["slots"][assigned_slot] = {
                "team": team_name,
                "tag": team_tag,
                "manager_id": manager.id,
                "vip": is_vip_member(manager),
                "confirmed": False,
            }
            _, slot_ch, idpw_ch = get_tourney20_channels(message.guild)
            await sync_tourney20_board(message.guild)
            await grant_view_access(slot_ch, manager)
            await grant_view_access(idpw_ch, manager)
            await message.add_reaction("✅")
            await message.reply(
                f"✅ 20:00 Tournament Slot **#{assigned_slot}** — **{team_name}** | {manager.mention}"
            )
            return

        hour = parse_hour_from_reg_channel(message.channel)
        if hour is None:
            await message.add_reaction("❌")
            return
        st = init_hour_state(hour)
        now_t = datetime.now(TIMEZONE).time()
        phase, deny_reason = get_scrim_access_phase(
            hour, manager, now_t, st.get("manual_open")
        )
        if phase is None or len(lines) < 3:
            await message.add_reaction("❌")
            if deny_reason:
                await message.reply(f"❌ {deny_reason}")
            return
        team_name, team_tag = lines[1], lines[2]
        if any(
            e["manager_id"] == manager.id or e["team"].lower() == team_name.lower()
            for e in st["slots"].values()
        ):
            await message.add_reaction("❌")
            return
        if any(
            w["manager_id"] == manager.id or w["team"].lower() == team_name.lower()
            for w in st["waitlist"]
        ):
            await message.add_reaction("❌")
            return

        _, slot_ch, wait_ch, idpw_ch = get_channels_for_hour(message.guild, hour)
        vip = is_vip_member(manager)
        assigned_slot = None
        if phase == "vip" and vip:
            assigned_slot = next(
                (s for s in [24, 23, 22, 21] if s not in st["slots"]), None
            )
        elif phase == "reg":
            order = ([24, 23, 22, 21] + list(range(3, 21))) if vip else list(range(3, 21))
            assigned_slot = next((s for s in order if s not in st["slots"]), None)

        if assigned_slot is not None:
            st["slots"][assigned_slot] = {
                "team": team_name,
                "tag": team_tag,
                "manager_id": manager.id,
                "vip": vip,
                "confirmed": False,
            }
            await sync_slot_board(message.guild, hour)
            await grant_view_access(slot_ch, manager)
            await grant_view_access(idpw_ch, manager)
            await message.add_reaction("✅")
            await message.reply(
                f"✅ {hour:02d}:00 Scrim Slot **#{assigned_slot}** — **{team_name}** [{team_tag}] | {manager.mention}"
            )
            return

        st["waitlist"].append(
            {"team": team_name, "tag": team_tag, "manager_id": manager.id}
        )
        await sync_slot_board(message.guild, hour)
        await grant_view_access(wait_ch, manager)
        await message.add_reaction("✅")
        await message.reply(
            f"⏳ {hour:02d}:00 Scrim **Wait-list** — **{team_name}** [{team_tag}] | {manager.mention}"
        )
        return

    await bot.process_commands(message)
class TicketButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="v4_scrim_ticket_button",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        ch_name = f"ticket-{user.id}"
        existing = discord.utils.get(guild.text_channels, name=ch_name)
        if existing:
            await interaction.response.send_message(
                f"Ticket already open: {existing.mention}", ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            )

        ticket_ch = await guild.create_text_channel(name=ch_name, overwrites=overwrites)
        await ticket_ch.send(f"🎫 {user.mention} opened a ticket.")
        await interaction.response.send_message(f"Created: {ticket_ch.mention}", ephemeral=True)


@bot.command(name="ticket")
@commands.has_permissions(manage_channels=True)
async def ticket_panel(ctx: commands.Context):
    await ctx.send("**Support / Ticket**", view=TicketButtonView())


@bot.command(name="t20idpw")
@commands.has_permissions(manage_channels=True)
async def t20_idpw_cmd(ctx: commands.Context, room_id: str, room_pw: str):
    if ctx.guild is None:
        return
    tourney20_state["room_id"] = room_id
    tourney20_state["room_pw"] = room_pw
    await update_tourney20_idpw_channel(ctx.guild)
    await ctx.send("✅ 20:00 Tournament ID/PW updated.")


@bot.command(name="unban")
@commands.has_permissions(manage_channels=True)
async def unban_cmd(ctx: commands.Context, target: str):
    digits = "".join(c for c in target if c.isdigit())
    if not digits:
        await ctx.send("❌ Usage: `%unban @user` or `%unban <id>`")
        return
    uid = int(digits)
    clear_user_ban(uid)
    await ctx.send(f"🔓 <@{uid}> unbanned ({UNBAN_FEE_EUR}€ fee).")


if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("Missing DISCORD_TOKEN.")

bot.run(TOKEN)
@bot.command(name="noshow")
@commands.has_permissions(manage_channels=True)
async def noshow_cmd(ctx: commands.Context, mode: str, arg1: str, arg2: str | None = None):
    if ctx.guild is None:
        return
    mode_l = mode.lower()
    target_uid = ctx.message.mentions[0].id if ctx.message.mentions else None
    removed_text = ""
    log_mode = "scrim" if mode_l == "scrim" else "tournament"

    if mode_l == "scrim":
        try:
            hour = int(arg1)
        except ValueError:
            await ctx.send("❌ Usage: `%noshow scrim <16|20|23> <slot | @user>`")
            return
        if hour not in SCHEDULES:
            await ctx.send("❌ Invalid hour (`16`, `20`, `23`).")
            return
        st = init_hour_state(hour)
        _, slot_ch, _, idpw_ch = get_channels_for_hour(ctx.guild, hour)
        if target_uid is None and arg2 is not None and arg2.isdigit():
            slot_num = int(arg2)
            entry = st["slots"].pop(slot_num, None)
            if entry:
                target_uid = entry["manager_id"]
                removed_text = f" (Slot #{slot_num} removed)"
                await sync_slot_board(ctx.guild, hour)
        elif target_uid is not None:
            for s, entry in list(st["slots"].items()):
                if entry["manager_id"] == target_uid:
                    del st["slots"][s]
                    removed_text = f" (Slot #{s} removed)"
                    await sync_slot_board(ctx.guild, hour)
                    break
        if target_uid is not None:
            m = ctx.guild.get_member(target_uid) or discord.Object(id=target_uid)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)

    elif mode_l in ("t20", "tourney20", "tournament"):
        if target_uid is None and arg1.isdigit():
            slot_num = int(arg1)
            entry = tourney20_state["slots"].pop(slot_num, None)
            if entry:
                target_uid = entry["manager_id"]
                removed_text = f" (Slot #{slot_num} removed)"
                await sync_tourney20_board(ctx.guild)
        elif target_uid is not None:
            for s, entry in list(tourney20_state["slots"].items()):
                if entry["manager_id"] == target_uid:
                    del tourney20_state["slots"][s]
                    removed_text = f" (Slot #{s} removed)"
                    await sync_tourney20_board(ctx.guild)
                    break
        _, slot_ch, idpw_ch = get_tourney20_channels(ctx.guild)
        if target_uid is not None:
            m = ctx.guild.get_member(target_uid) or discord.Object(id=target_uid)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)

    elif mode_l == "tdm":
        if target_uid is None and arg1.isdigit():
            slot_num = int(arg1)
            entry = tdm_state["slots"].pop(slot_num, None)
            if entry:
                target_uid = entry["manager_id"]
                removed_text = f" (Slot #{slot_num} removed)"
                await sync_tdm_board(ctx.guild)
        elif target_uid is not None:
            for s, entry in list(tdm_state["slots"].items()):
                if entry["manager_id"] == target_uid:
                    del tdm_state["slots"][s]
                    removed_text = f" (Slot #{s} removed)"
                    await sync_tdm_board(ctx.guild)
                    break
        slot_ch = discord.utils.get(ctx.guild.text_channels, name=TDM_SLOT_CHANNEL)
        idpw_ch = discord.utils.get(ctx.guild.text_channels, name=TDM_IDPW_CHANNEL)
        if target_uid is not None:
            m = ctx.guild.get_member(target_uid) or discord.Object(id=target_uid)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)
    else:
        await ctx.send("❌ Usage: `%noshow scrim <16|20|23> <slot>` | `%noshow t20 <slot>` | `%noshow tdm <slot>`")
        return

    if target_uid is None:
        await ctx.send("❌ Player/slot not found.")
        return

    res = apply_missed_match_ban(target_uid)
    line = f"Missed match #{res['misses']} -> **{res['duration_text']}**{removed_text}"
    await post_ban_log(ctx.guild, log_mode, target_uid, line, res["permanent"])
    await ctx.send(f"⚖️ <@{target_uid}> — {line}")


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    channel = (
        discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
        if WELCOME_CHANNEL_NAME
        else None
    )
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            f"👋 Welcome {member.mention} to **{guild.name}**! Enjoy your stay!"
        )
@bot.event
async def on_ready():
    global _view_registered, _schedule_task
    load_ban_records()
    load_results_log()
    if not _view_registered:
        bot.add_view(TicketButtonView())
        _view_registered = True
    for h in SCHEDULES:
        init_hour_state(h)
    for guild in bot.guilds:
        await sync_tdm_board(guild)
        await sync_tourney20_board(guild)
        for h in SCHEDULES:
            await sync_slot_board(guild, h)
    if _schedule_task is None or _schedule_task.done():
        _schedule_task = bot.loop.create_task(schedule_loop())
    print(f"[OK] Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot.user is None or payload.user_id == bot.user.id or payload.guild_id is None:
        return
    emoji = str(payload.emoji)
    if emoji not in ("✅", "❌"):
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
    if member.bot:
        return

    channel = guild.get_channel(payload.channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, member)
        except Exception:
            pass

    if tdm_state.get("slots_msg_id") == payload.message_id:
        target_slot = next(
            (s for s, e in tdm_state["slots"].items() if e["manager_id"] == member.id),
            None,
        )
        if target_slot is None:
            return
        if emoji == "✅":
            tdm_state["slots"][target_slot]["confirmed"] = True
            await sync_tdm_board(guild)
        else:
            del tdm_state["slots"][target_slot]
            await sync_tdm_board(guild)
            await revoke_view_access(
                discord.utils.get(guild.text_channels, name=TDM_SLOT_CHANNEL), member
            )
            await revoke_view_access(
                discord.utils.get(guild.text_channels, name=TDM_IDPW_CHANNEL), member
            )
        return

    if tourney20_state.get("slots_msg_id") == payload.message_id:
        target_slot = next(
            (s for s, e in tourney20_state["slots"].items() if e["manager_id"] == member.id),
            None,
        )
        if target_slot is None:
            return
        if emoji == "✅":
            tourney20_state["slots"][target_slot]["confirmed"] = True
            await sync_tourney20_board(guild)
        else:
            del tourney20_state["slots"][target_slot]
            await sync_tourney20_board(guild)
            _, slot_ch, idpw_ch = get_tourney20_channels(guild)
            await revoke_view_access(slot_ch, member)
            await revoke_view_access(idpw_ch, member)
        return

    for hour, st in scrim_state.items():
        if st.get("slots_msg_id") == payload.message_id:
            target_slot = next(
                (s for s, e in st["slots"].items() if e["manager_id"] == member.id),
                None,
            )
            if target_slot is None:
                return
            if emoji == "✅":
                st["slots"][target_slot]["confirmed"] = True
                await sync_slot_board(guild, hour)
            else:
                del st["slots"][target_slot]
                await sync_slot_board(guild, hour)
                _, slot_ch, _, idpw_ch = get_channels_for_hour(guild, hour)
                await revoke_view_access(slot_ch, member)
                await revoke_view_access(idpw_ch, member)
            return
def resolve_result_target_channel(
    guild: discord.Guild, mode_token: str | None
) -> tuple[discord.TextChannel | None, bool]:
    if not mode_token:
        ch = discord.utils.get(guild.text_channels, name=DEFAULT_RESULTS_CHANNEL)
        return (ch if isinstance(ch, discord.TextChannel) else None), False

    tok = mode_token.strip().lower()
    mapping = {
        "16": SCHEDULES[16]["result_channel"],
        "20": SCHEDULES[20]["result_channel"],
        "23": SCHEDULES[23]["result_channel"],
        "t20": TOURNAMENT_20_CFG["result_channel"],
        "tour20": TOURNAMENT_20_CFG["result_channel"],
        "tournament": TOURNAMENT_20_CFG["result_channel"],
        "tdm": TDM_RESULT_CHANNEL,
    }
    if tok in mapping:
        ch = discord.utils.get(guild.text_channels, name=mapping[tok])
        return (ch if isinstance(ch, discord.TextChannel) else None), True

    ch = discord.utils.get(guild.text_channels, name=DEFAULT_RESULTS_CHANNEL)
    return (ch if isinstance(ch, discord.TextChannel) else None), False


def build_result_embed(
    match_title: str,
    rows: list[dict],
    submitted_by: str,
    image_url: str | None = None,
) -> discord.Embed:
    winner = rows[0]
    most_kills_team = max(rows, key=lambda r: (r["kills"], r["total_points"]))
    total_kills = sum(r["kills"] for r in rows)
    total_points = sum(r["total_points"] for r in rows)

    leaderboard_lines = []
    for rank, r in enumerate(rows, start=1):
        icon = (
            "🥇 " if rank == 1 else ("🥈 " if rank == 2 else ("🥉 " if rank == 3 else f"`#{rank}` "))
        )
        leaderboard_lines.append(
            f"{icon}**{r['team']}** — **{r['total_points']} pts** "
            f"(Top #{r['place']}: {r['place_points']} + {r['kills']} kills: {r['kill_points']} pt)"
        )

    embed = discord.Embed(
        title=f"🏆 {match_title}",
        description="\n".join(leaderboard_lines),
        color=discord.Color.gold(),
        timestamp=datetime.now(TIMEZONE),
    )
    embed.add_field(
        name="Champion (1st)",
        value=f"**{winner['team']}** ({winner['total_points']} pts | {winner['kills']} kills)",
        inline=True,
    )
    embed.add_field(
        name="Most Kills",
        value=f"**{most_kills_team['team']}** ({most_kills_team['kills']} kills)",
        inline=True,
    )
    embed.add_field(
        name="Match Summary",
        value=f"Total Kills: **{total_kills}** | Total Points: **{total_points}**",
        inline=False,
    )
    embed.set_footer(text=f"Logged by {submitted_by} • Placement + 1 Kill = 1 Pt")
    if image_url:
        embed.set_image(url=image_url)
    return embed
async def run_schedule_tick():
    now = datetime.now(TIMEZONE)
    now_t = now.time()
    today_str = now.strftime("%Y-%m-%d")

    for guild in bot.guilds:
        for hour, cfg in SCHEDULES.items():
            st = init_hour_state(hour)
            reg_ch, _, wait_ch, _ = get_channels_for_hour(guild, hour)
            if st["last_day_reset"] != today_str and is_in_window(
                now_t, cfg["vip"][0], cfg["waitlist"][1]
            ):
                st["slots"] = {}
                st["waitlist"] = []
                st["last_day_reset"] = today_str
                st["manual_open"] = None
                st["last_announced_state"] = None
                await sync_slot_board(guild, hour)

            new_state = describe_scrim_window_state(hour, now_t, st.get("manual_open"))
            is_open = new_state in ("vip", "reg", "waitlist")
            await set_channel_writable(reg_ch, is_open)

            if st.get("last_announced_state") != new_state:
                prev_state = st.get("last_announced_state")
                st["last_announced_state"] = new_state
                if prev_state is not None or new_state != "closed":
                    await send_scrim_state_announcement(reg_ch, wait_ch, hour, new_state)

        t_reg_ch, _, _ = get_tourney20_channels(guild)
        await set_channel_writable(t_reg_ch, bool(tourney20_state.get("manual_open")))


async def sync_slot_board(guild: discord.Guild, hour: int):
    st = init_hour_state(hour)
    _, slot_ch, _, _ = get_channels_for_hour(guild, hour)
    await sync_board_message(
        slot_ch,
        st,
        format_slot_board_from_state(f"{hour:02d}:00 SCRIM", st, True),
    )


async def reset_scrim_hour(guild: discord.Guild, hour: int):
    st = init_hour_state(hour)
    _, slot_ch, wait_ch, idpw_ch = get_channels_for_hour(guild, hour)
    uids = {e["manager_id"] for e in st["slots"].values()} | {
        w["manager_id"] for w in st["waitlist"]
    }
    await revoke_user_ids_from_channels(guild, uids, [slot_ch, wait_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    st["slots"] = {}
    st["waitlist"] = []
    st["manual_open"] = None
    st["last_announced_state"] = None
    await sync_slot_board(guild, hour)

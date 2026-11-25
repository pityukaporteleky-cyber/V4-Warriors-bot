import asyncio
from datetime import datetime, time, timedelta
import json
import os
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
TIMEZONE = ZoneInfo("Europe/Bratislava")
PREFIX = "%"

BANS_FILE = "bans_data.json"
RESULTS_FILE = "results_log.json"

WELCOME_CHANNEL_NAME = "welcome"
TEAM_LOGO_CHANNEL = "team-logo"
DEFAULT_RESULTS_CHANNEL = "16-result-eredmény"
VIP_ROLE_NAME = "Vip_role"

SCRIM_MAX_SLOTS = 24
VIP_SLOT_NUMBERS = [24, 23, 22, 21]
REGULAR_SLOT_NUMBERS = list(range(3, 21))

SCHEDULES = {
    16: {
        "label": "16:00 SCRIM",
        "vip": (time(8, 0), time(15, 30)),
        "reg": (time(15, 30), time(15, 45)),
        "waitlist": (time(15, 45), time(15, 55)),
        "reg_channel": "16-registration",
        "slot_channel": "16-slots",
        "wait_channel": "16-wait-list",
        "idpw_channel": "16-id-pw",
        "result_channel": "16-result-eredmény",
    },
    20: {
        "label": "20:00 SCRIM",
        "vip": (time(8, 0), time(18, 0)),
        "reg": (time(18, 0), time(19, 20)),
        "waitlist": (time(19, 20), time(19, 50)),
        "reg_channel": "20-registration",
        "slot_channel": "20-slot",
        "wait_channel": "20-wait-list",
        "idpw_channel": "20-id-pw",
        "result_channel": "20-result-eredmény",
    },
    23: {
        "label": "23:00 SCRIM",
        "vip": (time(8, 0), time(18, 0)),
        "reg": (time(18, 0), time(22, 20)),
        "waitlist": (time(22, 20), time(22, 50)),
        "reg_channel": "23-registration",
        "slot_channel": "23-slot",
        "wait_channel": "23-wait-list",
        "idpw_channel": "23-id-pw",
        "result_channel": "23-result-eredmény",
    },
}

TDM_MAX_SLOTS = 40
TDM_REG_CHANNEL = "tdm-registration"
TDM_SLOT_CHANNEL = "tdm-slot"
TDM_IDPW_CHANNEL = "tdm-id-pw"
TDM_RESULT_CHANNEL = "tdm-result-eredmèny"

TOURNAMENT_20_CFG = {
    "label": "20:00 TOURNAMENT",
    "reg_channel": "20-tournament-registration",
    "slot_channel": "20-tournament-slot",
    "idpw_channel": "20-tournamnet-id-pw",
    "result_channel": "20-tournamnet-eredmémeny-result",
}

BAN_LOG_SCRIMS = "scrims-ban"
BAN_LOG_TOURNAMENT = "tournament-ban"
BAN_LOG_PERMABAN = "permaban"
UNBAN_FEE_EUR = 5

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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

scrim_state: dict[int, dict] = {}
tdm_state: dict = {
    "is_open": False,
    "slots": {},
    "slots_msg_id": None,
}
tourney20_state: dict = {
    "manual_open": False,
    "slots": {},
    "slots_msg_id": None,
    "room_id": "-",
    "room_pw": "-",
}
ban_records: dict[str, dict] = {}
results_log: list[dict] = []
_view_registered = False
_schedule_task: asyncio.Task | None = None


def load_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_ban_records() -> None:
    global ban_records
    data = load_json_file(BANS_FILE, {})
    ban_records = data if isinstance(data, dict) else {}


def save_ban_records() -> None:
    save_json_file(BANS_FILE, ban_records)


def load_results_log() -> None:
    global results_log
    data = load_json_file(RESULTS_FILE, [])
    results_log = data if isinstance(data, list) else []


def save_results_log() -> None:
    save_json_file(RESULTS_FILE, results_log)


def get_ban_info(user_id: int) -> dict:
    rec = ban_records.get(str(user_id))
    if not rec:
        return {"active": False, "permanent": False, "label": "None", "misses": 0}
    misses = int(rec.get("misses", 0))
    if rec.get("permanent"):
        return {"active": True, "permanent": True, "label": "PERMANENT BAN", "misses": misses}
    until_ts = rec.get("banned_until_ts")
    now_ts = datetime.now(ZoneInfo("UTC")).timestamp()
    if until_ts and now_ts < float(until_ts):
        until_dt = datetime.fromtimestamp(float(until_ts), tz=TIMEZONE).strftime("%Y-%m-%d %H:%M")
        return {"active": True, "permanent": False, "label": f"Until {until_dt}", "misses": misses}
    return {"active": False, "permanent": False, "label": "Expired", "misses": misses}


def apply_missed_match_ban(user_id: int) -> dict:
    key = str(user_id)
    rec = ban_records.get(key, {"misses": 0, "permanent": False, "banned_until_ts": None})
    misses = int(rec.get("misses", 0)) + 1
    now_utc = datetime.now(ZoneInfo("UTC"))

    if misses == 1:
        delta = timedelta(days=2)
        perm = False
        dur_text = "2 days ban"
    elif misses == 2:
        delta = timedelta(days=7)
        perm = False
        dur_text = "1 week (7 days) ban"
    elif misses == 3:
        delta = timedelta(days=30)
        perm = False
        dur_text = "1 month (30 days) ban"
    else:
        delta = None
        perm = True
        dur_text = "PERMANENT BAN"

    until_ts = (now_utc + delta).timestamp() if delta else None
    ban_records[key] = {
        "misses": misses,
        "permanent": perm,
        "banned_until_ts": until_ts,
    }
    save_ban_records()
    return {"misses": misses, "permanent": perm, "duration_text": dur_text}


def clear_user_ban(user_id: int) -> None:
    ban_records.pop(str(user_id), None)
    save_ban_records()


async def post_ban_log(
    guild: discord.Guild, mode: str, target_uid: int, text: str, permanent: bool
) -> None:
    ch_name = (
        BAN_LOG_PERMABAN
        if permanent
        else (BAN_LOG_SCRIMS if mode == "scrim" else BAN_LOG_TOURNAMENT)
    )
    ch = discord.utils.get(guild.text_channels, name=ch_name)
    if isinstance(ch, discord.TextChannel):
        await ch.send(f"⚖️ <@{target_uid}> (`{target_uid}`) — {text}")


def is_in_window(now_t: time, start_t: time, end_t: time) -> bool:
    if start_t <= end_t:
        return start_t <= now_t < end_t
    return now_t >= start_t or now_t < end_t


def init_hour_state(hour: int) -> dict:
    if hour not in scrim_state:
        scrim_state[hour] = {
            "slots": {},
            "waitlist": [],
            "slots_msg_id": None,
            "manual_open": None,
            "last_day_reset": None,
            "last_announced_state": None,
        }
    return scrim_state[hour]


def get_channels_for_hour(guild: discord.Guild, hour: int):
    cfg = SCHEDULES[hour]
    reg_ch = discord.utils.get(guild.text_channels, name=cfg["reg_channel"])
    slot_ch = discord.utils.get(guild.text_channels, name=cfg["slot_channel"])
    wait_ch = discord.utils.get(guild.text_channels, name=cfg["wait_channel"])
    idpw_ch = discord.utils.get(guild.text_channels, name=cfg["idpw_channel"])
    return reg_ch, slot_ch, wait_ch, idpw_ch


def get_tourney20_channels(guild: discord.Guild):
    reg_ch = discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["reg_channel"])
    slot_ch = discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["slot_channel"])
    idpw_ch = discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["idpw_channel"])
    return reg_ch, slot_ch, idpw_ch


def parse_hour_from_reg_channel(channel: discord.TextChannel) -> int | None:
    for hour, cfg in SCHEDULES.items():
        if channel.name == cfg["reg_channel"]:
            return hour
    return None


def is_vip_member(member: discord.Member) -> bool:
    return any(r.name.lower() == VIP_ROLE_NAME.lower() for r in member.roles)


def get_scrim_access_phase(
    hour: int, member: discord.Member, now_t: time, manual_open: bool | None
) -> tuple[str | None, str | None]:
    if manual_open is False:
        return None, f"{hour:02d}:00 scrim registration is closed."
    if manual_open is True:
        return ("vip" if is_vip_member(member) else "reg"), None

    cfg = SCHEDULES[hour]
    vip = is_vip_member(member)
    if is_in_window(now_t, *cfg["vip"]):
        if vip:
            return "vip", None
        return None, f"{hour:02d}:00 VIP window only right now."
    if is_in_window(now_t, *cfg["reg"]):
        return "reg", None
    if is_in_window(now_t, *cfg["waitlist"]):
        return "waitlist", None
    return None, f"{hour:02d}:00 scrim registration is closed."


def describe_scrim_window_state(hour: int, now_t: time, manual_open: bool | None) -> str:
    if manual_open is True:
        return "open"
    if manual_open is False:
        return "closed"
    cfg = SCHEDULES[hour]
    if is_in_window(now_t, *cfg["vip"]):
        return "vip"
    if is_in_window(now_t, *cfg["reg"]):
        return "reg"
    if is_in_window(now_t, *cfg["waitlist"]):
        return "waitlist"
    return "closed"


def is_tourney20_reg_open() -> bool:
    return bool(tourney20_state.get("manual_open"))


async def set_channel_writable(channel: discord.abc.GuildChannel | None, writable: bool) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    guild = channel.guild
    current = channel.overwrites_for(guild.default_role).send_messages
    if current is writable:
        return
    try:
        await channel.set_permissions(guild.default_role, send_messages=writable)
    except Exception as e:
        print(f"[ERROR] set_channel_writable #{channel.name}: {e}")


async def grant_view_access(
    channel: discord.abc.GuildChannel | None, target: discord.abc.Snowflake
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.set_permissions(
            target,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    except Exception as e:
        print(f"[ERROR] grant_view_access #{channel.name}: {e}")


async def revoke_view_access(
    channel: discord.abc.GuildChannel | None, target: discord.abc.Snowflake
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.set_permissions(target, overwrite=None)
    except Exception as e:
        print(f"[ERROR] revoke_view_access #{channel.name}: {e}")


async def revoke_user_ids_from_channels(
    guild: discord.Guild, user_ids: set[int], channels: list[discord.abc.GuildChannel | None]
) -> None:
    for uid in user_ids:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                member = None
        target = member if member is not None else discord.Object(id=uid)
        for ch in channels:
            await revoke_view_access(ch, target)


async def purge_idpw_channel(channel: discord.abc.GuildChannel | None) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.purge(limit=100)
    except Exception as e:
        print(f"[ERROR] purge_idpw_channel #{channel.name}: {e}")


async def announce_everyone(channel: discord.abc.GuildChannel | None, text: str) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(
            f"@everyone {text}",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
    except Exception as e:
        print(f"[ERROR] announce_everyone #{channel.name}: {e}")


async def send_scrim_state_announcement(
    reg_ch: discord.abc.GuildChannel | None,
    wait_ch: discord.abc.GuildChannel | None,
    hour: int,
    state: str,
) -> None:
    label = f"{hour:02d}:00 Scrim"
    fmt_hint = "\nFormat:\n`%register`\n`Team Name`\n`Tag`"
    if state == "vip":
        await announce_everyone(
            reg_ch,
            f"👑 **{label} VIP registration is OPEN! — {label} VIP regisztráció MEGNYÍLT!**{fmt_hint}",
        )
    elif state in ("reg", "open"):
        await announce_everyone(
            reg_ch,
            f"🟢 **{label} registration is OPEN! — {label} regisztráció MEGNYÍLT!**{fmt_hint}",
        )
    elif state == "waitlist":
        await announce_everyone(
            wait_ch or reg_ch,
            f"⏳ **{label} wait-list is OPEN! — {label} várólista MEGNYÍLT!**{fmt_hint}",
        )
    elif state == "closed":
        await announce_everyone(
            reg_ch,
            f"🔴 **{label} registration is CLOSED! — {label} regisztráció LEZÁRULT!**",
        )


async def ensure_board_reactions(msg: discord.Message) -> None:
    try:
        existing = {str(r.emoji) for r in msg.reactions if r.me}
        if "✅" not in existing:
            await msg.add_reaction("✅")
        if "❌" not in existing:
            await msg.add_reaction("❌")
    except Exception as e:
        print(f"[ERROR] ensure_board_reactions: {e}")


def format_slot_label(entry: dict) -> str:
    tag = f" [{entry['tag']}]" if entry.get("tag") else ""
    vip_badge = " 👑" if entry.get("vip") else ""
    base = f"**{entry['team']}**{tag}{vip_badge}"
    return f"__{base}__" if entry.get("confirmed") else base


def format_slot_board_from_state(title: str, st: dict, include_waitlist: bool = True) -> str:
    lines = [
        f"**📋 {title} — SLOT LIST**",
        "",
        "🔒 **Slot 1:** Locked",
        "🔒 **Slot 2:** Locked",
        "",
        "**Regular Slots (3–20):**",
    ]
    for s in range(3, 21):
        entry = st["slots"].get(s)
        if entry:
            lines.append(f"**Slot {s}:** {format_slot_label(entry)} — <@{entry['manager_id']}>")
        else:
            lines.append(f"**Slot {s}:** *Open*")

    lines.extend(["", f"**VIP Slots (24–21 — `{VIP_ROLE_NAME}`):**"])
    for s in [24, 23, 22, 21]:
        entry = st["slots"].get(s)
        if entry:
            lines.append(f"👑 **Slot {s}:** {format_slot_label(entry)} — <@{entry['manager_id']}>")
        else:
            lines.append(f"👑 **Slot {s}:** *Open (VIP)*")

    if include_waitlist and st.get("waitlist"):
        lines.extend(["", "**⏳ Wait-list:**"])
        for idx, w in enumerate(st["waitlist"], start=1):
            tag = f" [{w['tag']}]" if w.get("tag") else ""
            lines.append(f"{idx}. **{w['team']}**{tag} — <@{w['manager_id']}>")

    lines.extend(["", "React ✅ to confirm your slot | React ❌ to cancel your slot."])
    return "\n".join(lines)


def format_tourney20_board() -> str:
    status = "OPEN" if tourney20_state.get("manual_open") else "CLOSED"
    lines = [f"**🏆 20:00 TOURNAMENT — SLOT LIST ({status})**", ""]
    if not tourney20_state["slots"]:
        lines.append("*No registrations yet.*")
    else:
        for s in sorted(tourney20_state["slots"].keys()):
            entry = tourney20_state["slots"][s]
            tag = f" [{entry['tag']}]" if entry.get("tag") else ""
            label = f"**{entry['team']}**{tag}"
            if entry.get("confirmed"):
                label = f"__{label}__"
            lines.append(f"**Slot {s}:** {label} — <@{entry['manager_id']}>")
    lines.extend(["", "React ✅ to confirm your slot | React ❌ to cancel your slot."])
    return "\n".join(lines)


def format_tdm_board() -> str:
    status = "OPEN" if tdm_state["is_open"] else "CLOSED"
    lines = [f"**⚔️ TDM TOURNAMENT — SLOT LIST ({status})**", ""]
    for s in range(1, TDM_MAX_SLOTS + 1):
        entry = tdm_state["slots"].get(s)
        if entry:
            label = f"__{entry['name']}__" if entry.get("confirmed") else f"**{entry['name']}**"
            lines.append(f"**Slot {s}:** {label} — <@{entry['manager_id']}>")
        else:
            lines.append(f"**Slot {s}:** *Open*")
    lines.extend(["", "React ✅ to confirm your slot | React ❌ to cancel your slot."])
    return "\n".join(lines)


async def sync_board_message(
    channel: discord.abc.GuildChannel | None, state_obj: dict, content: str
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    msg_id = state_obj.get("slots_msg_id")
    msg: discord.Message | None = None
    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
        except Exception:
            msg = None
    if msg is None:
        msg = await channel.send(content)
        state_obj["slots_msg_id"] = msg.id
    else:
        try:
            await msg.edit(content=content)
        except Exception:
            msg = await channel.send(content)
            state_obj["slots_msg_id"] = msg.id
    await ensure_board_reactions(msg)


async def sync_slot_board(guild: discord.Guild, hour: int) -> None:
    st = init_hour_state(hour)
    _, slot_ch, _, _ = get_channels_for_hour(guild, hour)
    await sync_board_message(
        slot_ch, st, format_slot_board_from_state(f"{hour:02d}:00 SCRIM", st, True)
    )


async def sync_tourney20_board(guild: discord.Guild) -> None:
    _, slot_ch, _ = get_tourney20_channels(guild)
    await sync_board_message(slot_ch, tourney20_state, format_tourney20_board())


async def sync_tdm_board(guild: discord.Guild) -> None:
    slot_ch = discord.utils.get(guild.text_channels, name=TDM_SLOT_CHANNEL)
    await sync_board_message(slot_ch, tdm_state, format_tdm_board())


async def update_tourney20_idpw_channel(guild: discord.Guild) -> None:
    _, _, idpw_ch = get_tourney20_channels(guild)
    if not isinstance(idpw_ch, discord.TextChannel):
        return
    await purge_idpw_channel(idpw_ch)
    lines = [
        "🔐 **20:00 TOURNAMENT — ID / PW / SLOT**",
        f"• **Room ID:** `{tourney20_state.get('room_id', '-')}`",
        f"• **Password:** `{tourney20_state.get('room_pw', '-')}`",
        "",
        "**Registered Slots:**",
    ]
    if not tourney20_state["slots"]:
        lines.append("*No registered players.*")
    else:
        for s in sorted(tourney20_state["slots"].keys()):
            entry = tourney20_state["slots"][s]
            lines.append(f"**Slot {s}:** {entry['team']} — <@{entry['manager_id']}>")
    await idpw_ch.send("\n".join(lines))


async def reset_scrim_hour(guild: discord.Guild, hour: int) -> None:
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


async def reset_tourney20(guild: discord.Guild) -> None:
    _, slot_ch, idpw_ch = get_tourney20_channels(guild)
    uids = {e["manager_id"] for e in tourney20_state["slots"].values()}
    await revoke_user_ids_from_channels(guild, uids, [slot_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    tourney20_state["slots"] = {}
    tourney20_state["manual_open"] = False
    tourney20_state["room_id"] = "-"
    tourney20_state["room_pw"] = "-"
    await sync_tourney20_board(guild)


async def reset_tdm(guild: discord.Guild) -> None:
    slot_ch = discord.utils.get(guild.text_channels, name=TDM_SLOT_CHANNEL)
    idpw_ch = discord.utils.get(guild.text_channels, name=TDM_IDPW_CHANNEL)
    uids = {e["manager_id"] for e in tdm_state["slots"].values()}
    await revoke_user_ids_from_channels(guild, uids, [slot_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    tdm_state["slots"] = {}
    tdm_state["is_open"] = False
    await sync_tdm_board(guild)


def get_attached_image_url(message: discord.Message) -> str | None:
    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            return att.url
        if att.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return att.url
    return None


def parse_result_lines(raw_text: str) -> tuple[str, list[dict]] | tuple[None, str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, "Provide a match title on line 1 and team entries (`Team | Place | Kills`) below."

    match_title = lines[0]
    rows: list[dict] = []
    for idx, line in enumerate(lines[1:], start=2):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            return None, f"Line {idx} invalid (`Team | Place | Kills`): `{line}`"
        team_name = parts[0]
        try:
            place = int(parts[1])
            kills = int(parts[2])
        except ValueError:
            return None, f"Line {idx} place/kills must be numbers: `{line}`"
        if place < 1 or place > 40 or kills < 0:
            return None, f"Line {idx} out of range (place 1-40, kills >= 0): `{line}`"

        place_pts = PLACEMENT_POINTS.get(place, 0)
        kill_pts = kills
        total_pts = place_pts + kill_pts
        rows.append(
            {
                "team": team_name,
                "place": place,
                "kills": kills,
                "place_points": place_pts,
                "kill_points": kill_pts,
                "total_points": total_pts,
            }
        )

    rows.sort(key=lambda r: (-r["total_points"], -r["kills"], r["place"], r["team"].lower()))
    return match_title, rows


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


async def run_schedule_tick() -> None:
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


async def schedule_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await run_schedule_tick()
        except Exception as exc:
            print(f"[ERROR] schedule_loop: {exc}")
        await asyncio.sleep(15)


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
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        safe_name = "".join(c for c in user.name.lower() if c.isalnum() or c in "-_")[:20] or "user"
        channel_name = f"ticket-{safe_name}-{user.id}"
        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing is not None:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}", ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        category = (
            interaction.channel.category
            if isinstance(interaction.channel, discord.TextChannel)
            else None
        )
        ticket_ch = await guild.create_text_channel(
            name=channel_name, overwrites=overwrites, category=category
        )
        await ticket_ch.send(
            f"🎫 {user.mention} opened a ticket. Describe your issue or question and staff will respond shortly."
        )
        await interaction.response.send_message(
            f"Ticket created: {ticket_ch.mention}", ephemeral=True
        )


@bot.command(name="ticket")
@commands.has_permissions(manage_channels=True)
async def ticket_panel(ctx: commands.Context):
    await ctx.send(
        "**Scrim Support / Ticket**\nNeed help or coordination? Click the button below:",
        view=TicketButtonView(),
    )


@bot.command(name="t20idpw")
@commands.has_permissions(manage_channels=True)
async def t20_idpw_cmd(ctx: commands.Context, room_id: str, room_pw: str):
    if ctx.guild is None:
        return
    tourney20_state["room_id"] = room_id
    tourney20_state["room_pw"] = room_pw
    await update_tourney20_idpw_channel(ctx.guild)
    await ctx.send("✅ 20:00 Tournament ID, PW & Slot list updated.")


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
            await ctx.send("❌ Usage: `%noshow scrim <16|20|23> <slot_number | @user>`")
            return
        if hour not in SCHEDULES:
            await ctx.send("❌ Invalid scrim hour. Use `16`, `20`, or `23`.")
            return
        st = init_hour_state(hour)
        _, slot_ch, _, idpw_ch = get_channels_for_hour(ctx.guild, hour)
        if target_uid is None and arg2 is not None and arg2.isdigit():
            slot_num = int(arg2)
            entry = st["slots"].pop(slot_num, None)
            if entry:
                target_uid = entry["manager_id"]
                removed_text = f" (Slot #{slot_num} removed: {entry['team']} [{entry['tag']}])"
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
                removed_text = f" (Slot #{slot_num} removed: {entry['team']})"
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
                removed_text = f" (Slot #{slot_num} removed: {entry['name']})"
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
        await ctx.send(
            "❌ Usage: `%noshow scrim <16|20|23> <slot>` | `%noshow t20 <slot>` | `%noshow tdm <slot>`"
        )
        return

    if target_uid is None:
        await ctx.send("❌ Could not find a player/slot for that no-show.")
        return

    res = apply_missed_match_ban(target_uid)
    line = f"Missed match #{res['misses']} -> **{res['duration_text']}**{removed_text}"
    await post_ban_log(ctx.guild, log_mode, target_uid, line, res["permanent"])
    await ctx.send(f"⚖️ <@{target_uid}> — {line}")


@bot.command(name="unban")
@commands.has_permissions(manage_channels=True)
async def unban_cmd(ctx: commands.Context, target: str):
    if ctx.guild is None:
        return
    user_id = ctx.message.mentions[0].id if ctx.message.mentions else None
    if user_id is None:
        digits = "".join(c for c in target if c.isdigit())
        if digits:
            user_id = int(digits)
    if user_id is None:
        await ctx.send("❌ Usage: `%unban @user` or `%unban <user_id>`")
        return
    clear_user_ban(user_id)
    await ctx.send(f"🔓 <@{user_id}> has been **unbanned** ({UNBAN_FEE_EUR}€ unban processed).")


@bot.command(name="result")
async def result_cmd(ctx: commands.Context, *, body: str = ""):
    if ctx.guild is None:
        return
    first_token = body.strip().split()[0] if body.strip() else None
    target_ch, consumed_token = resolve_result_target_channel(ctx.guild, first_token)
    raw_payload = body.strip()
    if consumed_token and first_token:
        raw_payload = raw_payload[len(first_token) :].strip()

    parsed = parse_result_lines(raw_payload)
    if parsed[0] is None:
        await ctx.send(f"❌ {parsed[1]}")
        return

    match_title, rows = parsed
    img_url = get_attached_image_url(ctx.message)
    embed = build_result_embed(match_title, rows, ctx.author.display_name, img_url)

    record = {
        "id": len(results_log) + 1,
        "match_title": match_title,
        "winner": rows[0]["team"],
        "winner_points": rows[0]["total_points"],
        "standings": rows,
        "image_url": img_url,
    }
    results_log.append(record)
    save_results_log()

    if isinstance(target_ch, discord.TextChannel):
        await target_ch.send(embed=embed)
        await ctx.send(f"✅ Result posted to {target_ch.mention}.")
    else:
        await ctx.send(embed=embed)


@bot.command(name="reslog")
async def reslog_cmd(ctx: commands.Context, limit: int = 5):
    if not results_log:
        await ctx.send("No results yet.")
        return
    txt = "\n".join(
        f"#{i['id']} {i['match_title']}: {i['winner']} ({i['winner_points']}p)"
        for i in results_log[-limit:]
    )
    await ctx.send(txt)


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
async def on_member_join(member: discord.Member):
    guild = member.guild
    channel = (
        discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
        if WELCOME_CHANNEL_NAME
        else None
    )
    if channel is None and guild.me is not None:
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.send_messages:
                channel = ch
                break
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            f"👋 Szia {member.mention}! Üdvözlünk a **{guild.name}** szerveren! "
            f"Nézd meg a szabályzatot és a scrim / tournament csatornákat, és érezd jól magad!"
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
                message.channel,
                "🔄 **Registration reset! — A regisztráció alaphelyzetbe állt (Reset)!**",
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
                "🟢 **TDM registration is OPEN! — TDM regisztráció MEGNYÍLT!**"
                if is_opening
                else "🔴 **TDM registration is CLOSED! — TDM regisztráció LEZÁRULT!**",
            )
        elif ch_name == TOURNAMENT_20_CFG["reg_channel"]:
            tourney20_state["manual_open"] = is_opening
            await set_channel_writable(message.channel, is_opening)
            await sync_tourney20_board(message.guild)
            await message.add_reaction("✅")
            await announce_everyone(
                message.channel,
                "🟢 **20:00 Tournament registration is OPEN! — 20:00 Tournament regisztráció MEGNYÍLT!**"
                if is_opening
                else "🔴 **20:00 Tournament registration is CLOSED! — 20:00 Tournament regisztráció LEZÁRULT!**",
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
                (s for s in VIP_SLOT_NUMBERS if s not in st["slots"]), None
            )
        elif phase == "reg":
            order = (VIP_SLOT_NUMBERS + REGULAR_SLOT_NUMBERS) if vip else REGULAR_SLOT_NUMBERS
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


if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("Missing DISCORD_TOKEN.")

bot.run(TOKEN)

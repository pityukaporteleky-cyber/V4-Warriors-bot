import asyncio
from datetime import datetime, time, timedelta, timezone
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from discord.ext import commands

# =====================================================================
# BOT CONFIGURATION & CHANNEL SETTINGS
# =====================================================================

TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
PREFIX = "%"
VIP_ROLE_NAME = "Vip_role"
WELCOME_CHANNEL_NAME = ""
TEAM_LOGO_CHANNEL = "team-logo"

# TDM channels and max slots
TDM_REG_CHANNEL = "tdm-registration"
TDM_SLOT_CHANNEL = "tdm-slot"
TDM_IDPW_CHANNEL = "tdm-id-pw"
TDM_MAX_SLOTS = 40

# 20:00 Tournament channels (matching your server's channel names)
TOURNAMENT_20_CFG = {
    "label": "20:00 TOURNAMENT",
    "slot_channel": "20-tournamnet-slot",
    "reg_channel": "20-tournamnet-registration",
    "wait_channel": None,
    "idpw_channel": "20-tournamnet-id-pw",
    "vip": (time(14, 30), time(14, 55)),
    "reg": (time(15, 0), time(16, 30)),
    "waitlist": (time(19, 20), time(19, 30)),
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
SCHEDULES = {
    16: {
        "label": "16:00 SCRIM",
        "slot_channel": "16-slots",
        "reg_channel": "16-registration",
        "wait_channel": "16-wait-list",
        "idpw_channel": "16-id-pw",
        "vip": (time(9, 30), time(9, 55)),
        "reg": (time(10, 0), time(13, 0)),
        "waitlist": (time(15, 20), time(15, 30)),
    },
    20: {
        "label": "20:00 SCRIM",
        "slot_channel": "20-slot",
        "reg_channel": "20-registration",
        "wait_channel": "20-wait-list",
        "idpw_channel": "20-id-pw",
        "vip": (time(14, 30), time(14, 55)),
        "reg": (time(15, 0), time(16, 30)),
        "waitlist": (time(19, 20), time(19, 30)),
    },
    23: {
        "label": "23:00 SCRIM",
        "slot_channel": "23-slot",
        "reg_channel": "23-registration",
        "wait_channel": "23-wait-list",
        "idpw_channel": "23-id-pw",
        "vip": (time(15, 30), time(15, 55)),
        "reg": (time(16, 0), time(17, 30)),
        "waitlist": (time(22, 20), time(22, 30)),
    },
}

# =====================================================================
# INTENTS, BOT INSTANCE & STATE STORAGE
# =====================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

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

_view_registered = False
_schedule_task = None


# =====================================================================
# BAN RECORDS & PENALTY LOGIC
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
# TIME WINDOWS & CHANNEL HELPERS
# =====================================================================

def init_hour_state(hour: int):
    if hour not in scrim_state:
        scrim_state[hour] = {
            "slots": {},
            "waitlist": [],
            "slots_msg_id": None,
            "last_day_reset": None,
            "manual_open": None,
        }
    return scrim_state[hour]


def is_in_window(now_t: time, start_t: time, end_t: time) -> bool:
    if start_t <= end_t:
        return start_t <= now_t <= end_t
    return now_t >= start_t or now_t <= end_t


def get_phase_from_cfg(cfg: dict, now_t: time) -> str | None:
    if is_in_window(now_t, cfg["vip"][0], cfg["vip"][1]):
        return "vip"
    if is_in_window(now_t, cfg["reg"][0], cfg["reg"][1]):
        return "reg"
    if is_in_window(now_t, cfg["waitlist"][0], cfg["waitlist"][1]):
        return "waitlist"
    return None


def get_active_phase(hour: int, now_t: time, manual_open: bool | None = None) -> str | None:
    if manual_open is False:
        return None
    phase = get_phase_from_cfg(SCHEDULES[hour], now_t)
    if phase is not None:
        return phase
    return "reg" if manual_open is True else None


def is_tourney20_reg_open() -> bool:
    return bool(tourney20_state.get("manual_open"))


def parse_hour_from_reg_channel(channel: discord.abc.GuildChannel) -> int | None:
    if not isinstance(channel, discord.TextChannel):
        return None
    for h, cfg in SCHEDULES.items():
        if channel.name == cfg["reg_channel"]:
            return h
    return None


def get_channels_for_hour(guild: discord.Guild, hour: int):
    cfg = SCHEDULES[hour]
    return (
        discord.utils.get(guild.text_channels, name=cfg["reg_channel"]),
        discord.utils.get(guild.text_channels, name=cfg["slot_channel"]),
        discord.utils.get(guild.text_channels, name=cfg["wait_channel"]),
        discord.utils.get(guild.text_channels, name=cfg["idpw_channel"]),
    )


def get_tourney20_channels(guild: discord.Guild):
    return (
        discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["reg_channel"]),
        discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["slot_channel"]),
        discord.utils.get(guild.text_channels, name=TOURNAMENT_20_CFG["idpw_channel"]),
    )


async def set_channel_writable(channel: discord.TextChannel | None, allowed: bool):
    if channel is None or channel.guild.default_role is None:
        return
    try:
        current = channel.overwrites_for(channel.guild.default_role).send_messages
        if current is not allowed:
            await channel.set_permissions(channel.guild.default_role, send_messages=allowed)
    except Exception as e:
        print(f"[ERROR] set_channel_writable #{channel.name}: {e}")


async def grant_view_access(channel: discord.TextChannel | None, member: discord.Member):
    if channel is None:
        return
    try:
        await channel.set_permissions(member, view_channel=True, read_message_history=True)
    except Exception as e:
        print(f"[ERROR] grant_view_access #{channel.name}: {e}")


async def revoke_view_access(
    channel: discord.TextChannel | None,
    member: discord.Member | discord.Object,
):
    if channel is None:
        return
    try:
        await channel.set_permissions(member, overwrite=None)
    except Exception as e:
        print(f"[ERROR] revoke_view_access #{channel.name}: {e}")


async def purge_idpw_channel(idpw_ch: discord.TextChannel | None):
    if isinstance(idpw_ch, discord.TextChannel):
        try:
            await idpw_ch.purge(limit=100)
        except Exception as e:
            print(f"[ERROR] purge_idpw_channel #{idpw_ch.name}: {e}")


async def ensure_board_reactions(msg: discord.Message):
    try:
        existing = {str(r.emoji) for r in msg.reactions if r.me}
        if "✅" not in existing:
            await msg.add_reaction("✅")
        if "❌" not in existing:
            await msg.add_reaction("❌")
    except Exception as e:
        print(f"[ERROR] ensure_board_reactions: {e}")


def is_vip_member(member: discord.Member) -> bool:
    return any(r.name == VIP_ROLE_NAME for r in member.roles)


def has_attached_image(message: discord.Message) -> bool:
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            return True
        if att.filename.lower().endswith(image_exts):
            return True
    return False


# =====================================================================
# SLOT BOARDS FORMATTING & SYNCING
# =====================================================================

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


def format_tourney20_board() -> str:
    status = "OPEN" if tourney20_state.get("manual_open") else "CLOSED"
    lines = [f"**20:00 TOURNAMENT — SLOT LIST ({status})**", ""]

    if not tourney20_state["slots"]:
        lines.append("*No registrations yet.*")
    else:
        for s in sorted(tourney20_state["slots"].keys()):
            entry = tourney20_state["slots"][s]
            display_name = f"__{entry['team']}__" if entry.get("confirmed") else entry["team"]
            lines.append(f"**Slot {s}:** {display_name} — <@{entry['manager_id']}>")

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


async def sync_slot_board(guild: discord.Guild, hour: int):
    st = init_hour_state(hour)
    _, slot_ch, _, _ = get_channels_for_hour(guild, hour)
    await sync_board_message(
        slot_ch,
        st,
        format_slot_board_from_state(f"{hour:02d}:00 SCRIM", st, True),
    )


async def sync_tourney20_board(guild: discord.Guild):
    _, slot_ch, _ = get_tourney20_channels(guild)
    await sync_board_message(slot_ch, tourney20_state, format_tourney20_board())


async def update_tourney20_idpw_channel(guild: discord.Guild):
    _, _, idpw_ch = get_tourney20_channels(guild)
    if not isinstance(idpw_ch, discord.TextChannel):
        return
    await purge_idpw_channel(idpw_ch)
    lines = [
        "**20:00 TOURNAMENT — ID / PW / SLOT**",
        f"**ID:** `{tourney20_state.get('room_id', '-')}`",
        f"**PW:** `{tourney20_state.get('room_pw', '-')}`",
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


def format_tdm_board() -> str:
    status = "OPEN" if tdm_state["is_open"] else "CLOSED"
    lines = [f"**TDM TOURNAMENT — SLOT LIST ({status})**", ""]
    for s in range(1, TDM_MAX_SLOTS + 1):
        entry = tdm_state["slots"].get(s)
        if entry:
            label = f"__{entry['name']}__" if entry.get("confirmed") else entry["name"]
            lines.append(f"**Slot {s}:** {label} — <@{entry['manager_id']}>")
        else:
            lines.append(f"**Slot {s}:** *Open*")
    lines.extend(["", "React ✅ to confirm your slot | React ❌ to cancel your slot."])
    return "\n".join(lines)


async def sync_tdm_board(guild: discord.Guild):
    slot_ch = discord.utils.get(guild.text_channels, name=TDM_SLOT_CHANNEL)
    await sync_board_message(slot_ch, tdm_state, format_tdm_board())


# =====================================================================
# RESETS & SCHEDULE LOOP
# =====================================================================

async def revoke_user_ids_from_channels(
    guild: discord.Guild,
    user_ids: set[int],
    channels: list[discord.TextChannel | None],
):
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


async def reset_scrim_hour(guild: discord.Guild, hour: int):
    st = init_hour_state(hour)
    _, slot_ch, wait_ch, idpw_ch = get_channels_for_hour(guild, hour)
    user_ids = {entry["manager_id"] for entry in st["slots"].values()} | {
        w["manager_id"] for w in st["waitlist"]
    }
    await revoke_user_ids_from_channels(guild, user_ids, [slot_ch, wait_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    st["slots"] = {}
    st["waitlist"] = []
    st["manual_open"] = None
    await sync_slot_board(guild, hour)


async def reset_tourney20(guild: discord.Guild):
    _, slot_ch, idpw_ch = get_tourney20_channels(guild)
    user_ids = {entry["manager_id"] for entry in tourney20_state["slots"].values()}
    await revoke_user_ids_from_channels(guild, user_ids, [slot_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    tourney20_state["slots"] = {}
    tourney20_state["manual_open"] = False
    tourney20_state["room_id"] = "-"
    tourney20_state["room_pw"] = "-"
    await sync_tourney20_board(guild)


async def reset_tdm(guild: discord.Guild):
    slot_ch = discord.utils.get(guild.text_channels, name=TDM_SLOT_CHANNEL)
    idpw_ch = discord.utils.get(guild.text_channels, name=TDM_IDPW_CHANNEL)
    user_ids = {entry["manager_id"] for entry in tdm_state["slots"].values()}
    await revoke_user_ids_from_channels(guild, user_ids, [slot_ch, idpw_ch])
    await purge_idpw_channel(idpw_ch)
    tdm_state["slots"] = {}
    await sync_tdm_board(guild)


async def run_schedule_tick():
    now = datetime.now(TIMEZONE)
    now_t = now.time()
    today_str = now.strftime("%Y-%m-%d")

    for guild in bot.guilds:
        for hour, cfg in SCHEDULES.items():
            st = init_hour_state(hour)
            reg_ch, _, _, _ = get_channels_for_hour(guild, hour)
            if st["last_day_reset"] != today_str and is_in_window(
                now_t, cfg["vip"][0], cfg["waitlist"][1]
            ):
                st["slots"] = {}
                st["waitlist"] = []
                st["last_day_reset"] = today_str
                st["manual_open"] = None
                await sync_slot_board(guild, hour)

            if st.get("manual_open") is True:
                is_open = True
            elif st.get("manual_open") is False:
                is_open = False
            else:
                is_open = get_phase_from_cfg(cfg, now_t) is not None
            await set_channel_writable(reg_ch, is_open)

        t_reg_ch, _, _ = get_tourney20_channels(guild)
        await set_channel_writable(t_reg_ch, bool(tourney20_state.get("manual_open")))


async def schedule_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await run_schedule_tick()
        except Exception as e:
            print(f"[ERROR] schedule_loop: {e}")
        await asyncio.sleep(15)


# =====================================================================
# TICKET BUTTON & COMMANDS (%ticket, %t20idpw, %noshow, %unban)
# =====================================================================

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
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
            )
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
    target_user_id = ctx.message.mentions[0].id if ctx.message.mentions else None
    removed_slot_text = ""
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
        if target_user_id is None and arg2 is not None:
            try:
                slot_num = int(arg2)
                entry = st["slots"].pop(slot_num, None)
                if entry:
                    target_user_id = entry["manager_id"]
                    removed_slot_text = f" (Slot #{slot_num} deleted: {entry['team']} [{entry['tag']}])"
                    await sync_slot_board(ctx.guild, hour)
            except ValueError:
                pass
        elif target_user_id is not None:
            for s, entry in list(st["slots"].items()):
                if entry["manager_id"] == target_user_id:
                    del st["slots"][s]
                    removed_slot_text = f" (Slot #{s} deleted)"
                    await sync_slot_board(ctx.guild, hour)
                    break
        if target_user_id is not None:
            m = ctx.guild.get_member(target_user_id) or discord.Object(id=target_user_id)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)

    elif mode_l in ("t20", "tourney20", "tournament"):
        if target_user_id is None:
            try:
                slot_num = int(arg1)
                entry = tourney20_state["slots"].pop(slot_num, None)
                if entry:
                    target_user_id = entry["manager_id"]
                    removed_slot_text = f" (Slot #{slot_num} deleted: {entry['team']})"
                    await sync_tourney20_board(ctx.guild)
            except ValueError:
                pass
        else:
            for s, entry in list(tourney20_state["slots"].items()):
                if entry["manager_id"] == target_user_id:
                    del tourney20_state["slots"][s]
                    removed_slot_text = f" (Slot #{s} deleted)"
                    await sync_tourney20_board(ctx.guild)
                    break
        _, slot_ch, idpw_ch = get_tourney20_channels(ctx.guild)
        if target_user_id is not None:
            m = ctx.guild.get_member(target_user_id) or discord.Object(id=target_user_id)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)

    elif mode_l == "tdm":
        if target_user_id is None:
            try:
                slot_num = int(arg1)
                entry = tdm_state["slots"].pop(slot_num, None)
                if entry:
                    target_user_id = entry["manager_id"]
                    removed_slot_text = f" (Slot #{slot_num} deleted: {entry['name']})"
                    await sync_tdm_board(ctx.guild)
            except ValueError:
                pass
        else:
            for s, entry in list(tdm_state["slots"].items()):
                if entry["manager_id"] == target_user_id:
                    del tdm_state["slots"][s]
                    removed_slot_text = f" (Slot #{s} deleted)"
                    await sync_tdm_board(ctx.guild)
                    break
        slot_ch = discord.utils.get(ctx.guild.text_channels, name=TDM_SLOT_CHANNEL)
        idpw_ch = discord.utils.get(ctx.guild.text_channels, name=TDM_IDPW_CHANNEL)
        if target_user_id is not None:
            m = ctx.guild.get_member(target_user_id) or discord.Object(id=target_user_id)
            await revoke_view_access(slot_ch, m)
            await revoke_view_access(idpw_ch, m)
    else:
        await ctx.send(
            "❌ Usage: `%noshow scrim <16|20|23> <slot>` | `%noshow t20 <slot>` | `%noshow tdm <slot>`"
        )
        return

    if target_user_id is None:
        await ctx.send("❌ Could not find a player/slot for that no-show.")
        return

    res = apply_missed_match_ban(target_user_id)
    reason_line = f"Missed match #{res['misses']} -> **{res['duration_text']}**{removed_slot_text}"
    await post_ban_log(ctx.guild, log_mode, target_user_id, reason_line, res["permanent"])
    await ctx.send(f"⚖️ <@{target_user_id}> — {reason_line}")


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


# =====================================================================
# EVENT HANDLERS: JOIN, READY, REACTIONS, %LOGO, REGISTRATION
# =====================================================================

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
            f"👋 Welcome {member.mention} to **{guild.name}**! "
            f"Check the rules and scrim / tournament channels, and enjoy your stay!"
        )


@bot.event
async def on_ready():
    global _view_registered, _schedule_task
    load_ban_records()
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
            await message.channel.send("🔄 Reset complete.")
            return

        is_opening = raw == "open!"
        if ch_name == TDM_REG_CHANNEL:
            tdm_state["is_open"] = is_opening
            await set_channel_writable(message.channel, is_opening)
            await sync_tdm_board(message.guild)
        elif ch_name == TOURNAMENT_20_CFG["reg_channel"]:
            tourney20_state["manual_open"] = is_opening
            await set_channel_writable(message.channel, is_opening)
            await sync_tourney20_board(message.guild)
        else:
            hour = parse_hour_from_reg_channel(message.channel)
            if hour is not None:
                st = init_hour_state(hour)
                st["manual_open"] = is_opening
                await set_channel_writable(message.channel, is_opening)
                await sync_slot_board(message.guild, hour)

        await message.add_reaction("✅")
        await message.channel.send(
            "✅ Registration is now **OPEN**."
            if is_opening
            else "🔒 Registration is now **CLOSED**."
        )
        return

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if lines and lines[0].lower() == "%logo":
        if ch_name != TEAM_LOGO_CHANNEL:
            await message.add_reaction("❌")
            await message.reply(f"❌ Use `%logo` inside `#{TEAM_LOGO_CHANNEL}`.")
            return
        if len(lines) < 3 or not has_attached_image(message):
            await message.add_reaction("❌")
            await message.reply(
                "❌ Format:\n`%logo`\n`Team Name`\n`Tag`\n`+ Photo (attached image)`"
            )
            return
        logo_team = lines[1]
        logo_tag = lines[2]
        await message.add_reaction("✅")
        await message.reply(
            f"✅ Team logo received — **{logo_team}** [{logo_tag}] | Submitted by {message.author.mention}"
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
                f"✅ TDM Tournament | Slot **#{assigned_slot}** — **{game_name}** | Manager: {manager.mention}"
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
                f"✅ 20:00 Tournament | Slot **#{assigned_slot}** — **{team_name}** | Manager: {manager.mention}"
            )
            return

        hour = parse_hour_from_reg_channel(message.channel)
        if hour is None:
            await message.add_reaction("❌")
            return
        st = init_hour_state(hour)
        now_t = datetime.now(TIMEZONE).time()
        phase = get_active_phase(hour, now_t, st.get("manual_open"))
        if phase is None or len(lines) < 3:
            await message.add_reaction("❌")
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
                f"✅ {hour:02d}:00 Scrim | Slot **#{assigned_slot}** — **{team_name}** [{team_tag}] | Manager: {manager.mention}"
            )
            return

        st["waitlist"].append(
            {"team": team_name, "tag": team_tag, "manager_id": manager.id}
        )
        await sync_slot_board(message.guild, hour)
        await grant_view_access(wait_ch, manager)
        await message.add_reaction("✅")
        await message.reply(
            f"⏳ {hour:02d}:00 Scrim | **Wait-list** — **{team_name}** [{team_tag}] | Manager: {manager.mention}"
        )
        return

    await bot.process_commands(message)


if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError(
        "Missing bot token: set DISCORD_TOKEN in Railway Variables or replace TOKEN in bot.py."
    )

bot.run(TOKEN)

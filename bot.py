from datetime import datetime
import os
import re
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks

# ==========================================
# SETTINGS
# ==========================================
TZ = ZoneInfo("Europe/Bratislava")
MAX_SLOTS = 20

# Fixed ID/PW channel ID (int), or set it with !setidpchannel
IDPW_CHANNEL_ID: int | None = None

EMOJI_OK = "✅"
EMOJI_WAIT = "⏳"
EMOJI_BAD = "❌"
EMOJI_LOCK = "🔒"

# Time windows: (start_hour, start_minute, end_hour, end_minute)
SCRIM_CONFIGS: dict[str, dict] = {
    "16": {
        "label": "16:00 Scrim",
        "main_window": (10, 0, 13, 0),
        "waitlist_window": (15, 20, 15, 30),
        "open": False,
        "manual_override": False,
        "auto_opened": False,
        "slots": [],
        "waitlist": [],
        "room_id": None,
        "room_pw": None,
    },
    "20": {
        "label": "20:00 Scrim",
        "main_window": (15, 0, 16, 0),
        "waitlist_window": (19, 20, 19, 30),
        "open": False,
        "manual_override": False,
        "auto_opened": False,
        "slots": [],
        "waitlist": [],
        "room_id": None,
        "room_pw": None,
    },
    "23": {
        "label": "23:00 Scrim",
        "main_window": (16, 0, 17, 0),
        "waitlist_window": (22, 20, 22, 30),
        "open": False,
        "manual_override": False,
        "auto_opened": False,
        "slots": [],
        "waitlist": [],
        "room_id": None,
        "room_pw": None,
    },
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=["%", "!"], intents=intents)
last_reset_date = None


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def in_window(now_hm: tuple[int, int], win: tuple[int, int, int, int]) -> bool:
  sh, sm, eh, em = win
  return (sh, sm) <= now_hm < (eh, em)


def detect_scrim_key(
    text: str = "", channel: discord.abc.GuildChannel | None = None
) -> str | None:
  # 1. By channel name (e.g. #16-registration, #20-registration, #23-registration)
  if channel and getattr(channel, "name", None):
    cname = channel.name.lower()
    if cname.startswith("16") or "-16" in cname or "_16" in cname:
      return "16"
    if cname.startswith("20") or "-20" in cname or "_20" in cname:
      return "20"
    if cname.startswith("23") or "-23" in cname or "_23" in cname:
      return "23"

  # 2. By text
  t = (text or "").strip().lower()
  if "16:00" in t or t.startswith("16 ") or t.endswith(" 16") or t == "16":
    return "16"
  if "20:00" in t or t.startswith("20 ") or t.endswith(" 20") or t == "20":
    return "20"
  if "23:00" in t or t.startswith("23 ") or t.endswith(" 23") or t == "23":
    return "23"
  return None


def get_active_scrim_key(
    now_hm: tuple[int, int], channel: discord.abc.GuildChannel | None = None
) -> str | None:
  by_channel = detect_scrim_key("", channel)
  if by_channel and by_channel in SCRIM_CONFIGS:
    return by_channel

  for k, cfg in SCRIM_CONFIGS.items():
    if cfg["open"]:
      return k

  for k, cfg in SCRIM_CONFIGS.items():
    if in_window(now_hm, cfg["main_window"]) or in_window(
        now_hm, cfg["waitlist_window"]
    ):
      return k
  return None


def parse_register_body(raw_body: str, message: discord.Message):
  """
    Expected format:
    %register
    Team name
    Team tag
    @manager
    """
  lines = [line.strip() for line in raw_body.splitlines() if line.strip()]
  team_name = lines[0] if len(lines) >= 1 else None
  team_tag = lines[1] if len(lines) >= 2 else None

  manager = message.mentions[0] if message.mentions else None
  if not manager and len(lines) >= 3:
    m = re.search(r"<@!?(\d+)>", lines[2])
    if m and message.guild:
      manager = message.guild.get_member(int(m.group(1)))

  return team_name, team_tag, manager


# ==========================================
# AUTOMATIC OPEN / CLOSE AT EXACT TIMES
# ==========================================
@tasks.loop(minutes=1)
async def auto_scrim_timer():
  global last_reset_date
  now = datetime.now(TZ)
  today = now.date()
  now_hm = (now.hour, now.minute)

  # Reset daily scrim data at the start of a new day (00:00)
  if last_reset_date != today:
    last_reset_date = today
    for cfg in SCRIM_CONFIGS.values():
      cfg["open"] = False
      cfg["manual_override"] = False
      cfg["auto_opened"] = False
      cfg["slots"] = []
      cfg["waitlist"] = []
      cfg["room_id"] = None
      cfg["room_pw"] = None

  for key, cfg in SCRIM_CONFIGS.items():
    should_open = in_window(now_hm, cfg["main_window"]) or in_window(
        now_hm, cfg["waitlist_window"]
    )

    # Automatic opening at exact scheduled time
    if should_open and not cfg["open"] and not cfg["auto_opened"]:
      cfg["open"] = True
      cfg["auto_opened"] = True
      cfg["manual_override"] = False

    # Automatic closing at end of window (unless opened via manual override)
    elif not should_open and cfg["open"] and not cfg["manual_override"]:
      cfg["open"] = False
      cfg["auto_opened"] = False


# ==========================================
# REGISTRATION, SLOT, WAITLIST, ID/PW COMMANDS
# ==========================================
@bot.command(name="register")
async def register_cmd(ctx: commands.Context, *, body: str = ""):
  now_hm = (datetime.now(TZ).hour, datetime.now(TZ).minute)
  key = get_active_scrim_key(now_hm, ctx.channel)

  if not key:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  cfg = SCRIM_CONFIGS[key]
  if not cfg["open"]:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  team_name, team_tag, manager = parse_register_body(body, ctx.message)
  if not team_name or not team_tag or not manager:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  # Check for duplicate registration
  for s in cfg["slots"]:
    if s["manager_id"] == manager.id or s["user_id"] == ctx.author.id:
      await ctx.message.add_reaction(EMOJI_BAD)
      return
  for w in cfg["waitlist"]:
    if w["manager_id"] == manager.id or w["user_id"] == ctx.author.id:
      await ctx.message.add_reaction(EMOJI_WAIT)
      return

  if len(cfg["slots"]) < MAX_SLOTS:
    slot_num = len(cfg["slots"]) + 1
    cfg["slots"].append({
        "slot": slot_num,
        "user_id": ctx.author.id,
        "manager_id": manager.id,
        "team_name": team_name,
        "team_tag": team_tag,
    })
    await ctx.message.add_reaction(EMOJI_OK)
    await ctx.reply(
        f"{EMOJI_OK} **{cfg['label']}** | Slot **#{slot_num}** — "
        f"**{team_name}** `[{team_tag}]` | Manager: {manager.mention}",
        mention_author=False,
    )
  else:
    wait_num = len(cfg["waitlist"]) + 1
    cfg["waitlist"].append({
        "wait": wait_num,
        "user_id": ctx.author.id,
        "manager_id": manager.id,
        "team_name": team_name,
        "team_tag": team_tag,
    })
    await ctx.message.add_reaction(EMOJI_WAIT)
    await ctx.reply(
        f"{EMOJI_WAIT} **{cfg['label']}** | Waitlist **#{wait_num}** — "
        f"**{team_name}** `[{team_tag}]` | Manager: {manager.mention}",
        mention_author=False,
    )


@bot.command(name="slots")
async def slots_cmd(ctx: commands.Context, scrim: str = ""):
  now_hm = (datetime.now(TZ).hour, datetime.now(TZ).minute)
  key = detect_scrim_key(scrim, ctx.channel) or get_active_scrim_key(
      now_hm, ctx.channel
  )

  if not key:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  cfg = SCRIM_CONFIGS[key]
  slot_lines = (
      "\n".join([
          f"`#{s['slot']:02d}` **{s['team_name']}** `[{s['team_tag']}]` —"
          f" <@{s['manager_id']}>"
          for s in cfg["slots"]
      ])
      or "Empty"
  )
  wait_lines = (
      "\n".join([
          f"`W{w['wait']}` **{w['team_name']}** `[{w['team_tag']}]` —"
          f" <@{w['manager_id']}>"
          for w in cfg["waitlist"]
      ])
      or "Empty"
  )

  await ctx.send(
      f"**{cfg['label']}** — Status: `{'OPEN' if cfg['open'] else 'CLOSED'}`\n"
      f"**Slots ({len(cfg['slots'])}/{MAX_SLOTS}):**\n{slot_lines}\n\n"
      f"**Waitlist ({len(cfg['waitlist'])}):**\n{wait_lines}"
  )


@bot.command(name="setidpchannel")
@commands.has_permissions(administrator=True)
async def set_idp_channel_cmd(
    ctx: commands.Context, channel: discord.TextChannel = None
):
  global IDPW_CHANNEL_ID
  target = channel or ctx.channel
  IDPW_CHANNEL_ID = target.id
  await ctx.message.add_reaction(EMOJI_OK)
  await ctx.send(f"ID/PW channel set to: {target.mention}")


@bot.command(name="idpw")
@commands.has_permissions(manage_messages=True)
async def idpw_cmd(
    ctx: commands.Context, scrim_or_id: str, room_id: str, room_pw: str = ""
):
  now_hm = (datetime.now(TZ).hour, datetime.now(TZ).minute)
  key = detect_scrim_key(scrim_or_id, ctx.channel) or get_active_scrim_key(
      now_hm, ctx.channel
  )

  if not key:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  # If no scrim number was provided first, scrim_or_id is room_id and room_id is room_pw
  if detect_scrim_key(scrim_or_id) is None and room_pw:
    actual_id = scrim_or_id
    actual_pw = room_id
  else:
    actual_id = room_id
    actual_pw = room_pw

  if not actual_id or not actual_pw:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  cfg = SCRIM_CONFIGS[key]
  cfg["room_id"] = actual_id
  cfg["room_pw"] = actual_pw

  if not cfg["slots"]:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  # Send only to teams/managers currently in slots
  recipient_ids = []
  for s in cfg["slots"]:
    if s["manager_id"] not in recipient_ids:
      recipient_ids.append(s["manager_id"])

  mentions = " ".join([f"<@{uid}>" for uid in recipient_ids])
  info_text = (
      f"**{cfg['label']} — ID / PW**\n"
      f"Room ID: `{actual_id}`\n"
      f"Password: `{actual_pw}`\n"
      f"Slot teams: {mentions}"
  )

  target_channel = bot.get_channel(IDPW_CHANNEL_ID) if IDPW_CHANNEL_ID else None
  if target_channel:
    await target_channel.send(info_text)
  else:
    for uid in recipient_ids:
      user = bot.get_user(uid) or await bot.fetch_user(uid)
      try:
        await user.send(info_text)
      except discord.Forbidden:
        pass

  await ctx.message.add_reaction(EMOJI_OK)


# ==========================================
# EVENTS + MANUAL open! / close! IN CHAT
# ==========================================
@bot.event
async def on_ready():
  if not auto_scrim_timer.is_running():
    auto_scrim_timer.start()
  print(f"Bot started: {bot.user}")


@bot.event
async def on_message(message: discord.Message):
  if message.author.bot:
    return

  raw = message.content.strip()
  low = raw.lower()

  # Open or close anytime in chat with "open!" or "close!"
  if not raw.startswith(("%", "!")) and ("open" in low or "close" in low):
    now_hm = (datetime.now(TZ).hour, datetime.now(TZ).minute)
    key = detect_scrim_key(low, message.channel) or get_active_scrim_key(
        now_hm, message.channel
    )
    if key and key in SCRIM_CONFIGS:
      cfg = SCRIM_CONFIGS[key]
      if "open" in low:
        cfg["open"] = True
        cfg["manual_override"] = True
        await message.add_reaction(EMOJI_OK)
      elif "close" in low:
        cfg["open"] = False
        cfg["manual_override"] = True
        await message.add_reaction(EMOJI_LOCK)

  await bot.process_commands(message)

@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear_cmd(ctx: commands.Context, amount: int = 20):
  deleted = await ctx.channel.purge(limit=max(1, min(amount, 100)) + 1)
  await ctx.send(f"Deleted: {len(deleted) - 1} messages.", delete_after=3)


# ==========================================
# STARTUP
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") or "PUT_YOUR_BOT_TOKEN_HERE"
bot.run(TOKEN)


# ==========================================
# STARTUP
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") or "PUT_YOUR_BOT_TOKEN_HERE"
bot.run(TOKEN)

from datetime import datetime, time, timedelta
import os
import re
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

# ==========================================
# BOT SETUP & INTENTS
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Timezone for scrim schedule (Slovakia / CET)
SCRIM_TZ = ZoneInfo("Europe/Bratislava")

# VIP configuration: set your Discord VIP Role ID or exact role name
VIP_ROLE_ID = 0  # Replace with your VIP role ID (int), e.g. 123456789012345678
VIP_ROLE_NAME = "vip_role"
VIP_EARLY_MINUTES = 30

# Scrim configuration per hour prefix
# public_open: when non-VIP registration opens (VIPs can register 30 mins earlier)
SCRIMS = {
    "16": {
        "label": "16:00 Scrim",
        "scrim_time": time(16, 0),
        "public_open": time(15, 30),  # VIPs can register from 15:00
        "max_slots": 24,
        "blocked_slots": [1, 2],
        "vip_slots": [21, 22, 23, 24],
        "manual_open": False,
        "slots": {},  # {slot_number: entry_dict}
        "waitlist": [],
        "slot_board_msg_id": None,
    },
    "20": {
        "label": "20:00 Scrim",
        "scrim_time": time(20, 0),
        "public_open": time(19, 30),  # VIPs can register from 19:00
        "max_slots": 24,
        "blocked_slots": [1, 2],
        "vip_slots": [21, 22, 23, 24],
        "manual_open": False,
        "slots": {},
        "waitlist": [],
        "slot_board_msg_id": None,
    },
    "23": {
        "label": "23:00 Scrim",
        "scrim_time": time(23, 0),
        "public_open": time(22, 30),  # VIPs can register from 22:00
        "max_slots": 24,
        "blocked_slots": [1, 2],
        "vip_slots": [21, 22, 23, 24],
        "manual_open": False,
        "slots": {},
        "waitlist": [],
        "slot_board_msg_id": None,
    },
}


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def detect_hour_prefix(channel_name: str) -> str | None:
    """Detect scrim hour prefix ('16', '20', or '23') from channel name."""
    match = re.match(r"^(16|20|23)\b", channel_name.lower().strip())
    return match.group(1) if match else None


def is_vip_member(member: discord.abc.User | discord.Member) -> bool:
    """Check if a guild member has the configured VIP role."""
    if not isinstance(member, discord.Member):
        return False
    for role in member.roles:
        if VIP_ROLE_ID and role.id == VIP_ROLE_ID:
            return True
        if role.name.lower() == VIP_ROLE_NAME.lower():
            return True
    return False


def can_register_now(cfg: dict, member: discord.abc.User | discord.Member) -> tuple[bool, str]:
    """
    Check if registration is currently allowed for this member.
    VIP members can register 30 minutes earlier than public_open.
    """
    if not cfg.get("manual_open", False):
        return False, "closed"

    now = datetime.now(SCRIM_TZ)
    today_public_open = datetime.combine(now.date(), cfg["public_open"], tzinfo=SCRIM_TZ)
    today_vip_open = today_public_open - timedelta(minutes=VIP_EARLY_MINUTES)

    vip = is_vip_member(member)
    if vip:
        if now >= today_vip_open:
            return True, "vip"
        return False, "too_early_vip"

    if now >= today_public_open:
        return True, "public"
    return False, "vip_only_window"


def get_next_available_slot(cfg: dict, vip: bool) -> int | None:
    """
    Assign a slot number:
    - Slots 1 & 2 are blocked for everyone.
    - Slots 21..24 are VIP slots.
    - Slots 3..20 are regular slots.
    """
    blocked = set(cfg.get("blocked_slots", [1, 2]))
    vip_slots = sorted(cfg.get("vip_slots", [21, 22, 23, 24]))
    taken = set(cfg["slots"].keys())

    if vip:
        for slot_num in vip_slots:
            if slot_num not in blocked and slot_num not in taken:
                return slot_num

    for slot_num in range(1, cfg.get("max_slots", 24) + 1):
        if slot_num in blocked or slot_num in vip_slots or slot_num in taken:
            continue
        return slot_num

    return None


async def get_scrim_channels(guild: discord.Guild, hour_prefix: str):
    """Find slot, id-pw, and waitlist text channels for a given hour prefix."""
    slot_ch = None
    idpw_ch = None
    wait_ch = None

    for ch in guild.text_channels:
        name = ch.name.lower()
        if name.startswith(f"{hour_prefix}-") or name.startswith(hour_prefix):
            if "slot" in name:
                slot_ch = ch
            elif "id" in name or "pw" in name:
                idpw_ch = ch
            elif "wait" in name:
                wait_ch = ch

    return slot_ch, idpw_ch, wait_ch


async def grant_channel_access(channel: discord.TextChannel | None, target: discord.abc.Snowflake):
    """Grant view and read history access to a user/member on a channel."""
    if channel is None or target is None:
        return
    try:
        await channel.set_permissions(target, view_channel=True, read_message_history=True)
    except discord.Forbidden:
        pass


async def update_slot_channel_board(guild: discord.Guild, hour_prefix: str, cfg: dict):
    """Create or edit the live slot board message in #<hour>-slot / #<hour>-slots."""
    slot_ch, _, _ = await get_scrim_channels(guild, hour_prefix)
    if slot_ch is None:
        return

    total_slots = cfg.get("max_slots", 24)
    blocked_slots = set(cfg.get("blocked_slots", [1, 2]))
    vip_slots = set(cfg.get("vip_slots", [21, 22, 23, 24]))
    slots_map = cfg.get("slots", {})

    lines = [f"**{cfg['label']} — Slot List**"]
    for i in range(1, total_slots + 1):
        vip_tag = " *(VIP)*" if i in vip_slots else ""
        if i in blocked_slots:
            lines.append(f"`{i:02d}.` 🔒 *Locked*")
        elif i in slots_map:
            entry = slots_map[i]
            lines.append(f"`{i:02d}.` **{entry['team']}** `[{entry['tag']}]` — {entry['manager'].mention}{vip_tag}")
        else:
            lines.append(f"`{i:02d}.` —{vip_tag}")

    board_text = "\n".join(lines)
    board_msg_id = cfg.get("slot_board_msg_id")

    if board_msg_id:
        try:
            msg = await slot_ch.fetch_message(board_msg_id)
            await msg.edit(content=board_text)
            return
        except discord.NotFound:
            cfg["slot_board_msg_id"] = None

    async for msg in slot_ch.history(limit=15):
        if msg.author.id == bot.user.id and "Slot List" in msg.content:
            await msg.edit(content=board_text)
            cfg["slot_board_msg_id"] = msg.id
            return

    sent = await slot_ch.send(board_text)
    cfg["slot_board_msg_id"] = sent.id


async def handle_main_slot_added(guild: discord.Guild, hour_prefix: str, cfg: dict, author, manager=None):
    """Grant slot + id-pw access and refresh the slot list board when a main slot is assigned."""
    slot_ch, idpw_ch, _ = await get_scrim_channels(guild, hour_prefix)
    await grant_channel_access(slot_ch, author)
    await grant_channel_access(idpw_ch, author)
    if manager:
        await grant_channel_access(slot_ch, manager)
        await grant_channel_access(idpw_ch, manager)
    await update_slot_channel_board(guild, hour_prefix, cfg)


async def handle_waitlist_added(guild: discord.Guild, hour_prefix: str, author, manager=None):
    """Grant waitlist access when main slots are full."""
    _, _, wait_ch = await get_scrim_channels(guild, hour_prefix)
    await grant_channel_access(wait_ch, author)
    if manager:
        await grant_channel_access(wait_ch, manager)


def parse_registration_message(content: str, message: discord.Message):
    """
    Expected format:
    %register
    Team Name
    TAG
    @Manager
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < 3 or lines[0].lower() != "%register":
        return None

    team_name = lines[1]
    tag = lines[2]
    manager = message.mentions[0] if message.mentions else message.author
    return team_name, tag, manager


# ==========================================
# EVENTS & COMMANDS
# ==========================================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    content_lower = message.content.strip().lower()
    hour_prefix = detect_hour_prefix(message.channel.name)

    if hour_prefix and "registration" in message.channel.name.lower():
        cfg = SCRIMS[hour_prefix]

        if content_lower == "open!":
            cfg["manual_open"] = True
            await message.add_reaction("✅")
            await update_slot_channel_board(message.guild, hour_prefix, cfg)
            return

        if content_lower == "close!":
            cfg["manual_open"] = False
            await message.add_reaction("🔒")
            return

        if content_lower.startswith("%register"):
            allowed, status = can_register_now(cfg, message.author)
            if not allowed:
                await message.add_reaction("❌")
                if status == "vip_only_window":
                    await message.reply(
                        f"⏳ **{cfg['label']} — VIP early registration is active (30 mins early). Public registration opens at {cfg['public_open'].strftime('%H:%M')}.**",
                        mention_author=False,
                    )
                return

            parsed = parse_registration_message(message.content, message)
            if not parsed:
                await message.add_reaction("❌")
                return

            team_name, tag, manager = parsed

            already_registered = any(s["author_id"] == message.author.id for s in cfg["slots"].values()) or any(
                w["author_id"] == message.author.id for w in cfg["waitlist"]
            )
            if already_registered:
                await message.add_reaction("⚠️")
                return

            vip = is_vip_member(message.author)
            slot_number = get_next_available_slot(cfg, vip=vip)

            entry = {
                "author_id": message.author.id,
                "team": team_name,
                "tag": tag,
                "manager": manager,
                "vip": vip,
            }

            if slot_number is not None:
                cfg["slots"][slot_number] = entry
                vip_label = " *(VIP)*" if slot_number in cfg["vip_slots"] else ""
                await message.add_reaction("✅")
                await message.reply(
                    f"✅ **{cfg['label']} | Slot #{slot_number}{vip_label} — {team_name} `[{tag}]` | Manager: {manager.mention}**",
                    mention_author=False,
                )
                await handle_main_slot_added(message.guild, hour_prefix, cfg, message.author, manager)
            else:
                cfg["waitlist"].append(entry)
                await message.add_reaction("⏳")
                await message.reply(
                    f"⏳ **{cfg['label']} — Main slots full, added to Waitlist (#{len(cfg['waitlist'])}) for {team_name} `[{tag}]`.**",
                    mention_author=False,
                )
                await handle_waitlist_added(message.guild, hour_prefix, message.author, manager)
            return

    await bot.process_commands(message)


@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear_cmd(ctx: commands.Context, amount: int = 20):
    deleted = await ctx.channel.purge(limit=max(1, min(amount, 100)) + 1)
    await ctx.send(f"Deleted: {len(deleted) - 1} messages.", delete_after=3)


# ==========================================
# STARTUP
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") or "PUT_YOUR_BOT_TOKEN_HERE"
bot.run(TOKEN)



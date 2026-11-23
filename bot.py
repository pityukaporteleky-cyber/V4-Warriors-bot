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

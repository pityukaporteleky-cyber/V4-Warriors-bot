from datetime import datetime
import re
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks

TZ = ZoneInfo("Europe/Bratislava")
_last_day = None


def _in_win(now_hm: tuple[int, int], win: tuple[int, int, int, int]) -> bool:
  sh, sm, eh, em = win
  return (sh, sm) <= now_hm < (eh, em)


def _match_scrim(text: str) -> str | None:
  t = text.strip().lower()
  if "16:00" in t or t.startswith("16 ") or t.endswith(" 16") or t == "16":
    return "16"
  if "20:00" in t or t.startswith("20 ") or t.endswith(" 20") or t == "20":
    return "20"
  if "23:00" in t or t.startswith("23 ") or t.endswith(" 23") or t == "23":
    return "23"
  return None


# 1. Automatic opening & closing at exact scheduled times (16:00, 20:00, 23:00)
@tasks.loop(minutes=1)
async def auto_scrim_schedule():
  global _last_day
  now = datetime.now(TZ)
  today = now.date()
  now_hm = (now.hour, now.minute)

  if _last_day != today:
    _last_day = today
    for cfg in SCRIM_CONFIGS.values():
      cfg["open"] = False
      cfg["auto_opened"] = False
      cfg["manual_closed"] = False

  for key, cfg in SCRIM_CONFIGS.items():
    should_open = _in_win(now_hm, cfg["main_window"]) or _in_win(
        now_hm, cfg["waitlist_window"]
    )

    if should_open and not cfg["open"] and not cfg.get("manual_closed"):
      cfg["open"] = True
      cfg["auto_opened"] = True
    elif not should_open and cfg["open"] and cfg.get("auto_opened"):
      cfg["open"] = False
      cfg["auto_opened"] = False


# 2. Manual "open" / "close" messages in chat for 16:00, 20:00, and 23:00 scrims
async def handle_open_close_message(message: discord.Message):
  if message.author.bot:
    return
  low = message.content.strip().lower()
  if "open" not in low and "close" not in low:
    return

  key = _match_scrim(low)
  if not key or key not in SCRIM_CONFIGS:
    return

  cfg = SCRIM_CONFIGS[key]
  if "open" in low:
    cfg["open"] = True
    cfg["manual_closed"] = False
    await message.add_reaction("✅")
  elif "close" in low:
    cfg["open"] = False
    cfg["manual_closed"] = True
    await message.add_reaction("🔒")


# 3. %register format parser (Team name / Team tag / @manager) without overwriting your existing registration logic
def parse_percent_register(raw_args: str, message: discord.Message):
  lines = [line.strip() for line in raw_args.splitlines() if line.strip()]
  team_name = lines[0] if len(lines) >= 1 else None
  team_tag = lines[1] if len(lines) >= 2 else None

  manager = message.mentions[0] if message.mentions else None
  if not manager and len(lines) >= 3:
    m = re.search(r"<@!?(\d+)>", lines[2])
    if m and message.guild:
      manager = message.guild.get_member(int(m.group(1)))

  return team_name, team_tag, manager


@bot.command(name="register")
async def percent_register(ctx: commands.Context, *, body: str = ""):
  team_name, team_tag, manager = parse_percent_register(body, ctx.message)
  if not team_name or not team_tag or not manager:
    await ctx.message.add_reaction(EMOJI_BAD)
    return

  # Pass team_name, team_tag, and manager into your existing registration / slots / waitlist / emoji / ID-PW flow here.



import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
SCRIM_TIMEZONE = ZoneInfo("Europe/Budapest")

MAX_SLOTS = 20

# Optional: paste a fixed channel ID here (as integer without quotes),
# or use !setidpchannel inside the channel you want to use.
IDPW_CHANNEL_ID: int | None = None

EMOJI_OK = "✅"
EMOJI_WAIT = "⏳"
EMOJI_BAD = "❌"

# Windows defined as (start_hour, start_min, end_hour, end_min)
# End time is exclusive: start <= (h, m) < end
SCRIM_CONFIGS: dict[str, dict] = {
    "16": {
        "label": "16:00 Scrim",
        "main_window": (10, 0, 13, 0),
        "waitlist_window": (15, 20, 15, 30),
        "open": False,
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
        "slots": [],
        "waitlist": [],
        "room_id": None,
        "room_pw": None,
    },
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def current_time() -> datetime:
    return datetime.now(SCRIM_TIMEZONE)


def is_in_window(window: tuple[int, int, int, int]) -> bool:
    now = current_time()
    hm = (now.hour, now.minute)
    start = (window[0], window[1])
    end = (window[2], window[3])
    return start <= hm < end


def normalize_scrim_key(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().lower().replace(":00", "").replace("h", "")
    if cleaned in ("16", "20", "23"):
        return cleaned
    return None


def extract_mention_id(text: str) -> int | None:
    match = re.search(r"<@!?(\d+)>", text)
    return int(match.group(1)) if match else None


def find_user_in_scrim(scrim: dict, user_id: int) -> tuple[str | None, dict | None]:
    for entry in scrim["slots"]:
        if entry["register_user_id"] == user_id or entry["manager_id"] == user_id:
            return "slot", entry
    for entry in scrim["waitlist"]:
        if entry["register_user_id"] == user_id or entry["manager_id"] == user_id:
            return "waitlist", entry
    return None, None


def get_all_slotted_user_ids() -> set[int]:
    ids: set[int] = set()
    for scrim in SCRIM_CONFIGS.values():
        for s in scrim["slots"]:
            if s.get("register_user_id"):
                ids.add(s["register_user_id"])
            if s.get("manager_id"):
                ids.add(s["manager_id"])
    return ids


def get_all_waitlist_user_ids() -> set[int]:
    ids: set[int] = set()
    for scrim in SCRIM_CONFIGS.values():
        for w in scrim["waitlist"]:
            if w.get("register_user_id"):
                ids.add(w["register_user_id"])
            if w.get("manager_id"):
                ids.add(w["manager_id"])
    return ids


async def resolve_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.HTTPException:
        return None


async def sync_idpw_channel_permissions(guild: discord.Guild, channel: discord.TextChannel):
    slotted_ids = get_all_slotted_user_ids()
    waitlist_ids = get_all_waitlist_user_ids()

    overwrites = dict(channel.overwrites)
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_permissions=True,
        )

    for uid in slotted_ids:
        member = await resolve_member(guild, uid)
        if member and not member.bot:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=False,
            )

    for uid in waitlist_ids - slotted_ids:
        member = await resolve_member(guild, uid)
        if member and not member.bot:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=False,
                read_message_history=False,
                send_messages=False,
            )

    stale_targets = [
        target
        for target in list(overwrites.keys())
        if isinstance(target, discord.Member)
        and not target.bot
        and target.id != (guild.me.id if guild.me else 0)
        and target.id not in (slotted_ids | waitlist_ids)
    ]
    for target in stale_targets:
        del overwrites[target]

    await channel.edit(overwrites=overwrites, reason="Sync slot-only ID/PW channel permissions")


async def post_idpw_to_channel(guild: discord.Guild, scrim_key: str) -> tuple[bool, str]:
    scrim = SCRIM_CONFIGS[scrim_key]
    if not IDPW_CHANNEL_ID:
        return False, "No ID/PW channel set! Use `!setidpchannel` inside the target channel first."

    ch = guild.get_channel(IDPW_CHANNEL_ID)
    if not isinstance(ch, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(IDPW_CHANNEL_ID)
            ch = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.HTTPException:
            ch = None

    if ch is None:
        return False, f"ID/PW channel `{IDPW_CHANNEL_ID}` not found."

    try:
        await sync_idpw_channel_permissions(guild, ch)
    except discord.Forbidden:
        return False, f"Missing permissions to edit {ch.mention} (needs **Manage Channels / Manage Roles**)."

    if not scrim["room_id"] or not scrim["room_pw"]:
        return True, f"Channel {ch.mention} synced for slotted teams (ID/PW not set yet for {scrim['label']})."

    slot_summary = "\n".join(
        f"• **Slot #{s['slot']}** — {s['team']} `[{s['tag']}]` ({s['manager_text']})"
        for s in scrim["slots"]
    ) or "No slotted teams."

    try:
        await ch.send(
            f"**{scrim['label']} — Room ID & Password (Slotted Teams Only)**\n"
            f"**Room ID:** `{scrim['room_id']}`\n"
            f"**Password:** `{scrim['room_pw']}`\n\n"
            f"**Confirmed Slots:**\n{slot_summary}"
        )
    except discord.Forbidden:
        return False, f"Cannot send message to {ch.mention}."

    return True, f"{scrim['label']} ID & PW posted in {ch.mention} (only slotted teams can see it; waitlist cannot)."


async def refresh_permissions_if_channel_set(guild: discord.Guild | None):
    if not guild or not IDPW_CHANNEL_ID:
        return
    ch = guild.get_channel(IDPW_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        try:
            await sync_idpw_channel_permissions(guild, ch)
        except discord.Forbidden:
            pass


async def safe_react(message: discord.Message, emoji: str):
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


def detect_active_scrim_registration() -> tuple[str | None, str | None]:
    for key, scrim in SCRIM_CONFIGS.items():
        if not scrim["open"]:
            continue
        if is_in_window(scrim["main_window"]):
            return key, "main"
        if is_in_window(scrim["waitlist_window"]):
            return key, "waitlist"
    return None, None


async def process_scrim_register(message: discord.Message):
    scrim_key, mode = detect_active_scrim_registration()
    if not scrim_key or not mode:
        await safe_react(message, EMOJI_BAD)
        await message.channel.send(
            f"{message.author.mention} No open scrim registration window right now."
        )
        return

    raw_lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if not raw_lines or not raw_lines[0].lower().startswith("%register"):
        return

    first_rest = raw_lines[0][len("%register"):].strip()
    data_lines = ([first_rest] if first_rest else []) + raw_lines[1:]

    if len(data_lines) < 3:
        await safe_react(message, EMOJI_BAD)
        await message.channel.send(
            f"{message.author.mention} Invalid format! Use:\n"
            "```\n"
            "%register\n"
            "Team name\n"
            "Team tag\n"
            "@manager\n"
            "```"
        )
        return

    scrim = SCRIM_CONFIGS[scrim_key]
    team_name = data_lines[0]
    team_tag = data_lines[1]
    manager_text = data_lines[2]
    manager_id = extract_mention_id(manager_text)

    existing_role, _ = find_user_in_scrim(scrim, message.author.id)
    if existing_role:
        await safe_react(message, EMOJI_BAD)
        await message.channel.send(
            f"{message.author.mention} you are already registered in **{scrim['label']}** ({existing_role})!"
        )
        return

    if mode == "main":
        if len(scrim["slots"]) < MAX_SLOTS:
            slot_num = len(scrim["slots"]) + 1
            entry = {
                "slot": slot_num,
                "team": team_name,
                "tag": team_tag,
                "manager_text": manager_text,
                "manager_id": manager_id,
                "register_user_id": message.author.id,
            }
            scrim["slots"].append(entry)
            await safe_react(message, EMOJI_OK)
            await message.channel.send(
                f"**{scrim['label']} — Slot #{slot_num}** (`{slot_num}/{MAX_SLOTS}`) | "
                f"**{team_name}** `[{team_tag}]` | Manager: {manager_text}"
            )
            await refresh_permissions_if_channel_set(message.guild)
        else:
            wl_pos = len(scrim["waitlist"]) + 1
            entry = {
                "pos": wl_pos,
                "team": team_name,
                "tag": team_tag,
                "manager_text": manager_text,
                "manager_id": manager_id,
                "register_user_id": message.author.id,
            }
            scrim["waitlist"].append(entry)
            await safe_react(message, EMOJI_WAIT)
            await message.channel.send(
                f"**{scrim['label']}** slots are full! Added to **Waitlist #{wl_pos}** | "
                f"**{team_name}** `[{team_tag}]` | Manager: {manager_text}"
            )
            await refresh_permissions_if_channel_set(message.guild)

    elif mode == "waitlist":
        wl_pos = len(scrim["waitlist"]) + 1
        entry = {
            "pos": wl_pos,
            "team": team_name,
            "tag": team_tag,
            "manager_text": manager_text,
            "manager_id": manager_id,
            "register_user_id": message.author.id,
        }
        scrim["waitlist"].append(entry)
        await safe_react(message, EMOJI_WAIT)
        await message.channel.send(
            f"**{scrim['label']} (Waitlist Window)** — Added to **Waitlist #{wl_pos}** | "
            f"**{team_name}** `[{team_tag}]` | Manager: {manager_text}"
        )
        await refresh_permissions_if_channel_set(message.guild)


@bot.event
async def on_ready():
    print(f"Logged in as: {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    stripped = message.content.lstrip()
    if stripped.lower().startswith("%register"):
        await process_scrim_register(message)
        return

    await bot.process_commands(message)


@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send(f"Pong! ({round(bot.latency * 1000)} ms)")


@bot.command(name="setidpchannel")
@commands.has_permissions(manage_guild=True)
async def set_idp_channel_cmd(ctx, channel: discord.TextChannel = None):
    global IDPW_CHANNEL_ID
    target = channel or ctx.channel
    IDPW_CHANNEL_ID = target.id
    await refresh_permissions_if_channel_set(ctx.guild)
    await ctx.send(f"ID/PW channel set to {target.mention} (slot-only access synced).")


@bot.command(name="scrimopen")
@commands.has_permissions(manage_guild=True)
async def scrim_open_cmd(ctx, scrim_arg: str = None):
    key = normalize_scrim_key(scrim_arg)
    if not key:
        await ctx.send("Usage: `!scrimopen <16|20|23>`")
        return

    scrim = SCRIM_CONFIGS[key]
    mw = scrim["main_window"]
    ww = scrim["waitlist_window"]
    in_main = is_in_window(mw)
    in_wl = is_in_window(ww)

    if not in_main and not in_wl:
        await ctx.send(
            f"**{scrim['label']}** can only be opened during its main window "
            f"(**{mw[0]:02d}:{mw[1]:02d}-{mw[2]:02d}:{mw[3]:02d}**) or waitlist window "
            f"(**{ww[0]:02d}:{ww[1]:02d}-{ww[2]:02d}:{ww[3]:02d}**)."
        )
        return

    scrim["open"] = True
    scrim["slots"] = []
    scrim["waitlist"] = []
    scrim["room_id"] = None
    scrim["room_pw"] = None
    await refresh_permissions_if_channel_set(ctx.guild)
    await ctx.send(f"**{scrim['label']}** registration is now **OPEN**! (`0/{MAX_SLOTS}` slots)")


@bot.command(name="scrimclose")
@commands.has_permissions(manage_guild=True)
async def scrim_close_cmd(ctx, scrim_arg: str = None):
    key = normalize_scrim_key(scrim_arg)
    if not key:
        await ctx.send("Usage: `!scrimclose <16|20|23>`")
        return

    scrim = SCRIM_CONFIGS[key]
    scrim["open"] = False
    await ctx.send(
        f"**{scrim['label']}** registration is now **CLOSED**! "
        f"Slots: `{len(scrim['slots'])}/{MAX_SLOTS}` | Waitlist: `{len(scrim['waitlist'])}`"
    )


@bot.command(name="scrimstatus")
async def scrim_status_cmd(ctx):
    now = current_time()
    parts = [f"**Current Time (`Europe/Budapest`):** `{now.strftime('%Y-%m-%d %H:%M')}`"]
    for key, scrim in SCRIM_CONFIGS.items():
        mw = scrim["main_window"]
        ww = scrim["waitlist_window"]
        state = "OPEN" if scrim["open"] else "CLOSED"
        win_txt = (
            "MAIN window" if is_in_window(mw)
            else "WAITLIST window" if is_in_window(ww)
            else "outside windows"
        )
        parts.append(
            f"• **{scrim['label']}**: **{state}** ({win_txt}) | "
            f"Main `{mw[0]:02d}:{mw[1]:02d}-{mw[2]:02d}:{mw[3]:02d}`, "
            f"WL `{ww[0]:02d}:{ww[1]:02d}-{ww[2]:02d}:{ww[3]:02d}` | "
            f"Slots `{len(scrim['slots'])}/{MAX_SLOTS}`, WL `{len(scrim['waitlist'])}`"
        )
    await ctx.send("\n".join(parts))


@bot.command(name="slots")
async def slots_cmd(ctx, scrim_arg: str = "16"):
    key = normalize_scrim_key(scrim_arg)
    if not key:
        await ctx.send("Usage: `!slots <16|20|23>`")
        return

    scrim = SCRIM_CONFIGS[key]
    slot_lines = [
        f"**Slot #{s['slot']}:** {s['team']} `[{s['tag']}]` — {s['manager_text']}"
        for s in scrim["slots"]
    ] or ["No teams in slots yet."]

    wl_lines = [
        f"**WL #{w['pos']}:** {w['team']} `[{w['tag']}]` — {w['manager_text']}"
        for w in scrim["waitlist"]
    ] or ["No teams in waitlist."]

    await ctx.send(
        f"**{scrim['label']} Slots (`{len(scrim['slots'])}/{MAX_SLOTS}`):**\n"
        + "\n".join(slot_lines)
        + "\n\n**Waitlist:**\n"
        + "\n".join(wl_lines)
    )


@bot.command(name="scrimidp")
@commands.has_permissions(manage_guild=True)
async def scrim_idp_cmd(ctx, scrim_arg: str = None, room_id: str = None, room_pw: str = None):
    key = normalize_scrim_key(scrim_arg)
    if not key or not room_id or not room_pw:
        await ctx.send("Usage: `!scrimidp <16|20|23> <RoomID> <Password>`")
        return

    scrim = SCRIM_CONFIGS[key]
    scrim["room_id"] = room_id.strip()
    scrim["room_pw"] = room_pw.strip()
    _, status_msg = await post_idpw_to_channel(ctx.guild, key)
    await ctx.send(status_msg)

import os
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()

# Paste your Help category ID here (as an integer, without quotes), e.g.:
# HELP_CATEGORY_ID = 123456789012345678
HELP_CATEGORY_ID = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def create_user_ticket(guild: discord.Guild, member: discord.Member, fallback_category):
    safe_user = "".join(c for c in member.name.lower() if c.isalnum() or c in "-_")[:20] or "user"
    suffix = member.discriminator if member.discriminator != "0" else str(member.id % 10000)
    channel_name = f"ticket-{safe_user}-{suffix}"

    existing = discord.utils.get(guild.text_channels, name=channel_name)
    if existing:
        return existing, False

    category = guild.get_channel(HELP_CATEGORY_ID) if HELP_CATEGORY_ID else fallback_category

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        ),
    }

    for role in guild.roles:
        if role.permissions.administrator or role.permissions.manage_guild:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    ticket_channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Ticket opened by {member} (ID:{member.id})",
    )

    await ticket_channel.send(
        f"{member.mention} your ticket has been created!\n"
        f"Describe your issue here. Use `!closeticket` to close it."
    )
    return ticket_channel, True


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.green,
        custom_id="v4_open_ticket_button",
    )
    async def open_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This button only works inside a server.", ephemeral=True)
            return

        try:
            channel, created = await create_user_ticket(
                interaction.guild,
                interaction.user,
                interaction.channel.category if interaction.channel else None,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Missing permissions! Give the bot **Manage Channels** permission.",
                ephemeral=True,
            )
            return

        if created:
            await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        else:
            await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)


@bot.event
async def on_ready():
    bot.add_view(TicketPanelView())
    print(f"Logged in as: {bot.user} (ID: {bot.user.id})")


@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send(f"Pong! ({round(bot.latency * 1000)} ms)")


@bot.command(name="ticketpanel")
@commands.has_permissions(manage_guild=True)
async def ticket_panel_cmd(ctx):
    embed = discord.Embed(
        title="Support / Help Ticket",
        description="Need help with scrims, registration, or server issues?\nClick **Open Ticket** below to open a private ticket channel.",
        color=discord.Color.blue(),
    )
    await ctx.send(embed=embed, view=TicketPanelView())


@bot.command(name="closeticket")
async def close_ticket_cmd(ctx):
    if not ctx.channel.name.startswith("ticket-"):
        await ctx.send("This command can only be used inside a ticket channel.")
        return

    is_owner = ctx.channel.topic and f"ID:{ctx.author.id}" in ctx.channel.topic
    has_perm = (
        ctx.author.guild_permissions.manage_channels
        or ctx.author.guild_permissions.administrator
        or is_owner
    )
    if not has_perm:
        await ctx.send("You do not have permission to close this ticket.")
        return

    await ctx.send("Closing ticket...")
    await ctx.channel.delete(reason=f"Closed by {ctx.author}")


if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable!")

bot.run(TOKEN)

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable!")

bot.run(TOKEN)


%register
Team name
Team tag
@manager


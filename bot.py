import asyncio
from datetime import datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
PREFIX = "%"
VIP_ROLE_NAME = "Vip_role"


def get_local_timezone():
    try:
        return ZoneInfo("Europe/Budapest")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=1))


TIMEZONE = get_local_timezone()

# Schedules per scrim hour:
# vip: (open_time, close_time)
# reg: (open_time, close_time) -> regular registration (slots 3-20)
# waitlist: (open_time, close_time)
SCHEDULES = {
    16: {
        "slot_channel": "16-slots",
        "reg_channel": "16-registration",
        "wait_channel": "16-wait-list",
        "idpw_channel": "16-id-pw",
        "vip": (time(9, 30), time(9, 55)),
        "reg": (time(10, 0), time(13, 0)),
        "waitlist": (time(15, 20), time(15, 30)),
    },
    20: {
        "slot_channel": "20-slot",
        "reg_channel": "20-registration",
        "wait_channel": "20-wait-list",
        "idpw_channel": "20-id-pw",
        "vip": (time(14, 30), time(14, 55)),
        "reg": (time(15, 0), time(16, 30)),
        "waitlist": (time(19, 20), time(19, 30)),
    },
    23: {
        "slot_channel": "23-slot",
        "reg_channel": "23-registration",
        "wait_channel": "23-wait-list",
        "idpw_channel": "23-id-pw",
        "vip": (time(15, 30), time(16, 55)),
        "reg": (time(16, 0), time(17, 30)),
        "waitlist": (time(22, 20), time(22, 30)),
    },
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

scrim_state = {}
_view_registered = False
_schedule_task = None


def init_hour_state(hour: int):
    if hour not in scrim_state:
        scrim_state[hour] = {
            "slots": {},
            "waitlist": [],
            "slots_msg_id": None,
            "last_day_reset": None,
        }
    return scrim_state[hour]


def is_in_window(now_t: time, start_t: time, end_t: time) -> bool:
    if start_t <= end_t:
        return start_t <= now_t <= end_t
    return now_t >= start_t or now_t <= end_t


def get_active_phase(hour: int, now_t: time) -> str | None:
    cfg = SCHEDULES[hour]
    if is_in_window(now_t, cfg["vip"][0], cfg["vip"][1]):
        return "vip"
    if is_in_window(now_t, cfg["reg"][0], cfg["reg"][1]):
        return "reg"
    if is_in_window(now_t, cfg["waitlist"][0], cfg["waitlist"][1]):
        return "waitlist"
    return None


def parse_hour_from_reg_channel(channel: discord.abc.GuildChannel) -> int | None:
    if not isinstance(channel, discord.TextChannel):
        return None
    for h, cfg in SCHEDULES.items():
        if channel.name == cfg["reg_channel"]:
            return h
    return None


def get_channels_for_hour(guild: discord.Guild, hour: int):
    cfg = SCHEDULES[hour]
    reg_ch = discord.utils.get(guild.text_channels, name=cfg["reg_channel"])
    slot_ch = discord.utils.get(guild.text_channels, name=cfg["slot_channel"])
    wait_ch = discord.utils.get(guild.text_channels, name=cfg["wait_channel"])
    idpw_ch = discord.utils.get(guild.text_channels, name=cfg["idpw_channel"])
    return reg_ch, slot_ch, wait_ch, idpw_ch


async def set_reg_channel_writable(reg_ch: discord.TextChannel | None, allowed: bool):
    if reg_ch is None or reg_ch.guild.default_role is None:
        return
    try:
        current = reg_ch.overwrites_for(reg_ch.guild.default_role).send_messages
        if current is not allowed:
            await reg_ch.set_permissions(reg_ch.guild.default_role, send_messages=allowed)
    except discord.Forbidden:
        print(f"[WARN] Missing Manage Permissions in #{reg_ch.name}")
    except Exception as e:
        print(f"[ERROR] set_reg_channel_writable #{reg_ch.name}: {e}")


async def grant_view_access(channel: discord.TextChannel | None, member: discord.Member):
    if channel is None:
        return
    try:
        await channel.set_permissions(
            member,
            view_channel=True,
            read_message_history=True,
        )
    except discord.Forbidden:
        print(f"[WARN] Missing Manage Permissions in #{channel.name} for {member}")
    except Exception as e:
        print(f"[ERROR] grant_view_access #{channel.name}: {e}")


def format_slot_board(hour: int) -> str:
    st = init_hour_state(hour)
    lines = [f"**{hour:02d}:00 SCRIM — SLOT LIST**", ""]
    lines.append("🔒 **Slot 1:** Locked")
    lines.append("🔒 **Slot 2:** Locked")
    lines.append("")
    lines.append("**Regular Slots (3–20):**")
    for s in range(3, 21):
        entry = st["slots"].get(s)
        if entry:
            lines.append(
                f"**Slot {s}:** {entry['team']} [{entry['tag']}] — <@{entry['manager_id']}>"
            )
        else:
            lines.append(f"**Slot {s}:** *Open*")

    lines.append("")
    lines.append(f"**VIP Slots (24–21 — `{VIP_ROLE_NAME}`):**")
    for s in [24, 23, 22, 21]:
        entry = st["slots"].get(s)
        if entry:
            lines.append(
                f"👑 **Slot {s}:** {entry['team']} [{entry['tag']}] — <@{entry['manager_id']}>"
            )
        else:
            lines.append(f"👑 **Slot {s}:** *Open (VIP)*")

    if st["waitlist"]:
        lines.append("")
        lines.append("**Wait-list:**")
        for idx, w in enumerate(st["waitlist"], start=1):
            lines.append(
                f"{idx}. {w['team']} [{w['tag']}] — <@{w['manager_id']}>"
            )

    return "\n".join(lines)


async def sync_slot_board(guild: discord.Guild, hour: int):
    st = init_hour_state(hour)
    _, slot_ch, _, _ = get_channels_for_hour(guild, hour)
    if slot_ch is None:
        return

    content = format_slot_board(hour)
    msg_id = st.get("slots_msg_id")

    try:
        if msg_id:
            try:
                msg = await slot_ch.fetch_message(msg_id)
                await msg.edit(content=content)
                return
            except discord.NotFound:
                pass

        msg = await slot_ch.send(content=content)
        st["slots_msg_id"] = msg.id
    except discord.Forbidden:
        print(f"[WARN] Missing send/edit permissions in #{slot_ch.name}")
    except Exception as e:
        print(f"[ERROR] sync_slot_board #{slot_ch.name}: {e}")


def is_vip_member(member: discord.Member) -> bool:
    return any(r.name == VIP_ROLE_NAME for r in member.roles)


async def run_schedule_tick():
    now = datetime.now(TIMEZONE)
    now_t = now.time()
    today_str = now.strftime("%Y-%m-%d")

    for guild in bot.guilds:
        for hour, cfg in SCHEDULES.items():
            st = init_hour_state(hour)
            reg_ch, _, _, _ = get_channels_for_hour(guild, hour)

            vip_start = cfg["vip"][0]
            if (
                st["last_day_reset"] != today_str
                and is_in_window(now_t, vip_start, cfg["waitlist"][1])
            ):
                st["slots"] = {}
                st["waitlist"] = []
                st["slots_msg_id"] = None
                st["last_day_reset"] = today_str
                await sync_slot_board(guild, hour)

            phase = get_active_phase(hour, now_t)
            await set_reg_channel_writable(reg_ch, phase is not None)


async def schedule_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await run_schedule_tick()
        except Exception as e:
            print(f"[ERROR] schedule_loop: {e}")
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
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        safe_name = "".join(c for c in user.name.lower() if c.isalnum() or c in "-_")[:20] or "user"
        channel_name = f"ticket-{safe_name}-{user.id}"

        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing is not None:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}",
                ephemeral=True,
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

        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None
        try:
            ticket_ch = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                reason=f"Scrim ticket opened by {user}",
            )
            await ticket_ch.send(
                f"🎫 {user.mention} opened a ticket. Describe your issue or question and staff will respond shortly."
            )
            await interaction.response.send_message(
                f"Ticket created: {ticket_ch.mention}",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to create ticket channels.",
                ephemeral=True,
            )


@bot.command(name="ticket")
@commands.has_permissions(manage_channels=True)
async def ticket_panel(ctx: commands.Context):
    await ctx.send(
        "**Scrim Support / Ticket**\nNeed help or coordination? Click the button below:",
        view=TicketButtonView(),
    )


@bot.event
async def on_ready():
    global _view_registered, _schedule_task
    if not _view_registered:
        bot.add_view(TicketButtonView())
        _view_registered = True
    for h in SCHEDULES:
        init_hour_state(h)
    if _schedule_task is None or _schedule_task.done():
        _schedule_task = bot.loop.create_task(schedule_loop())
    print(f"[OK] Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if lines and lines[0].lower() == "%register":
        hour = parse_hour_from_reg_channel(message.channel)
        if hour is None:
            await message.add_reaction("❌")
            return

        now_t = datetime.now(TIMEZONE).time()
        phase = get_active_phase(hour, now_t)
        if phase is None:
            await message.add_reaction("❌")
            return

        if len(lines) < 3:
            await message.add_reaction("❌")
            return

        team_name = lines[1]
        team_tag = lines[2]
        manager = message.mentions[0] if message.mentions else message.author

        st = init_hour_state(hour)

        for existing in st["slots"].values():
            if (
                existing["manager_id"] == manager.id
                or existing["team"].lower() == team_name.lower()
            ):
                await message.add_reaction("❌")
                return

        for w in st["waitlist"]:
            if (
                w["manager_id"] == manager.id
                or w["team"].lower() == team_name.lower()
            ):
                await message.add_reaction("❌")
                return

        _, slot_ch, wait_ch, idpw_ch = get_channels_for_hour(message.guild, hour)
        vip = is_vip_member(manager)
        assigned_slot = None

        if phase == "vip":
            if vip:
                for s in [24, 23, 22, 21]:
                    if s not in st["slots"]:
                        assigned_slot = s
                        break
        elif phase == "reg":
            if vip:
                for s in [24, 23, 22, 21] + list(range(3, 21)):
                    if s not in st["slots"]:
                        assigned_slot = s
                        break
            else:
                for s in range(3, 21):
                    if s not in st["slots"]:
                        assigned_slot = s
                        break
        elif phase == "waitlist":
            assigned_slot = None

        if assigned_slot is not None:
            st["slots"][assigned_slot] = {
                "team": team_name,
                "tag": team_tag,
                "manager_id": manager.id,
                "vip": vip,
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
            {
                "team": team_name,
                "tag": team_tag,
                "manager_id": manager.id,
            }
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
    raise RuntimeError("Missing bot token: set DISCORD_TOKEN in Railway Variables or replace TOKEN in bot.py.")

bot.run(TOKEN)


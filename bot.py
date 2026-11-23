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

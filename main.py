import discord
from discord.ext import commands
import os
import re
import json
from datetime import datetime, timedelta, timezone
import asyncio
from flask import Flask
from threading import Thread
import base64
import requests
from discord import SelectOption

GUILD_ID = 1330703193591644180
EVENTS_FILE = "events.json"
STAFF_ROLE_IDS = {1443106123153543309}
NOTIFIER_ROLE_ID = 828406807285202974
PARTICIPANT_ROLE_ID = 1048722332165873844

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

from discord import app_commands

app = Flask(__name__)

scheduled_tasks = {}  # Stores asyncio tasks for events


@app.route('/')
def home():
    print("\U0001F501 Ping received from UptimeRobot (or browser)")
    return "Bot is online!"


def run():
    app.run(host='0.0.0.0', port=8080)


def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()


@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild)
        print(
            f"\u2705 Synced {len(synced)} slash command(s) to guild {GUILD_ID}"
        )
    except Exception as e:
        print(f"\u274C Sync failed: {e}")

    bot.loop.create_task(periodic_event_sync())

    await schedule_upcoming_events()


def staff_only():

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id in STAFF_ROLE_IDS
                   for role in interaction.user.roles)

    return app_commands.check(predicate)


def fetch_github_events():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("\u274C GITHUB_TOKEN not set!")
        return []

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        content = response.json()["content"]
        return json.loads(base64.b64decode(content).decode())
    else:
        print(f"\u274C Failed to fetch events.json: {response.status_code}")
        print("Response:", response.text)
        return []


def commit_github_events(data):
    token = os.getenv("GITHUB_TOKEN")
    branch = "main"
    if not token:
        print("\u274C GITHUB_TOKEN not set!")
        return

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    get_resp = requests.get(url, headers=headers)
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    else:
        print(
            f"\u26A0\uFE0F Couldn't retrieve current file SHA: {get_resp.status_code}"
        )
        print("Response:", get_resp.text)
        sha = None

    content = base64.b64encode(
        json.dumps([{
            **e, "start_time":
            e["start_time"].isoformat()
            if isinstance(e["start_time"], datetime) else e["start_time"]
        } for e in data],
                   indent=4).encode()).decode()

    payload = {
        "message": "Update events",
        "content": content,
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        print("\u2705 events.json updated on GitHub.")
    else:
        print("\u274C Failed to update events.json on GitHub:")
        print("Status:", put_resp.status_code)
        print("Response:", put_resp.text)


def load_events():
    data = fetch_github_events()
    for e in data:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])
    return data


def save_events():
    commit_github_events(events)


events = load_events()


def parse_time_delay(time_str: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd])", time_str.lower())
    if not match:
        raise ValueError(
            "Invalid time format. Use number + s/m/h/d, e.g. 30s, 5m, 48h, 2d."
        )
    value, unit = match.groups()
    value = int(value)
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


async def announce_event(event):
    now = datetime.now(tz=timezone.utc)
    delay = (event["start_time"] - now).total_seconds()
    try:
        if delay > 0:
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        print(f"🛑 Announcement task for '{event['name']}' was cancelled.")
        return  # Stop further execution if cancelled

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"Failed to get guild {GUILD_ID} for event {event['name']}")
        return

    # Use provided channel if available, otherwise default to first available
    channel = guild.get_channel(event.get("channel_id"))
    if channel is None:
        print(
            f"Fallback: no stored channel for event {event['name']}, using first available."
        )
        channel = next((ch for ch in guild.text_channels
                        if ch.permissions_for(guild.me).send_messages), None)

    if channel is None:
        print(f"No suitable channel found for event {event['name']}")
        return

    role_mention = "<@&1382621918024433697>"
    await channel.send(role_mention,
                       allowed_mentions=discord.AllowedMentions(roles=True))

    embed = discord.Embed(title=event["name"].upper(),
                          description=event["info"],
                          color=discord.Color.blue())

    if event.get("reward1"):
        embed.add_field(name="\U0001F381 1st Place Reward",
                        value=event["reward1"],
                        inline=False)
    if event.get("reward2"):
        embed.add_field(name="\U0001F381 2nd Place Reward",
                        value=event["reward2"],
                        inline=False)
    if event.get("reward3"):
        embed.add_field(name="\U0001F381 3rd Place Reward",
                        value=event["reward3"],
                        inline=False)
    if event.get("participation_reward"):
        embed.add_field(name="\U0001F381 Participation Reward",
                        value=event["participation_reward"],
                        inline=False)

    embed.add_field(
        name="",
        value=
        "To participate in this event, tick the reaction below and you will be given the Participant role.",
        inline=False)

    embed.set_footer(text=f"Created by {event['creator']['name']}")

    message = await channel.send(embed=embed)
    await message.add_reaction("\u2705")

    event["started"] = True
    save_events()
    print(f"Event announced: {event['name']}")


async def schedule_upcoming_events():
    global scheduled_tasks
    global events
    now = datetime.now(tz=timezone.utc)

    for idx, event in enumerate(events):
        if isinstance(event["start_time"], str):
            event["start_time"] = datetime.fromisoformat(event["start_time"])

        if not event.get("started", False) and event["start_time"] > now:
            # Cancel any previously scheduled task for this index
            existing_task = scheduled_tasks.get(idx)
            if existing_task and not existing_task.done():
                existing_task.cancel()
                print(f"❌ Cancelled previous task for event {event['name']}")

            # Schedule new task
            task = asyncio.create_task(announce_event(event))
            scheduled_tasks[idx] = task
            print(f"✅ Scheduled announcement for {event['name']}")


async def periodic_event_sync():
    await bot.wait_until_ready()
    while not bot.is_closed():
        print("🔄 Checking GitHub for event updates...")
        new_events = load_events()

        # Convert start_time strings to datetime
        for e in new_events:
            if isinstance(e["start_time"], str):
                e["start_time"] = datetime.fromisoformat(e["start_time"])

        # Overwrite in-memory event list
        global events
        events = new_events

        # Reschedule announcements
        await schedule_upcoming_events()

        await asyncio.sleep(30)


@bot.tree.command(
    name="rolemessage",
    description=
    "Give the Participant role to users who ticked the first reaction",
    guild=discord.Object(id=GUILD_ID))
@staff_only()
@app_commands.describe(
    message_id="The ID of the message to scan for reactions")
async def rolemessage(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)

    channel = interaction.channel
    try:
        message = await channel.fetch_message(int(message_id))
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch message: {e}",
                                        ephemeral=True)
        return

    role = discord.utils.get(interaction.guild.roles, name="Participant")
    if not role:
        await interaction.followup.send("❌ 'Participant' role not found.",
                                        ephemeral=True)
        return

    if not message.reactions:
        await interaction.followup.send("❌ No reactions found on the message.",
                                        ephemeral=True)
        return

    first_reaction = message.reactions[0]
    assigned_users = set()

    async for user in first_reaction.users():
        if user.bot:
            continue
        member = interaction.guild.get_member(user.id)
        if member and role not in member.roles:
            await member.add_roles(role)
            assigned_users.add(user.id)

    await interaction.followup.send(
        f"✅ Assigned 'Participant' role to {len(assigned_users)} users who reacted to the message's first reaction.",
        ephemeral=True)


@bot.tree.command(name="editevent",
                  description="Edit one of your scheduled events",
                  guild=discord.Object(id=GUILD_ID))
@staff_only()
async def editevent(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    now = datetime.now(tz=timezone.utc)
    user_id = interaction.user.id
    current_events = load_events()

    def parse_start(event):
        if isinstance(event.get("start_time"), str):
            try:
                return datetime.fromisoformat(event["start_time"])
            except ValueError:
                return None
        return event.get("start_time")

    # Get user's editable upcoming events
    editable = [(i, e) for i, e in enumerate(current_events)
                if not e.get("started") and e["creator"]["id"] == user_id and (
                    start := parse_start(e)) and start > now]

    if not editable:
        await interaction.followup.send("You have no upcoming events to edit.",
                                        ephemeral=True)
        return

    class EditSelector(discord.ui.Select):

        def __init__(self):
            options = [
                discord.SelectOption(label=e["name"], value=str(i))
                for i, (i_orig, e) in enumerate(editable)
            ]
            super().__init__(placeholder="Choose an event to edit",
                             options=options)

        async def callback(self, select_interaction):
            selected = int(self.values[0])
            original_index, event = editable[selected]

            class EditModal(discord.ui.Modal, title="Edit Event"):
                name = discord.ui.TextInput(label="Event Name",
                                            default=event["name"])
                info = discord.ui.TextInput(label="Description",
                                            default=event["info"],
                                            style=discord.TextStyle.paragraph)
                delay = discord.ui.TextInput(
                    label="Time until event (e.g. 5m, 1h)",
                    required=False,
                    placeholder="Leave blank to keep")
                participation = discord.ui.TextInput(
                    label="Participation Reward",
                    default=event.get("participation_reward", ""),
                    required=False)

                async def on_submit(self,
                                    modal_interaction: discord.Interaction):
                    event["name"] = self.name.value
                    event["info"] = self.info.value
                    event["participation_reward"] = self.participation.value

                    if self.delay.value.strip():
                        try:
                            seconds = parse_time_delay(
                                self.delay.value.strip())
                            new_start = datetime.now(
                                tz=timezone.utc) + timedelta(seconds=seconds)
                            event["start_time"] = new_start
                        except ValueError:
                            await modal_interaction.response.send_message(
                                "❌ Invalid delay format!", ephemeral=True)
                            return

                    # Update the event in the main list and save
                    current_events[original_index] = event
                    global events
                    events = current_events
                    save_events()
                    await schedule_upcoming_events()
                    await modal_interaction.response.send_message(
                        f"✅ Event **{event['name']}** has been updated!",
                        ephemeral=True)

            await select_interaction.response.send_modal(EditModal())

    class EditView(discord.ui.View):

        def __init__(self):
            super().__init__(timeout=60)
            self.add_item(EditSelector())

    await interaction.followup.send("Select the event to edit:",
                                    view=EditView(),
                                    ephemeral=True)


@bot.tree.command(name="deleteevent",
                  description="Mark one of your upcoming events as deleted",
                  guild=discord.Object(id=GUILD_ID))
@staff_only()
async def deleteevent(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id
    now = datetime.now(timezone.utc)
    current_events = load_events()

    global events
    events = current_events

    def parse_start(event):
        if isinstance(event.get("start_time"), str):
            try:
                return datetime.fromisoformat(event["start_time"])
            except ValueError:
                return None
        return event.get("start_time")

    deletable = [
        (i, e) for i, e in enumerate(events)
        if not e.get("started") and e["creator"]["id"] == user_id and (
            start := parse_start(e)) and start > now
    ]

    if not deletable:
        await interaction.followup.send(
            "You have no upcoming events to delete.", ephemeral=True)
        return

    class DeleteSelector(discord.ui.Select):

        def __init__(self):
            options = [
                discord.SelectOption(label=e["name"], value=str(i))
                for i, (i_orig, e) in enumerate(deletable)
            ]
            super().__init__(placeholder="Choose an event to delete",
                             options=options)

        async def callback(self, select_interaction):
            selected = int(self.values[0])
            original_index, event = deletable[selected]

            class ConfirmDeleteModal(discord.ui.Modal,
                                     title="Confirm Delete Event"):
                confirm = discord.ui.TextInput(label="Type DELETE to confirm",
                                               placeholder="DELETE",
                                               required=True)

                async def on_submit(self,
                                    modal_interaction: discord.Interaction):
                    if self.confirm.value.strip().upper() != "DELETE":
                        await modal_interaction.response.send_message(
                            "❌ Deletion cancelled.", ephemeral=True)
                        return

                    # Mark as deleted (past timestamp)
                    event["start_time"] = "2000-01-01T00:00:00+00:00"
                    current_events[original_index] = event

                    global events
                    events = current_events
                    save_events()

                    # Cancel existing scheduled task
                    task = scheduled_tasks.get(original_index)
                    if task and not task.done():
                        task.cancel()
                        print(
                            f"🛑 Cancelled announcement for deleted event '{event['name']}'"
                        )

                    # Reload and reschedule
                    events = load_events()
                    await schedule_upcoming_events()

                    await modal_interaction.response.send_message(
                        f"🗑️ Event **{event['name']}** has been marked as deleted.",
                        ephemeral=True)

            await select_interaction.response.send_modal(ConfirmDeleteModal())

    class DeleteView(discord.ui.View):

        def __init__(self):
            super().__init__(timeout=60)
            self.add_item(DeleteSelector())

    await interaction.followup.send("Select the event to delete:",
                                    view=DeleteView(),
                                    ephemeral=True)


@bot.tree.command(name="createevent",
                  description="Create an event",
                  guild=discord.Object(id=GUILD_ID))
@staff_only()
async def createevent(interaction: discord.Interaction,
                      name: str,
                      info: str,
                      delay: str = "0s",
                      reward1: str = "",
                      reward2: str = "",
                      reward3: str = "",
                      participation_reward: str = ""):
    try:
        delay_seconds = parse_time_delay(delay)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid time format. Use number + s/m/h/d, e.g. 30s, 5m, 48h, 2d.",
            ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)  # ✅ Always defer quickly

    start_time = datetime.now(tz=timezone.utc) + timedelta(
        seconds=delay_seconds)
    creator = {"id": interaction.user.id, "name": str(interaction.user)}

    event_data = {
        "name": name,
        "info": info,
        "reward1": reward1,
        "reward2": reward2,
        "reward3": reward3,
        "participation_reward": participation_reward,
        "start_time": start_time,
        "started": False,
        "creator": creator,
        "channel_id": interaction.channel_id
    }

    events.append(event_data)
    save_events()

    # Schedule with tracking
    idx = len(events) - 1  # Index of the new event
    existing_task = scheduled_tasks.get(idx)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    scheduled_tasks[idx] = asyncio.create_task(announce_event(event_data))

    if delay_seconds > 0:
        await interaction.followup.send(
            f"⏳ Event '{name}' will be posted in {delay_seconds} seconds.")
    else:
        await interaction.followup.send(f"✅ Event '{name}' has been posted!")


@bot.tree.command(
    name="end",
    description="Sends the event info and clears the Participant role",
    guild=discord.Object(id=GUILD_ID))
@staff_only()
async def end(interaction: discord.Interaction):
    now = datetime.now(tz=timezone.utc)
    current_events = load_events()

    await interaction.response.send_message(
        "Ending event and removing Participant role.", ephemeral=True)

    # Remove "Participant" role from everyone who has it
    guild = interaction.guild
    participant_role = discord.utils.get(guild.roles, name="Participant")
    if participant_role:
        for member in guild.members:
            if participant_role in member.roles:
                try:
                    await member.remove_roles(participant_role,
                                              reason="Event ended")
                    print(
                        f"Removed Participant role from {member.display_name}")
                except Exception as e:
                    print(
                        f"Failed to remove role from {member.display_name}: {e}"
                    )
    else:
        print("Participant role not found.")

    # Prepare and send the embed
    for e in current_events:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])

    upcoming = [
        e for e in current_events if e["start_time"] > now and not e["started"]
    ]

    description_text = (
        "This channel is temporarily closed until an event is being held. It will reopen once the event starts.\n"
        "If you have any questions about upcoming events, feel free to ping the host, DM them, or ask in ⁠https://discord.com/channels/457619956687831050/666452996967628821\n\n"
    )

    if upcoming:
        description_text += "🗓️ **Current Upcoming Events:**"
    else:
        description_text += "🚫 **There are currently no upcoming events scheduled via the bot.**"

    embed = discord.Embed(title="🎉 Event Information",
                          description=description_text,
                          color=discord.Color.orange())

    for e in upcoming:
        embed.add_field(
            name=e["name"],
            value=
            f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>\n",
            inline=False)

    embed.add_field(
        name="",
        value=
        f"Keep an eye out for future events in here or ⁠https://discord.com/channels/457619956687831050/1349087527557922988! 👀",
        inline=False)

    try:
        await interaction.channel.send(embed=embed)
    except discord.InteractionResponded:
        pass
        

@bot.tree.command(
    name="eventping",
    description="Ping the Event Notifier role",
    guild=discord.Object(id=GUILD_ID)
)
@staff_only()
async def eventping(interaction: discord.Interaction):
    role = interaction.guild.get_role(NOTIFIER_ROLE_ID)
    if role is None:
        await interaction.response.send_message(
            "❌ Event Notifier role not found. Check the ID.", ephemeral=True
        )
        return

    # Send an ephemeral confirmation to staff first
    await interaction.response.send_message("✅ Event ping sent!", ephemeral=True)

    # Then send the actual ping to the channel
    await interaction.followup.send(f"{role.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

@bot.tree.command(
    name="participantping",
    description="Ping the Participant role",
    guild=discord.Object(id=GUILD_ID)
)
@staff_only()
async def participantping(interaction: discord.Interaction):
    role = interaction.guild.get_role(PARTICIPANT_ROLE_ID)
    if role is None:
        await interaction.response.send_message(
            "❌ Participant role not found. Check the ID.", ephemeral=True
        )
        return

    # Confirm to staff privately
    await interaction.response.send_message("✅ Participant ping sent!", ephemeral=True)

    # Send actual ping to the channel
    await interaction.followup.send(
        f"{role.mention}",
        allowed_mentions=discord.AllowedMentions(roles=True)
    )


@bot.tree.command(
    name="eventroler",
    description="Send an Event Roler message to the current channel",
    guild=discord.Object(id=GUILD_ID))
@staff_only()
async def eventroler(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # Send immediately
    channel = interaction.channel

    embed = discord.Embed(title="AFFIRM YES",
                          description="This is for the event above",
                          color=discord.Color.blue())

    embed.add_field(
        name="",
        value=
        "To participate in this event, tick the reaction below and you will be given the Participant role.",
        inline=False)

    embed.set_footer(text=f"Created by {interaction.user}")

    message = await channel.send(embed=embed)
    await message.add_reaction("\u2705")

    await interaction.followup.send(
        f"✅ 'AFFIRM YES' prompt sent to this channel.", ephemeral=True)

@bot.tree.command(name="events",
                  description="Shows all upcoming events",
                  guild=discord.Object(id=GUILD_ID))
async def events_command(interaction: discord.Interaction):
    now = datetime.now(tz=timezone.utc)
    current_events = load_events()
    for e in current_events:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])

    upcoming = [
        e for e in current_events if e["start_time"] > now and not e["started"]
    ]

    if not upcoming:
        await interaction.response.send_message(
            "There are no upcoming events planned.")
        return

    embed = discord.Embed(title="📅 Upcoming Events",
                          color=discord.Color.green())
    for e in upcoming:
        embed.add_field(
            name=e["name"],
            value=
            f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>",
            inline=False)
    await interaction.response.send_message(embed=embed)


@staff_only()
async def editevent(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id
    now = datetime.now(tz=timezone.utc)

    def parse_start_time(event):
        start = event.get("start_time")
        if isinstance(start, str):
            try:
                return datetime.fromisoformat(start)
            except ValueError:
                return None
        elif isinstance(start, datetime):
            return start
        return None


async def bot_reacted_to_message(message):
    for reaction in message.reactions:
        if reaction.emoji == "✅":
            async for user in reaction.users():
                if user.id == bot.user.id:
                    return True
    return False


@bot.event
async def on_raw_reaction_add(payload):
    if payload.emoji.name != "✅" or payload.user_id == bot.user.id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    try:
        message = await channel.fetch_message(payload.message_id)
    except:
        return

    if not await bot_reacted_to_message(message):
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member:
        return
    role = discord.utils.get(guild.roles, name="Participant")
    if role and role not in member.roles:
        await member.add_roles(role)
        print(f"✅ Assigned Participant role to {member.display_name}")


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.emoji.name != "✅":
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = discord.utils.get(guild.roles,
                             name="Participant") if guild else None
    if member and role and role in member.roles:
        await member.remove_roles(role)
        print(f"❎ Removed Participant role from {member.display_name}")


# --- EVENT PLANNER (claim/unclaim) ---
EVENTPLANNER_FILE = "eventplanner.json"
EVENTPLANNER_REPO_URL = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/eventplanner.json"


def fetch_github_planner():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ GITHUB_TOKEN not set for eventplanner!")
        return {}
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(EVENTPLANNER_REPO_URL, headers=headers)
    if resp.status_code == 200:
        content = resp.json()["content"]
        return json.loads(base64.b64decode(content).decode())
    return {}


def commit_github_planner(data):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ GITHUB_TOKEN not set for eventplanner!")
        return
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    get_resp = requests.get(EVENTPLANNER_REPO_URL, headers=headers)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    payload = {
        "message": "Update eventplanner",
        "content": content,
        "branch": "main"
    }
    if sha: payload["sha"] = sha
    put_resp = requests.put(EVENTPLANNER_REPO_URL,
                            headers=headers,
                            json=payload)
    if put_resp.status_code in (200, 201):
        print("✅ eventplanner.json updated on GitHub.")
    else:
        print("❌ Failed to update eventplanner.json:", put_resp.status_code,
              put_resp.text)


def generate_month(year, month):
    import calendar
    weeks = []
    _, days_in_month = calendar.monthrange(year, month)
    start_day = 1

    while start_day <= days_in_month:
        end_day = start_day + 6
        if end_day > days_in_month:
            # Merge remaining days into the last week
            if weeks:
                weeks[-1]["range"] = f"{weeks[-1]['range'].split('-')[0]}-{days_in_month} {calendar.month_name[month]} {year}"
            else:
                # if it's the first week and month < 7 days, just make a week
                weeks.append({
                    "range": f"{start_day}-{days_in_month} {calendar.month_name[month]} {year}",
                    "slots": [None, None]
                })
            break
        weeks.append({
            "range": f"{start_day}-{end_day} {calendar.month_name[month]} {year}",
            "slots": [None, None]
        })
        start_day += 7
    return weeks


from datetime import datetime





def ensure_schedule():
    """Ensure schedule contains full current + next month; old weeks remain in file but are pruned only if month is before current."""
    schedule = fetch_github_planner()
    now = datetime.now()
    year, month = now.year, now.month
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Add months if missing
    for y, m in [(year, month), (next_year, next_month)]:
        key = f"{y}-{m}"
        if key not in schedule:
            schedule[key] = generate_month(y, m)

    # Prune only completely old months (not individual weeks)
    for key in list(schedule.keys()):
        y, m = map(int, key.split("-"))
        if y < year or (y == year and m < month):
            del schedule[key]

    return schedule

def filter_future_weeks(weeks, month_key):
    """Return only weeks whose end date is today or later, but keep original week numbers."""
    now = datetime.now()
    filtered = []
    for idx, week in enumerate(weeks):
        week_number = idx + 1
        # Determine week end date from range string
        end_day = int(week["range"].split("-")[1].split()[0])
        year, month = map(int, month_key.split("-"))
        week_end_date = datetime(year=year, month=month, day=end_day)
        if week_end_date >= now:
            filtered.append((week_number, week))
    return filtered

# --- EVENTPLANNER COMMAND ---
@bot.tree.command(
    name="eventplanner",
    description="Show the event schedule for this and next month",
    guild=discord.Object(id=GUILD_ID)
)
async def eventplanner(interaction: discord.Interaction):
    schedule = ensure_schedule()
    embed = discord.Embed(title="📅 Event Planner", color=discord.Color.blue())

    for month_key, weeks in schedule.items():
        future_weeks = filter_future_weeks(weeks, month_key)
        if not future_weeks:
            continue  # skip months with no upcoming weeks

        embed.add_field(name=f"**{month_key}**", value="\u200b", inline=False)
        for week_number, week in future_weeks:
            claims = ", ".join(u if u else "[Open]" for u in week["slots"])
            embed.add_field(
                name=f"Week {week_number} ({week['range']})",
                value=f"Slots: {claims}",
                inline=False
            )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- CLAIM COMMAND ---
@bot.tree.command(
    name="claim",
    description="Claim a week slot",
    guild=discord.Object(id=GUILD_ID)
)
@staff_only()
@app_commands.describe(
    month_index="1 = first month, 2 = second month",
    week="Week number in the month (original number)"
)
async def claim(interaction: discord.Interaction, month_index: int, week: int):
    schedule = ensure_schedule()
    months = list(schedule.keys())

    if month_index < 1 or month_index > len(months):
        return await interaction.response.send_message("❌ Invalid month index. Choose 1 or 2.", ephemeral=True)

    month_key = months[month_index - 1]
    weeks = schedule[month_key]
    future_weeks = dict(filter_future_weeks(weeks, month_key))

    if week not in future_weeks:
        return await interaction.response.send_message("❌ This week has already passed or is invalid.", ephemeral=True)

    slots = future_weeks[week]["slots"]
    if interaction.user.display_name in slots:
        return await interaction.response.send_message("❌ You already claimed this slot.", ephemeral=True)

    try:
        idx = slots.index(None)
        slots[idx] = interaction.user.display_name
        commit_github_planner(schedule)
        await interaction.response.send_message(f"✅ You claimed week {week} of {month_key}.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("❌ Both slots are already filled.", ephemeral=True)

# --- UNCLAIM COMMAND ---
@bot.tree.command(
    name="unclaim",
    description="Unclaim your week slot",
    guild=discord.Object(id=GUILD_ID)
)
@staff_only()
@app_commands.describe(
    month_index="1 = first month, 2 = second month",
    week="Week number in the month (original number)"
)
async def unclaim(interaction: discord.Interaction, month_index: int, week: int):
    schedule = ensure_schedule()
    months = list(schedule.keys())

    if month_index < 1 or month_index > len(months):
        return await interaction.response.send_message("❌ Invalid month index. Choose 1 or 2.", ephemeral=True)

    month_key = months[month_index - 1]
    weeks = schedule[month_key]
    future_weeks = dict(filter_future_weeks(weeks, month_key))

    if week not in future_weeks:
        return await interaction.response.send_message("❌ This week has already passed or is invalid.", ephemeral=True)

    slots = future_weeks[week]["slots"]
    if interaction.user.display_name in slots:
        slots[slots.index(interaction.user.display_name)] = None
        commit_github_planner(schedule)
        await interaction.response.send_message(f"✅ You unclaimed week {week} of {month_key}.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ You didn't claim this week.", ephemeral=True)



keep_alive()
print("🔁 Starting bot...")
bot.run(os.getenv("DISCORD_TOKEN"))

port = int(os.environ.get(
    "PORT", 8080))  # Use Render's assigned port or default to 8080
app.run(host='0.0.0.0', port=port)

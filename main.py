import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os
import json
from datetime import datetime, date, timedelta

# ================== CONFIG / TOKEN ==================

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

if token is None:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")

intents = discord.Intents.default()
# For slash commands you don't need message_content, but it's fine either way.
bot = commands.Bot(command_prefix="!", intents=intents)  # prefix unused, we use slash


# ================== REMINDER STORAGE ==================

REMINDERS_FILE = "reminders.json"

# Reminder structure:
# {
#   "id": int,
#   "guild_id": int | null,
#   "channel_id": int,
#   "author_id": int,
#   "name": str,
#   "date": "YYYY-MM-DD",
#   "repeat": "once" | "yearly",
#   "days_before": int
# }

def load_reminders():
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_reminders(reminders_list):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders_list, f, indent=4)


reminders = load_reminders()

# Compute next_id and assign IDs to any old reminders missing "id"
if reminders:
    current_max_id = 0
    for r in reminders:
        if "id" not in r:
            current_max_id += 1
            r["id"] = current_max_id
        else:
            current_max_id = max(current_max_id, r["id"])
    next_id = current_max_id + 1
    save_reminders(reminders)
else:
    next_id = 1


# ================== EVENTS ==================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Sync slash commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} app command(s).")
    except Exception as e:
        print(f"Error syncing commands: {e}")

    # Start reminder loop
    if not reminder_loop.is_running():
        reminder_loop.start()


# ================== SLASH COMMANDS ==================

@bot.tree.command(name="reminder", description="Add a calendar reminder")
@app_commands.describe(
    date_str="Date in format YYYY-MM-DD",
    repeat="Should this repeat once or every year?",
    days_before="How many days early to remind you (0 for none)",
    name="Name of the event",
)
@app_commands.choices(
    repeat=[
        app_commands.Choice(name="Once", value="once"),
        app_commands.Choice(name="Yearly", value="yearly"),
    ]
)
async def slash_reminder(
    interaction: discord.Interaction,
    date_str: str,
    repeat: app_commands.Choice[str],
    days_before: int,
    name: str,
):
    """
    /reminder date_str repeat days_before name
    Example:
    /reminder 2026-03-15 yearly 7 Moms birthday
    /reminder 2025-12-31 once 0 New Year party
    """
    global next_id

    # Parse date
    try:
        event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await interaction.response.send_message(
            "❌ Use date format: `YYYY-MM-DD` (e.g. 2025-12-31).",
            ephemeral=True,
        )
        return

    if days_before < 0:
        await interaction.response.send_message(
            "❌ `days_before` must be 0 or a positive number.",
            ephemeral=True,
        )
        return

    repeat_value = repeat.value  # "once" or "yearly"

    reminder = {
        "id": next_id,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "author_id": interaction.user.id,
        "name": name,
        "date": event_date.isoformat(),
        "repeat": repeat_value,
        "days_before": int(days_before),
    }

    reminders.append(reminder)
    save_reminders(reminders)

    await interaction.response.send_message(
        f"✅ Reminder saved (ID: `{next_id}`):\n"
        f"- **Name:** {name}\n"
        f"- **Date:** {event_date.isoformat()}\n"
        f"- **Repeat:** {repeat_value}\n"
        f"- **Early reminder:** {days_before} day(s) before"
    )

    next_id += 1


@bot.tree.command(name="myreminders", description="List your reminders")
async def slash_myreminders(interaction: discord.Interaction):
    user_id = interaction.user.id
    user_rems = [r for r in reminders if r.get("author_id") == user_id]

    if not user_rems:
        await interaction.response.send_message(
            "📭 You have no reminders saved.", ephemeral=True
        )
        return

    # Build a text list
    lines = []
    for r in sorted(user_rems, key=lambda x: (x.get("date", ""), x.get("id", 0))):
        lines.append(
            f"ID: `{r['id']}` | **{r['name']}** | Date: `{r['date']}` | "
            f"Repeat: `{r['repeat']}` | Early: `{r['days_before']}` day(s) | "
            f"Channel: <#{r['channel_id']}>"
        )

    msg = "📝 **Your reminders:**\n" + "\n".join(lines)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="delreminder", description="Delete one of your reminders by ID")
@app_commands.describe(reminder_id="The ID of the reminder to delete")
async def slash_delreminder(interaction: discord.Interaction, reminder_id: int):
    user_id = interaction.user.id
    found = None

    for r in reminders:
        if r.get("id") == reminder_id:
            found = r
            break

    if found is None:
        await interaction.response.send_message(
            f"❌ No reminder found with ID `{reminder_id}`.", ephemeral=True
        )
        return

    if found.get("author_id") != user_id:
        await interaction.response.send_message(
            "❌ You can only delete your **own** reminders.", ephemeral=True
        )
        return

    reminders.remove(found)
    save_reminders(reminders)

    await interaction.response.send_message(
        f"🗑️ Reminder ID `{reminder_id}` (**{found['name']}**) deleted.",
        ephemeral=True,
    )


# ================== REMINDER LOOP ==================

@tasks.loop(minutes=1)
async def reminder_loop():
    today = date.today()
    to_remove = []

    for rem in reminders:
        try:
            event_date = datetime.strptime(rem["date"], "%Y-%m-%d").date()
        except ValueError:
            continue

        days_before = rem.get("days_before", 0)
        repeat = rem.get("repeat", "once")
        name = rem.get("name", "Unnamed event")

        channel = bot.get_channel(rem["channel_id"])
        if channel is None:
            continue

        author_id = rem.get("author_id")
        mention = f"<@{author_id}>" if author_id else ""

        # ONCE reminders
        if repeat == "once":
            # early reminder
            if days_before > 0:
                early_day = event_date - timedelta(days=days_before)
                if today == early_day:
                    await channel.send(
                        f"⏰ {mention} Early reminder "
                        f"({days_before} days ahead): **{name}** on **{event_date.isoformat()}**"
                    )

            # day-of reminder
            if today == event_date:
                await channel.send(
                    f"🎉 {mention} Today is **{name}**! (**{event_date.isoformat()}**)"
                )
                to_remove.append(rem)

        # YEARLY reminders
        elif repeat == "yearly":
            this_year_date = date(today.year, event_date.month, event_date.day)

            # early reminder
            if days_before > 0:
                early_day = this_year_date - timedelta(days=days_before)
                if today == early_day:
                    await channel.send(
                        f"⏰ {mention} Early reminder "
                        f"({days_before} days ahead): **{name}** on **{this_year_date.isoformat()}**"
                    )

            # day-of reminder
            if today == this_year_date:
                await channel.send(
                    f"🎉 {mention} Today is **{name}**! (**{this_year_date.isoformat()}**)"
                )

    # Delete one-time reminders that already triggered
    if to_remove:
        for rem in to_remove:
            if rem in reminders:
                reminders.remove(rem)
        save_reminders(reminders)


@reminder_loop.before_loop
async def before_reminder_loop():
    await bot.wait_until_ready()


# ================== RUN BOT ==================

bot.run(token)

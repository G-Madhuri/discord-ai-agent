"""Discord bot process.

Command surface is registered and wired to the backend via thin HTTP client;
the bot itself holds no domain logic and no database connection.

The bot does not start without DISCORD_BOT_TOKEN. Reads from settings.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.core.config import settings
from app.core.logging import get_logger
from app.discord.api_client import BackendClient
from app.discord.command_handler import handle_command

logger = get_logger(__name__)

MAX_DISCORD_MESSAGE = 1900


def _context(interaction: discord.Interaction) -> dict:
    """Guild/channel/user identity travels with every backend call.

    `discord_guild_id` is the isolation key: the backend scopes everything to it.
    """
    return {
        "discord_guild_id": str(interaction.guild_id) if interaction.guild_id else "unknown-guild",
        "guild_name": interaction.guild.name if interaction.guild else None,
        "discord_channel_id": str(interaction.channel_id) if interaction.channel_id else None,
        "requested_by_discord_id": str(interaction.user.id),
    }


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_DISCORD_MESSAGE else text[: MAX_DISCORD_MESSAGE - 1] + "…"


class AssignmentBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # slash commands only
        super().__init__(command_prefix="!", intents=intents)
        self.backend = BackendClient()

    async def setup_hook(self) -> None:
        await self.backend.start()
        register_commands(self)

        guild_id = settings.discord_guild_id or settings.discord_dev_guild_id
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("slash commands synced to dev guild %s", guild_id)
        else:
            logger.warning("DISCORD_GUILD_ID is empty; falling back to global sync (propagation can take up to an hour)")
            await self.tree.sync()

    async def on_ready(self) -> None:
        healthy = await self.backend.health()
        user_name = str(self.user) if self.user else "unknown"
        user_id = str(self.user.id) if self.user else "unknown"
        logger.info(
            "Bot connected as %s (id=%s) | guilds=%s | backend=%s",
            user_name,
            user_id,
            len(self.guilds),
            "reachable" if healthy else "UNREACHABLE",
        )

    async def close(self) -> None:
        await self.backend.close()
        await super().close()


def register_commands(bot: AssignmentBot) -> None:
    project_group = app_commands.Group(name="project", description="Project management commands")
    member_group = app_commands.Group(name="member", description="Team member management commands")
    task_group = app_commands.Group(name="task", description="Task management commands")
    assignment_group = app_commands.Group(name="assignment", description="Assignment history commands")

    async def _exec(
        interaction: discord.Interaction, full_command: str, args: dict
    ) -> None:
        await interaction.response.defer(thinking=True)
        ctx = _context(interaction)
        msg, is_err = await handle_command(bot.backend, full_command, args, ctx)
        if is_err:
            await interaction.followup.send(_truncate(msg), ephemeral=True)
        else:
            await interaction.followup.send(_truncate(msg), ephemeral=False)

    # --- /assign ---
    @bot.tree.command(name="assign", description="Assign an existing task to the best-suited member")
    @app_commands.describe(
        task_id="The existing task key, e.g. TASK-101",
        project="Project key, if the server has more than one project",
        reassign="Replace an existing assignee",
    )
    async def assign(
        interaction: discord.Interaction,
        task_id: str,
        project: str | None = None,
        reassign: bool = False,
    ) -> None:
        await _exec(
            interaction,
            "assign",
            {"task_id": task_id, "project": project, "reassign": reassign},
        )

    # --- /assignment history ---
    @assignment_group.command(name="history", description="Show recent assignment decisions")
    @app_commands.describe(task_id="Limit history to a specific task key")
    async def assignment_history(
        interaction: discord.Interaction, task_id: str | None = None
    ) -> None:
        await _exec(interaction, "assignment history", {"task_id": task_id})

    @bot.tree.command(name="assignment_history", description="Show recent assignment decisions")
    @app_commands.describe(task_id="Limit history to a specific task key")
    async def assignment_history_top(
        interaction: discord.Interaction, task_id: str | None = None
    ) -> None:
        await _exec(interaction, "assignment history", {"task_id": task_id})

    # --- /project ---
    @project_group.command(name="create", description="Create a new project in this server")
    @app_commands.describe(name="Project name", description="Optional project description", key="Optional project key")
    async def project_create(
        interaction: discord.Interaction, name: str, description: str | None = None, key: str | None = None
    ) -> None:
        await _exec(interaction, "project create", {"name": name, "description": description, "key": key})

    @project_group.command(name="info", description="Show project status and team members")
    @app_commands.describe(name="Project key, e.g. DEMO")
    async def project_info(interaction: discord.Interaction, name: str) -> None:
        await _exec(interaction, "project info", {"name": name})

    @project_group.command(name="knowledge", description="List indexed project knowledge documents")
    @app_commands.describe(project="Project key, e.g. DEMO")
    async def project_knowledge(interaction: discord.Interaction, project: str) -> None:
        await _exec(interaction, "project knowledge", {"project": project})

    @project_group.command(name="member_add", description="Add a server member explicitly to a project")
    @app_commands.describe(project="Project key, e.g. DEMO", user="Discord member", role="Optional role on project")
    async def project_member_add(
        interaction: discord.Interaction, project: str, user: discord.Member, role: str | None = None
    ) -> None:
        await _exec(interaction, "project member add", {"project": project, "user": user.id, "role": role})

    # --- /task ---
    @task_group.command(name="create", description="Create a task in a project")
    @app_commands.describe(
        project="Project key, e.g. DEMO",
        title="Task title",
        description="Optional task description",
        skills="Comma-separated required skills",
    )
    async def task_create(
        interaction: discord.Interaction,
        project: str,
        title: str,
        description: str | None = None,
        skills: str | None = None,
    ) -> None:
        await _exec(
            interaction,
            "task create",
            {"project": project, "title": title, "description": description, "skills": skills},
        )

    @task_group.command(name="list", description="List tasks in a project")
    @app_commands.describe(project="Optional project key to filter tasks")
    async def task_list(interaction: discord.Interaction, project: str | None = None) -> None:
        await _exec(interaction, "task list", {"project": project})

    # --- /member ---
    @member_group.command(name="add", description="Add or update a team member")
    @app_commands.describe(user="Discord member", role="Member role, e.g. Backend Engineer", skills="Comma-separated skills")
    async def member_add(
        interaction: discord.Interaction,
        user: discord.Member,
        role: str | None = None,
        skills: str | None = None,
    ) -> None:
        await _exec(
            interaction,
            "member add",
            {
                "user": user.id,
                "display_name": user.display_name,
                "username": user.name,
                "role": role,
                "skills": skills,
            },
        )

    @member_group.command(name="profile", description="Show a member's profile")
    @app_commands.describe(user="Discord member")
    async def member_profile(interaction: discord.Interaction, user: discord.Member) -> None:
        await _exec(interaction, "member profile", {"user": user.id})

    @member_group.command(name="skills", description="Set a member's skills")
    @app_commands.describe(user="Discord member", skills="Comma-separated skills, e.g. Python, React")
    async def member_skills(
        interaction: discord.Interaction, user: discord.Member, skills: str
    ) -> None:
        await _exec(interaction, "member skills", {"user": user.id, "skills": skills})

    bot.tree.add_command(project_group)
    bot.tree.add_command(task_group)
    bot.tree.add_command(member_group)
    bot.tree.add_command(assignment_group)


def run() -> None:
    if settings.discord_bot_token is None:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    AssignmentBot().run(settings.discord_bot_token.get_secret_value())

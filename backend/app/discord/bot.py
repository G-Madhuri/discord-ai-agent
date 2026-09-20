"""Discord bot skeleton.

Command surface is registered and wired to the backend; the bot itself holds no
domain logic. `/assign` is the important one — it forwards the task key and the
guild context to the backend, which runs the assignment workflow and returns
the message to post.

The bot does not start without DISCORD_BOT_TOKEN. Nothing here is hardcoded.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.core.config import settings
from app.core.logging import get_logger
from app.discord.api_client import BackendClient, BackendError

logger = get_logger(__name__)

MAX_DISCORD_MESSAGE = 1900


def _context(interaction: discord.Interaction) -> dict:
    """Guild/channel/user identity travels with every backend call.

    `discord_guild_id` is the isolation key: the backend scopes everything to it.
    """
    return {
        "discord_guild_id": str(interaction.guild_id),
        "guild_name": interaction.guild.name if interaction.guild else None,
        "discord_channel_id": str(interaction.channel_id) if interaction.channel_id else None,
        "requested_by_discord_id": str(interaction.user.id),
    }


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_DISCORD_MESSAGE else text[: MAX_DISCORD_MESSAGE - 1] + "…"


class AssignmentBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # slash commands only for now
        super().__init__(command_prefix="!", intents=intents)
        self.backend = BackendClient()

    async def setup_hook(self) -> None:
        await self.backend.start()
        register_commands(self)
        if settings.discord_dev_guild_id:
            guild = discord.Object(id=int(settings.discord_dev_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("slash commands synced to dev guild %s", settings.discord_dev_guild_id)
        else:
            await self.tree.sync()
            logger.info("slash commands synced globally (propagation can take up to an hour)")

    async def on_ready(self) -> None:
        healthy = await self.backend.health()
        logger.info(
            "logged in as %s | guilds=%s | backend=%s",
            self.user,
            len(self.guilds),
            "reachable" if healthy else "UNREACHABLE",
        )

    async def close(self) -> None:
        await self.backend.close()
        await super().close()


def register_commands(bot: AssignmentBot) -> None:
    project_group = app_commands.Group(name="project", description="Project commands")
    member_group = app_commands.Group(name="member", description="Team member commands")
    task_group = app_commands.Group(name="task", description="Task commands")

    async def _reply(interaction: discord.Interaction, text: str) -> None:
        await interaction.followup.send(_truncate(text))

    # ---------------------------------------------------------------- assign
    @bot.tree.command(
        name="assign", description="Assign an existing task to the best-suited member"
    )
    @app_commands.describe(
        task_key="The existing task key, e.g. TASK-101",
        project_key="Project key, if the server has more than one project",
        reassign="Replace an existing assignee",
    )
    async def assign(
        interaction: discord.Interaction,
        task_key: str,
        project_key: str | None = None,
        reassign: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            result = await bot.backend.post(
                "/assignments/assign",
                json={
                    "context": _context(interaction),
                    "task_key": task_key,
                    "project_key": project_key,
                    "reassign": reassign,
                },
            )
            await _reply(interaction, result.get("message", "No response from the agent."))
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @bot.tree.command(name="assignment_history", description="Show recent assignment decisions")
    @app_commands.describe(task_key="Limit to one task")
    async def assignment_history(
        interaction: discord.Interaction, task_key: str | None = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            entries = await bot.backend.post(
                "/assignments/history",
                json=_context(interaction),
                params={"task_key": task_key} if task_key else None,
            )
            if not entries:
                await _reply(interaction, "No assignment history yet.")
                return
            lines = [
                f"• {e['task_key']} → {e.get('member_display_name') or 'unassigned'} "
                f"({e['event']}) {e['created_at'][:19]}"
                for e in entries
            ]
            await _reply(interaction, "**Assignment history**\n" + "\n".join(lines))
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    # --------------------------------------------------------------- project
    @project_group.command(name="create", description="Create a project in this server")
    async def project_create(
        interaction: discord.Interaction, key: str, name: str, description: str | None = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            project = await bot.backend.post(
                "/projects",
                json={
                    "context": _context(interaction),
                    "key": key,
                    "name": name,
                    "description": description,
                },
            )
            await _reply(interaction, f"✅ Project {project['key']} — {project['name']} created.")
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @project_group.command(name="info", description="Show project status and team")
    async def project_info(interaction: discord.Interaction, project_key: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            info = await bot.backend.post(
                f"/projects/{project_key}/info", json=_context(interaction)
            )
            project = info["project"]
            members = ", ".join(m["display_name"] for m in info["members"]) or "none"
            await _reply(
                interaction,
                f"**{project['key']} — {project['name']}**\n"
                f"Status: {project['status']} | Plan: {project['plan_status']}\n"
                f"Tasks: {info['open_task_count']} open of {info['task_count']}\n"
                f"Documents: {info['document_count']}\n"
                f"Team: {members}",
            )
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @project_group.command(name="knowledge", description="List indexed project knowledge")
    async def project_knowledge(interaction: discord.Interaction, project_key: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            docs = await bot.backend.post(
                f"/projects/{project_key}/knowledge/list", json=_context(interaction)
            )
            if not docs:
                await _reply(interaction, "No project knowledge indexed yet.")
                return
            lines = [
                f"• {d['title']} ({d['source_type']}, {d['chunk_count']} chunks)" for d in docs
            ]
            await _reply(interaction, "**Project knowledge**\n" + "\n".join(lines))
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    # ---------------------------------------------------------------- member
    @member_group.command(name="add", description="Add or update a team member")
    @app_commands.describe(skills="Comma-separated, e.g. React, JavaScript, Frontend")
    async def member_add(
        interaction: discord.Interaction,
        user: discord.Member,
        role: str | None = None,
        skills: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        skill_list = [{"name": s.strip()} for s in (skills or "").split(",") if s.strip()]
        try:
            member = await bot.backend.post(
                "/members",
                json={
                    "context": _context(interaction),
                    "discord_user_id": str(user.id),
                    "username": user.name,
                    "display_name": user.display_name,
                    "role": role,
                    "skills": skill_list,
                },
            )
            names = ", ".join(s["name"] for s in member["skills"]) or "none recorded"
            await _reply(interaction, f"✅ {member['display_name']} saved. Skills: {names}")
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @member_group.command(name="profile", description="Show a member's profile")
    async def member_profile(interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(thinking=True)
        try:
            member = await bot.backend.post(
                f"/members/{user.id}/profile", json=_context(interaction)
            )
            skills = ", ".join(f"{s['name']} ({s['proficiency']}/5)" for s in member["skills"])
            await _reply(
                interaction,
                f"**{member['display_name']}**\n"
                f"Role: {member['role'] or 'not set'} | Availability: {member['availability']}\n"
                f"Skills: {skills or 'none recorded'}",
            )
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @member_group.command(name="skills", description="Set a member's skills")
    async def member_skills(
        interaction: discord.Interaction, user: discord.Member, skills: str, replace: bool = False
    ) -> None:
        await interaction.response.defer(thinking=True)
        payload = {
            "skills": [{"name": s.strip()} for s in skills.split(",") if s.strip()],
            "replace": replace,
        }
        try:
            member = await bot.backend.post(f"/members/{user.id}/skills", json=payload, params=None)
            names = ", ".join(s["name"] for s in member["skills"])
            await _reply(interaction, f"✅ Skills for {member['display_name']}: {names}")
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    # ------------------------------------------------------------------ task
    @task_group.command(name="create", description="Create a task in a project")
    async def task_create(
        interaction: discord.Interaction,
        project_key: str,
        key: str,
        title: str,
        skills: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            task = await bot.backend.post(
                "/tasks",
                json={
                    "context": _context(interaction),
                    "project_key": project_key,
                    "key": key,
                    "title": title,
                    "required_skills": [
                        {"name": s.strip()} for s in (skills or "").split(",") if s.strip()
                    ],
                },
            )
            await _reply(interaction, f"✅ {task['key']} — {task['title']} created.")
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    @task_group.command(name="list", description="List a project's tasks")
    async def task_list(interaction: discord.Interaction, project_key: str | None = None) -> None:
        await interaction.response.defer(thinking=True)
        try:
            tasks = await bot.backend.post(
                "/tasks/list",
                json=_context(interaction),
                params={"project_key": project_key} if project_key else None,
            )
            if not tasks:
                await _reply(interaction, "No tasks yet.")
                return
            lines = [
                f"• `{t['key']}` {t['title']} — {t['status']}"
                + (f" → {t['assignee_display_name']}" if t.get("assignee_display_name") else "")
                for t in tasks
            ]
            await _reply(interaction, "**Tasks**\n" + "\n".join(lines))
        except BackendError as exc:
            await _reply(interaction, f"⚠️ {exc.message}")

    bot.tree.add_command(project_group)
    bot.tree.add_command(member_group)
    bot.tree.add_command(task_group)


def run() -> None:
    if settings.discord_bot_token is None:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    AssignmentBot().run(settings.discord_bot_token.get_secret_value())

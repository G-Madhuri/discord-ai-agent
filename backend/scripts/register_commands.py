"""Register slash commands to Discord API (Global and Guild).

Sends HTTP PUT to:
- https://discord.com/api/v10/applications/{APP_ID}/commands (Global)
- https://discord.com/api/v10/applications/{APP_ID}/guilds/{GUILD_ID}/commands (Guild)
"""

from __future__ import annotations

import os
import sys
import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath("backend"))

load_dotenv()

from app.core.config import settings

COMMANDS = [
    {
        "name": "assign",
        "description": "Assign an existing task to the best-suited member",
        "options": [
            {
                "name": "task_id",
                "description": "The existing task key, e.g. TASK-101",
                "type": 3,  # STRING
                "required": True,
            },
            {
                "name": "project",
                "description": "Project key",
                "type": 3,  # STRING
                "required": False,
            },
            {
                "name": "reassign",
                "description": "Replace an existing assignee",
                "type": 5,  # BOOLEAN
                "required": False,
            },
        ],
    },
    {
        "name": "assignment",
        "description": "Assignment history commands",
        "options": [
            {
                "name": "history",
                "description": "Show recent assignment decisions",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "task_id",
                        "description": "Limit history to a specific task key",
                        "type": 3,  # STRING
                        "required": False,
                    }
                ],
            }
        ],
    },
    {
        "name": "project",
        "description": "Project management commands",
        "options": [
            {
                "name": "create",
                "description": "Create a new project using agentic planning",
                "type": 1,  # SUB_COMMAND
                "options": [],  # No options -> triggers Discord Modal directly
            },
            {
                "name": "add-members",
                "description": "Add team members to an existing project",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "project",
                        "description": "Project key, e.g. PROJ",
                        "type": 3,
                        "required": True,
                    }
                ],
            },
            {
                "name": "info",
                "description": "Show project status and team members",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "name",
                        "description": "Project key, e.g. DEMO",
                        "type": 3,
                        "required": True,
                    }
                ],
            },
            {
                "name": "knowledge",
                "description": "List indexed project knowledge documents",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "project",
                        "description": "Project key, e.g. DEMO",
                        "type": 3,
                        "required": True,
                    }
                ],
            },
            {
                "name": "member",
                "description": "Manage project members",
                "type": 2,  # SUB_COMMAND_GROUP
                "options": [
                    {
                        "name": "add",
                        "description": "Add a server member explicitly to a project",
                        "type": 1,  # SUB_COMMAND
                        "options": [
                            {
                                "name": "project",
                                "description": "Project key, e.g. DEMO",
                                "type": 3,
                                "required": True,
                            },
                            {
                                "name": "user",
                                "description": "Discord member",
                                "type": 6,  # USER
                                "required": True,
                            },
                            {
                                "name": "role",
                                "description": "Optional role on project",
                                "type": 3,
                                "required": False,
                            },
                        ],
                    }
                ],
            },
        ],
    },
    {
        "name": "assign-project",
        "description": "Run assignment on all unassigned tasks in a project",
        "options": [
            {
                "name": "project",
                "description": "Project key, e.g. PROJ",
                "type": 3,  # STRING
                "required": True,
            }
        ],
    },
    {
        "name": "task",
        "description": "Task management commands",
        "options": [
            {
                "name": "create",
                "description": "Create a task in a project",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "project",
                        "description": "Project key, e.g. DEMO",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "title",
                        "description": "Task title",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "description",
                        "description": "Optional task description",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "skills",
                        "description": "Comma-separated required skills",
                        "type": 3,
                        "required": False,
                    },
                ],
            },
            {
                "name": "list",
                "description": "List tasks in a project",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "project",
                        "description": "Optional project key to filter tasks",
                        "type": 3,
                        "required": False,
                    }
                ],
            },
        ],
    },
    {
        "name": "member",
        "description": "Team member management commands",
        "options": [
            {
                "name": "add",
                "description": "Add or update a team member",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "user",
                        "description": "Discord member",
                        "type": 6,  # USER
                        "required": True,
                    },
                    {
                        "name": "role",
                        "description": "Member role, e.g. Backend Engineer",
                        "type": 3,
                        "required": False,
                    },
                    {
                        "name": "skills",
                        "description": "Comma-separated skills",
                        "type": 3,
                        "required": False,
                    },
                ],
            },
            {
                "name": "profile",
                "description": "Show a member's profile",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "user",
                        "description": "Discord member",
                        "type": 6,  # USER
                        "required": True,
                    }
                ],
            },
            {
                "name": "skills",
                "description": "Set a member's skills",
                "type": 1,  # SUB_COMMAND
                "options": [
                    {
                        "name": "user",
                        "description": "Discord member",
                        "type": 6,  # USER
                        "required": True,
                    },
                    {
                        "name": "skills",
                        "description": "Comma-separated skills, e.g. Python, React",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
        ],
    },
]


def register_commands() -> None:
    token = settings.discord_bot_token.get_secret_value() if settings.discord_bot_token else None
    app_id = settings.discord_application_id
    guild_id = settings.discord_guild_id or settings.discord_dev_guild_id

    if not token or not app_id:
        print(f"Error: Missing credentials. token={bool(token)}, app_id={app_id}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    # 1. Register Global Commands
    global_url = f"https://discord.com/api/v10/applications/{app_id}/commands"
    print(f"--- Attempting Global Command Registration ({len(COMMANDS)} top-level structures) ---")
    resp_global = httpx.put(global_url, headers=headers, json=COMMANDS, timeout=10.0)
    print(f"Global HTTP Status: {resp_global.status_code}")
    print(f"Global Response: {resp_global.text}")

    # 2. Register Guild Commands (if guild_id present)
    if guild_id:
        guild_url = f"https://discord.com/api/v10/applications/{app_id}/guilds/{guild_id}/commands"
        print(f"\n--- Attempting Guild Command Registration for Guild {guild_id} ---")
        resp_guild = httpx.put(guild_url, headers=headers, json=COMMANDS, timeout=10.0)
        print(f"Guild HTTP Status: {resp_guild.status_code}")
        print(f"Guild Response: {resp_guild.text}")

    if resp_global.status_code in (200, 201):
        data = resp_global.json()
        print(f"\n[SUCCESS] Successfully registered {len(data)} global commands!")
        for cmd in data:
            print(f"  * /{cmd['name']}")
    elif guild_id and resp_guild.status_code in (200, 201):
        data = resp_guild.json()
        print(f"\n[SUCCESS] Successfully registered {len(data)} guild commands!")
        for cmd in data:
            print(f"  * /{cmd['name']}")
    else:
        print("\n[ERROR] Command registration failed. See responses above.")
        sys.exit(1)


if __name__ == "__main__":
    register_commands()

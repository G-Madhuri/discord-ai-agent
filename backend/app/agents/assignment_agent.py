"""Google ADK agent definition.

The ADK and Vertex SDKs are imported lazily: the API, the deterministic
assignment path and the test suite all run without Google credentials. Only
AGENT_MODE=llm pulls the model in.
"""

from __future__ import annotations

import os
from typing import Any

from app.agents.prompts import ASSIGNMENT_AGENT_INSTRUCTION
from app.core.config import settings
from app.core.logging import get_logger
from app.tools.context import ToolContext
from app.tools.toolset import build_toolset

logger = get_logger(__name__)

AGENT_NAME = "assignment_agent"


def configure_vertex_env() -> None:
    """ADK reads Vertex configuration from the environment."""
    os.environ.setdefault(
        "GOOGLE_GENAI_USE_VERTEXAI", "TRUE" if settings.google_genai_use_vertexai else "FALSE"
    )
    if settings.google_cloud_project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)


def build_agent(ctx: ToolContext) -> Any:
    """Create an LlmAgent whose tools are bound to one Discord server."""
    from google.adk.agents import LlmAgent
    from google.adk.tools import FunctionTool

    configure_vertex_env()
    if not settings.google_cloud_project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is required for AGENT_MODE=llm. "
            "Use AGENT_MODE=deterministic to run without Vertex AI."
        )

    tools = [FunctionTool(func=fn) for fn in build_toolset(ctx).values()]
    return LlmAgent(
        name=AGENT_NAME,
        model=settings.gemini_model,
        description="Assigns existing tasks to the best-suited team member of a Discord server.",
        instruction=ASSIGNMENT_AGENT_INSTRUCTION,
        tools=tools,
    )

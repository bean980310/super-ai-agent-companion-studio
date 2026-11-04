# Tool-calling bridge for agent skills.
"""Expose skills to the model through two local tools.

The catalog (name + description) is injected into the system prompt, while the
full skill body is fetched on demand. This mirrors the progressive-disclosure
pattern used by agent skill implementations.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ai_companion_gateway import ToolResult, ToolSpec

from .models import SkillError
from .registry import SkillRegistry, get_skill_registry

SKILL_SERVER_NAME = "skills"
LOAD_SKILL_TOOL = "load_skill"
READ_SKILL_FILE_TOOL = "read_skill_file"


def _skill_names(registry: SkillRegistry, names: Optional[Iterable[str]]) -> List[str]:
    return [skill.name for skill in registry.enabled_skills(names)]


def build_skill_tool_specs(
    registry: Optional[SkillRegistry] = None,
    names: Optional[Iterable[str]] = None,
) -> List[ToolSpec]:
    """Build tool specs for the currently enabled skills.

    Returns an empty list when no skill is enabled so callers can fall back to
    normal generation without exposing unusable tools.
    """
    registry = registry or get_skill_registry()
    available = _skill_names(registry, names)
    if not available:
        return []

    return [
        ToolSpec(
            name=LOAD_SKILL_TOOL,
            server=SKILL_SERVER_NAME,
            description=(
                "Load the full instructions for an agent skill. Call this before answering when the "
                "user's request matches one of the skills listed in the system prompt."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name to load.",
                        "enum": available,
                    }
                },
                "required": ["name"],
            },
        ),
        ToolSpec(
            name=READ_SKILL_FILE_TOOL,
            server=SKILL_SERVER_NAME,
            description=(
                "Read a supporting file bundled with an agent skill. Use the paths returned by "
                "load_skill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name.",
                        "enum": available,
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the skill folder, e.g. 'references/guide.md'.",
                    },
                },
                "required": ["name", "path"],
            },
        ),
    ]


def _short_tool_name(tool_name: str) -> str:
    return tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name


def build_skill_executor(
    registry: Optional[SkillRegistry] = None,
    names: Optional[Iterable[str]] = None,
):
    """Build an executor for the skill tools.

    Only skills that were exposed to the model are callable, so a model cannot
    reach a disabled skill by guessing its name.
    """
    registry = registry or get_skill_registry()
    allowed = set(_skill_names(registry, names))

    def executor(tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        short_name = _short_tool_name(tool_name)
        arguments = arguments or {}
        skill_name = str(arguments.get("name", "")).strip()

        if skill_name not in allowed:
            return ToolResult(success=False, error=f"Unknown or disabled skill: {skill_name or '(missing name)'}")

        try:
            if short_name == LOAD_SKILL_TOOL:
                return ToolResult(success=True, content=registry.load_skill_text(skill_name))
            if short_name == READ_SKILL_FILE_TOOL:
                relative_path = str(arguments.get("path", "")).strip()
                return ToolResult(
                    success=True,
                    content=registry.read_skill_file(skill_name, relative_path),
                )
        except SkillError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:  # defensive: a tool must never crash the loop
            return ToolResult(success=False, error=f"Skill tool failed: {exc}")

        return ToolResult(success=False, error=f"Unknown skill tool: {tool_name}")

    return executor


def build_skill_prompt(
    registry: Optional[SkillRegistry] = None,
    names: Optional[Iterable[str]] = None,
) -> str:
    """Return the skill catalog injected into the system prompt."""
    registry = registry or get_skill_registry()
    return registry.build_catalog(names)

# Agent Skills
"""Agent skill discovery, management, and tool-calling support.

Skills are folders containing a ``SKILL.md`` file. They are discovered from
``~/.ai-companion/skills``, ``~/.agents/skills``, the project ``skills/``
folder, and the project ``.agents/skills`` folder (or
``AI_COMPANION_SKILLS_DIRS``). Only the catalog is sent to the model up front;
the full instructions are loaded on demand.
"""

from .models import (
    Skill,
    SkillError,
    character_matches,
    load_skill_file,
    normalize_character_slug,
    normalize_characters,
    parse_skill_document,
    skill_matches_character,
    slugify_skill_name,
)
from .registry import SkillRegistry, default_agent_roots, default_skill_roots, get_skill_registry
from .tools import (
    LOAD_SKILL_TOOL,
    READ_SKILL_FILE_TOOL,
    SKILL_SERVER_NAME,
    build_skill_executor,
    build_skill_prompt,
    build_skill_tool_specs,
)

__all__ = [
    "Skill",
    "SkillError",
    "SkillRegistry",
    "LOAD_SKILL_TOOL",
    "READ_SKILL_FILE_TOOL",
    "SKILL_SERVER_NAME",
    "build_skill_executor",
    "build_skill_prompt",
    "build_skill_tool_specs",
    "character_matches",
    "default_agent_roots",
    "default_skill_roots",
    "get_skill_registry",
    "load_skill_file",
    "normalize_character_slug",
    "normalize_characters",
    "parse_skill_document",
    "skill_matches_character",
    "slugify_skill_name",
]

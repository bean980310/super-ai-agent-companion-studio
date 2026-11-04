# Agent Skill data model and SKILL.md parsing.
"""Agent Skills are folders containing a ``SKILL.md`` file.

The file uses a small YAML frontmatter block followed by Markdown
instructions, following the same convention used by Claude/Codex agent
skills::

    ---
    name: my-skill
    description: One or two sentences describing when to use this skill.
    ---

    # Instructions
    ...

Only the catalog (name + description) is sent to the model up front; the
full body is loaded on demand through the ``load_skill`` tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple

try:  # PyYAML is a transitive dependency, but keep a graceful fallback.
    import yaml
except Exception:  # pragma: no cover - only hit in minimal installs
    yaml = None  # type: ignore[assignment]


class SkillError(Exception):
    """Raised when a skill cannot be parsed or accessed."""


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)
_NAME_RE = re.compile(r"[^a-z0-9_-]+")
_VALID_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_MAX_DESCRIPTION_CHARS = 1024


@dataclass
class Skill:
    """A discovered agent skill."""

    name: str
    description: str
    path: Path
    body: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @property
    def directory(self) -> Path:
        return self.path.parent

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "source": self.source,
            "metadata": dict(self.metadata),
        }


def slugify_skill_name(value: str) -> str:
    """Normalize an arbitrary string into a valid skill name."""
    slug = _NAME_RE.sub("-", str(value or "").strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64].strip("-_")


def is_valid_skill_name(value: str) -> bool:
    return bool(_VALID_NAME_RE.fullmatch(str(value or "")))


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_meta, body = match.group(1), match.group(2)
    if not raw_meta.strip():
        return {}, body

    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_meta)
        except Exception as exc:  # malformed YAML should not hide the skill
            raise SkillError(f"Invalid SKILL.md frontmatter: {exc}") from exc
        if parsed is None:
            return {}, body
        if not isinstance(parsed, dict):
            raise SkillError("SKILL.md frontmatter must be a YAML mapping")
        return dict(parsed), body

    # Minimal fallback parser: ``key: value`` pairs only.
    meta: Dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("'\"")
    return meta, body


def _first_paragraph(body: str) -> str:
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if lines:
                break
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def parse_skill_document(text: str, fallback_name: str) -> Tuple[str, str, str, Dict[str, Any]]:
    """Parse a SKILL.md document.

    Returns ``(name, description, body, metadata)``.
    """
    metadata, body = _parse_frontmatter(text or "")

    raw_name = metadata.get("name") or fallback_name
    name = slugify_skill_name(str(raw_name)) or slugify_skill_name(fallback_name)
    if not name:
        raise SkillError("Skill name is missing or invalid")

    description = str(metadata.get("description") or "").strip()
    if not description:
        description = _first_paragraph(body)
    if not description:
        description = f"Agent skill '{name}'"
    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[:_MAX_DESCRIPTION_CHARS].rstrip() + "..."

    return name, description, body.strip(), metadata


def load_skill_file(path: Path | str, source: str = "") -> Skill:
    """Load a skill from a ``SKILL.md`` path."""
    path = Path(path)
    if not path.is_file():
        raise SkillError(f"SKILL.md not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError(f"Failed to read {path}: {exc}") from exc

    name, description, body, metadata = parse_skill_document(text, fallback_name=path.parent.name)
    return Skill(
        name=name,
        description=description,
        path=path,
        body=body,
        metadata=metadata,
        source=source,
    )

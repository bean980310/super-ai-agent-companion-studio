# Agent Skill registry.
"""Discover, persist, and read agent skills from the local filesystem."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from ai_companion_core import logger

from src.common.apppath import APPDATA_PATH

from .models import (
    Skill,
    SkillError,
    character_matches,
    is_valid_skill_name,
    load_skill_file,
    normalize_character_slug,
    normalize_characters,
    slugify_skill_name,
)

SKILL_FILE_NAME = "SKILL.md"
DEFAULT_MAX_FILE_BYTES = 512 * 1024
_TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".env",
    ".gitignore",
}

# Directories that are never scanned for skills. Hidden directories are also
# skipped; skill folders themselves use plain lowercase names.
_SKIP_DIR_NAMES = {
    "node_modules",
    "__pycache__",
    "venv",
    "env",
    "dist",
    "build",
    "site-packages",
    "target",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
}


def iter_skill_files(root: Path):
    """Yield ``SKILL.md`` paths under ``root``, recursively and top-down.

    A directory containing ``SKILL.md`` is treated as a skill and is not
    descended into, so bundled reference material cannot register as a nested
    skill. Hidden directories and common dependency/build folders are skipped.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in _SKIP_DIR_NAMES
        )
        if SKILL_FILE_NAME in filenames:
            yield Path(dirpath) / SKILL_FILE_NAME
            dirnames[:] = []


def _dedupe_paths(roots: Sequence[Path]) -> List[Path]:
    seen = set()
    unique: List[Path] = []
    for root in roots:
        key = str(root.expanduser().resolve()) if root.exists() else str(root.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def default_skill_roots() -> List[Path]:
    """Return the default skill search roots, highest priority first."""
    roots: List[Path] = []

    env_dirs = os.environ.get("AI_COMPANION_SKILLS_DIRS", "")
    for part in env_dirs.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())

    roots.append(APPDATA_PATH / "skills")
    roots.append(Path.home() / ".agents" / "skills")
    roots.append(Path.cwd() / "skills")
    roots.append(Path.cwd() / ".agents" / "skills")

    return _dedupe_paths(roots)


def default_agent_roots() -> List[Path]:
    """Return roots that may contain ``<character>/skills/<skill>/SKILL.md``.

    Each root is scanned for character profile folders. Supported layouts::

        ~/.ai-companion/agents/<character>/skills/<skill>/SKILL.md
        ~/.agents/<character>/skills/<skill>/SKILL.md
        <project>/agents/<character>/skills/<skill>/SKILL.md
    """
    roots: List[Path] = []

    env_dirs = os.environ.get("AI_COMPANION_AGENTS_DIRS", "")
    for part in env_dirs.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())

    roots.append(APPDATA_PATH / "agents")
    roots.append(Path.home() / ".agents")
    roots.append(Path.cwd() / "agents")

    return _dedupe_paths(roots)


class SkillRegistry:
    """Filesystem-backed registry of agent skills.

    Discovery is lazy: the first access recursively scans all roots.
    ``refresh()`` forces a rescan. Enable/disable state is persisted to
    ``skills.json`` next to the application data directory, while the skill
    files themselves stay in ``~/.ai-companion/skills`` (user),
    ``~/.agents/skills`` (shared agent skills), or a project ``skills/``
    directory.

    Character-only skills may also live under
    ``<agent-root>/<character>/skills/<skill>/SKILL.md``. Agent roots default
    to the standard locations for the process-wide registry; when ``roots`` is
    supplied explicitly (for example in tests), pass ``agent_roots`` as well to
    opt in.
    """

    def __init__(
        self,
        roots: Optional[Sequence[Path | str]] = None,
        config_path: Optional[Path | str] = None,
        user_root: Optional[Path | str] = None,
        agent_roots: Optional[Sequence[Path | str]] = None,
    ) -> None:
        self.user_root = Path(user_root).expanduser() if user_root else APPDATA_PATH / "skills"
        configured_roots = [Path(r).expanduser() for r in roots] if roots is not None else default_skill_roots()
        # The user skill directory always wins when two skills share a name.
        configured_roots = [r for r in configured_roots if not self._same_path(r, self.user_root)]
        configured_roots.insert(0, self.user_root)
        self.roots: List[Path] = configured_roots

        if agent_roots is None:
            agent_roots = default_agent_roots() if roots is None else []
        self.agent_roots: List[Path] = _dedupe_paths([Path(r).expanduser() for r in agent_roots])

        self.config_path = Path(config_path).expanduser() if config_path else APPDATA_PATH / "skills.json"

        self._skills: Dict[str, Skill] = {}
        self._disabled: set[str] = set()
        self._character_overrides: Dict[str, List[str]] = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def _same_path(a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return a == b

    def _source_label(self, root: Path) -> str:
        if self._same_path(root, self.user_root):
            return "user"
        try:
            rel = root.resolve().relative_to(Path.cwd().resolve())
            return str(rel) or "."
        except (OSError, ValueError):
            pass
        try:
            rel = root.resolve().relative_to(Path.home().resolve())
            return f"~/{rel.as_posix()}"
        except (OSError, ValueError):
            return str(root)

    def _resolve_character_name(self, folder_name: str) -> str:
        """Map an ``agents/<folder>`` name to a display name when possible."""
        try:
            from src.common.character_info import characters
        except Exception:
            return folder_name

        if folder_name in characters:
            return folder_name

        slug = normalize_character_slug(folder_name)
        if slug:
            for display_name in characters:
                if normalize_character_slug(display_name) == slug:
                    return display_name
        return folder_name

    def _load_agent_skills(self, skills: Dict[str, Skill]) -> None:
        """Load ``<agent-root>/<character>/skills/**/SKILL.md`` entries."""
        for agent_root in self.agent_roots:
            agent_root = Path(agent_root).expanduser()
            if not agent_root.is_dir():
                continue

            root_label = self._source_label(agent_root)
            for character_dir in sorted(agent_root.iterdir(), key=lambda path: path.name.lower()):
                if not character_dir.is_dir():
                    continue
                if character_dir.name.startswith(".") or character_dir.name in _SKIP_DIR_NAMES:
                    continue

                skills_dir = character_dir / "skills"
                if not skills_dir.is_dir():
                    continue

                character = self._resolve_character_name(character_dir.name)
                label = f"{root_label}/{character_dir.name}"
                for skill_file in iter_skill_files(skills_dir):
                    try:
                        skill = load_skill_file(skill_file, source=label)
                    except SkillError as exc:
                        logger.warning(f"Skipping invalid skill at {skill_file}: {exc}")
                        continue

                    if character not in skill.characters:
                        skill.characters.insert(0, character)

                    if skill.name in skills:
                        logger.warning(
                            f"Duplicate skill '{skill.name}' at {skill_file}; keeping {skills[skill.name].path}"
                        )
                        continue
                    skills[skill.name] = skill

    def refresh(self) -> List[Skill]:
        """Rescan all roots recursively and return the discovered skills."""
        with self._lock:
            self._load_config()
            skills: Dict[str, Skill] = {}
            for root in self.roots:
                root = Path(root).expanduser()
                if not root.is_dir():
                    continue
                label = self._source_label(root)
                for skill_file in iter_skill_files(root):
                    try:
                        skill = load_skill_file(skill_file, source=label)
                    except SkillError as exc:
                        logger.warning(f"Skipping invalid skill at {skill_file}: {exc}")
                        continue
                    if skill.name in skills:
                        logger.warning(
                            f"Duplicate skill '{skill.name}' at {skill_file}; keeping {skills[skill.name].path}"
                        )
                        continue
                    skills[skill.name] = skill

            self._load_agent_skills(skills)

            self._skills = skills
            self._loaded = True
            return self._sorted_skills()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def _sorted_skills(self) -> List[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]

    # ------------------------------------------------------------------
    # Enable / disable persistence
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        disabled: set[str] = set()
        character_overrides: Dict[str, List[str]] = {}
        try:
            if self.config_path.is_file():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    raw = data.get("disabled", [])
                    if isinstance(raw, list):
                        disabled = {str(name) for name in raw}
                    raw_characters = data.get("characters", {})
                    if isinstance(raw_characters, dict):
                        for name, value in raw_characters.items():
                            character_overrides[str(name)] = normalize_characters(value)
        except Exception as exc:
            logger.warning(f"Failed to read skill config {self.config_path}: {exc}")
        self._disabled = disabled
        self._character_overrides = character_overrides

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "disabled": sorted(self._disabled),
                "characters": {
                    name: list(characters)
                    for name, characters in sorted(self._character_overrides.items())
                },
            }
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to write skill config {self.config_path}: {exc}")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_skills(self, include_disabled: bool = True) -> List[Skill]:
        with self._lock:
            self._ensure_loaded()
            skills = self._sorted_skills()
            if include_disabled:
                return skills
            return [skill for skill in skills if skill.name not in self._disabled]

    def enabled_skills(
        self,
        names: Optional[Iterable[str]] = None,
        character: Optional[str] = None,
    ) -> List[Skill]:
        """Return enabled skills, optionally filtered by name and character.

        An empty/``None`` ``names`` selection means "all enabled skills".
        A global skill (no character scope) is available to every character;
        a character-scoped skill is only returned when ``character`` matches.
        """
        with self._lock:
            self._ensure_loaded()
            skills = [s for s in self._sorted_skills() if s.name not in self._disabled]
            if character:
                skills = [s for s in skills if self.skill_matches_character(s.name, character)]
            selected = {str(n) for n in names} if names else set()
            if selected:
                skills = [s for s in skills if s.name in selected]
            return skills

    def skill_characters(self, name: str) -> List[str]:
        """Return the effective character scope for a skill.

        An app-managed override (set in the Skills settings tab) takes
        precedence over the ``characters`` frontmatter. An empty list means the
        skill is global.
        """
        with self._lock:
            self._ensure_loaded()
            name = str(name)
            if name in self._character_overrides:
                return list(self._character_overrides[name])
            skill = self._skills.get(name)
            return list(skill.characters) if skill else []

    def set_skill_characters(self, name: str, characters: Optional[Iterable[str]]) -> List[str]:
        """Persist a character scope override for a skill."""
        with self._lock:
            self._ensure_loaded()
            name = str(name)
            if name not in self._skills:
                raise SkillError(f"Unknown skill: {name}")
            normalized = normalize_characters(characters)
            self._character_overrides[name] = normalized
            self._save_config()
            return list(normalized)

    def skill_matches_character(self, name: str, character: Optional[str]) -> bool:
        """Whether the skill is available while chatting as ``character``."""
        if not character:
            return True
        characters = self.skill_characters(name)
        if not characters:
            return True
        return any(character_matches(value, character) for value in characters)

    def get(self, name: str) -> Optional[Skill]:
        with self._lock:
            self._ensure_loaded()
            return self._skills.get(str(name))

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return str(name) not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._ensure_loaded()
            if name not in self._skills:
                raise SkillError(f"Unknown skill: {name}")
            if enabled:
                self._disabled.discard(name)
            else:
                self._disabled.add(name)
            self._save_config()

    def set_enabled_skills(self, names: Iterable[str]) -> None:
        """Mark exactly ``names`` as enabled; every other discovered skill is disabled."""
        with self._lock:
            self._ensure_loaded()
            enabled = {str(n) for n in names}
            self._disabled = set(self._skills) - enabled
            self._save_config()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def list_skill_files(self, name: str, limit: int = 200) -> List[str]:
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill: {name}")
        files: List[str] = []
        try:
            for path in sorted(skill.directory.rglob("*")):
                if not path.is_file() or path == skill.path:
                    continue
                try:
                    files.append(path.relative_to(skill.directory).as_posix())
                except ValueError:
                    continue
                if len(files) >= limit:
                    break
        except OSError as exc:
            raise SkillError(f"Failed to list files for skill '{name}': {exc}") from exc
        return files

    def load_skill_text(self, name: str) -> str:
        """Return the full instructions for a skill plus its supporting files."""
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill: {name}")

        parts = [f"# Skill: {skill.name}", "", skill.description, ""]
        if skill.body:
            parts.extend([skill.body, ""])
        else:
            parts.extend(["(This skill has no instructions.)", ""])

        files = self.list_skill_files(skill.name)
        if files:
            parts.append("## Supporting files")
            parts.append("Use `read_skill_file` with the skill name and one of these paths:")
            parts.extend(f"- {path}" for path in files)
        else:
            parts.append("## Supporting files\n(none)")
        return "\n".join(parts).strip()

    def _resolve_member(self, skill: Skill, relative_path: str) -> Path:
        raw = str(relative_path or "").strip().replace("\\", "/")
        if not raw:
            raise SkillError("A relative file path is required")
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            raise SkillError("Skill file paths must stay inside the skill directory")

        base = skill.directory.resolve()
        target = (skill.directory / rel).resolve()
        if target != base and not target.is_relative_to(base):
            raise SkillError("Skill file paths must stay inside the skill directory")
        return target

    def read_skill_file(self, name: str, relative_path: str, max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> str:
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill: {name}")

        target = self._resolve_member(skill, relative_path)
        if not target.is_file():
            raise SkillError(f"File not found in skill '{name}': {relative_path}")
        if target.suffix.lower() not in _TEXT_SUFFIXES:
            raise SkillError(f"Unsupported file type: {target.suffix or '(none)'}")
        try:
            if target.stat().st_size > max_bytes:
                raise SkillError(f"File is too large to read (>{max_bytes} bytes)")
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SkillError(f"Failed to read {relative_path}: {exc}") from exc

        rel = target.relative_to(skill.directory.resolve()).as_posix()
        return f"# {skill.name}/{rel}\n\n{text}"

    # ------------------------------------------------------------------
    # Catalog / prompt
    # ------------------------------------------------------------------
    def build_catalog(
        self,
        names: Optional[Iterable[str]] = None,
        character: Optional[str] = None,
    ) -> str:
        skills = self.enabled_skills(names, character=character)
        if not skills:
            return ""
        lines = [
            "# Available Agent Skills",
            "Skills are instruction packs for specialized tasks. When a request matches a skill's description, "
            "call the `load_skill` tool with that skill's name before answering. Use `read_skill_file` to read "
            "supporting files referenced by the skill.",
            "",
        ]
        for skill in skills:
            lines.append(f"- **{skill.name}** — {skill.description}")
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Mutations (UI helpers)
    # ------------------------------------------------------------------
    def create_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        characters: Optional[Iterable[str]] = None,
    ) -> Skill:
        """Create a new skill under the user skill root.

        ``characters`` optionally scopes the skill to specific character display
        names. An empty scope makes the skill global.
        """
        slug = slugify_skill_name(name)
        if not is_valid_skill_name(slug):
            raise SkillError("Skill names must use lowercase letters, digits, '-' or '_'")

        destination = self.user_root / slug
        if destination.exists():
            raise SkillError(f"Skill '{slug}' already exists")

        description = str(description or "").strip()
        if not description:
            raise SkillError("A skill description is required")

        body = str(instructions or "").strip() or f"# {slug}\n\nDescribe how to perform this task."
        scope = normalize_characters(characters)

        frontmatter = [
            "---",
            f"name: {slug}",
            f"description: {json.dumps(description, ensure_ascii=False)}",
        ]
        if scope:
            frontmatter.append("characters:")
            frontmatter.extend(f"  - {json.dumps(character, ensure_ascii=False)}" for character in scope)
        frontmatter.extend(["---", ""])

        destination.mkdir(parents=True, exist_ok=False)
        content = "\n".join(frontmatter) + f"\n{body}\n"
        (destination / SKILL_FILE_NAME).write_text(content, encoding="utf-8")

        self.refresh()
        skill = self.get(slug)
        if skill is None:
            raise SkillError(f"Skill '{slug}' was created but could not be loaded")
        return skill

    def import_skill(self, source: Path | str) -> Skill:
        """Copy a skill folder (or a standalone .md file) into the user root."""
        source_path = Path(source).expanduser()
        if not source_path.exists():
            raise SkillError(f"Path does not exist: {source_path}")

        if source_path.is_file():
            if source_path.suffix.lower() != ".md":
                raise SkillError("Only skill folders or .md files can be imported")
            skill = load_skill_file(source_path, source="import")
            staging = source_path
            is_file = True
        elif source_path.is_dir():
            skill_file = source_path / SKILL_FILE_NAME
            if not skill_file.is_file():
                raise SkillError(f"{source_path} does not contain {SKILL_FILE_NAME}")
            skill = load_skill_file(skill_file, source="import")
            staging = source_path
            is_file = False
        else:
            raise SkillError(f"Unsupported skill source: {source_path}")

        destination = self.user_root / skill.name
        if destination.exists():
            raise SkillError(f"Skill '{skill.name}' already exists")

        self.user_root.mkdir(parents=True, exist_ok=True)
        if is_file:
            destination.mkdir(parents=True, exist_ok=False)
            shutil.copy2(staging, destination / SKILL_FILE_NAME)
        else:
            shutil.copytree(staging, destination)

        self.refresh()
        imported = self.get(skill.name)
        if imported is None:
            raise SkillError(f"Skill '{skill.name}' was imported but could not be loaded")
        return imported

    def delete_skill(self, name: str) -> None:
        """Delete a user skill. Bundled/project skills are read-only."""
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill: {name}")

        user_root = self.user_root.resolve()
        try:
            skill_dir = skill.directory.resolve()
        except OSError as exc:
            raise SkillError(f"Failed to resolve skill path: {exc}") from exc
        if skill_dir == user_root or not skill_dir.is_relative_to(user_root):
            raise SkillError("Only skills in the user skill directory can be deleted")

        shutil.rmtree(skill_dir)
        self._disabled.discard(skill.name)
        self._character_overrides.pop(skill.name, None)
        self._save_config()
        self.refresh()


_registry: Optional[SkillRegistry] = None
_registry_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    """Return the process-wide skill registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = SkillRegistry()
    return _registry

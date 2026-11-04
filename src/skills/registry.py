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
    is_valid_skill_name,
    load_skill_file,
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

    # Preserve order while removing duplicates.
    seen = set()
    unique: List[Path] = []
    for root in roots:
        key = str(root.expanduser().resolve()) if root.exists() else str(root.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


class SkillRegistry:
    """Filesystem-backed registry of agent skills.

    Discovery is lazy: the first access recursively scans all roots.
    ``refresh()`` forces a rescan. Enable/disable state is persisted to
    ``skills.json`` next to the application data directory, while the skill
    files themselves stay in ``~/.ai-companion/skills`` (user),
    ``~/.agents/skills`` (shared agent skills), or a project ``skills/``
    directory.
    """

    def __init__(
        self,
        roots: Optional[Sequence[Path | str]] = None,
        config_path: Optional[Path | str] = None,
        user_root: Optional[Path | str] = None,
    ) -> None:
        self.user_root = Path(user_root).expanduser() if user_root else APPDATA_PATH / "skills"
        configured_roots = [Path(r).expanduser() for r in roots] if roots is not None else default_skill_roots()
        # The user skill directory always wins when two skills share a name.
        configured_roots = [r for r in configured_roots if not self._same_path(r, self.user_root)]
        configured_roots.insert(0, self.user_root)
        self.roots: List[Path] = configured_roots
        self.config_path = Path(config_path).expanduser() if config_path else APPDATA_PATH / "skills.json"

        self._skills: Dict[str, Skill] = {}
        self._disabled: set[str] = set()
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
        try:
            if self.config_path.is_file():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                raw = data.get("disabled", []) if isinstance(data, dict) else []
                if isinstance(raw, list):
                    disabled = {str(name) for name in raw}
        except Exception as exc:
            logger.warning(f"Failed to read skill config {self.config_path}: {exc}")
        self._disabled = disabled

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"disabled": sorted(self._disabled)}
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

    def enabled_skills(self, names: Optional[Iterable[str]] = None) -> List[Skill]:
        """Return enabled skills, optionally filtered by ``names``.

        An empty/``None`` selection means "all enabled skills".
        """
        with self._lock:
            self._ensure_loaded()
            skills = [s for s in self._sorted_skills() if s.name not in self._disabled]
            selected = {str(n) for n in names} if names else set()
            if selected:
                skills = [s for s in skills if s.name in selected]
            return skills

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
    def build_catalog(self, names: Optional[Iterable[str]] = None) -> str:
        skills = self.enabled_skills(names)
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
    def create_skill(self, name: str, description: str, instructions: str) -> Skill:
        """Create a new skill under the user skill root."""
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
        destination.mkdir(parents=True, exist_ok=False)
        content = f"---\nname: {slug}\ndescription: {description}\n---\n\n{body}\n"
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

"""Unit tests for agent skill discovery, parsing, and tool-calling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skills.models import (
    SkillError,
    character_matches,
    load_skill_file,
    normalize_characters,
    parse_skill_document,
    slugify_skill_name,
)
from src.skills.registry import SkillRegistry, default_skill_roots
from src.skills.tools import (
    LOAD_SKILL_TOOL,
    READ_SKILL_FILE_TOOL,
    build_skill_executor,
    build_skill_prompt,
    build_skill_tool_specs,
)


def write_skill(
    root: Path,
    name: str,
    description: str = "A test skill.",
    body: str = "# Steps\n\nDo the thing.",
    extra_files: dict[str, str] | None = None,
    characters: list[str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if characters:
        lines.append("characters:")
        lines.extend(f"  - {json.dumps(character, ensure_ascii=False)}" for character in characters)
    lines.extend(["---", "", body, ""])
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    for relative, content in (extra_files or {}).items():
        target = skill_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


@pytest.fixture()
def registry(tmp_path: Path) -> SkillRegistry:
    user_root = tmp_path / "user-skills"
    project_root = tmp_path / "project-skills"
    config_path = tmp_path / "skills.json"
    return SkillRegistry(
        roots=[project_root, user_root],
        user_root=user_root,
        config_path=config_path,
    )


def test_parse_frontmatter():
    text = "---\nname: My Skill!\ndescription: Use for tests.\nmetadata:\n  author: me\n---\n\n# Body\ntext"
    name, description, body, metadata = parse_skill_document(text, fallback_name="fallback")
    assert name == "my-skill"
    assert description == "Use for tests."
    assert body == "# Body\ntext"
    assert metadata["metadata"] == {"author": "me"}


def test_parse_without_frontmatter_uses_fallback_and_first_paragraph():
    name, description, body, metadata = parse_skill_document("# Heading\n\nFirst line.\nSecond line.\n", "fallback-skill")
    assert name == "fallback-skill"
    assert description == "First line. Second line."
    assert body.startswith("# Heading")
    assert metadata == {}


def test_slugify_skill_name():
    assert slugify_skill_name("My Skill!") == "my-skill"
    assert slugify_skill_name("  ") == ""
    assert len(slugify_skill_name("a" * 100)) == 64


def test_default_roots_include_home_agents(monkeypatch, tmp_path: Path):
    extra = tmp_path / "extra-skills"
    monkeypatch.setenv("AI_COMPANION_SKILLS_DIRS", str(extra))

    roots = default_skill_roots()
    assert roots[0] == extra
    assert Path.home() / ".agents" / "skills" in roots
    assert Path.cwd() / ".agents" / "skills" in roots


def test_load_skill_file(tmp_path: Path):
    skill_dir = write_skill(tmp_path, "demo", "Demo description")
    skill = load_skill_file(skill_dir / "SKILL.md", source="test")
    assert skill.name == "demo"
    assert skill.description == "Demo description"
    assert skill.source == "test"
    assert "Do the thing" in skill.body


def test_load_skill_file_parses_characters(tmp_path: Path):
    skill_dir = write_skill(
        tmp_path,
        "demo",
        "Demo description",
        characters=["미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"],
    )
    skill = load_skill_file(skill_dir / "SKILL.md")
    assert skill.characters == ["미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"]
    assert skill.is_global is False

    global_dir = write_skill(tmp_path, "global-demo")
    assert load_skill_file(global_dir / "SKILL.md").is_global is True


def test_normalize_characters():
    assert normalize_characters(None) == []
    assert normalize_characters(["all"]) == []
    assert normalize_characters("*") == []
    assert normalize_characters("A; B\nC") == ["A", "B", "C"]
    assert normalize_characters(["A", "a", "B"]) == ["A", "B"]


def test_character_matching_accepts_display_name_and_slug():
    display = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    assert character_matches(display, display) is True
    assert character_matches("minami-asuka", display) is True
    assert character_matches("minami_asuka", display) is True
    assert character_matches("Minami Asuka", display) is True
    assert character_matches("최유나 (崔有娜, チェ·ユナ, Choi Yuna)", display) is False
    assert character_matches("", display) is True


def test_registry_filters_skills_by_character(tmp_path: Path):
    user_root = tmp_path / "user"
    minami = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    yuna = "최유나 (崔有娜, チェ·ユナ, Choi Yuna)"
    write_skill(user_root, "global-skill")
    write_skill(user_root, "minami-skill", characters=[minami])
    write_skill(user_root, "yuna-skill", characters=[yuna])

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    assert {s.name for s in registry.enabled_skills(character=minami)} == {"global-skill", "minami-skill"}
    assert {s.name for s in registry.enabled_skills(character=yuna)} == {"global-skill", "yuna-skill"}
    assert {s.name for s in registry.enabled_skills()} == {"global-skill", "minami-skill", "yuna-skill"}
    assert registry.enabled_skills(names=["minami-skill"], character=yuna) == []
    assert registry.skill_characters("minami-skill") == [minami]


def test_character_scope_override_persists(tmp_path: Path):
    user_root = tmp_path / "user"
    minami = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    yuna = "최유나 (崔有娜, チェ·ユナ, Choi Yuna)"
    write_skill(user_root, "demo", characters=[minami])
    config_path = tmp_path / "skills.json"

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=config_path)
    registry.set_skill_characters("demo", [yuna])
    assert registry.skill_characters("demo") == [yuna]
    assert json.loads(config_path.read_text())["characters"] == {"demo": [yuna]}

    reloaded = SkillRegistry(roots=[user_root], user_root=user_root, config_path=config_path)
    assert reloaded.skill_characters("demo") == [yuna]
    assert {s.name for s in reloaded.enabled_skills(character=yuna)} == {"demo"}
    assert reloaded.enabled_skills(character=minami) == []

    reloaded.set_skill_characters("demo", [])
    assert reloaded.skill_characters("demo") == []
    assert {s.name for s in reloaded.enabled_skills(character=minami)} == {"demo"}

    reloaded.set_skill_characters("demo", "yuna")
    assert reloaded.skill_characters("demo") == ["yuna"]


def test_build_skill_tools_respect_character_scope(tmp_path: Path):
    user_root = tmp_path / "user"
    minami = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    yuna = "최유나 (崔有娜, チェ·ユナ, Choi Yuna)"
    write_skill(user_root, "global-skill")
    write_skill(user_root, "minami-skill", characters=[minami])
    write_skill(user_root, "yuna-skill", characters=[yuna])

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")
    specs = build_skill_tool_specs(registry, character=minami)
    enum = specs[0].parameters["properties"]["name"]["enum"]
    assert enum == ["global-skill", "minami-skill"]

    executor = build_skill_executor(registry, character=minami)
    denied = executor(f"skills__{LOAD_SKILL_TOOL}", {"name": "yuna-skill"})
    assert denied.success is False
    allowed = executor(f"skills__{LOAD_SKILL_TOOL}", {"name": "minami-skill"})
    assert allowed.success is True
    assert "Do the thing" in allowed.content


def test_registry_discovery_and_priority(tmp_path: Path):
    user_root = tmp_path / "user"
    project_root = tmp_path / "project"
    write_skill(project_root, "shared", "project version")
    write_skill(user_root, "shared", "user version")
    write_skill(project_root, "project-only", "project only")

    registry = SkillRegistry(
        roots=[project_root, user_root],
        user_root=user_root,
        config_path=tmp_path / "skills.json",
    )
    skills = {skill.name: skill for skill in registry.list_skills()}
    assert set(skills) == {"shared", "project-only"}
    assert skills["shared"].description == "user version"
    assert skills["shared"].source == "user"


def test_registry_discovers_nested_skills(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "top-level")
    nested = user_root / "category" / "subcategory" / "nested-skill"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: nested-skill\ndescription: Nested skill.\n---\n\n# Nested\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")
    names = [skill.name for skill in registry.list_skills()]
    assert names == ["nested-skill", "top-level"]


def test_registry_does_not_descend_into_skill_folder(tmp_path: Path):
    user_root = tmp_path / "user"
    outer = write_skill(user_root, "outer")
    inner = outer / "examples" / "inner"
    inner.mkdir(parents=True)
    (inner / "SKILL.md").write_text(
        "---\nname: inner\ndescription: Should not be discovered.\n---\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")
    assert [skill.name for skill in registry.list_skills()] == ["outer"]


def test_registry_skips_hidden_and_dependency_dirs(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "visible")
    write_skill(user_root / ".hidden", "hidden-skill")
    write_skill(user_root / "node_modules" / "pkg", "dependency-skill")
    write_skill(user_root / "venv" / "lib", "venv-skill")

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")
    assert [skill.name for skill in registry.list_skills()] == ["visible"]


def test_enable_disable_persists(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha")
    write_skill(user_root, "beta")
    config_path = tmp_path / "skills.json"

    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=config_path)
    registry.set_enabled("alpha", False)
    assert registry.is_enabled("alpha") is False
    assert json.loads(config_path.read_text())["disabled"] == ["alpha"]

    reloaded = SkillRegistry(roots=[user_root], user_root=user_root, config_path=config_path)
    assert reloaded.is_enabled("alpha") is False
    assert reloaded.is_enabled("beta") is True


def test_set_enabled_skills_is_exclusive(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha")
    write_skill(user_root, "beta")
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    registry.set_enabled_skills(["beta"])
    assert [skill.name for skill in registry.enabled_skills()] == ["beta"]
    assert registry.is_enabled("alpha") is False


def test_catalog_and_prompt_only_include_enabled(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha", "Alpha description")
    write_skill(user_root, "beta", "Beta description")
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")
    registry.set_enabled("alpha", False)

    catalog = registry.build_catalog()
    assert "beta" in catalog
    assert "alpha" not in catalog
    assert build_skill_prompt(registry) == catalog


def test_load_skill_text_lists_supporting_files(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha", extra_files={"references/guide.md": "guide"})
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    text = registry.load_skill_text("alpha")
    assert "# Skill: alpha" in text
    assert "Do the thing" in text
    assert "references/guide.md" in text
    assert registry.read_skill_file("alpha", "references/guide.md").endswith("guide")


@pytest.mark.parametrize("bad_path", ["../secret.md", "../../etc/passwd", "/etc/passwd"])
def test_read_skill_file_blocks_escape(tmp_path: Path, bad_path: str):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha")
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    with pytest.raises(SkillError):
        registry.read_skill_file("alpha", bad_path)


def test_read_skill_file_rejects_binary_suffix(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha", extra_files={"image.png": "not really an image"})
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    with pytest.raises(SkillError):
        registry.read_skill_file("alpha", "image.png")


def test_build_skill_tool_specs_and_executor(tmp_path: Path):
    user_root = tmp_path / "user"
    write_skill(user_root, "alpha", "Alpha description", body="# Alpha\n\nAlpha steps.")
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    specs = build_skill_tool_specs(registry)
    names = {spec.qualified_name for spec in specs}
    assert names == {f"skills__{LOAD_SKILL_TOOL}", f"skills__{READ_SKILL_FILE_TOOL}"}

    executor = build_skill_executor(registry)
    result = executor(f"skills__{LOAD_SKILL_TOOL}", {"name": "alpha"})
    assert result.success is True
    assert "Alpha steps." in result.content

    denied = executor(f"skills__{LOAD_SKILL_TOOL}", {"name": "missing"})
    assert denied.success is False
    assert "Unknown or disabled" in denied.error


def test_tool_specs_empty_when_no_skills(tmp_path: Path):
    registry = SkillRegistry(roots=[tmp_path / "empty"], user_root=tmp_path / "user", config_path=tmp_path / "skills.json")
    assert build_skill_tool_specs(registry) == []
    assert build_skill_executor(registry)(f"skills__{LOAD_SKILL_TOOL}", {"name": "x"}).success is False


def test_create_skill(tmp_path: Path):
    registry = SkillRegistry(roots=[tmp_path / "user"], user_root=tmp_path / "user", config_path=tmp_path / "skills.json")
    skill = registry.create_skill("My New Skill", "Use when testing.", "# Steps\n\n1. Test.")
    assert skill.name == "my-new-skill"
    assert (tmp_path / "user" / "my-new-skill" / "SKILL.md").is_file()
    assert registry.get("my-new-skill") is not None

    with pytest.raises(SkillError):
        registry.create_skill("My New Skill", "duplicate", "")


def test_create_skill_with_characters(tmp_path: Path):
    minami = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    registry = SkillRegistry(roots=[tmp_path / "user"], user_root=tmp_path / "user", config_path=tmp_path / "skills.json")
    skill = registry.create_skill(
        "Minami Helper",
        "Character-specific test skill.",
        "# Steps\n\nHelp Minami.",
        characters=[minami],
    )

    assert skill.characters == [minami]
    assert registry.skill_characters("minami-helper") == [minami]
    assert {s.name for s in registry.enabled_skills(character=minami)} == {"minami-helper"}
    assert registry.enabled_skills(character="최유나 (崔有娜, チェ·ユナ, Choi Yuna)") == []

    content = (tmp_path / "user" / "minami-helper" / "SKILL.md").read_text(encoding="utf-8")
    assert "characters:" in content


def test_import_skill_folder_and_markdown(tmp_path: Path):
    source_root = tmp_path / "source"
    source_skill = write_skill(source_root, "imported", "Imported skill", extra_files={"refs/a.md": "A"})
    user_root = tmp_path / "user"
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    skill = registry.import_skill(source_skill)
    assert skill.name == "imported"
    assert (user_root / "imported" / "refs" / "a.md").read_text() == "A"

    standalone = tmp_path / "standalone.md"
    standalone.write_text("---\nname: standalone\ndescription: Standalone.\n---\n\nBody", encoding="utf-8")
    imported = registry.import_skill(standalone)
    assert imported.name == "standalone"
    assert (user_root / "standalone" / "SKILL.md").is_file()


def test_delete_only_user_skills(tmp_path: Path):
    user_root = tmp_path / "user"
    project_root = tmp_path / "project"
    write_skill(user_root, "user-skill")
    write_skill(project_root, "bundled-skill")
    registry = SkillRegistry(roots=[project_root, user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    registry.delete_skill("user-skill")
    assert registry.get("user-skill") is None

    with pytest.raises(SkillError):
        registry.delete_skill("bundled-skill")


def test_run_tool_agent_skills_only(monkeypatch, tmp_path: Path):
    from src.mcp import agent as agent_module

    user_root = tmp_path / "user"
    write_skill(user_root, "alpha", "Alpha description", body="# Alpha\n\nAlpha steps.")
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    captured: dict = {}

    def fake_generate_answer(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(agent_module, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(agent_module, "build_skill_tool_specs", lambda names=None, character=None: build_skill_tool_specs(registry, names, character))
    monkeypatch.setattr(agent_module, "build_skill_executor", lambda names=None, character=None: build_skill_executor(registry, names, character))
    monkeypatch.setattr(agent_module, "build_skill_prompt", lambda names=None, character=None: build_skill_prompt(registry, names, character))

    class FakeManager:
        def list_tools(self):
            return []

    answer = agent_module.run_tool_agent(
        history=[{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "hello"}],
        selected_model="test-model",
        provider="openai",
        manager=FakeManager(),
        mcp_tools_enabled=False,
        skills_enabled=True,
    )

    assert answer == "ok"
    spec_names = {spec.qualified_name for spec in captured["tool_specs"]}
    assert spec_names == {f"skills__{LOAD_SKILL_TOOL}", f"skills__{READ_SKILL_FILE_TOOL}"}
    assert "Available Agent Skills" in captured["history"][0]["content"]
    assert "alpha" in captured["history"][0]["content"]

    result = captured["tool_executor"](f"skills__{LOAD_SKILL_TOOL}", {"name": "alpha"})
    assert result.success is True
    assert "Alpha steps." in result.content


def test_run_tool_agent_filters_by_character(monkeypatch, tmp_path: Path):
    from src.mcp import agent as agent_module

    minami = "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
    yuna = "최유나 (崔有娜, チェ·ユナ, Choi Yuna)"
    user_root = tmp_path / "user"
    write_skill(user_root, "global-skill", "Global skill.")
    write_skill(user_root, "minami-skill", "Minami skill.", characters=[minami])
    write_skill(user_root, "yuna-skill", "Yuna skill.", characters=[yuna])
    registry = SkillRegistry(roots=[user_root], user_root=user_root, config_path=tmp_path / "skills.json")

    captured: dict = {}

    def fake_generate_answer(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(agent_module, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(agent_module, "build_skill_tool_specs", lambda names=None, character=None: build_skill_tool_specs(registry, names, character))
    monkeypatch.setattr(agent_module, "build_skill_executor", lambda names=None, character=None: build_skill_executor(registry, names, character))
    monkeypatch.setattr(agent_module, "build_skill_prompt", lambda names=None, character=None: build_skill_prompt(registry, names, character))

    class FakeManager:
        def list_tools(self):
            return []

    answer = agent_module.run_tool_agent(
        history=[{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "hello"}],
        selected_model="test-model",
        provider="openai",
        manager=FakeManager(),
        mcp_tools_enabled=False,
        skills_enabled=True,
        character=minami,
    )

    assert answer == "ok"
    enum = captured["tool_specs"][0].parameters["properties"]["name"]["enum"]
    assert enum == ["global-skill", "minami-skill"]
    prompt = captured["history"][0]["content"]
    assert "minami-skill" in prompt
    assert "yuna-skill" not in prompt

    denied = captured["tool_executor"](f"skills__{LOAD_SKILL_TOOL}", {"name": "yuna-skill"})
    assert denied.success is False


def test_run_tool_agent_without_tools_raises(tmp_path: Path):
    from src.mcp import agent as agent_module

    class FakeManager:
        def list_tools(self):
            return []

    with pytest.raises(RuntimeError):
        agent_module.run_tool_agent(
            history=[{"role": "user", "content": "hello"}],
            selected_model="test-model",
            provider="openai",
            manager=FakeManager(),
            mcp_tools_enabled=False,
            skills_enabled=False,
        )

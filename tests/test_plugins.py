"""Unit tests for plugin discovery, approval gating, and loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.plugins import (
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_REJECTED,
    PluginError,
    PluginRegistry,
    compute_fingerprint,
    parse_manifest,
)

DEFAULT_BODY = """
def register(api):
    api.register_page(id="hello", label="Hello", builder=lambda: None)
"""


def write_plugin(
    root: Path,
    plugin_id: str = "test-plugin",
    *,
    body: str | None = None,
    manifest_extra: str = "",
    folder: str | None = None,
) -> Path:
    directory = root / (folder or plugin_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.toml").write_text(
        "[plugin]\n"
        f'id = "{plugin_id}"\n'
        f'name = "{plugin_id}"\n'
        'version = "0.1.0"\n'
        f"{manifest_extra}",
        encoding="utf-8",
    )
    (directory / "plugin.py").write_text(body or DEFAULT_BODY, encoding="utf-8")
    return directory


@pytest.fixture()
def registry(tmp_path: Path) -> PluginRegistry:
    root = tmp_path / "plugins"
    root.mkdir()
    return PluginRegistry(
        roots=[root],
        user_root=root,
        config_path=tmp_path / "plugins.json",
        data_root=tmp_path / "plugin-data",
    )


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------
def test_parse_manifest(tmp_path: Path):
    directory = write_plugin(tmp_path)
    manifest = parse_manifest(directory / "plugin.toml")
    assert manifest.id == "test-plugin"
    assert manifest.name == "test-plugin"
    assert manifest.version == "0.1.0"
    assert manifest.entrypoint == "plugin:register"
    assert manifest.module == "plugin"
    assert manifest.attribute == "register"


def test_parse_manifest_rejects_newer_api(tmp_path: Path):
    directory = write_plugin(tmp_path, manifest_extra="api_version = 99\n")
    with pytest.raises(PluginError):
        parse_manifest(directory / "plugin.toml")


def test_parse_manifest_rejects_bad_entrypoint(tmp_path: Path):
    directory = write_plugin(tmp_path, manifest_extra='entrypoint = "nonsense"\n')
    with pytest.raises(PluginError):
        parse_manifest(directory / "plugin.toml")


def test_parse_manifest_derives_id_from_folder(tmp_path: Path):
    directory = write_plugin(tmp_path, folder="My Plugin!")
    (directory / "plugin.toml").write_text('[plugin]\nname = "x"\n', encoding="utf-8")
    manifest = parse_manifest(directory / "plugin.toml", fallback_id="My Plugin!")
    assert manifest.id == "my-plugin"


# ----------------------------------------------------------------------
# Fingerprint
# ----------------------------------------------------------------------
def test_fingerprint_changes_with_source(tmp_path: Path):
    directory = write_plugin(tmp_path)
    before = compute_fingerprint(directory)
    (directory / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    assert compute_fingerprint(directory) != before


def test_fingerprint_ignores_cache_dirs(tmp_path: Path):
    directory = write_plugin(tmp_path)
    before = compute_fingerprint(directory)
    cache = directory / "__pycache__"
    cache.mkdir()
    (cache / "plugin.cpython-312.pyc").write_bytes(b"junk")
    assert compute_fingerprint(directory) == before


# ----------------------------------------------------------------------
# Discovery and approval
# ----------------------------------------------------------------------
def test_discovery_defaults_to_pending(registry: PluginRegistry):
    write_plugin(registry.user_root)
    plugins = registry.refresh()
    assert [plugin.id for plugin in plugins] == ["test-plugin"]
    assert registry.status("test-plugin") == STATUS_PENDING


def test_approve_then_code_change_requires_reapproval(registry: PluginRegistry):
    directory = write_plugin(registry.user_root)
    registry.refresh()
    registry.approve("test-plugin")
    assert registry.status("test-plugin") == STATUS_APPROVED

    (directory / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    registry.refresh()
    assert registry.status("test-plugin") == STATUS_PENDING


def test_reject_and_disable(registry: PluginRegistry):
    write_plugin(registry.user_root)
    registry.refresh()
    registry.approve("test-plugin")

    registry.reject("test-plugin")
    assert registry.status("test-plugin") == STATUS_REJECTED

    registry.approve("test-plugin")
    registry.set_enabled("test-plugin", False)
    assert registry.status("test-plugin") == STATUS_DISABLED


def test_approval_persists(registry: PluginRegistry, tmp_path: Path):
    write_plugin(registry.user_root)
    registry.refresh()
    registry.approve("test-plugin")

    reloaded = PluginRegistry(
        roots=[registry.user_root],
        user_root=registry.user_root,
        config_path=registry.config_path,
        data_root=tmp_path / "plugin-data-2",
    )
    reloaded.refresh()
    assert reloaded.status("test-plugin") == STATUS_APPROVED


def test_invalid_plugin_is_skipped(registry: PluginRegistry):
    directory = write_plugin(registry.user_root, plugin_id="bad-manifest")
    (directory / "plugin.toml").write_text("not = [valid toml\n", encoding="utf-8")
    assert registry.refresh() == []


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def test_unapproved_plugin_is_not_imported(registry: PluginRegistry):
    write_plugin(registry.user_root, plugin_id="never-imported")
    registry.refresh()
    registry.load_approved()
    assert "ai_companion_plugins.never-imported" not in sys.modules
    assert registry.pages() == []


def test_approved_plugin_contributes_page(registry: PluginRegistry):
    write_plugin(registry.user_root, plugin_id="page-plugin")
    registry.refresh()
    registry.approve("page-plugin")

    assert registry.load_approved() == ["page-plugin"]
    pages = registry.pages()
    assert [page.id for page in pages] == ["hello"]
    assert pages[0].plugin_id == "page-plugin"
    assert pages[0].label == "Hello"


def test_plugin_can_register_settings_tab_and_router(registry: PluginRegistry):
    write_plugin(
        registry.user_root,
        plugin_id="multi-plugin",
        body=(
            "from fastapi import APIRouter\n"
            "\n"
            "def register(api):\n"
            "    api.register_page(id='multi', label='Multi', builder=lambda: None)\n"
            "    api.register_settings_tab('Multi', lambda: None)\n"
            "    api.register_api_route(APIRouter())\n"
        ),
    )
    registry.refresh()
    registry.approve("multi-plugin")
    registry.load_approved()

    assert [tab.label for tab in registry.settings_tabs()] == ["Multi"]
    assert len(registry.api_routers()) == 1


def test_relative_imports_work(registry: PluginRegistry):
    directory = write_plugin(
        registry.user_root,
        plugin_id="relative-plugin",
        body=(
            "from .helpers import LABEL\n"
            "\n"
            "def register(api):\n"
            "    api.register_page(id='rel', label=LABEL, builder=lambda: None)\n"
        ),
    )
    (directory / "helpers.py").write_text("LABEL = 'Relative'\n", encoding="utf-8")

    registry.refresh()
    registry.approve("relative-plugin")
    registry.load_approved()
    assert [page.label for page in registry.pages()] == ["Relative"]


def test_broken_plugin_is_isolated(registry: PluginRegistry):
    write_plugin(
        registry.user_root,
        plugin_id="broken-plugin",
        body="def register(api):\n    raise RuntimeError('boom')\n",
    )
    registry.refresh()
    registry.approve("broken-plugin")

    # Must not raise: one bad plugin cannot stop the host app.
    registry.load_approved()
    assert registry.status("broken-plugin") == STATUS_ERROR
    assert "boom" in registry.error("broken-plugin")


def test_plugin_without_register_is_error(registry: PluginRegistry):
    write_plugin(registry.user_root, plugin_id="no-register", body="X = 1\n")
    registry.refresh()
    registry.approve("no-register")
    registry.load_approved()
    assert registry.status("no-register") == STATUS_ERROR


def test_duplicate_page_id_within_plugin_is_rejected(registry: PluginRegistry):
    write_plugin(
        registry.user_root,
        plugin_id="dup-plugin",
        body=(
            "def register(api):\n"
            "    api.register_page(id='dup', label='One', builder=lambda: None)\n"
            "    api.register_page(id='dup', label='Two', builder=lambda: None)\n"
        ),
    )
    registry.refresh()
    registry.approve("dup-plugin")
    registry.load_approved()
    assert registry.status("dup-plugin") == STATUS_ERROR
    assert "already registered" in registry.error("dup-plugin")


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------
def test_clone_rejects_non_git_url(registry: PluginRegistry):
    with pytest.raises(PluginError):
        registry.clone("not a url")


def test_clone_rejects_existing_folder(registry: PluginRegistry):
    (registry.user_root / "existing").mkdir(parents=True)
    with pytest.raises(PluginError):
        registry.clone("https://example.com/existing.git")

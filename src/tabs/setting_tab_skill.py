# setting_tab_skill.py
"""설정 탭: 에이전트 스킬 관리.

- 발견된 스킬 목록/설명/캐릭터 범위/출처 확인
- 스킬 활성화/비활성화 (영구 저장)
- 스킬 폴더 또는 SKILL.md 임포트
- 새 스킬 생성 및 사용자 스킬 삭제
- 기존 스킬에 캐릭터별 사용 범위 지정
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr

from ai_companion_core import logger

from ..common.translations import _
from ..skills import SkillError, default_skill_roots, get_skill_registry

DF_HEADERS = [
    _("skill_col_name"),
    _("skill_col_description"),
    _("skill_col_characters"),
    _("skill_col_source"),
    _("skill_col_enabled"),
]


def _character_names() -> list[str]:
    """Return built-in and imported character display names."""
    from ..common.character_info import characters

    return list(characters.keys())


def _rows(registry) -> list:
    rows = []
    for skill in registry.list_skills():
        characters = registry.skill_characters(skill.name)
        scope = ", ".join(characters) if characters else _("skill_characters_global")
        rows.append(
            [
                skill.name,
                skill.description,
                scope,
                skill.source,
                registry.is_enabled(skill.name),
            ]
        )
    return rows


def _refresh_values():
    """Return main-tab updates: table, enabled, scope skill, delete, status."""
    registry = get_skill_registry()
    registry.refresh()
    skills = registry.list_skills()
    names = [skill.name for skill in skills]
    enabled = [skill.name for skill in skills if registry.is_enabled(skill.name)]

    if skills:
        status = _("skills_info_summary", enabled=len(enabled), total=len(skills))
    else:
        status = _("skills_empty")

    return (
        _rows(registry),
        gr.update(choices=names, value=enabled),
        gr.update(choices=names, value=None),
        gr.update(choices=names, value=None),
        status,
    )


def create_skill_management_tab(demo: gr.Blocks | None = None):
    """Build the Skills settings tab and wire its events."""
    registry = get_skill_registry()
    roots_text = "\n".join(f"- `{root}`" for root in default_skill_roots())
    character_names = _character_names()

    with gr.Tab(_("skill_tab_title")):
        gr.Markdown(_("skill_tab_intro"))
        with gr.Accordion(_("skill_paths_title"), open=False):
            gr.Markdown(roots_text)

        with gr.Row():
            refresh_btn = gr.Button(_("skill_reload_button"), variant="secondary")
            status = gr.Markdown(_("skills_empty"))

        skills_table = gr.Dataframe(
            headers=DF_HEADERS,
            datatype=["str", "str", "str", "str", "bool"],
            value=_rows(registry),
            interactive=False,
            wrap=True,
            label=_("skill_table_label"),
        )

        with gr.Row():
            enabled_checkboxes = gr.CheckboxGroup(
                label=_("skill_enabled_label"),
                choices=[skill.name for skill in registry.list_skills()],
                value=[skill.name for skill in registry.list_skills() if registry.is_enabled(skill.name)],
                interactive=True,
            )
            save_btn = gr.Button(_("skill_save_button"), variant="primary")

        with gr.Accordion(_("skill_scope_title"), open=False):
            gr.Markdown(_("skill_scope_info"))
            scope_skill = gr.Dropdown(
                label=_("skill_scope_skill_label"),
                choices=[skill.name for skill in registry.list_skills()],
                value=None,
                interactive=True,
            )
            scope_characters = gr.CheckboxGroup(
                label=_("skill_scope_characters_label"),
                choices=character_names,
                value=[],
                interactive=True,
            )
            scope_save_btn = gr.Button(_("skill_scope_save_button"), variant="primary")
            scope_status = gr.Markdown("")

        with gr.Accordion(_("skill_import_title"), open=False):
            import_path = gr.Textbox(
                label=_("skill_import_path_label"),
                placeholder=str(Path.home() / "my-skill"),
                info=_("skill_import_path_info"),
            )
            import_btn = gr.Button(_("skill_import_button"), variant="secondary")

        with gr.Accordion(_("skill_create_title"), open=False):
            skill_name = gr.Textbox(label=_("skill_name_label"), placeholder="my-skill")
            skill_description = gr.Textbox(label=_("skill_description_label"), placeholder=_("skill_description_placeholder"))
            skill_characters = gr.Dropdown(
                label=_("skill_characters_label"),
                choices=character_names,
                value=[],
                multiselect=True,
                interactive=True,
                info=_("skill_characters_info"),
            )
            skill_instructions = gr.Textbox(
                label=_("skill_instructions_label"),
                lines=8,
                placeholder="# Instructions\n\nDescribe how the agent should perform this task.",
            )
            create_btn = gr.Button(_("skill_create_button"), variant="primary")

        with gr.Accordion(_("skill_delete_title"), open=False):
            delete_dropdown = gr.Dropdown(label=_("skill_delete_label"), choices=[skill.name for skill in registry.list_skills()], value=None, interactive=True)
            delete_btn = gr.Button(_("skill_delete_button"), variant="stop")

    def _refresh():
        return _refresh_values()

    def _load_scope(skill_name_value):
        if not skill_name_value:
            return gr.update(choices=_character_names(), value=[])
        return gr.update(choices=_character_names(), value=get_skill_registry().skill_characters(skill_name_value))

    def _save_scope(skill_name_value, character_values):
        if not skill_name_value:
            rows, _enabled, _scope_skill, _delete, _status = _refresh_values()
            return rows, f"❌ {_('skill_scope_select_skill')}"
        try:
            saved = get_skill_registry().set_skill_characters(skill_name_value, character_values or [])
        except Exception as exc:
            logger.error(f"Failed to save skill character scope: {exc}")
            rows, _enabled, _scope_skill, _delete, _status = _refresh_values()
            return rows, f"❌ {exc}"
        rows, _enabled, _scope_skill, _delete, _status = _refresh_values()
        scope = ", ".join(saved) if saved else _("skill_characters_global")
        return rows, f"{_('skill_scope_saved', name=skill_name_value)}\n`{scope}`"

    def _save_enabled(enabled_names):
        try:
            get_skill_registry().set_enabled_skills(enabled_names or [])
        except Exception as exc:
            logger.error(f"Failed to save enabled skills: {exc}")
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        return _refresh_values()

    def _import(path_value):
        if not str(path_value or "").strip():
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {_('skill_import_path_required')}"
        try:
            skill = get_skill_registry().import_skill(path_value)
        except SkillError as exc:
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to import skill: {exc}")
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        rows, enabled_update, scope_skill, delete_update, status = _refresh_values()
        return rows, enabled_update, scope_skill, delete_update, f"{status}\n\n{_('skill_import_success', name=skill.name)}"

    def _create(name, description, characters, instructions):
        try:
            skill = get_skill_registry().create_skill(name, description, instructions, characters=characters or [])
        except SkillError as exc:
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to create skill: {exc}")
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        rows, enabled_update, scope_skill, delete_update, status = _refresh_values()
        return rows, enabled_update, scope_skill, delete_update, f"{status}\n\n{_('skill_create_success', name=skill.name)}"

    def _delete(name):
        if not name:
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {_('skill_delete_required')}"
        try:
            get_skill_registry().delete_skill(name)
        except SkillError as exc:
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to delete skill: {exc}")
            rows, enabled_update, scope_skill, delete_update, _status = _refresh_values()
            return rows, enabled_update, scope_skill, delete_update, f"❌ {exc}"
        rows, enabled_update, scope_skill, delete_update, status = _refresh_values()
        return rows, enabled_update, scope_skill, delete_update, f"{status}\n\n{_('skill_delete_success', name=name)}"

    outputs = [skills_table, enabled_checkboxes, scope_skill, delete_dropdown, status]

    refresh_btn.click(fn=_refresh, inputs=[], outputs=outputs)
    save_btn.click(fn=_save_enabled, inputs=[enabled_checkboxes], outputs=outputs)
    import_btn.click(fn=_import, inputs=[import_path], outputs=outputs)
    create_btn.click(fn=_create, inputs=[skill_name, skill_description, skill_characters, skill_instructions], outputs=outputs)
    delete_btn.click(fn=_delete, inputs=[delete_dropdown], outputs=outputs)

    scope_skill.change(fn=_load_scope, inputs=[scope_skill], outputs=[scope_characters])
    scope_save_btn.click(fn=_save_scope, inputs=[scope_skill, scope_characters], outputs=[skills_table, scope_status])

    if demo is not None:
        demo.load(fn=_refresh, inputs=[], outputs=outputs, queue=False)

    return {
        "skills_table": skills_table,
        "enabled_checkboxes": enabled_checkboxes,
        "delete_dropdown": delete_dropdown,
        "scope_skill": scope_skill,
        "scope_characters": scope_characters,
        "status": status,
    }

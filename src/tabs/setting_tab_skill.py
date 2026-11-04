# setting_tab_skill.py
"""설정 탭: 에이전트 스킬 관리.

- 발견된 스킬 목록/설명/출처 확인
- 스킬 활성화/비활성화 (영구 저장)
- 스킬 폴더 또는 SKILL.md 임포트
- 새 스킬 생성 및 사용자 스킬 삭제
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
    _("skill_col_source"),
    _("skill_col_enabled"),
]


def _rows(registry) -> list:
    return [
        [skill.name, skill.description, skill.source, registry.is_enabled(skill.name)]
        for skill in registry.list_skills()
    ]


def _refresh_values():
    """Return (table rows, enabled checkbox update, delete dropdown update, status)."""
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
        status,
    )


def create_skill_management_tab(demo: gr.Blocks | None = None):
    """Build the Skills settings tab and wire its events."""
    registry = get_skill_registry()
    roots_text = "\n".join(f"- `{root}`" for root in default_skill_roots())

    with gr.Tab(_("skill_tab_title")):
        gr.Markdown(_("skill_tab_intro"))
        with gr.Accordion(_("skill_paths_title"), open=False):
            gr.Markdown(roots_text)

        with gr.Row():
            refresh_btn = gr.Button(_("skill_reload_button"), variant="secondary")
            status = gr.Markdown(_("skills_empty"))

        skills_table = gr.Dataframe(
            headers=DF_HEADERS,
            datatype=["str", "str", "str", "bool"],
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

    def _save_enabled(enabled_names):
        try:
            get_skill_registry().set_enabled_skills(enabled_names or [])
        except Exception as exc:
            logger.error(f"Failed to save enabled skills: {exc}")
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        rows, enabled_update, delete_update, status = _refresh_values()
        return rows, enabled_update, delete_update, status

    def _import(path_value):
        if not str(path_value or "").strip():
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {_('skill_import_path_required')}"
        try:
            skill = get_skill_registry().import_skill(path_value)
        except SkillError as exc:
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to import skill: {exc}")
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        rows, enabled_update, delete_update, status = _refresh_values()
        return rows, enabled_update, delete_update, f"{status}\n\n{_('skill_import_success', name=skill.name)}"

    def _create(name, description, instructions):
        try:
            skill = get_skill_registry().create_skill(name, description, instructions)
        except SkillError as exc:
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to create skill: {exc}")
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        rows, enabled_update, delete_update, status = _refresh_values()
        return rows, enabled_update, delete_update, f"{status}\n\n{_('skill_create_success', name=skill.name)}"

    def _delete(name):
        if not name:
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {_('skill_delete_required')}"
        try:
            get_skill_registry().delete_skill(name)
        except SkillError as exc:
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to delete skill: {exc}")
            rows, enabled_update, delete_update, _status = _refresh_values()
            return rows, enabled_update, delete_update, f"❌ {exc}"
        rows, enabled_update, delete_update, status = _refresh_values()
        return rows, enabled_update, delete_update, f"{status}\n\n{_('skill_delete_success', name=name)}"

    outputs = [skills_table, enabled_checkboxes, delete_dropdown, status]

    refresh_btn.click(fn=_refresh, inputs=[], outputs=outputs)
    save_btn.click(fn=_save_enabled, inputs=[enabled_checkboxes], outputs=outputs)
    import_btn.click(fn=_import, inputs=[import_path], outputs=outputs)
    create_btn.click(fn=_create, inputs=[skill_name, skill_description, skill_instructions], outputs=outputs)
    delete_btn.click(fn=_delete, inputs=[delete_dropdown], outputs=outputs)

    if demo is not None:
        demo.load(fn=_refresh, inputs=[], outputs=outputs, queue=False)

    return {
        "skills_table": skills_table,
        "enabled_checkboxes": enabled_checkboxes,
        "delete_dropdown": delete_dropdown,
        "status": status,
    }

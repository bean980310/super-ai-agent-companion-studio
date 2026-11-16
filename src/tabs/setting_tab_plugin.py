# setting_tab_plugin.py
"""설정 탭: 플러그인 관리.

- 발견된 플러그인 목록/버전/출처/상태 확인
- 첫 실행 시 승인(approve) / 거부(reject) — 승인 전에는 코드가 실행되지 않음
- 활성화/비활성화 (영구 저장)
- git 저장소 URL로 플러그인 설치
"""

from __future__ import annotations

import gradio as gr
from ai_companion_core import logger

from ..common.translations import _
from ..plugins import (
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_REJECTED,
    PluginError,
    get_plugin_registry,
)

DF_HEADERS = [
    _("plugin_col_id"),
    _("plugin_col_name"),
    _("plugin_col_version"),
    _("plugin_col_source"),
    _("plugin_col_status"),
]

_STATUS_KEYS = {
    STATUS_PENDING: "plugin_status_pending",
    STATUS_APPROVED: "plugin_status_approved",
    STATUS_REJECTED: "plugin_status_rejected",
    STATUS_DISABLED: "plugin_status_disabled",
    STATUS_ERROR: "plugin_status_error",
}


def _status_label(status: str) -> str:
    return _(_STATUS_KEYS.get(status, "plugin_status_error"))


def _rows(registry) -> list:
    return [
        [
            plugin.id,
            plugin.manifest.name,
            plugin.manifest.version,
            plugin.source,
            _status_label(registry.status(plugin.id)),
        ]
        for plugin in registry.list_plugins()
    ]


def _choices(registry) -> list:
    return [plugin.id for plugin in registry.list_plugins()]


def _summary(registry) -> str:
    plugins = registry.list_plugins()
    if not plugins:
        return _("plugin_empty")
    approved = sum(1 for plugin in plugins if registry.status(plugin.id) == STATUS_APPROVED)
    pending = sum(1 for plugin in plugins if registry.status(plugin.id) == STATUS_PENDING)
    return _("plugin_summary", total=len(plugins), approved=approved, pending=pending)


def _refresh_values():
    """Return (table rows, dropdown update, detail, status)."""
    registry = get_plugin_registry()
    registry.refresh()
    return (
        _rows(registry),
        gr.update(choices=_choices(registry), value=None),
        "",
        _summary(registry),
    )


def _detail(registry, plugin_id: str) -> str:
    plugin = registry.get(plugin_id)
    if plugin is None:
        return ""
    lines = [f"### {plugin.manifest.name} `{plugin.id}`"]
    if plugin.manifest.description:
        lines.append(plugin.manifest.description)
    lines.append(f"- {_('plugin_col_version')}: {plugin.manifest.version}")
    if plugin.manifest.author:
        lines.append(f"- {_('plugin_col_author')}: {plugin.manifest.author}")
    if plugin.manifest.homepage:
        lines.append(f"- {_('plugin_col_homepage')}: {plugin.manifest.homepage}")
    lines.append(f"- {_('plugin_col_path')}: `{plugin.directory}`")
    lines.append(f"- {_('plugin_col_status')}: {_status_label(registry.status(plugin.id))}")

    error = registry.error(plugin.id)
    if error:
        lines.append(f"\n**{_('plugin_error_label')}**\n\n```\n{error}\n```")
    return "\n".join(lines)


def create_plugin_management_tab(demo: gr.Blocks | None = None):
    """Build the Plugins settings tab and wire its events."""
    registry = get_plugin_registry()
    roots_text = "\n".join(f"- `{root}`" for root in registry.roots)

    with gr.Tab(_("plugin_tab_title")):
        gr.Markdown(_("plugin_tab_intro"))
        with gr.Accordion(_("plugin_paths_title"), open=False):
            gr.Markdown(roots_text)

        with gr.Row():
            refresh_btn = gr.Button(_("plugin_refresh_button"), variant="secondary")
            status = gr.Markdown(_("plugin_empty"))

        plugins_table = gr.Dataframe(
            headers=DF_HEADERS,
            datatype=["str", "str", "str", "str", "str"],
            value=_rows(registry),
            interactive=False,
            wrap=True,
            label=_("plugin_table_label"),
        )

        with gr.Row():
            plugin_dropdown = gr.Dropdown(
                label=_("plugin_select_label"),
                choices=_choices(registry),
                value=None,
                interactive=True,
            )

        with gr.Row():
            approve_btn = gr.Button(_("plugin_approve_button"), variant="primary")
            reject_btn = gr.Button(_("plugin_reject_button"), variant="stop")
            enable_btn = gr.Button(_("plugin_enable_button"), variant="secondary")
            disable_btn = gr.Button(_("plugin_disable_button"), variant="secondary")

        detail = gr.Markdown("")

        with gr.Accordion(_("plugin_clone_title"), open=False):
            clone_url = gr.Textbox(
                label=_("plugin_clone_url_label"),
                placeholder="https://github.com/owner/ai-companion-plugin",
                info=_("plugin_clone_url_info"),
            )
            clone_name = gr.Textbox(
                label=_("plugin_clone_name_label"),
                placeholder="my-plugin",
            )
            clone_btn = gr.Button(_("plugin_clone_button"), variant="primary")

    def _refresh():
        return _refresh_values()

    def _select(plugin_id):
        return _detail(get_plugin_registry(), plugin_id) if plugin_id else ""

    def _action(plugin_id, action):
        if not plugin_id:
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {_('plugin_select_required')}"
        try:
            plugin = action(plugin_id)
        except PluginError as exc:
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Plugin action failed: {exc}")
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {exc}"

        rows, dropdown_update, _detail_text, summary = _refresh_values()
        return rows, dropdown_update, _detail(get_plugin_registry(), plugin.id), summary

    def _approve(plugin_id):
        rows, dropdown_update, detail_text, summary = _action(plugin_id, get_plugin_registry().approve)
        if not summary.startswith("❌"):
            summary = f"{summary}\n\n{_('plugin_approve_success', name=plugin_id)}\n\n{_('plugin_restart_hint')}"
        return rows, dropdown_update, detail_text, summary

    def _reject(plugin_id):
        rows, dropdown_update, detail_text, summary = _action(plugin_id, get_plugin_registry().reject)
        if not summary.startswith("❌"):
            summary = f"{summary}\n\n{_('plugin_reject_success', name=plugin_id)}"
        return rows, dropdown_update, detail_text, summary

    def _enable(plugin_id):
        rows, dropdown_update, detail_text, summary = _action(
            plugin_id, lambda value: get_plugin_registry().set_enabled(value, True)
        )
        if not summary.startswith("❌"):
            summary = f"{summary}\n\n{_('plugin_enable_success', name=plugin_id)}\n\n{_('plugin_restart_hint')}"
        return rows, dropdown_update, detail_text, summary

    def _disable(plugin_id):
        rows, dropdown_update, detail_text, summary = _action(
            plugin_id, lambda value: get_plugin_registry().set_enabled(value, False)
        )
        if not summary.startswith("❌"):
            summary = f"{summary}\n\n{_('plugin_disable_success', name=plugin_id)}\n\n{_('plugin_restart_hint')}"
        return rows, dropdown_update, detail_text, summary

    def _clone(url, name):
        if not str(url or "").strip():
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {_('plugin_clone_url_required')}"
        try:
            plugin = get_plugin_registry().clone(url, name or None)
        except PluginError as exc:
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {exc}"
        except Exception as exc:
            logger.error(f"Failed to clone plugin: {exc}")
            rows, dropdown_update, _detail_text, summary = _refresh_values()
            return rows, dropdown_update, "", f"❌ {exc}"

        rows, dropdown_update, _detail_text, summary = _refresh_values()
        return (
            rows,
            dropdown_update,
            _detail(get_plugin_registry(), plugin.id),
            f"{summary}\n\n{_('plugin_clone_success', name=plugin.id)}\n\n{_('plugin_restart_hint')}",
        )

    outputs = [plugins_table, plugin_dropdown, detail, status]

    refresh_btn.click(fn=_refresh, inputs=[], outputs=outputs)
    plugin_dropdown.change(fn=_select, inputs=[plugin_dropdown], outputs=[detail])
    approve_btn.click(fn=_approve, inputs=[plugin_dropdown], outputs=outputs)
    reject_btn.click(fn=_reject, inputs=[plugin_dropdown], outputs=outputs)
    enable_btn.click(fn=_enable, inputs=[plugin_dropdown], outputs=outputs)
    disable_btn.click(fn=_disable, inputs=[plugin_dropdown], outputs=outputs)
    clone_btn.click(fn=_clone, inputs=[clone_url, clone_name], outputs=outputs)

    if demo is not None:
        demo.load(fn=_refresh, inputs=[], outputs=outputs, queue=False)

    return {
        "plugins_table": plugins_table,
        "plugin_dropdown": plugin_dropdown,
        "detail": detail,
        "status": status,
    }

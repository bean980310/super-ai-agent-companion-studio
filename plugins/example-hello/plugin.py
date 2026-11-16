"""Reference plugin.

Copy this folder to ``~/.ai-companion/plugins/<your-plugin>`` (or clone a
repository that looks like this) to start a new feature. The host imports this
module only after the user approves the plugin in Settings > Plugins.
"""

from __future__ import annotations

import gradio as gr


def build_page():
    """Draw the page inside the host's ``gr.TabItem`` context."""
    gr.Markdown("# Hello from a plugin")
    gr.Markdown("This page was contributed by `plugins/example-hello`.")

    name = gr.Textbox(label="Your name", placeholder="World")
    output = gr.Markdown("")
    button = gr.Button("Greet", variant="primary")
    button.click(
        fn=lambda value: f"Hello, {value or 'World'}!",
        inputs=[name],
        outputs=[output],
    )


def build_settings_tab():
    gr.Markdown("### Example plugin settings")
    gr.Markdown("Plugins can add their own settings tab next to the built-in ones.")


def register(api):
    """Entrypoint declared by ``entrypoint = "plugin:register"``."""
    api.register_page(
        id="example-hello",
        label="Hello",
        builder=build_page,
        order=900,
    )
    api.register_settings_tab("Hello", build_settings_tab, order=900)

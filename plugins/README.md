# Plugins

New features live here instead of in `src/`. A plugin is a folder with a
`plugin.toml` manifest and a Python module that exposes `register(api)`.

The app discovers plugins from:

1. `$AI_COMPANION_PLUGINS_DIRS` (colon-separated, highest priority)
2. `~/.ai-companion/plugins` — user plugins, where `git clone` installs
3. `./plugins` — plugins shipped with this checkout

## Safety model

Discovery only reads `plugin.toml`. The plugin's Python code is imported **only
after you approve it** in Settings > Plugins. Approvals are stored in
`~/.ai-companion/plugins.json` and bound to a fingerprint of the plugin's
source, so a freshly cloned or updated plugin stays inert until it is approved
again.

Approving, enabling, or disabling a plugin takes effect after a restart, because
Gradio tabs are built once at startup.

## Layout

```
plugins/my-feature/
    plugin.toml
    plugin.py          # def register(api): ...
```

```toml
[plugin]
id = "my-feature"          # lowercase letters, digits, '-' or '_'
name = "My Feature"
version = "0.1.0"
entrypoint = "plugin:register"
api_version = 1
description = "What this plugin adds."
author = "you"
order = 100                # sidebar position; built-in pages are 0-99
```

## API

```python
def register(api):
    api.register_page(id, label, builder, order=100)
    api.register_settings_tab(label, builder, order=100)
    api.register_api_route(router)          # FastAPI APIRouter
```

- `builder` is called inside the host's `gr.TabItem` context. Create Gradio
  components; do not create your own `gr.Blocks`.
- `api.config_dir` is a per-plugin directory (`~/.ai-companion/plugin-data/<id>`)
  for persistent data, created on first access.
- `api.logger` is the shared application logger.

Relative imports work (`from .helpers import x`). Absolute imports of sibling
files also work while the module is being imported.

## Installing

Clone a repository into the plugin folder:

```bash
git clone https://github.com/owner/ai-companion-plugin ~/.ai-companion/plugins/my-plugin
```

Then open Settings > Plugins, approve it, and restart the app. The same flow is
available from the UI.

## Reference plugin

`plugins/example-hello` is a minimal working example. It shows up as *pending*
until approved; delete the folder if you do not want it.

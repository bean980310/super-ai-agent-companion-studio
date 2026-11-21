# Agent Skills

AI Companion supports agent skills: reusable instruction packs that the model
loads only when they are relevant. Skills follow the `SKILL.md` convention used
by other agent runtimes.

## Skill layout

```
my-skill/
├── SKILL.md
├── references/
│   └── checklist.md
└── scripts/
    └── helper.py
```

`SKILL.md` starts with a YAML frontmatter block:

```markdown
---
name: my-skill
description: Use this skill when the user asks to ...
characters:
  - "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
---

# My Skill

1. First step.
2. Second step.
```

- `name` is required (or falls back to the folder name). Use lowercase letters,
  digits, `-`, and `_`, up to 64 characters.
- `description` is shown in the skill catalog and tells the model when to load
  the skill.
- `characters` is optional. Omit it (or use `all`/`*`) to make the skill global.
  List character display names to make the skill available only while chatting
  with those characters.
- The Markdown body contains the full instructions.
- Supporting files are read on demand with the `read_skill_file` tool.

## Character-specific skills

A skill can be scoped to one or more characters:

```yaml
---
name: minami-roleplay
description: Roleplay guidance for Minami Asuka.
characters:
  - "미나미 아스카 (南飛鳥, みなみあすか, Minami Asuka)"
  - minami-asuka
---
```

Character entries can be:

- the exact display name shown in the character dropdown,
- a short slug such as `minami-asuka` / `minami_asuka`,
- `all`, `any`, `global`, or `*` for every character.

The same character entry can be repeated; duplicates are ignored. When the
selected character changes, the Chat side panel only lists skills available to
that character, and the model only receives those skill tools. Skills with no
character scope stay available to everyone.

## Where skills are discovered

Highest priority first:

1. `AI_COMPANION_SKILLS_DIRS` (OS-path-separated list of extra roots)
2. `~/.ai-companion/skills`
3. `~/.agents/skills`
4. `<project>/skills`
5. `<project>/.agents/skills`

If two skills share a name, the one from the higher-priority root wins.

Each root is searched recursively, so skills can be grouped in subfolders
(for example `skills/coding/review/SKILL.md`). A directory containing
`SKILL.md` is treated as a skill and is not searched further, and hidden
directories plus common dependency/build folders (`node_modules`, `venv`,
`__pycache__`, `dist`, `build`, ...) are skipped.

The repository ships one example skill in `skills/skill-creator/`.

## Using skills

1. Open **Settings → Configuration → Skills**.
2. Enable the skills the agent may use. The enabled/disabled state is stored in
   `~/.ai-companion/skills.json`.
3. Use **Character-specific skills** in the same tab to assign existing skills
   to characters. Selections made here are stored in `skills.json` and override
   the `characters` frontmatter without editing the skill files.
4. In the Chat side panel, enable **Use Agent Skills** and optionally select a
   subset of the skills available for the current character.
5. Ask a question that matches a skill description. The model sees only the
   catalog up front and calls `load_skill` to fetch the full instructions.

Skills use the same tool-calling path as MCP tools, so the selected provider
must be OpenAI-compatible (or a `custom:*` provider profile).

## Managing skills in the UI

The **Skills** settings tab can:

- list discovered skills with their description, character scope, and source
- enable/disable skills
- assign character scopes to existing skills
- import a skill folder or a standalone `SKILL.md`
- create a new skill under `~/.ai-companion/skills`, optionally scoped to
  characters
- delete user skills (bundled and project skills are read-only)

## Tools exposed to the model

| Tool | Purpose |
| --- | --- |
| `skills__load_skill` | Return a skill's full instructions and file list |
| `skills__read_skill_file` | Read a supporting file inside a skill folder |

Paths are confined to the skill folder; absolute paths and `..` traversal are
rejected. Binary files and files larger than 512 KiB are not readable.

## Implementation

- `src/skills/models.py` — `SKILL.md` parsing, character scope matching, and the `Skill` model
- `src/skills/registry.py` — discovery, persistence, character scope, import/create/delete
- `src/skills/tools.py` — tool specs, executor, and catalog prompt
- `src/tabs/setting_tab_skill.py` — Gradio management tab
- `src/mcp/agent.py` — combines MCP tools and skill tools for the model loop

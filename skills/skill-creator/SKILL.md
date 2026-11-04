---
name: skill-creator
description: Guide for creating a new agent skill. Use when the user asks to create, scaffold, or improve a skill, or when a reusable workflow should be captured as a SKILL.md instruction pack.
---

# Skill Creator

Help the user turn a repeatable workflow into a reusable agent skill.

## What a skill is

A skill is a folder containing `SKILL.md` plus optional supporting files:

```
my-skill/
├── SKILL.md
├── references/
│   └── checklist.md
└── scripts/
    └── helper.py
```

The agent sees only the skill's name and description up front. The full body
and supporting files are loaded on demand, so keep the description specific
enough to tell the agent when the skill applies.

## SKILL.md format

```markdown
---
name: my-skill
description: One or two sentences describing when to use this skill.
---

# My Skill

Step-by-step instructions for the agent.
```

Rules for `name`:

- lowercase letters, digits, `-`, and `_` only
- 1–64 characters
- match the folder name when possible

## Writing good instructions

1. State the goal in one sentence.
2. List the exact steps in order.
3. Mention required inputs and expected outputs.
4. Point to supporting files with relative paths.
5. Include a short verification step.
6. Avoid secrets, credentials, and machine-specific absolute paths.

## Creating the skill

1. Ask for the workflow name, the trigger conditions, and the desired output.
2. Draft the frontmatter `name` and `description` first.
3. Write concise imperative instructions.
4. Put long references or examples in `references/` instead of bloating
   `SKILL.md`.
5. Save the folder under `~/.ai-companion/skills/<name>/` so the app discovers
   it, then enable it in Settings → Skills.

## Verification

- `SKILL.md` starts with a valid `---` frontmatter block.
- `name` is lowercase and unique.
- The description explains *when* to use the skill.
- Every referenced file exists inside the skill folder.

# Character agent skills

Put a character's own agent skills in a profile folder:

```
agents/
└── minami-asuka/
    └── skills/
        └── roleplay/
            └── SKILL.md
```

The folder name can be the full character display name or a slug such as
`minami-asuka` / `minami_asuka`. Skills inside it are available only while
chatting with that character.

`SKILL.md` uses the same format as global skills:

```markdown
---
name: minami-roleplay
description: Roleplay guidance for Minami Asuka.
---

# Instructions

...
```

Other supported locations are `~/.ai-companion/agents`,
`~/.agents`, and any root listed in `AI_COMPANION_AGENTS_DIRS`.
See `docs/SKILLS.md` for details.

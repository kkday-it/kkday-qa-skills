# Agents Guide

This is a cross-tool agent guide for non-Claude agents (Codex CLI, OpenClaw, etc.).
For Claude Code, refer to `CLAUDE.md` for the full version with KKday-specific gotchas.

## Repo Purpose

KKday QA team's shared library of Agent Skills and Agent Teams.

## Setup

```bash
# Install dependencies (if any tool scripts exist)
# Currently no global deps required.

# Verify SKILL.md files
find skills -name "SKILL.md" -exec head -5 {} \;
```

## Conventions

- All `SKILL.md` files follow the open Agent Skills standard
- YAML frontmatter required: `name`, `description`
- Output for team-facing docs (Confluence, Slack): Traditional Chinese
- Internal progress files: English OK
- Never invoke destructive operations without human approval

## Test Commands

```bash
# Lint SKILL.md frontmatter
# (TBD - add a validator script in scripts/)
```

## Tier 1 Rules (apply to every task)

1. Atlassian MCP: call `getAccessibleAtlassianResources` first to get cloudId
2. KKday cloudId: `8b890302-cc52-42ce-a15e-697446426613`
3. Confluence writes prefer ADF over markdown for special blocks
4. Maintain `claude-progress.txt` for long tasks

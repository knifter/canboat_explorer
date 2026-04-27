# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow Modes

This project uses three custom slash commands to separate concerns:

| Command | Purpose | Can touch |
|---------|---------|-----------|
| `/design` | Discuss and document design decisions | Design docs only (`DESIGN*.md`, `SPEC*.md`, `design/`) |
| `/build` | Implement features | Source code and config; must follow design docs strictly |
| `/talk` | Discuss freely | Read-only; propose changes explicitly and wait for confirmation |

Always check which mode is active before taking action. When in doubt, default to `/talk` behavior: discuss and confirm before changing anything.

## Design Documents

Design specifications live in `DESIGN.md` and any files under `design/`. Read these before building anything.

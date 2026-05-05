# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is a greenfield repo for a **copy-trade system** (`copy_trader`). At the time of writing the only committed content is the README, LICENSE, `.gitignore`, and the OpenSpec workflow scaffolding under `openspec/` and the various `.<tool>/` directories. There is **no source code yet** — no build system, no test runner, no language commands to document. The Python-style `.gitignore` is the only signal about the intended stack; treat it as a hint, not a commitment.

When the user asks you to start building, expect to bootstrap the project structure (package layout, dependency manager, test runner) as the first concrete change.

## Working through OpenSpec

All non-trivial work in this repo is meant to flow through the **OpenSpec spec-driven workflow** before code is written. The `openspec` CLI must be on PATH; `openspec/config.yaml` declares `schema: spec-driven`.

Four stages, exposed as both slash commands and skills:

1. `/opsx:explore` — think through the problem before committing to a proposal.
2. `/opsx:propose <name-or-description>` — `openspec new change "<name>"` then generate `proposal.md`, `design.md`, `tasks.md` under `openspec/changes/<name>/`. Drive artifact order from `openspec status --change "<name>" --json` and pull per-artifact guidance from `openspec instructions <artifact-id> --change "<name>" --json`.
3. `/opsx:apply [<name>]` — read context files listed by `openspec instructions apply --change "<name>" --json`, then implement `tasks.md` one item at a time, flipping `- [ ]` → `- [x]` as you go.
4. `/opsx:archive` — finalize a completed change.

Key rules when authoring artifacts:

- The `context` and `rules` fields returned by `openspec instructions` are **constraints for you**, not content to paste into the artifact file. Never copy `<context>`, `<rules>`, or `<project_context>` blocks into the output.
- Use the returned `template` as the structure of the file you write.
- Always read completed dependency artifacts before generating a new one.
- Re-run `openspec status --change "<name>" --json` after each artifact to confirm `applyRequires` items are `done`.

The same skill definitions are mirrored across `.claude/`, `.cursor/`, `.codex/`, `.gemini/`, `.qoder/`, `.qwen/`, `.trae/` so the workflow works in any of those harnesses — keep them in sync if you edit one.

## When there is no spec yet

If the user asks for code changes without an existing OpenSpec change, default to suggesting `/opsx:propose` first rather than writing code directly. Skipping the proposal stage is acceptable only for trivial fixes (typos, doc tweaks, gitignore adjustments) or when the user explicitly says so.

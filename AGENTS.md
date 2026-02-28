# Repository Guidelines

## Project Structure & Module Organization
Source code lives at `server.py`, which implements the MCP stdio server that surfaces every pack tool. Packs sit under `packs/`, each in its own versioned directory (for example `packs/funnel.activation.0.1`). Inside a pack you will find `pack.json`, the JSON Schemas under `schemas/`, deterministic prompt templates in `prompts/`, and sample contexts in `examples/`. Keep new packs aligned to this structure so the registry autoloader in `server.py` can discover them.

## Build, Test, and Development Commands
Create the virtual environment once with `python -m venv .venv` and activate it via `source .venv/bin/activate`. Install dependencies locally using `pip install "mcp[cli]"`. Run the server during development with `python server.py --packs-dir ./packs` (override `PACKS_DIR` to test alternate pack trees). When integrating with Codex MCP tooling, register it through `codex mcp add pack-registry -- python server.py --packs-dir ./packs` and verify via `/mcp` inside the TUI.

## Coding Style & Naming Conventions
Target Python 3.11+ with `mcp`'s type-friendly APIs. Use four-space indentation, type hints, and dataclasses as already established in `server.py`. Keep helper names descriptive (e.g., `sanitize_tool_token`). JSON/Markdown assets should remain ASCII and deterministic so packs stay reproducible; prefer kebab-case for pack IDs and snake_case for tool helpers. Run `ruff` or `black` locally if you add them, but commit the formatted output, not the tool configuration files, unless shared across packs.

## Testing Guidelines
There is no automated suite yet, so rely on smoke tests through the MCP CLI. After edits, run `python server.py --packs-dir ./packs` and issue representative `call_tool` requests (describe/requirements/plan/execute/validate) using `codex mcp call` or your MCP client to ensure schemas are satisfied and `needs_context` is reported correctly. When you add formal tests, mirror pack directories under `tests/` and name files `test_<module>.py` so pytest discovery remains predictable. Maintain sample contexts in `packs/<pack>/examples/` that cover both minimal and full inputs.

## Commit & Pull Request Guidelines
Use concise, present-tense commit messages (`feat: add pack loader caching`) because there is no existing history to mirror. Each PR should call out the affected packs, describe schema or prompt changes, list validation commands run, and attach any relevant screenshots/logs from Codex MCP verification. Link to tracking issues or discussions so downstream tool callers understand why deterministic templates changed.

## Security & Configuration Notes
Avoid checking secrets into `pack.json` or prompts; prefer environment variables such as `PACKS_DIR`, `MCP_SERVER_NAME`, and `MCP_SERVER_VERSION` for deployment-specific values. If you distribute packs, document their `required_fields` clearly so remote agents never guess missing context.

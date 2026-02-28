low-level MCP server in Python that:

Loads your pack directories from disk (pack.json + schemas + prompts)

Exposes each pack method as a separate MCP tool named pack.<pack_id>.<method>

Enforces required-context checks and returns needs_context plus missing_fields instead of guessing

Uses structured output (with outputSchema) so clients can validate and treat packs as interchangeable tool heads

It is intentionally deterministic. The tools return structured “plans”, “executions”, and “validations” using rule-based templates. Any tool-calling LLM can then refine outputs if desired, but the server itself is stable and swappable.

Install
python -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"

The official Python SDK supports low-level MCP servers over stdio with structured output schemas.

Run (stdio transport)
python server.py --packs-dir ./packs

If you are using a client like Claude Code or an Agents SDK MCP client, configure it to launch this process as a stdio MCP server.

Each pack method is a separate tool with its own inputSchema and outputSchema, enabling discovery and interchangeability via list_tools and call_tool.

The server uses the MCP low-level server pattern over stdio, which is the canonical baseline transport and matches the official Python SDK examples.

Structured output schemas are first-class for tools in newer protocol revisions, so callers can treat outputs as machine objects rather than text blobs.




packs/
  funnel.activation-trial-to-paid.v0_1/
    pack.json
    schemas/
      context.schema.json
      output.schema.json
      events.schema.json
    prompts/
      plan.md
      execute.md
      validate.md
    examples/
      context.minimal.json
      context.full.json
      output.example.json


How to expose as interchangeable MCP tools

If you run an MCP server, expose each pack method as a tool. The important part is that every pack has the same method names and stable I/O:

pack.<pack_id>.describe

pack.<pack_id>.requirements

pack.<pack_id>.plan

pack.<pack_id>.execute

pack.<pack_id>.validate

All tools accept JSON, and the pack supplies JSON Schema references for validation. That is what makes them interchangeable “tool heads”.


----
## Setup

    python -m venv .venv
    source .venv/bin/activate
    pip install "mcp[cli]"

    python server.py --packs-dir ./packs

    codex mcp add pack-registry -- python server.py --packs-dir ./pack

Then open Codex and check MCP servers:

In the Codex TUI: /mcp shows active MCP servers.

Or run codex mcp --help to see MCP commands.


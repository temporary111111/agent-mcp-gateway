# Agent Gateway

A production-oriented **local agent gateway** that gives ChatGPT Web
(**GPT-5.6 Sol**) deterministic access to your local machine through the
**Model Context Protocol (MCP)** over Streamable HTTP.

**Direct mode is the default architecture.** The gateway exposes precise,
deterministic MCP tools (filesystem, search, process, git) that run inside
operator-authorized directories. **GPT-5.6 Sol is the only reasoning agent**:
it owns the planning and decision loop, and the gateway executes its tool
calls — no second LLM is ever invoked by the gateway. The gateway works with
**no OpenCode server and no model/provider configured**.

Optional **OpenCode agent mode** (disabled by default) additionally delegates
whole tasks to a local [OpenCode](https://opencode.ai) agent for longer,
autonomous work.

```
ChatGPT Web (GPT-5.6 Sol)  <-- the reasoning agent
        │
        │  MCP over HTTPS (Streamable HTTP)
        ▼
Cloudflare Quick Tunnel
        │
        ▼
Agent Gateway  http://127.0.0.1:8000/mcp
        │
        ├── Direct mode (default): deterministic tools
        │       workspace_open / file_* / code_search / process_run / git_*
        │       (filesystem, search, process, git — no OpenCode, no LLM)
        │
        └── OpenCode agent mode (optional, ENABLE_OPENCODE_AGENT=true)
                agent_start_task / agent_status / agent_messages / agent_diff
                → OpenCode  http://127.0.0.1:4096  (localhost only)
        │
        ▼
Local system / repositories / tools
```

This project replaces the earlier `chatgpt-local-repo-mcp` prototype with a
clean, tested, extensible foundation. It is **not** a copy of that prototype.

---

## Why a gateway

- **ChatGPT cannot reach your localhost.** A tunneled MCP endpoint is the
  verified bridge.
- **Direct deterministic tools beat a second agent.** For most file, search,
  process, and git work, the gateway's primitives are exact, fast, and need no
  extra model. GPT-5.6 Sol keeps the reasoning; the gateway keeps the machine.
- **You can grow.** OpenCode (or a future Codex/Claude Code adapter) can be
  enabled behind the same stable MCP interface for autonomous task delegation.

## Trust boundaries

| Boundary | Trust |
| --- | --- |
| ChatGPT ⇄ Cloudflare tunnel | Public; HTTPS |
| Cloudflare tunnel ⇄ gateway | Local tunnel; MCP transport security + optional bearer token |
| Gateway ⇄ OpenCode (when enabled) | Localhost only (`127.0.0.1:4096`), optional Basic Auth |
| Gateway ⇄ filesystem | Only directories explicitly listed in `AGENT_ALLOWED_ROOTS` |

The gateway's `/mcp` endpoint is the only public surface. Remote callers
cannot:

- access directories outside the configured allowed roots,
- run unrestricted shell commands (commands are opt-in via
  `AGENT_ENABLE_COMMANDS` and time-bounded),
- delegate tasks to OpenCode unless the operator enabled that mode,
- auto-approve their own permission requests (never implemented),
- reach the gateway without the bearer token when `AGENT_GATEWAY_TOKEN` is set.

## Architecture

```
tools/  (MCP tools)            thin, callable by GPT-5.6 Sol
   │
   ├── tools/direct.py         deterministic primitives (default mode)
   │       workspace_open → workspace_tree / file_read / file_stat /
   │       file_find / code_search / file_write / file_replace /
   │       file_apply_patch / process_run / git_status / git_diff /
   │       git_log / git_show
   │
   ├── workspaces/             WorkspaceManager: opaque ws_ IDs bound to
   │                           allowed roots; every path re-validated
   │
   └── services/delegation.py  OpenCode mode: session lifecycle + registry
           │
           ▼
       executors/base.py       Executor interface (health, sessions, prompts,
           │                   status, messages, diffs, abort, permissions)
           ▼
       executors/opencode/     OpenCodeExecutor → OpenCodeClient → HTTP API
```

Every module depends on the layer below it; the MCP tools never touch `httpx`
directly.

## MCP tools — Direct mode (default, no OpenCode, no model)

The direct tools are available whenever the gateway runs. They only operate
inside workspaces opened via `workspace_open` (which requires the directory to
be inside `AGENT_ALLOWED_ROOTS`).

| Tool | Read-only | Purpose |
| --- | --- | --- |
| `workspace_open` | no | Validate a directory and bind it to an opaque `ws_...` ID |
| `workspace_tree` | yes | Directory tree listing (depth/entry caps) |
| `file_read` | yes | Read a file (with size cap and offset/limit) |
| `file_stat` | yes | Metadata for a file or directory |
| `file_find` | yes | Find files by name/glob under a directory |
| `code_search` | yes | Case-insensitive content search with line hits |
| `file_write` | no | Create/replace a file |
| `file_replace` | no | Exact old-string → new-string replacement (single occurrence) |
| `file_apply_patch` | no | Unified-diff patch with context verification |
| `process_run` | no | Run a command inside the workspace (opt-in, bounded) |
| `git_status` | yes | Working-tree status |
| `git_diff` | yes | Working-tree diff |
| `git_log` | yes | Commit history |
| `git_show` | yes | Commit/file content at a revision |

All direct tools: validated paths (no absolute paths, no `..`, no symlink
escapes), size caps on reads, entry caps on listings, strict relative-path
arguments inside the bound workspace.

### Direct-mode loop (as ChatGPT uses it)

```
workspace_open("C:\...\project")
   → ws_abc123
file_read(ws_abc123, "src/main.py")          → current content
file_apply_patch(ws_abc123, "src/main.py", <<<diff>>>)   → patch applied
process_run(ws_abc123, "pytest -q", timeout=60)         → verification
git_diff(ws_abc123)                           → review the change set
```

## MCP tools — OpenCode agent mode (optional)

Enabled only with `ENABLE_OPENCODE_AGENT=true`. Adds the generic delegation
lifecycle plus OpenCode diagnostics:

| Tool | Read-only | Backend operation |
| --- | --- | --- |
| `gateway_health` | yes | health checks of gateway + each executor |
| `agent_executors` | yes | list configured executors and capabilities |
| `agent_start_task` | no | create session + async `prompt_async` |
| `agent_continue` | no | async follow-up prompt on an existing session |
| `agent_status` | yes | session state (busy / idle / retry) + pending permissions |
| `agent_session` | yes | session metadata + change summary |
| `agent_messages` | yes | message history with text and tool-call parts |
| `agent_diff` | yes | per-file diffs the agent produced |
| `agent_abort` | no | abort a busy session |
| `agent_pending_permissions` | yes | list permission requests awaiting a decision |
| `agent_reply_permission` | no | reply `once` / `always` / `reject` |
| `opencode_health` | yes | detailed backend health/version/url |
| `opencode_agents` | yes | list OpenCode agents |
| `opencode_providers` | yes | list model providers (no secrets) |

### Delegation lifecycle

```
agent_start_task(executor, task, directory)
      │
      ▼  returns session ID immediately (async dispatch)
agent_status(session_id)
      │
      ├── busy / retry   → wait and poll again
      │
      └── idle + completed
            ├── agent_messages(session_id)   → read what the agent did
            ├── agent_diff(session_id)        → review file changes
            ├── agent_continue(session_id, followup)  → keep going
            └── agent_abort(session_id)       → stop runaway work
```

Long-running agent work uses OpenCode's async prompt API
(`POST /session/{id}/prompt_async`). The gateway returns immediately and never
holds an MCP request open while the agent works. Completion is reported only
when the session is idle **and** the last assistant turn finished with
`finish="stop"`.

Every operation on an existing session re-verifies that the session's real
directory is still inside `AGENT_ALLOWED_ROOTS` (fail closed).

### Permission workflow

The gateway **never** auto-approves. When the agent needs approval it raises a
permission request, the supervisor sees it via `agent_status` /
`agent_pending_permissions`, and a human decides via
`agent_reply_permission`. Allowed replies: `once`, `always`, `reject`.

## Security model

- **Transport auth**: when `AGENT_GATEWAY_TOKEN` is set, every request to
  `/mcp` must carry `Authorization: Bearer <token>` (constant-time compare).
  Requests without a valid token get 401.
- **Directory security**: `AGENT_ALLOWED_ROOTS` is a semicolon-separated list
  of absolute paths. If unset, every directory is rejected (fail closed). Each
  candidate is canonicalized, must exist (for task roots), must not be a
  filesystem root, and must sit **inside** an allowed root. Traversal, symlink
  escapes, and sibling-prefix spoofing (`sample` vs `sample-evil`) are
  rejected; comparisons are case-insensitive on Windows.
- **No unrestricted shell by default**: `process_run` requires
  `AGENT_ENABLE_COMMANDS=true` and enforces a timeout (default 300 s).
- **Bounded I/O**: read size, tree entries, search results, and process output
  are capped; huge payloads are truncated instead of streamed unbounded.
- **OpenCode mode is opt-in** (`ENABLE_OPENCODE_AGENT=true`); without it the
  `agent_*` tools are not registered and no backend is contacted.
- **Permissions never auto-approved** in OpenCode mode.
- **Network**: OpenCode stays localhost-only. Only `/mcp` is tunneled.
  `PUBLIC_MCP_HOST` allow-lists the tunnel host while keeping DNS-rebinding
  protection enabled.
- **Secrets**: passwords and Authorization headers are never logged; the
  config summary masks the password; provider model lists exclude keys.

## Installation

Requirements: Python 3.11+ (tested on 3.14), git. OpenCode CLI is **only**
needed for the optional OpenCode mode.

```powershell
cd C:\Users\dev\Desktop\chatgpt-like\chatgpt-agent-gateway
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and edit, or set environment variables directly:

```powershell
$env:MCP_PORT = "8000"
$env:PUBLIC_MCP_HOST = "your-tunnel.trycloudflare.com"   # optional
$env:AGENT_ALLOWED_ROOTS = "C:\Users\dev\Desktop\sample-repo;C:\Users\dev\Desktop\projects"
$env:AGENT_GATEWAY_TOKEN = "generate-a-long-random-token"   # recommended
$env:AGENT_ENABLE_COMMANDS = "true"   # allow process_run
$env:LOG_LEVEL = "INFO"
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | Gateway bind address (keep localhost) |
| `MCP_PORT` | `8000` | Gateway port |
| `PUBLIC_MCP_HOST` | *(none)* | Public hostname (e.g. Cloudflare tunnel) added to MCP transport security |
| `AGENT_ALLOWED_ROOTS` | *(empty)* | Semicolon-separated allowed directories; empty ⇒ **fail closed** |
| `AGENT_GATEWAY_TOKEN` | *(empty)* | Bearer token for `/mcp`; empty ⇒ no token required (localhost only) |
| `AGENT_INSECURE_NO_TOKEN_OPT_OUT` | `false` | Required to run without a token when `PUBLIC_MCP_HOST` is set (dangerous) |
| `AGENT_ENABLE_COMMANDS` | `false` | Enable `process_run` |
| `AGENT_PROCESS_TIMEOUT_MAX` | `300` | Max seconds a `process_run` may take |
| `AGENT_MAX_READ_BYTES` | `1_000_000` | Cap for `file_read` output |
| `AGENT_MAX_TREE_ENTRIES` | `1000` | Cap for `workspace_tree` entries |
| `AGENT_MAX_SEARCH_RESULTS` | `200` | Cap for `code_search` results |
| `AGENT_MAX_PROCESS_OUTPUT_BYTES` | `200_000` | Cap for `process_run` output |
| `ENABLE_OPENCODE_AGENT` | `false` | Enable optional OpenCode agent mode |
| `OPENCODE_URL` | `http://127.0.0.1:4096` | Local OpenCode headless server |
| `OPENCODE_USERNAME` / `OPENCODE_PASSWORD` | *(empty)* | Optional Basic Auth for OpenCode |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Running the gateway

```powershell
agent-gateway
# or
python -m agent_gateway.server
```

Local MCP endpoint: `http://127.0.0.1:8000/mcp`

## Exposing via Cloudflare

The gateway's transport security keeps DNS-rebinding protection enabled and
allows localhost plus the hostname you set in `PUBLIC_MCP_HOST`. Start a Quick
Tunnel pointing at `http://127.0.0.1:8000`:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Take the printed `https://<id>.trycloudflare.com`, set it as `PUBLIC_MCP_HOST`,
then restart the gateway. **Set `AGENT_GATEWAY_TOKEN`** — the gateway refuses
to expose a tokenless `/mcp` publicly unless you explicitly set
`AGENT_INSECURE_NO_TOKEN_OPT_OUT=true`. Security checks are never disabled to
make the tunnel work; the public host is explicitly allow-listed instead.

## Running OpenCode (optional agent mode)

```powershell
opencode serve --port 4096 --hostname 127.0.0.1
```

Verify: `Invoke-RestMethod http://127.0.0.1:4096/global/health`

## Testing

```powershell
pytest                     # default suite: direct mode only (150 passed, 9 skipped)
pytest tests/unit          # unit tests — no services needed
$env:ENABLE_OPENCODE_AGENT = "true"
pytest                     # full suite incl. OpenCode mode (158 passed, 1 skipped)
```

The e2e suite starts a real gateway process on a temporary port and drives it
over MCP-over-HTTP with the exact protocol ChatGPT uses. Two flavors:

- `tests/e2e/test_direct_e2e.py` — **direct mode with no OpenCode and no
  model**: 401 without token, tool list, workspace → read → patch → write →
  process → git diff, and rejection of unauthorized directories. Runs in the
  default suite.
- `tests/e2e/test_opencode_e2e.py` — OpenCode mode (gated on
  `ENABLE_OPENCODE_AGENT=true` and a live server): read-only delegated task
  (repository verified byte-for-byte unmodified) plus the full MCP protocol
  flow including error cases.

## Repository layout

```
src/agent_gateway/
├── config.py            typed configuration (env-driven, validated)
├── errors.py            gateway error taxonomy
├── logging.py           redacted logging
├── security/
│   ├── paths.py         allowed-roots enforcement
│   └── auth.py          bearer-token middleware (constant-time compare)
├── workspaces/          WorkspaceManager: ws_ IDs, per-workspace validation
├── direct/              deterministic primitives (filesystem, search,
│   │                    process, git) shared by the direct tools
├── executors/
│   ├── base.py          Executor interface
│   └── opencode/        OpenCode client, models, errors, executor
├── services/delegation.py  OpenCode orchestration + session registry
├── tools/               MCP tool registration (direct, gateway, delegation,
│   │                    permissions, opencode)
└── server.py            MCP server assembly + entry point
```

## Future executor architecture

Add a new backend by implementing `executors/base.py`, registering it in
`executors/__init__.py`, and adding any backend-specific diagnostic tools in
`tools/`. The generic `agent_*` tools and the delegation service require no
changes. No fake Codex/Claude adapters are shipped.

## Limitations

- The OpenCode session registry is in-memory; a gateway restart forgets which
  directories sessions came from (OpenCode itself persists sessions by ID).
- Direct-mode workspaces (`ws_...`) also expire on gateway restart; reopen
  them with `workspace_open`.
- `file_apply_patch` requires exact context matches; no fuzzy application.
- OpenCode API is consumed as a superset of the v1 OpenAPI paths; future
  backend versions should be re-verified against their own `/doc`.
- The `always` permission reply is supported at the protocol level; operators
  may want to disable it globally to enforce per-run approvals.
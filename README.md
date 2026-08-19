# Agent Gateway

A production-oriented, extensible **local agent gateway** that lets ChatGPT Web
delegate coding and system work to **local agent executors** (currently
[OpenCode](https://opencode.ai)) through the **Model Context Protocol (MCP)**
over Streamable HTTP.

```
ChatGPT Web (GPT-5.6 Sol)
        │
        │  MCP over HTTPS
        ▼
Cloudflare Quick Tunnel
        │
        ▼
Agent Gateway  http://127.0.0.1:8000/mcp
        │
        ├── Executor Adapter: OpenCode  http://127.0.0.1:4096   [implemented]
        ├── Executor Adapter: Codex        (future)
        └── Executor Adapter: Claude Code (future)
        │
        ▼
Local system / repositories / tools
```

This project replaces the earlier `chatgpt-local-repo-mcp` prototype with a
clean, tested, extensible foundation. It is **not** a copy of that prototype;
it is a from-scratch design built on the lessons it proved.

---

## Why a gateway

- **ChatGPT cannot reach your localhost.** A tunneled MCP endpoint is the
  verified bridge.
- **You want a human-in-the-loop.** The gateway never auto-approves permission
  requests from the agent.
- **You want to grow.** One gateway can later expose several executors
  (OpenCode today, Codex and Claude Code later) behind one stable MCP
  interface.

## Trust boundaries

| Boundary | Trust |
| --- | --- |
| ChatGPT ⇄ Cloudflare tunnel | Public; HTTPS |
| Cloudflare tunnel ⇄ gateway | Local tunnel; validated by MCP transport security |
| Gateway ⇄ OpenCode | Localhost only (`127.0.0.1:4096`), optional Basic Auth |
| Gateway ⇄ filesystem | Only directories explicitly listed in `AGENT_ALLOWED_ROOTS` |

OpenCode itself is **never** exposed publicly. Only the gateway's `/mcp`
endpoint is tunneled. Remote callers cannot:

- run an unrestricted shell (the agent decides how to use shell/files/git),
- access directories outside the configured allowed roots,
- auto-approve their own permission requests.

## Architecture

```
tools/  (MCP tools)            thin, describe the generic agent lifecycle
   │
   ▼
services/delegation.py         orchestration: session lifecycle + session registry
   │
   ▼
executors/base.py              Executor interface (health, sessions, prompts,
   │                           status, messages, diffs, abort, permissions)
   ▼
executors/opencode/            OpenCodeExecutor → OpenCodeClient → HTTP API
```

Every module depends on the layer below it; the MCP tools never touch `httpx`
directly.

## Delegation lifecycle

```
agent_start_task(executor, task, directory)
      │
      ▼  returns session ID immediately (async dispatch)
agent_status(session_id)
      │
      ├── busy / retry   → wait and poll again
      │
      └── idle
            ├── agent_messages(session_id)   → read what the agent did
            ├── agent_diff(session_id)        → review file changes
            ├── agent_continue(session_id, followup)  → keep going
            └── agent_abort(session_id)       → stop runaway work
```

Long-running agent work uses OpenCode's async prompt API
(`POST /session/{id}/prompt_async`). The gateway returns immediately and never
holds an MCP request open while the agent works.

## Installation

Requirements: Python 3.11+ (tested on 3.14), the OpenCode CLI, git.

```powershell
cd C:\Users\dev\Desktop\chatgpt-agent-gateway
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and edit. The gateway also reads plain
environment variables, so you can set them directly in PowerShell:

```powershell
$env:MCP_PORT = "8000"
$env:PUBLIC_MCP_HOST = "your-tunnel.trycloudflare.com"   # optional
$env:OPENCODE_URL = "http://127.0.0.1:4096"
$env:AGENT_ALLOWED_ROOTS = "C:\Users\dev\Desktop\sample-repo;C:\Users\dev\Desktop\projects"
$env:LOG_LEVEL = "INFO"
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | Gateway bind address (keep localhost) |
| `MCP_PORT` | `8000` | Gateway port |
| `PUBLIC_MCP_HOST` | *(none)* | Public hostname (e.g. Cloudflare tunnel) added to MCP transport security |
| `OPENCODE_URL` | `http://127.0.0.1:4096` | Local OpenCode headless server |
| `OPENCODE_USERNAME` | `opencode` | Basic Auth username |
| `OPENCODE_PASSWORD` | *(empty)* | Basic Auth password; empty ⇒ no auth |
| `AGENT_ALLOWED_ROOTS` | *(empty)* | Semicolon-separated allowed task directories; empty ⇒ **fail closed** |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

Passwords are never logged and never appear in gateway output.

### Directory security

- No `AGENT_ALLOWED_ROOTS` ⇒ no task directory is allowed (fail closed).
- Every task directory is canonicalized and must exist and live **inside** one
  of the allowed roots.
- Traversal (`..`), symlink escapes, filesystem roots, and case variants are
  rejected. On Windows comparisons are case-insensitive.
- The gateway never defaults to `C:\` or the whole user profile.

## Running OpenCode

Start the headless server on localhost (with or without Basic Auth):

```powershell
# no auth (localhost only)
opencode serve --port 4096 --hostname 127.0.0.1

# with auth
$env:OPENCODE_SERVER_PASSWORD = "your-password"
$env:OPENCODE_SERVER_USERNAME = "opencode"
opencode serve --port 4096 --hostname 127.0.0.1
```

Verify: `Invoke-RestMethod http://127.0.0.1:4096/global/health`

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
then restart the gateway. Security checks are **not** disabled to make the
tunnel work; the public host is explicitly allow-listed instead.

## MCP tools

Generic lifecycle tools (executor name defaults to `opencode`):

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

OpenCode-specific diagnostics (not part of the generic lifecycle):

| Tool | Read-only | Purpose |
| --- | --- | --- |
| `opencode_health` | yes | detailed backend health/version/url |
| `opencode_agents` | yes | list OpenCode agents |
| `opencode_providers` | yes | list model providers (no secrets) |

### Permission workflow

The gateway **never** auto-approves. When the agent needs approval it raises a
permission request, the supervisor sees it via `agent_status` /
`agent_pending_permissions`, and a human decides via
`agent_reply_permission`. Allowed replies: `once`, `always`, `reject`
(verified against the OpenCode OpenAPI).

## Testing

```powershell
pytest tests/unit          # unit tests (mock backend) - no services needed
pytest tests/integration   # live OpenCode backend (skips if not reachable)
pytest tests/e2e           # full stack incl. MCP-over-HTTP protocol test
pytest                     # everything
```

The e2e suite starts a real gateway process on a temporary port, connects to it
with the MCP client library (the same protocol ChatGPT uses), and runs a
read-only task on the sample repository, verifying that **no file is modified**.

## Future executor architecture

Add a new backend by implementing `executors/base.py` (see the README of
`agent_gateway.executors.base`), registering it in
`executors/__init__.py`, and adding any backend-specific diagnostic tools in
`tools/`. The generic `agent_*` tools and the delegation service require no
changes. No fake Codex/Claude adapters are shipped.

## Repository layout

```
src/agent_gateway/
├── config.py            typed configuration (env-driven, validated)
├── errors.py            gateway error taxonomy
├── logging.py           redacted logging
├── security/paths.py    allowed-roots enforcement
├── executors/
│   ├── base.py          Executor interface
│   └── opencode/        OpenCode client, models, errors, executor
├── services/delegation.py  orchestration + session registry
├── tools/               MCP tool registration (gateway, delegation, permissions, opencode)
└── server.py            MCP server assembly + entry point
```

## Limitations (V1)

- Executor registry is in-memory; a gateway restart forgets which directory
  sessions came from (OpenCode itself persists sessions by ID).
- Only read-backs of agent results; no structured result schema yet.
- OpenCode API is consumed as a superset of the v1 OpenAPI paths; future
  backend versions should be re-verified against their own `/doc`.
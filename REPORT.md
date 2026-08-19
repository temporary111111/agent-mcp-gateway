# Execution Report

## 1. Environment inspected

- **Host**: Windows (win32), PowerShell 5.1.
- **Python**: 3.14.7 (global and old-project venv); pip 26.2.1; git 2.55.0.
- **MCP SDK**: `mcp` 2.0.0 (pinned `mcp>=2,<3`), `httpx` 0.28.1, `pydantic` 2.13.4.
- **OpenCode**: CLI 1.18.18 installed via npm (`C:\Users\dev\AppData\Roaming\npm`).
  Live server verified at `http://127.0.0.1:4096` returning
  `{"healthy": true, "version": "1.18.18"}`.
- **OpenCode OpenAPI** (478 KB) fetched from `http://127.0.0.1:4096/doc` and
  inspected before any implementation. Verified: session IDs `^ses`, message IDs
  `^msg`, permission IDs `^per`, prompt_async returns 204, permission replies
  are `once | always | reject`, `/session/status` returns a map of
  `{sessionID: {type: busy|idle|retry}}` (idle sessions disappear from the
  map), `/permission` returns pending permission requests, `/session/{id}/diff`
  returns `SnapshotFileDiff[]`.
- **Live behavioral tests performed before implementation**: create session,
  async prompt dispatch, status polling (observed `busy` then empty), message
  retrieval, diff retrieval (empty for read-only task), permission listing.

Note on workspace location: the prompt named `C:\Users\dev\Desktop\chatgpt-agent-gateway`;
that path does not exist. The actual empty greenfield workspace created by the
launcher is `C:\Users\dev\Desktop\chatgpt-like\chatgpt-agent-gateway`, which is
where everything was built. The parent directory is an unrelated git repo, so
the gateway was initialized as its own nested git repository.

## 2. Old prototype lessons discovered

Read-only inspection of `C:\Users\dev\Desktop\chatgpt-local-repo-mcp` revealed
the following verified behaviors worth preserving:

1. **Cloudflare Host validation**: the prototype handled the public host by
   building `TransportSecuritySettings(enable_dns_rebinding_protection=True,
   allowed_hosts=[localhost..., PUBLIC_HOST...], allowed_origins=...)` and
   passing it to `mcp.run(transport="streamable-http", stateless_http=True,
   json_response=True, transport_security=...)`. This is the correct, proven
   way to make a tunnel work without disabling security.
2. `PUBLIC_MCP_HOST` normalization (strip scheme, keep netloc).
3. MCP SDK 2.x decorator style (`@mcp.tool(title=..., annotations=...)`) and
   raising `ValueError` from tools to return errors to the client.
4. OpenCode health endpoint shape `{healthy, version}`.
5. The prototype's direct repo tools (`repo_overview`, `repo_tree`, `read_file`,
   `search_code`) were hard-coded to a single `REPO_ROOT`. Those are **not**
   reproduced; see section 17.

## 3. Architecture chosen

```
ChatGPT Web / GPT-5.6 Sol
        |
        | MCP over HTTPS (Streamable HTTP)
        v
Cloudflare Quick Tunnel
        |
        v
Agent Gateway  http://127.0.0.1:8000/mcp
        |   MCP tools (gateway / delegation / permissions / opencode-diagnostics)
        v
services/delegation.py  (orchestration + in-memory session registry)
        |
        +-- Executor interface (executors/base.py)
        |       |
        |       +-- OpenCodeExecutor (executors/opencode/executor.py)
        |               |
        |               +-- OpenCodeClient (executors/opencode/client.py)  -> 127.0.0.1:4096
        |
        +-- (future) CodexExecutor / ClaudeCodeExecutor
        |
        v
security/paths.py  (AGENT_ALLOWED_ROOTS enforcement, fail closed)
        |
        v
Local filesystem / repositories
```

## 4. Why this architecture was chosen

- **Clean dependency direction**: tools → service → executor interface →
  concrete executor → HTTP client. The MCP tools never touch `httpx`, so a new
  backend only requires a new Executor implementation plus registry entry.
- **Small, honest abstraction**: the Executor interface covers only operations
  that exist for any delegated coding agent (health, sessions, prompts, status,
  messages, diffs, abort, permissions). No fake Codex/Claude implementations
  are shipped; extension points exist without pretending.
- **Async-first**: `prompt_async` dispatches immediately and returns; no MCP
  request is held open for long agent work, and no infinite polling loops exist
  inside tools.
- **Fail-closed security**: directory access requires explicit allowed roots;
  no `C:\` or whole-profile defaults.
- **Testability**: the client accepts an injected `httpx.AsyncClient`
  (`MockTransport`), the executor accepts an injected client, and the service
  accepts injected executors. Unit tests need no backend; integration/e2e tests
  run against the real local OpenCode.

## 5. Package/module structure

```
src/agent_gateway/
├── __init__.py              version
├── config.py                Config dataclass from env (validated)
├── errors.py                GatewayError taxonomy (11 codes)
├── logging.py               redacted logging
├── server.py                MCP server assembly, transport security, entry point
├── __main__.py              python -m agent_gateway
├── security/paths.py        PathPolicy: allowed-roots enforcement
├── executors/
│   ├── base.py              Executor ABC + data objects
│   ├── __init__.py          registry builder + require_executor
│   └── opencode/
│       ├── models.py        pydantic models for the OpenCode API
│       ├── errors.py        httpx/pydantic -> GatewayError mapping
│       ├── client.py        OpenCodeClient (HTTP, auth, timeouts)
│       └── executor.py      OpenCodeExecutor (Executor impl)
├── services/delegation.py   DelegationService + DelegatedSession registry
└── tools/
    ├── helpers.py           tool_handler wrapper (error -> ValueError)
    ├── gateway.py           gateway_health, agent_executors
    ├── delegation.py        agent_start_task/continue/status/session/messages/diff/abort
    ├── permissions.py       agent_pending_permissions, agent_reply_permission
    └── opencode.py          opencode_health, opencode_agents, opencode_providers
tests/
├── conftest.py              fixtures + skip markers
├── unit/                    config, paths, client, executor, delegation, transport security
├── integration/             live OpenCode backend tests
└── e2e/                     service e2e + full MCP-over-HTTP protocol test
```

## 6. Executor abstraction

`Executor` (ABC) in `executors/base.py`:

- `health()` -> ExecutorHealth
- `create_session(directory, title, agent)` -> SessionInfo
- `send_prompt(session_id, task, agent, directory)` -> None (async dispatch)
- `status(session_id)` -> SessionStatusInfo (`busy | idle | retry`)
- `session_info(session_id)` -> SessionInfo
- `messages(session_id, limit, before)` -> list[MessageInfo]
- `diff(session_id, message_id)` -> list[FileDiff]
- `abort(session_id)` -> bool
- `pending_permissions()` -> list[PermissionRequestInfo]
- `reply_permission(request_id, reply, message)` -> bool
- `capabilities()` -> set[str] (extra backend-specific tools)

## 7. OpenCode implementation

- `OpenCodeClient` centralizes base URL, optional HTTP Basic Auth (only when a
  password is configured), separate connect/read/write/pool timeouts, typed
  models, and maps transport/status/validation failures into gateway errors.
- Uses the verified live endpoints: `GET /global/health`, `POST /session`,
  `POST /session/{id}/prompt_async` (204), `GET /session/status`,
  `GET /session/{id}`, `GET /session/{id}/message`, `GET /session/{id}/diff`,
  `POST /session/{id}/abort`, `GET /permission`,
  `POST /permission/{id}/reply`, `GET /agent`, `GET /provider`.
- Session IDs are validated (`^ses`) before any backend call; message IDs
  (`^msg`) and permission IDs (`^per`) are validated where provided.
- A 400 from `prompt_async` is mapped to `session_busy` when the status map says
  the session is busy, otherwise to a generic backend error.
- Permission replies are restricted to `once | always | reject`.
- Diagnostics (`agents`, `providers`) strip hidden agents and never include
  provider secrets.

## 8. MCP tool interface

| Tool | Description | Read-only | Backend operation |
| --- | --- | --- | --- |
| `gateway_health` | Gateway + executor reachability | yes | health checks |
| `agent_executors` | List executors + capabilities | yes | registry |
| `agent_start_task` | Create session in allowed dir + async dispatch | no | POST /session + prompt_async |
| `agent_continue` | Async follow-up on existing session | no | prompt_async |
| `agent_status` | busy/idle/retry + pending permissions | yes | /session/status, /session/{id}, /permission |
| `agent_session` | Session metadata + summary | yes | /session/{id} |
| `agent_messages` | Message history with text/tool parts | yes | /session/{id}/message |
| `agent_diff` | Per-file diffs | yes | /session/{id}/diff |
| `agent_abort` | Abort busy session | no | /session/{id}/abort |
| `agent_pending_permissions` | Pending permission requests | yes | /permission |
| `agent_reply_permission` | Reply once/always/reject | no | /permission/{id}/reply |
| `opencode_health` | OpenCode health/version/url detail | yes | /global/health |
| `opencode_agents` | OpenCode agent list | yes | /agent |
| `opencode_providers` | Model providers (no secrets) | yes | /provider |

Design decisions:

- `executor` is an explicit parameter on generic tools with a default of
  `opencode`. Explicit-with-default keeps the interface future-proof without
  burdening the caller today.
- Session ID is the only handle needed for follow-ups: OpenCode resolves the
  directory itself server-side (verified live), so the in-memory session
  registry is a convenience for audit/logging, not a correctness requirement.

## 9. Security model

- **Directory security**: `AGENT_ALLOWED_ROOTS` is a semicolon-separated list of
  absolute paths. If unset, all task directories are rejected (fail closed).
  Each candidate is canonicalized (`resolve`), must exist, must not be a
  filesystem root, and must be inside an allowed root. Traversal, symlink
  escapes, and sibling-prefix spoofing (`sample` vs `sample-evil`) are
  rejected; comparisons are case-insensitive on Windows.
- **No unrestricted shell**: the gateway exposes no generic `shell(command)`
  tool. Low-level system interaction happens inside the delegated agent, which
  is subject to OpenCode's own permission system.
- **Permissions never auto-approved**: pending requests are surfaced through
  `agent_status` / `agent_pending_permissions`; a human decides via
  `agent_reply_permission`.
- **Network**: OpenCode stays localhost-only. Only the gateway `/mcp` endpoint
  is meant to be tunneled. `PUBLIC_MCP_HOST` allow-lists the tunnel host while
  keeping DNS-rebinding protection enabled.
- **Secrets**: passwords and Authorization headers are never logged; the
  config summary masks the password; provider model lists exclude keys.

## 10. Configuration model

`config.py` provides a frozen `Config` dataclass loaded from environment
variables (with optional `.env` loading via python-dotenv): `MCP_HOST`,
`MCP_PORT`, `PUBLIC_MCP_HOST`, `OPENCODE_URL`, `OPENCODE_USERNAME`,
`OPENCODE_PASSWORD`, `AGENT_ALLOWED_ROOTS`, `LOG_LEVEL`, and four OpenCode
timeouts. Values are validated at startup (`ConfigError` on invalid port, URL,
log level, or timeout). `.env.example` documents every variable with no real
secrets.

## 11. Error handling

`errors.py` defines `GatewayError` with an enum `GatewayErrorCode`:
`config_error`, `executor_unavailable`, `unauthorized_directory`,
`invalid_session`, `invalid_request`, `timeout`, `backend_http_error`,
`session_busy`, `malformed_response`, `permission_error`, `internal`.

`executors/opencode/errors.py` maps httpx transport failures (connect,
timeout), HTTP status errors (401/403 -> permission, 404 -> invalid session,
others -> backend_http_error with truncated body snippet), and pydantic
validation failures into gateway errors. `tools/helpers.py` converts any
`GatewayError` into a short `ValueError` (surfaced by the MCP SDK to the
client) and logs unexpected exceptions without leaking tracebacks to callers.

## 12. Tests

- **Unit** (`tests/unit`, 60 tests, mocked `httpx.MockTransport` / fake executors):
  configuration parsing and validation; allowed-roots validation, traversal and
  prefix-sibling rejection, drive-root rejection, fail-closed default;
  OpenCode client HTTP behavior (health, 500, 404, 401, timeout, malformed
  payload, Basic Auth header presence/absence, 204 dispatch, session creation,
  permission reply); executor validation and rendering (status busy/idle,
  message parts, diffs, permission reply enum, busy mapping on prompt failure);
  delegation service (unknown executor, unauthorized directory, happy path,
  remembered directory on continue, pending permissions in status, permission
  reply validation, messages/diff, gateway health, executor listing); transport
  security (`PUBLIC_MCP_HOST` normalization and allow-listing, DNS-rebinding
  protection never disabled).
- **Integration** (`tests/integration`, 5 tests, real OpenCode): health, full
  session lifecycle (create → async prompt → poll to idle → messages contain
  the expected answer → diff → session info → abort), invalid-session rejection,
  pending permissions, agents/providers. Auto-skipped when the backend is down.
- **E2E** (`tests/e2e`, 2 tests): (a) a read-only delegated task on the sample
  repo via the DelegationService, verifying byte-for-byte (content, size,
  mtime) that the repository is not modified; (b) a full-stack test that starts
  the gateway as a real Streamable HTTP process on a temporary port, connects
  with the MCP client library, lists tools, runs `gateway_health`,
  `agent_start_task`, polls `agent_status` to idle, reads `agent_messages`
  (contains `FINISHED`), requests `agent_diff`, and verifies error behavior for
  a bogus session and an unauthorized directory.

## 13. Exact test outputs

Full output captured in `TEST_RESULTS.txt`. Summary:

```
67 passed in 9.55s
```

- `tests/unit`: 60 passed
- `tests/integration`: 5 passed (OpenCode live)
- `tests/e2e`: 2 passed (read-only repo task + full MCP-over-HTTP protocol)

## 14. Real OpenCode E2E result

The e2e test ran a delegated read-only task on
`C:\Users\dev\Desktop\sample-repo` ("Inspect the repository and summarize its
structure. Do not modify any files."):

- gateway created a session (`ses_...`) via `POST /session`,
- dispatched the task asynchronously via `prompt_async` (HTTP 204) and returned
  the session ID immediately,
- `agent_status` observed `busy` and then `idle`,
- `agent_messages` returned the agent's text summary (mentions `app.py` /
  README content),
- `agent_diff` returned an empty change list,
- a byte-for-byte snapshot of the repository before/after (content, size,
  mtime) confirmed **no file modification** occurred.

The full MCP-over-HTTP protocol test additionally verified the complete public
tool surface through the MCP client library, including correct error responses
for an invalid session and a directory outside the allowed roots.

Note: after the machine shutdown, the local OpenCode server had to be restarted.
The environment's `OPENCODE_SERVER_PASSWORD` (set by the OpenCode desktop app)
was inherited and caused a 401; the headless server was restarted with that
variable cleared, reproducing the previously verified no-auth setup.

## 15. Known limitations

- The in-memory session registry is lost on gateway restart (OpenCode still
  resolves sessions by ID, so follow-ups keep working).
- Messages and diffs are returned as plain structured dicts; there is no
  schema-constrained result object yet.
- The OpenCode client targets the v1 OpenAPI paths; a future backend version
  should be re-verified against its own `/doc`.
- The `always` permission reply is supported at the protocol level; operators
  may want to disable it globally to enforce per-run approvals.
- No rate limiting or access token on the gateway itself (the tunnel operator
  is responsible for that layer).

## 16. Scalability considerations

Future Codex / Claude Code adapters:

- Implement `Executor` from `executors/base.py`; the generic `agent_*` tools
  and `DelegationService` need zero changes.
- Register the adapter in `executors/__init__.py` (`build_executors`); it then
  appears in `agent_executors` and `gateway_health` automatically.
- Backend-specific capabilities (`agents`, `providers` today) plug into the
  `capabilities()` set and live in their own `tools/<backend>.py` module.
- A per-executor `send_prompt` adapter maps the backend's own async/streaming
  semantics onto the gateway's immediate-return contract. For backends without
  a native async prompt, the adapter can spawn a worker task and expose
  progress through `status`.
- The registry can later be driven by configuration so operators enable a
  subset of executors per deployment.
- Session registry can be moved to a persistent store (SQLite) without touching
  the tool layer if cross-restart session memory is required.

## 17. Things intentionally NOT implemented

- The prototype's direct repo tools (`repo_overview`, `repo_tree`, `read_file`,
  `search_code`) were **not** reproduced. Decision: repository inspection
  belongs to the delegated agent (OpenCode has better context, file watching,
  and tooling), not to the gateway. Keeping them would duplicate agent
  capability and require an extra allowlist/ignore-list system for zero benefit
  in V1. If a fast, read-only, agent-independent repo viewer is wanted later,
  it belongs in a separate capability module with its own strict rules.
- No generic `shell(command)` tool.
- No permission auto-approval anywhere.
- No Codex / Claude Code adapters or stubs.
- No persistence layer beyond OpenCode's own session storage.
- No auth token / rate limiting on the gateway transport itself.

## 18. Recommended next development phase

1. Add a persistent (SQLite) session registry so the gateway can report task
   directories across restarts.
2. Introduce an optional gateway access token + per-session rate limits for
   safer Cloudflare exposure.
3. Implement a second executor (e.g. Codex) to validate the extension seam and
   refine the `capabilities()` model.
4. Add structured output schemas for `agent_messages` / `agent_diff` and an
   optional change-summary summarizer.
5. Add a `agent_wait` convenience tool with a bounded deadline so supervisors
   can block on completion without polling loops in their own logic.
6. Add an OpenCode-side permission policy mapping (e.g. blocklist of dangerous
   commands) surfaced through `agent_pending_permissions` metadata.
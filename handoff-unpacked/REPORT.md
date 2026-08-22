# Execution Report — V2 (Direct Mode as the Default Architecture)

Commit: `10652e9577121dfd2283f5b009fcbabadafb816c` ("Agent Gateway v0.2.0:
direct mode as the default architecture"). Previous release: v0.1.0
(`0db35b7`).

## 1. Environment inspected

- **Host**: Windows (win32), PowerShell 5.1.
- **Python**: 3.14.7; pip 26.2.1; git 2.55.0.
- **MCP SDK**: `mcp` 2.0.0 (pinned `mcp>=2,<3`), `httpx` 0.28.1, `pydantic` 2.13.4.
- **OpenCode**: CLI 1.18.18 (`C:\Users\dev\AppData\Roaming\npm`), headless
  server verified at `http://127.0.0.1:4096` returning
  `{"healthy": true, "version": "1.18.18"}`. Only required for the optional
  OpenCode agent mode.
- **Sample repository**: `C:\Users\dev\Desktop\sample-repo` (a git repo used
  as the read-only fixture for delegated tasks).

## 2. V2 objective and the corrective refactor

The V1 report documented a gateway whose only execution path was delegation to
a local OpenCode agent. V2 corrects that direction: **direct mode is the
default architecture**.

- **GPT-5.6 Sol (the ChatGPT client model) is the only reasoning agent.** The
  gateway exposes deterministic filesystem, search, process, and git MCP tools
  and executes exactly what the model calls. The gateway itself invokes **no
  OpenCode server and no second LLM** in direct mode.
- **OpenCode agent delegation is an optional, disabled-by-default mode**
  (`ENABLE_OPENCODE_AGENT=true`) for whole-task autonomy.
- **Transport auth**: optional bearer token (`AGENT_GATEWAY_TOKEN`) on every
  `/mcp` request with constant-time comparison; public exposure without a
  token requires an explicit `AGENT_INSECURE_NO_TOKEN_OPT_OUT=true`.
- **Hardened session authorization** in OpenCode mode: every operation on an
  existing session re-verifies that the session's real directory is still
  inside `AGENT_ALLOWED_ROOTS` (fail closed, even after a gateway restart).

## 3. Architecture chosen (V2)

```
ChatGPT Web / GPT-5.6 Sol            <-- the reasoning agent
        |
        | MCP over HTTPS (Streamable HTTP, stateless + JSON responses)
        v
Cloudflare Quick Tunnel
        |
        v
Agent Gateway  http://127.0.0.1:8000/mcp   [optional bearer auth]
        |
        |-- Direct mode (DEFAULT, no OpenCode / no model):
        |      tools/direct.py -> workspaces/manager.py -> direct/ primitives
        |      workspace_open, workspace_tree, file_read, file_stat,
        |      file_find, code_search, file_write, file_replace,
        |      file_apply_patch, process_run, git_status, git_diff,
        |      git_log, git_show
        |
        +-- OpenCode agent mode (opt-in, ENABLE_OPENCODE_AGENT=true):
               services/delegation.py -> executors/opencode/ -> 127.0.0.1:4096
               gateway_health, agent_executors, agent_start_task,
               agent_continue, agent_status, agent_session, agent_messages,
               agent_diff, agent_abort, agent_pending_permissions,
               agent_reply_permission, opencode_health, opencode_agents,
               opencode_providers
        |
        v
security/paths.py + security/auth.py   (allowed roots + bearer token)
        |
        v
Local filesystem / repositories / tools
```

`build_server` registers the direct tools unconditionally; the OpenCode
registry and tools are added only when `ENABLE_OPENCODE_AGENT=true` (28 tools
registered in OpenCode mode, 16 in direct mode).

## 4. Direct mode implementation

- `tools/direct.py` — the 14 MCP tools. Each takes an opaque `ws_...` workspace
  ID (from `workspace_open`) plus workspace-relative paths only; absolute
  paths and `..` are rejected by argument validation.
- `workspaces/manager.py` — `WorkspaceManager` binds directories inside
  `AGENT_ALLOWED_ROOTS` to opaque IDs and re-validates containment and
  symlink escapes on every call.
- `direct/` — pure primitives: `filesystem` (read with size cap + offset,
  stat, find), `search` (case-insensitive content search with line hits),
  `process` (time-bounded execution with output cap), `git` (status/diff/log/
  show), `hashing` (sha256 for patch verification).
- `file_apply_patch` applies a unified diff with context verification and
  returns the applied change plus an `expected_sha256` for client-side
  confirmation; `file_write` is an exact-content write; `file_replace` is a
  single-occurrence exact old→new replacement.
- `process_run` is opt-in (`AGENT_ENABLE_COMMANDS=true`), runs in the
  workspace directory, enforces `AGENT_PROCESS_TIMEOUT_MAX` (default 300 s),
  and caps output at `AGENT_MAX_PROCESS_OUTPUT_BYTES`.
- All I/O is capped: `AGENT_MAX_READ_BYTES`, `AGENT_MAX_TREE_ENTRIES`,
  `AGENT_MAX_SEARCH_RESULTS`.

### Two MCP-SDK integration bugs found and fixed in `tools/direct.py`

1. **Eager annotation evaluation.** Tool functions are defined inside
   `register_direct_tools(mcp, manager, config)`, and the MCP SDK's
   `func_metadata` re-evaluates string annotations with
   `inspect.signature(func, eval_str=True)` against **module** globals. With
   `from __future__ import annotations`, the `Field(le=config.max_read_bytes)`
   annotations failed at server startup with `NameError: name 'config' is not
   defined`. Fixed by removing the future import so annotations evaluate
   eagerly in the closure scope (documented in the module docstring).
2. **Closure body name shadowing.** Inside the local tool defs, calling the
   module-level functions by their plain names (e.g. `file_apply_patch(...)`)
   resolved to the **local** async def, returning unawaited coroutine objects
   (a lazy infinite recursion that surfaced as wrong tool results). Fixed by
   importing `from .. import direct as _direct` and prefixing all call sites;
   the previous `direct_file_read`/`direct_git_diff` aliases were removed.

## 5. Transport authentication

- `security/auth.py`: Starlette `BaseHTTPMiddleware` wrapping
  `mcp.streamable_http_app(...)`; every request must carry
  `Authorization: Bearer <token>` when `AGENT_GATEWAY_TOKEN` is set.
  Comparison is constant-time (`secrets.compare_digest`); failures return 401
  with a generic body.
- Startup policy: setting `PUBLIC_MCP_HOST` without a token fails startup
  unless `AGENT_INSECURE_NO_TOKEN_OPT_OUT=true` (documented in config and
  README).
- The MCP SDK 2.0.0 client has no auth hook, so the e2e tests pass the token
  via a prebuilt `httpx2.AsyncClient(headers={"Authorization": ...})` passed
  as `http_client=` to `streamable_http_client`.

## 6. OpenCode agent mode (optional)

- `DelegationService(executors, path_policy, *, opencode_enabled=False,
  commands_enabled=False)`; the registry is in-memory and the service is
  process-global (built once in `build_server`).
- **Authorization hardening**: `_authorize_session` resolves the session's
  real directory (registry, else backend `session_info` after a restart) and
  re-checks containment in `AGENT_ALLOWED_ROOTS` on every operation; anything
  unverifiable is denied (fail closed). Permission requests for unverifiable
  sessions are dropped, never surfaced, never approvable.
- **Completion heuristic fixed**: `is_completed` requires the session idle
  **and** the last assistant message's `finish == "stop"`. The previous
  implementation inspected `assistant[0]` (oldest-first message order) and
  treated idle alone as done — it never completed because the oldest assistant
  message finishes with `finish="tool-calls"` while the model is mid-work.
  Unit test `test_is_completed_requires_stop_finish` added.
- **Diff semantics confirmed against the live backend (v1.18.18)**: diffs are
  keyed by the origin user message id (`messageID`); a session-wide diff (no
  messageID) and assistant-message diffs both return `[]`. `DelegationService.diff`
  therefore defaults to the remembered `origin_message_id`; the temporary
  `find_diff_message_id` (newest assistant message) was removed from
  `executors/base.py` and `executors/opencode/executor.py`.
- `prompt_async` payload is `{"parts": [{"type": "text", "text": task}]}`
  (the client was already correct).

## 7. Configuration model (V2 additions)

New variables: `AGENT_GATEWAY_TOKEN`, `AGENT_INSECURE_NO_TOKEN_OPT_OUT`,
`ENABLE_OPENCODE_AGENT`, `AGENT_ENABLE_COMMANDS`, `AGENT_MAX_READ_BYTES`,
`AGENT_MAX_TREE_ENTRIES`, `AGENT_MAX_SEARCH_RESULTS`,
`AGENT_MAX_PROCESS_OUTPUT_BYTES`, `AGENT_PROCESS_TIMEOUT_MAX`.
`AGENT_GATEWAY_TOKEN` is in `SENSITIVE_ENV_KEYS` (never logged; `Config.summary`
masks it). Version bumped to 0.2.0; pyproject adds `uvicorn>=0.27` and an
`opencode` pytest marker.

## 8. Testing

- **Unit** (`tests/unit`): config + direct-config limits, path policy
  (traversal/symlink/prefix-sibling/root/fail-closed), auth middleware (401
  paths, constant-time, public-exposure gate), workspaces manager, filesystem,
  search, process, git tools, delegation service, OpenCode client/executor
  (incl. the completion-heuristic test).
- **Integration** (`tests/integration`): live OpenCode lifecycle tests gated
  on `needs_opencode_mode` (`ENABLE_OPENCODE_AGENT=true`), plus
  `test_opencode_write_task.py` (write task on a copy of the sample repo,
  origin-message diff shows the added file once the snapshot settles; diff
  retried 10×1 s).
- **E2E** (`tests/e2e`):
  - `test_direct_e2e.py` — **direct mode with NO OpenCode and NO model**:
    real gateway subprocess, 401 without token, tool list (direct tools
    present, OpenCode-only tools absent), `gateway_health` direct mode,
    workspace_open → file_read → file_apply_patch (expected_sha256) →
    file_write → process_run → git_diff/git_status, unauthorized-directory
    rejection. Server env pops `ENABLE_OPENCODE_AGENT`.
  - `test_opencode_e2e.py` — read-only delegated task (repository verified
    byte-for-byte unmodified: content, size, mtime) plus a full MCP protocol
    flow over raw JSON-RPC POSTs (the exact wire contract of a stateless
    json_response server): initialize → tools/list → gateway_health →
    agent_start_task → agent_status poll to completed → agent_messages
    (contains `FINISHED`) → agent_diff → error cases (bogus session,
    unauthorized directory).
  - `tests/e2e/test_gateway_e2e.py` (V1) deleted; `tests/conftest.py` rewritten
    with `needs_backend` (5 s health timeout), `needs_repo`,
    `needs_opencode_mode`.

### Test results

Default suite (no OpenCode enabled — direct mode only):

```
150 passed, 9 skipped in 30.01s
```

(the 8 skipped are OpenCode-gated, 1 is the symlink-escape test that requires
Windows symlink privileges this machine lacks; a directory-junction fallback
was added but still skips in this environment).

Full suite with `ENABLE_OPENCODE_AGENT=true` (live backend):

```
158 passed, 1 skipped in 76.78s
```

Full output in `TEST_RESULTS.txt` (default suite).

## 9. Debugging story: the OpenCode e2e "hang"

`test_full_mcp_server_protocol` intermittently hung mid-poll. Instrumentation
(narrowed to raw JSON-RPC POSTs with per-call timing) showed the gateway's own
backend request started but never finished, the gateway's 60 s read timeout
never fired, and faulthandler produced no dump. In-process uvicorn never
reproduced it; a subprocess with `stdout=DEVNULL` also never reproduced it.
**Root cause: the test never drained the gateway child's stdout/stderr pipes.
The Windows pipe buffer (~4 KiB) filled with the uvicorn banner, httpx INFO
request logs, and SDK session-manager notes, and the child's next `write`
blocked its event loop.** Fixed by draining both pipes from reader threads in
both e2e tests (and driving the OpenCode e2e with raw JSON-RPC POSTs instead
of the SDK client's SSE stream, which idles silently for minutes during a
delegated task). The gateway itself was verified correct: a genuinely stalled
backend still produces an error response within the configured 60 s read
timeout.

## 10. Known limitations

- The OpenCode session registry is in-memory (OpenCode itself persists
  sessions by ID; post-restart operations are re-authorized from backend
  metadata).
- Direct-mode workspace IDs expire on gateway restart; reopen with
  `workspace_open`.
- `file_apply_patch` requires exact context matches; no fuzzy application.
- OpenCode API is consumed as a superset of the v1 OpenAPI paths; a future
  backend version should be re-verified against its own `/doc`.
- The `always` permission reply is supported at the protocol level; operators
  may want to disable it globally.

## 11. Files changed (V2, vs `0db35b7`)

New: `src/agent_gateway/direct/*` (5 modules), `src/agent_gateway/workspaces/*`
(2), `src/agent_gateway/security/auth.py`, `src/agent_gateway/tools/direct.py`,
`tests/unit/test_{auth,direct_config,filesystem,git_tools,process,search,workspaces}.py`,
`tests/e2e/test_{direct,opencode}_e2e.py`,
`tests/integration/test_opencode_write_task.py`.
Modified: `config.py`, `server.py`, `services/delegation.py`,
`executors/{base,opencode/executor}.py`, `security/paths.py`, `logging.py`,
`errors.py`, `pyproject.toml`, `.env.example`, `README.md`, `tests/conftest.py`,
`tests/integration/test_opencode_live.py`, several unit tests.
Deleted: `tests/e2e/test_gateway_e2e.py`.

## 12. Verification of the V2 core claim

Direct mode was exercised end-to-end with **no OpenCode server reachable and
no model involved**: `tests/e2e/test_direct_e2e.py` spawns the gateway without
`ENABLE_OPENCODE_AGENT`, confirms the OpenCode-only tools are absent, and
completes the full workspace → read → patch → write → process → git loop
against the real MCP-over-HTTP endpoint. The delegated-agent suite runs only
when explicitly enabled.
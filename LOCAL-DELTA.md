# LOCAL DELTA — this checkout is not pristine

Upstream: https://github.com/Mbvjdev/onshape-mcp (MIT), pinned at commit
`fa4eb7410e297c54b223a90882653dd7ad7920c4`.
Upstream report: **PR #1 — https://github.com/Mbvjdev/onshape-mcp/pull/1** — carries two commits from
the fork https://github.com/Jaylouisw/onshape-mcp (branch `fix/4xx-not-data`):

| commit | subject |
| --- | --- |
| `188966d` | fix: raise on non-429 4xx instead of returning the error body as data |
| `4509586` | fix: report tool failures as failed calls (isError=true), not successful ones |

Both halves of the same bug class: **this server used to report failures as successes.** PR #1 has no
human review yet; an automated Copilot review left one note about `rate_limiter.report_success()`,
answered in the PR body (the guard leaves `_consecutive_429s` alone, which is the conservative
direction; a 401 is neither a 429 nor a success, and 4xx is never retried).

Owner of this file: **fitter** (capability register `~/capability/`); the bot that uses the server
is **vertex**. Recorded 2026-09-17 by fitter; upstream PR and the durable carriers added
2026-09-17 by **forge**; the second half (the `server.py` error path) added 2026-09-17 by **forge**.

## The change

### 1. `src/onshape_mcp/client.py` — 4xx is not data

In `OnshapeClient._request()`: a guard so that **any non-429 4xx response raises** instead of being
returned as data.

```python
if resp.status_code >= 400:
    ... raise RuntimeError(f"Onshape API {resp.status_code} on {method} {url}: {detail}")
```

`detail` prefers the response's own `message`/`error` field and falls back to the response text
when the body is not JSON (a 404 does not have to be JSON). `429` and `5xx` handling is untouched.

### 2. `src/onshape_mcp/server.py` — a failed tool call is a failed call

At the end of `handle_call_tool()` the catch-all returned the failure as ordinary content:

```python
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=f"Error: {e}")]
```

A content list makes the SDK report the call as a **success** (`isError=false`), so a client that
gates on `isError` still read our 401 as a successful, empty `list_documents`. It now propagates:

```python
    except Exception:
        logger.exception(f"Tool {name} failed")
        raise
```

Under the pinned SDK (`mcp>=1.27,<2` → 1.30.0) the registered `CallToolRequest` handler converts a
raised exception into `CallToolResult(isError=true)`
(`mcp/server/lowlevel/server.py` → `Server._make_error_result`). An explicit
`CallToolResult(isError=True)` reaches the client the same way — both were driven through the real
handler over the in-memory transport — so raising was chosen for a uniform handler return type, one
owner for the flag, and the smaller diff. `onshape_help` is answered before the client is built and
stays outside the `try`, so it still works with no credentials configured.

### Also carried, as the regression proof

- `tests/test_client.py` — 401 (real Onshape body shape), 403, 404 (non-JSON body),
  "4xx is not retried" (asserts a single HTTP call), and a `5xx` retry lock.
- `tests/test_server.py` — the error-path test asserts the exception propagates, plus three tests
  that drive the server's own registered handler over the in-memory transport and assert the
  client-visible result: `isError=true` for a failing call, `isError=false` for a successful one,
  `isError=false` for `onshape_help` without credentials.
- `tests/conftest.py` — `MockResponse.text`, mirroring `httpx.Response`, so the non-JSON
  fallback is testable. The mock is the only reason this file is in the delta.

## Why

Upstream handles `429` (backoff + retry) and `5xx` (retry) but lets every other 4xx fall through
to `report_success()` and `return data`. Onshape reports an unauthenticated request with

```
HTTP 401  {"message":"Unauthenticated API request", "status":401}
```

which has no `error` key (the client only raises on `data["error"]`). The error body was therefore
returned as if it were the payload, and `list_documents` — which looks for `items` in a dict —
silently produced an **empty list**. A missing, wrong or expired API key was indistinguishable from
an account with no documents — and until the second half was fixed, the tool call that surfaced the
401 was still flagged as a success.

## Evidence — the same stdio probe before and after the second half

`probe_stdio_401.py` drives the checkout over stdio with deliberately invalid credentials (junk
placeholders; the requests they produce are unauthenticated, so no Onshape account quota is
touched). Against this checkout after the first half and before the second (2026-09-17):

```
TOOLS ADVERTISED: 21
CALL list_documents: isError=False
CALL payload: Error: Onshape API 401 on GET https://cad.onshape.com/api/v6/documents: Unauthenticated API request
```

and after:

```
TOOLS ADVERTISED: 21
CALL list_documents: isError=True
CALL payload: Onshape API 401 on GET https://cad.onshape.com/api/v6/documents: Unauthenticated API request
```

A pristine clone of `fa4eb74` (neither half) gives `isError=False` with payload `[]`, and a direct
`curl -u <invalid>:<invalid> https://cad.onshape.com/api/v6/documents?limit=1` returns
`HTTP 401 {"message":"Unauthenticated API request", "status":401}`.
Upstream's own suite plus the added tests: `45 passed`.

## Carriers, and how to reapply — one command

The delta is committed on branch **`local/4xx-guard`** in this checkout (so the working tree is
clean, not a hand-edited dirty file) and pushed to the fork. It ships two artefacts:

- `deploy/4xx-guard.patch` — the diff against the pinned commit (`git diff fa4eb74 4509586`),
  applies with `git apply` (`-p1`)
- `deploy/apply-delta.sh` — idempotent applier: reports which of the two guards is missing, applies
  the patch when one is, says "already applied" when both are present, then runs the test suite if a
  `.venv` exists

Reapply after any of these:

1. **Working tree reverted / commit checked out inside this repo** (the tracked `deploy/` files
   vanish with a `git checkout <commit>`, but the objects stay in the repo):

       git -C /home/jay/onshape-mcp cherry-pick local/4xx-guard

2. **`deploy/` is present and a guard is missing** (reinstall, `git checkout -- <paths>`, a
   stash that dropped the change):

       bash /home/jay/onshape-mcp/deploy/apply-delta.sh

3. **Truly fresh clone anywhere** (no local repo to cherry-pick from):

       git clone -b local/4xx-guard https://github.com/Jaylouisw/onshape-mcp.git

All three were exercised on 2026-09-17 (path 2 on a fresh clone of `fa4eb74`: patch applied
clean, `45 passed`; the resulting `client.py`/`server.py`/`conftest.py`/`test_client.py`/
`test_server.py` were byte-identical to this checkout's, same sha256).

## Moving the pin

`git -C /home/jay/onshape-mcp fetch origin && git -C /home/jay/onshape-mcp merge origin/main`
keeps the delta in place (branch `local/4xx-guard` carries it). Then re-run the suite
(`.venv/bin/python -m pytest -q`, expect 45 passed) and re-test through
`hermes -p vertex mcp test onshape`. If instead you check out a bare commit, use reapply path 1
above.

## When upstream merges the fix

Delete `deploy/`, this file and the `local/4xx-guard` branch, pin the merge commit, and record the
change in `~/cad/log.md` (per the `onshape-cad` skill). The PR is the place to watch for that.

## Known remaining gap (not part of this delta)

`handle_call_tool()`'s `else` branch still returns `Unknown tool: <name>` as ordinary content
(`isError=false`). Not fixed here: it is a client-side mistake rather than a silent-data failure and
the text is unambiguous.

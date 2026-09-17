# LOCAL DELTA — this checkout is not pristine

Upstream: https://github.com/Mbvjdev/onshape-mcp (MIT), pinned at commit
`fa4eb7410e297c54b223a90882653dd7ad7920c4`.
Upstream report: **PR #1 — https://github.com/Mbvjdev/onshape-mcp/pull/1** —
"fix: raise on non-429 4xx instead of returning the error body as data (401 looks like an empty
account)", opened 2026-09-17 from the fork https://github.com/Jaylouisw/onshape-mcp
(branch `fix/4xx-not-data`, commit `188966d`).

Owner of this file: **fitter** (capability register `~/capability/`); the bot that uses the server
is **vertex**. Recorded 2026-09-17 by fitter; upstream PR and the durable carriers added
2026-09-17 by **forge**.

## The change

`src/onshape_mcp/client.py`, in `OnshapeClient._request()`: a guard so that **any non-429 4xx
response raises** instead of being returned as data.

```python
if resp.status_code >= 400:
    ... raise RuntimeError(f"Onshape API {resp.status_code} on {method} {url}: {detail}")
```

`detail` prefers the response's own `message`/`error` field and falls back to the response text
when the body is not JSON (a 404 does not have to be JSON). `429` and `5xx` handling is untouched.

Also carried, as the regression proof:

- `tests/test_client.py` — 401 (real Onshape body shape), 403, 404 (non-JSON body),
  "4xx is not retried" (asserts a single HTTP call), and a `5xx` retry lock.
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
an account with no documents.

Verified against a pristine clone of `fa4eb74` (own venv, invalid placeholder keys over stdio —
unauthenticated requests, so no account quota is touched):

```
BEFORE  TOOLS ADVERTISED: 21 | CALL list_documents: isError=False | payload: []
AFTER   TOOLS ADVERTISED: 21 | CALL list_documents payload:
        Error: Onshape API 401 on GET https://cad.onshape.com/api/v6/documents: Unauthenticated API request
```

and a direct `curl -u <invalid>:<invalid> https://cad.onshape.com/api/v6/documents?limit=1`
returns `HTTP 401 {"message":"Unauthenticated API request", "status":401}`.
Upstream's own suite plus the five new tests: `42 passed`.

## Carriers, and how to reapply — one command

The delta is committed on branch **`local/4xx-guard`** in this checkout (so the working tree is
clean, not a hand-edited dirty file) and pushed to the fork. It ships two artefacts:

- `deploy/4xx-guard.patch` — the diff against the pinned commit, applies with `git apply` (`-p1`)
- `deploy/apply-delta.sh` — idempotent applier: applies the patch if the guard is missing,
  says "already applied" if it is there, then runs the test suite when a `.venv` exists

Reapply after any of these:

1. **Working tree reverted / commit checked out inside this repo** (the tracked `deploy/` files
   vanish with a `git checkout <commit>`, but the objects stay in the repo):

       git -C /home/jay/onshape-mcp cherry-pick local/4xx-guard

2. **`deploy/` is present and the guard is missing** (reinstall, `git checkout -- <paths>`, a
   stash that dropped the change):

       bash /home/jay/onshape-mcp/deploy/apply-delta.sh

3. **Truly fresh clone anywhere** (no local repo to cherry-pick from):

       git clone -b local/4xx-guard https://github.com/Jaylouisw/onshape-mcp.git

All three were exercised on 2026-09-17 (path 2 on a fresh clone of `fa4eb74`: patch applied
clean, `42 passed`; the resulting `client.py`/`conftest.py`/`test_client.py` were byte-identical
to this checkout's, same sha256).

## Moving the pin

`git -C /home/jay/onshape-mcp fetch origin && git -C /home/jay/onshape-mcp merge origin/main`
keeps the delta in place (branch `local/4xx-guard` carries it). Then re-run the suite
(`.venv/bin/python -m pytest -q`) and re-test through `hermes -p vertex mcp test onshape`.
If instead you check out a bare commit, use reapply path 1 above.

## When upstream merges the fix

Delete `deploy/`, this file and the `local/4xx-guard` branch, pin the merge commit, and record the
change in `~/cad/log.md` (per the `onshape-cad` skill). The PR is the place to watch for that.

## Known remaining gap (not part of this delta)

`server.py`'s tool error path returns `[TextContent(text=f"Error: {e}")]` without `isError=True`,
so an MCP client that only checks `isError` still sees a successful call — the text is explicit,
so the agent reads the failure, but the flag is wrong. Raised as a follow-up note on PR #1; not
fixed here to keep the upstream diff focused.

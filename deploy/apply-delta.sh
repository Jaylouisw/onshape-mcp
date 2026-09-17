#!/usr/bin/env bash
# Reapply the local "failures are reported as failures" delta to an onshape-mcp checkout.
#
# WHAT IT APPLIES (one patch, two halves of the same bug class)
#  1. src/onshape_mcp/client.py — OnshapeClient._request() raises on any non-429 4xx instead of
#     falling through to report_success()/return data. Onshape answers a missing, wrong or expired
#     API key with {"message":"Unauthenticated API request","status":401}, which has no "error" key
#     — returned as data it made a dead credential look like an account with no documents.
#  2. src/onshape_mcp/server.py — handle_call_tool() lets a tool exception propagate instead of
#     returning "Error: ..." as ordinary content. A content list is reported by the SDK as a
#     successful call (isError=false), so a client that gates on isError read that same 401 as a
#     successful read. The SDK turns the raised exception into CallToolResult(isError=true).
#  429 and 5xx handling is deliberately untouched (those retries are load-bearing against the
#  account-wide rate limit). The patch also carries the regression tests for both halves: 401/403/404
#  raising, "4xx is not retried", the 5xx retry lock, and the client-visible isError shape.
#
# USAGE
#   bash deploy/apply-delta.sh [checkout-dir]      # default: the repo this script lives in
#
#   Idempotent: if both guards are present it says so and exits 0.
#   Exit codes: 0 ok/already applied, 2 bad usage/missing files, 3 patch did not apply.
#
# Upstream report: see LOCAL-DELTA.md. If the upstream project has merged the fixes, this script
# becomes a no-op you can delete along with deploy/.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-$(cd "$here/.." && pwd)}"
patch="$here/4xx-guard.patch"
client="$target/src/onshape_mcp/client.py"
server="$target/src/onshape_mcp/server.py"

[ -f "$patch" ] || { echo "FAIL: missing patch file $patch" >&2; exit 2; }
[ -f "$client" ] || { echo "FAIL: $client not found (not an onshape-mcp checkout?)" >&2; exit 2; }
[ -f "$server" ] || { echo "FAIL: $server not found (not an onshape-mcp checkout?)" >&2; exit 2; }
git -C "$target" rev-parse --git-dir >/dev/null 2>&1 || { echo "FAIL: $target is not a git checkout" >&2; exit 2; }

echo "checkout: $target"
echo "patch:    $patch"

# The two halves ship as one patch, so report exactly which half is missing instead of guessing.
missing=""
grep -q "Any other 4xx is a real failure" "$client" || missing="$missing client.py:4xx-guard"
grep -q "Report the failure as a failure" "$server" || missing="$missing server.py:isError"

if [ -z "$missing" ]; then
    echo "already applied: both guards are present (client.py 4xx-guard, server.py isError)"
else
    echo "missing:$missing"
    # Plain apply first: it touches only the working tree, which is what a reader expects.
    # Fallback --3way tolerates upstream moving lines around the insertion point, and also repairs a
    # half-applied state (the pre-image blobs are in the repo whenever the checkout is a clone of
    # Mbvjdev/onshape-mcp); it implies --index, so it leaves the delta staged.
    if git -C "$target" apply "$patch"; then
        echo "applied (working tree): $patch"
    elif git -C "$target" apply --3way "$patch"; then
        echo "applied (3-way merge; the delta is staged in the index — 'git reset' to unstage)"
    else
        echo "FAIL: patch did not apply cleanly to $target — resolve by hand:" >&2
        echo "      git -C $target apply --3way $patch" >&2
        echo "      (or cherry-pick the delta branch from the fork recorded in LOCAL-DELTA.md)" >&2
        exit 3
    fi
fi

if [ -x "$target/.venv/bin/python" ]; then
    echo "verifying: ./.venv/bin/python -m pytest -q"
    ( cd "$target" && ./.venv/bin/python -m pytest -q ) || {
        echo "FAIL: test suite failed after applying the delta" >&2; exit 3; }
else
    echo "note: no .venv in $target — skipping tests (see CONTRIBUTING.md for setup)"
fi

echo "done: onshape-mcp now raises on non-429 4xx and reports tool failures as isError=true"

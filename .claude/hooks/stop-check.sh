#!/usr/bin/env bash
# Stop: run the full quality gate (`make check`). Exit 2 pushes Claude back to
# work with the failure output. Guards: stop_hook_active prevents infinite
# loops; a clean working tree skips the run entirely (nothing changed).
set -u
in=$(cat)
[ "$(printf '%s' "$in" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
if [ -z "$(git status --porcelain -- '*.py' '*.toml' Makefile 2>/dev/null)" ]; then
  exit 0
fi
out=$(make check 2>&1)
if [ $? -ne 0 ]; then
  printf '%s\n' "$out" | grep -vE '^(uv run|make|Scanning|Assuming)' | tail -25 >&2
  echo "-- make check failed; fix the errors above before finishing." >&2
  exit 2
fi
exit 0

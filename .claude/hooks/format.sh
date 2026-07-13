#!/usr/bin/env bash
# PostToolUse (Write|Edit): autofix + format the edited Python file (~100ms).
# Unfixable violations are echoed back to Claude via exit 2 so they get fixed
# immediately instead of piling up for the Stop hook.
set -u
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in
  *.py) [ -f "$f" ] || exit 0 ;;
  *) exit 0 ;;
esac
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
uv run ruff check --fix -q "$f" >/dev/null 2>&1
uv run ruff format -q "$f" >/dev/null 2>&1
remaining=$(uv run ruff check --output-format concise -q "$f" 2>/dev/null)
if [ -n "$remaining" ]; then
  printf '%s\n' "$remaining" | head -10 >&2
  exit 2
fi
exit 0

# Quality gates. `make check` is the single entrypoint — CI and the Claude
# Code Stop hook both run it. Keep it green.

PKG_DIRS := packages/quints packages/beangulp-mt940 packages/beangulp-stripe packages/beangulp-wise packages/beanprice-bazg

.PHONY: check static test format typebaseline

check: static test

static:
	uv run ruff format --check .
	uv run ruff check .
	uv run basedpyright
	uv run lint-imports
	@for p in $(PKG_DIRS); do \
		echo "deptry: $$p"; \
		(cd $$p && uv run deptry src) || exit 1; \
	done
	uv run vulture

test:
	uv run pytest packages -q

# Apply all auto-fixes (what the PostToolUse hook does per-file).
format:
	uv run ruff check --fix .
	uv run ruff format .

# Re-pin the type-error baseline. Only run this to ratchet DOWN (after fixing
# baselined errors) — never to bury new ones.
typebaseline:
	uv run basedpyright --writebaseline

# Quality gates. `make check` is the single entrypoint — CI and the Claude
# Code Stop hook both run it. Keep it green.

PKG_DIRS := packages/quints packages/beangulp-mt940 packages/beangulp-stripe packages/beangulp-wise packages/beanprice-bazg packages/quints-mailroom

.PHONY: check static test format typebaseline docs docs-serve media

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

# Docs site (Zensical → site/). Every quints command in docs/ and README.md
# is executed by tests/test_docs.py — `make check` guards the content,
# `make docs` the build. The invoicing JSON Schemas are regenerated into
# docs/schema/ (committed; sync-tested) and published with the site, so
# scaffolded YAMLs can point their $schema modelines at stable URLs.
docs:
	uv run quints schema --out docs/schema
	uv run --only-group docs zensical build --clean

docs-serve:
	uv run --only-group docs zensical serve

# Regenerate the terminal GIFs and PDF previews in docs/assets/ from the
# committed VHS tapes (media/*.tape) and the sample project. Needs vhs and
# poppler — see media/generate.sh. Run before a release so visuals track the CLI.
media:
	./media/generate.sh

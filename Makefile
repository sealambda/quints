# Quality gates. `make check` is the Python gate: the Claude Code Stop hook runs
# it, and the CI jobs `quality` and `test` run its two halves. Every other CI
# job has a command here or in flake.nix: see CONTRIBUTING.md.

PKG_DIRS := packages/quints packages/beangulp-mt940 packages/beangulp-stripe packages/beangulp-wise packages/beangulp-yapeal packages/beanprice-bazg

.PHONY: check static test format typebaseline docs docs-serve docs-lint legal-links media pdfcheck

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

# Lint the Markdown and its links. Needs markdownlint-cli2 and lychee: the Nix
# dev shell has both (`nix develop -c make docs-lint`). lychee reads the tracked
# files only, because a glob would also reach the Markdown in `.venv`.
docs-lint:
	markdownlint-cli2 "**/*.md"
	git ls-files -z '*.md' | xargs -0 lychee --no-progress

# Check the legal sources that the rate tables cite (ledger.RATE_SOURCES). This
# run needs the network, so it skips the offline `lychee.toml`.
legal-links:
	lychee --no-progress --config /dev/null packages/quints/src/quints/ledger.py

# Regenerate the terminal GIFs and PDF previews in docs/assets/ from the
# committed VHS tapes (media/*.tape) and the sample project. Needs vhs and
# poppler — see media/generate.sh. Run before a release so visuals track the CLI.
media:
	./media/generate.sh

# Validate the sample invoices (PDF/A-3a) and statements (PDF/A-2a) plus
# PDF/UA-1 with veraPDF (docker).
# Not part of `check`: it needs docker and takes longer than the test suite.
pdfcheck:
	./media/pdfcheck.sh

---
name: release
description: Cut and publish a quints monorepo release — decide per-package version bumps, tag, PyPI trusted publishing, release notes, post-release smoke test. Use when asked to release, publish, or tag a new version.
---

# Releasing quints

Five independently-versioned distributions in one uv workspace
(`packages/*`). One git tag — `vX.Y.Z`, following the **quints** version —
publishes everything; unchanged packages are no-ops.

## 1. Decide the bumps

- See what changed since the last release: `git tag --list | tail -3`, then
  `git diff v<last>..HEAD --stat -- packages/`.
- Bump only packages whose *shipped code* changed (not tests). New CLI
  surface, scaffold output, or API → minor; fixes only → patch.
- If `quints` starts relying on new API of a building block
  (`beangulp-*`/`beanprice-*`), bump that block **and** raise the constraint
  in `packages/quints/pyproject.toml` (e.g. `beanprice-bazg>=0.2.0`) —
  otherwise PyPI can pair new quints with an old block and crash at runtime.

## 2. Apply

- Edit `version = "X.Y.Z"` in each bumped package's `pyproject.toml`. That is
  the only place: `__version__` attributes are derived from installed
  metadata, never edited by hand.
- `uv lock` (records the new workspace versions), then `make check`.
- If CLI output, the scaffold, or the PDFs changed since the last release,
  refresh the visuals: `make media` locally (needs vhs + poppler) or the
  "Media" workflow (`gh workflow run media.yml`), and commit `docs/assets/`.

## 3. Commit, tag, push

- One commit: `release: quints X.Y.Z[, <pkg> A.B.C …]`, including `uv.lock`.
- `git tag vX.Y.Z` (the quints version) and `git push origin main vX.Y.Z`.

## 4. The tag does the rest

`.github/workflows/publish.yaml` on a `v*.*.*` tag: builds all five behind
`make check`, publishes each to PyPI via trusted publishing (per-package
`pypi-<package>` environment, `skip-existing: true` so unbumped versions
no-op), Sigstore-signs the artifacts, and creates a GitHub release with
**empty** notes.

## 5. Verify, write notes, smoke-test

- `gh run list --repo sealambda/quints` → `gh run watch <id> --exit-status`.
- Fill the notes: `gh release edit vX.Y.Z --repo sealambda/quints --notes …`
  — one `##` section per bumped package stating what changed, plus a line
  noting which packages are unchanged.
- Smoke the published artifact (uvx caches aggressively — force a refresh):
  `uvx --refresh-package quints quints@X.Y.Z --version`, then scaffold a
  scratch project and run one or two commands against it.

---
name: ratchet
description: Pay down the basedpyright baseline - fix a batch of pinned type errors and re-pin the baseline lower. Use when asked to ratchet, reduce type debt, or shrink the baseline.
---

# Ratchet down the type-error baseline

`.basedpyright/baseline.json` pins pre-existing strict-mode errors so only new
ones fail `make check`. This skill pays that debt down in controlled batches.

## Procedure

1. Count the debt: `jq '[.files[] | length] | add' .basedpyright/baseline.json`
2. Pick a batch — one file or one rule at a time, not a random scatter.
   List what's pinned per file: `jq -r '.files | to_entries[] | "\(.value | length)\t\(.key)"' .basedpyright/baseline.json | sort -rn | head`
   To see the actual diagnostics for a file, temporarily move the baseline
   aside and run `uv run basedpyright <file>`, then move it back.
3. Fix the errors properly:
   - `reportMissingParameterType` → add real annotations (`tmp_path: Path` in tests).
   - `reportOptionalMemberAccess` on beancount postings → guard or assert the
     Optional; these are often real latent bugs, not noise.
   - Do NOT fix with `# pyright: ignore` or `Any` — that's burying, not paying.
4. Re-pin: `make typebaseline`, then confirm the count from step 1 went DOWN.
   If it went up, you introduced new errors — fix them before re-pinning.
5. `make check` must be green; tests must still pass.

## Rules

- Never run `make typebaseline` while basedpyright reports errors caused by
  code you just wrote — fix those directly.
- Baseline count only ever decreases in a commit. Mention the before/after
  count in your summary.

# Check the books in CI

Run `quints check` on every change. The runner annotates each problem at
its file and line, and the build fails until the books are consistent. The
same runner builds the statements, shows what a change does to the figures,
and posts a weekly digest.

!!! abstract "Applies if"
    - **Legal form, VAT method, filing period:** any. Nothing here differs.
    - **VAT status:** any. Registered, `check` also runs every VAT return
      not settled yet. Not registered, it checks the ledger and the
      statements only.
    - **Where the books live:** a git repository on GitHub, GitLab or
      Azure DevOps, or one a Jenkins job checks out. Other runners read the
      text or the SARIF output.
    - **Not covered:** GitHub code scanning (see
      [below](#what-quints-doesnt-do-here)).

## 1. Run locally what CI runs

```bash
quints check
quints check --output-format github
```

`quints check` exits 1 on an error, and on a warning too with `--strict`.
`--output-format` picks what the runner reads: `github`, `gitlab`, `azure`,
`sarif`, or `json` for a script. quints never detects the runner, so the
same flag on your machine prints what CI prints. `QUINTS_OUTPUT_FORMAT`
sets it for every `quints check` in a job.
[Diagnostics](../reference/diagnostics.md) lists every problem and its fix.

## 2. GitHub

`quints init` writes `.github/workflows/books.yml` (`--ci github`, the
default). For books scaffolded earlier, add it by hand:

```yaml
name: books

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 6 1 * *"
  workflow_dispatch:

permissions: {}

jobs:
  check:
    if: ${{ github.event_name == 'pull_request' || github.event_name == 'push' }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: sealambda/quints/actions/check@v0.25.1

  statements:
    if: ${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}
    permissions:
      contents: read
    uses: sealambda/quints/.github/workflows/statements.yml@v0.25.1
```

1. **Pin both `uses:` lines to the same quints release**, by tag or by
   commit SHA. The action lives in the quints repository, not on the
   Marketplace.
2. **Commit `uv.lock`.** The action runs the quints it pins
   (`uv sync --frozen`). To run another, set `quints:` to a requirement, a
   path, a wheel or a git URL (`quints: quints==0.25.1`).
3. **Books in a subdirectory?** Set `working-directory:`, and `file:` if
   the main file isn't `main.bean`. Annotation paths stay relative to the
   repository root.
4. **Open a pull request.** Each problem shows on the diff at its line,
   and the job fails. GitHub shows about ten annotations of each kind per
   step,[^annotations] so the job summary lists every problem in a
   table.[^summary]

The action's inputs: `quints`, `file`, `working-directory`, `args` (more
`check` flags, such as `--strict`) and `python-version`.

### Build the statements every month

The `statements` job above calls a reusable workflow. It checks the books,
writes every statutory file with `quints report bundle`, puts the
Erfolgsrechnung in the job summary, and uploads the files as an artifact.
Its inputs: `year` (default: the current year), or `from` and `to` for an
extended fiscal year, and `langs`, `file`, `quints`, `python-version` and
`release`.

At the year-end close, publish the files as a GitHub Release. This job
runs only when you start the workflow by hand (*Run workflow*):

```yaml
  close:
    if: ${{ github.event_name == 'workflow_dispatch' }}
    permissions:
      actions: read # the release job downloads the artifact
      contents: write # and creates the release
    uses: sealambda/quints/.github/workflows/statements.yml@v0.25.1
    with:
      year: "2026"
      release: true
```

### Show what a pull request changes

```yaml
  impact:
    if: ${{ github.event_name == 'pull_request' }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          fetch-depth: 0 # the base commit must be there
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
      - env:
          BASE: ${{ github.event.pull_request.base.sha }}
        run: |
          uv sync --frozen
          uv run quints diff --base-ref "$BASE" --output-format markdown >> "$GITHUB_STEP_SUMMARY"
```

`quints diff` lists the transactions the change adds or removes, each with
its place and whether it links a document, and how the result, EBIT, total
assets and the current VAT period move. It reads the base with
`git archive`, without a worktree. A base that doesn't load counts as empty,
so a pull request that fixes broken books still gets its summary.

### A weekly digest

```yaml
  digest:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
      - run: |
          uv sync --frozen
          uv run quints digest --output-format markdown >> "$GITHUB_STEP_SUMMARY"
```

Run it on a weekly `schedule`. The digest lists VAT periods to settle or
pay, open invoices by age, the inbox, and undocumented bookings. It always
exits 0.

## 3. GitLab

`quints init --ci gitlab` writes this `.gitlab-ci.yml`:

```yaml
image: ghcr.io/astral-sh/uv:python3.13-bookworm-slim

check:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "push"
  script:
    - uv sync --frozen
    - uv run --frozen quints check --output-format gitlab --output-file gl-code-quality.json
  artifacts:
    when: always
    reports:
      codequality: gl-code-quality.json

statements:
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule" || $CI_PIPELINE_SOURCE == "web"
  script:
    - uv sync --frozen
    - uv run --frozen quints check
    - uv run --frozen quints report bundle --year "$(date -u +%Y)" --lang en,de --out statements
  artifacts:
    paths:
      - statements/
```

1. **Commit `uv.lock`**: both jobs run the quints it pins.
2. **Open a merge request.** The Code Quality widget lists each problem
   with its file and line.[^codequality] An error fails the job; the report
   uploads either way. A finding's fingerprint leaves the line out, so
   moving a transaction doesn't make it new.
3. **Schedule the statements** under *Build › Pipeline schedules*, or run
   the pipeline by hand.

## 4. Azure Pipelines

```yaml
steps:
  - script: |
      uv sync --frozen
      uv run quints check --output-format azure
    displayName: quints check
```

Each problem becomes a `task.logissue` error or warning with its file and
line.[^azure] Logged errors alone don't fail an Azure step: the exit code 1
of `quints check` does.

## 5. Jenkins

The Warnings Next Generation plugin reads SARIF.[^warnings-ng] Record the
issues before you fail the build, so a red build has them too:

```groovy
stage('quints check') {
  steps {
    script {
      def status = sh(script: 'uv run quints check --output-format sarif --output-file quints.sarif', returnStatus: true)
      recordIssues(tools: [sarif(pattern: 'quints.sarif', id: 'quints', name: 'quints')])
      if (status != 0) { error('quints check failed') }
    }
  }
}
```

The same file works on your machine:

```bash
quints check --output-format sarif --output-file quints.sarif
```

SARIF 2.1.0[^sarif] is the portable format: each code is a rule with a link
to its entry under [Diagnostics](../reference/diagnostics.md).

## 6. reviewdog, and any other runner

The text output starts each line with `path:line:`, which editors and
error parsers read. reviewdog turns it into review comments:[^reviewdog]

```sh
quints check 2>&1 | reviewdog -efm="%f:%l: %m" -name=quints -reporter=github-pr-review
```

## What quints doesn't do here

- **GitHub code scanning.** SARIF upload works, but code scanning on a
  private repository needs a GitHub Advanced Security licence,[^ghas] and
  books repositories are private. The `github` annotations need none.
- **Detect the runner.** Pass `--output-format`, or set
  `QUINTS_OUTPUT_FORMAT`.
- **Bitbucket Pipelines.** No dedicated format: use the text output or
  SARIF.

[^annotations]: GitHub Docs, *Workflow commands for GitHub Actions*, "Setting an error message":
    <https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#setting-an-error-message>.
    The limits on annotations per step are under *Troubleshooting workflows*.
[^summary]: GitHub Docs, *Workflow commands for GitHub Actions*, "Adding a job summary":
    <https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#adding-a-job-summary>.
[^codequality]: GitLab Docs, *Code Quality*, "Code Quality report format":
    <https://docs.gitlab.com/ci/testing/code_quality/#code-quality-report-format>.
[^azure]: Microsoft Learn, *Logging commands* (Azure Pipelines), `task.logissue`:
    <https://learn.microsoft.com/en-us/azure/devops/pipelines/scripts/logging-commands>.
[^warnings-ng]: Jenkins plugins, *Warnings Next Generation*:
    <https://plugins.jenkins.io/warnings-ng/>.
[^sarif]: OASIS, *Static Analysis Results Interchange Format (SARIF) Version 2.1.0*:
    <https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html>.
[^reviewdog]: reviewdog, "errorformat":
    <https://github.com/reviewdog/reviewdog#errorformat>.
[^ghas]: GitHub Docs, *About code scanning*:
    <https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning>.

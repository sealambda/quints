# Check the books as you edit

CI checks a change once it is pushed. This page sets up the same check
earlier: after every edit an agent makes, and on every save in your editor.
An agent that sees its mistake one edit later fixes it. A small local model
needs that more than a large one does.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Nothing on this page differs
      between them.
    - **VAT status, method and filing period:** any. The check is the one
      `quints check` runs; [Diagnostics](../reference/diagnostics.md) says
      which codes appear in which case.
    - **Who edits the books:** an AI coding agent (steps 1 to 3), a person
      in an editor (steps 4 and 5), or both.
    - **Where the books live:** a git repository. Steps 1 to 3 compare with
      the last commit.
    - **Not covered:** VS Code and aider (see
      [below](#what-quints-doesnt-do-here)).

## 1. Run by hand what the agent runs

```bash
quints check --review
quints explain document-missing
quints check --fix
```

- `--review` adds what is still to do to what is wrong: `!` flags, entries
  with no document, bank balances not asserted for a quarter.
- `quints explain <code>` prints what a code means and how to fix it. It
  reads the installed package, so it works with no network.
- `--fix` applies the fixes that need no judgement, then checks again.

After an edit, one more flag keeps the answer short:

<!-- no-test: needs a git repository with a commit -->
```bash
quints check --review --strict --base-ref HEAD
```

`--base-ref HEAD` checks the books twice, as committed and as they are now,
and reports only the problems the commit doesn't have: what the edit
brought. `--strict` makes a new warning exit 1 too. Books that already have
fifty problems still answer "you added this one".

## 2. Claude Code: nothing to do

`quints init` writes `.claude/settings.json`. Claude Code then runs the
command of step 1 after each edit it makes, and the problems the edit
brought go back to the model.[^hooks] For books scaffolded earlier, upgrade
quints first (`uv lock --upgrade-package quints`: older releases lack the
flags), then add the file by hand:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && uv run quints check --review --strict --base-ref HEAD 1>&2 || exit 2",
            "timeout": 60,
            "statusMessage": "quints check"
          }
        ]
      }
    ]
  }
}
```

Commit once the check is clean. The next edit is then compared with that
commit.

## 3. Other agents

Any agent that reads `AGENTS.md` is told to run the command of step 1 after
each edit. Nothing else is needed.

OpenCode can also take the problems from the language server of step 4 and
hand them to the model.[^opencode] In `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "lsp": {
    "quints": {
      "command": ["uv", "run", "quints", "lsp"],
      "extensions": [".bean"]
    }
  }
}
```

The language server reports every problem in the books, not only the new
ones. On books that already have many, prefer the command of step 1.

## 4. Your editor: `quints lsp`

`quints lsp` is a language server.[^lsp] It shows each problem at its file
and line when the editor opens the books, and again each time you save. The
code links to its entry in [Diagnostics](../reference/diagnostics.md), and a
problem that has a fix offers it as a quick fix. What `--review` adds shows
as information, not as a warning.

It needs an extra, in the books' project:

```bash
uv add 'quints[lsp]'
```

=== "Helix"

    In Helix, add `.helix/languages.toml` to the books. Helix already runs
    `beancount-language-server` on beancount files; this adds quints beside
    it, and Helix shows the diagnostics of both.[^helix]

    ```toml
    [language-server.quints]
    command = "uv"
    args = ["run", "quints", "lsp"]

    [[language]]
    name = "beancount"
    language-servers = ["beancount-language-server", "quints"]
    ```

=== "Neovim"

    In Neovim 0.11 or later, add this to your configuration.[^neovim]

    ```lua
    vim.lsp.config('quints', {
      cmd = { 'uv', 'run', 'quints', 'lsp' },
      filetypes = { 'beancount' },
      root_markers = { 'quints.toml' },
    })
    vim.lsp.enable('quints')
    ```

Any other editor that starts a language server from a command works the
same way: the command is `uv run quints lsp`, in the directory that holds
`main.bean`, for beancount files.

## 5. Completion, formatting and the other files

`quints lsp` reports problems and nothing else. Three more servers cover the
rest, and each runs beside it:

- **Beancount files:** `beancount-language-server` completes accounts and
  payees and formats entries. It runs beancount with the books' `.venv`, so
  after `uv sync` it loads `quints.plugins.kmu` and reports a missing `kmu:`
  code in the editor.[^bls]
- **`quints.toml`:** the first line `quints init` writes,
  `#:schema …/schema/quints.schema.json`, points Taplo (Even Better TOML) at
  the schema: an unknown key or a value of the wrong kind is underlined.[^taplo]
  For a `quints.toml` written earlier, add the line at the top.
- **Invoice YAML:** each file under `invoicing/` carries a
  `yaml-language-server` modeline to its schema.

```bash
quints schema
```

writes the same schemas to `invoicing/schema/`, for an editor with no
network.

## What quints doesn't do here

- **VS Code.** VS Code starts a language server only from an extension, and
  quints has none. Use the terminal (`quints check`), or the Claude Code hook
  of step 2.
- **aider.** aider's `--lint-cmd` passes the edited files to the command,
  and `quints check` takes none: it always checks the whole books. Wrap it in
  a script that ignores its arguments.
- **Unsaved changes.** The check reads the books from disk. An edit shows up
  when you save it.
- **Fixes that need judgement.** `--fix` and the quick fix book the hash of
  a filed invoice, and nothing else today. They never change an amount, an
  account or a tag.

[^hooks]: Claude Code Docs, *Hooks reference*, `PostToolUse` and "Exit code 2 behavior":
    <https://code.claude.com/docs/en/hooks>.
[^opencode]: OpenCode Docs, *LSP Servers*, "Custom LSP servers":
    <https://opencode.ai/docs/lsp/>.
[^lsp]: Microsoft, *Language Server Protocol Specification 3.17*, "Publish Diagnostics" and "Code Action":
    <https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/>.
[^helix]: Helix Docs, *Languages*, "Configuring Language Servers for a language":
    <https://docs.helix-editor.com/languages.html>.
[^neovim]: Neovim Docs, *LSP*, `vim.lsp.config()` and `vim.lsp.enable()`:
    <https://neovim.io/doc/user/lsp/>.
[^bls]: polarmutex, *beancount-language-server* README, "Diagnostics":
    <https://github.com/polarmutex/beancount-language-server>.
[^taplo]: Taplo Docs, *Directives*, "The schema directive":
    <https://taplo.tamasfe.dev/configuration/directives.html>.

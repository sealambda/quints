# Check the books as you edit

CI checks a change once it is pushed. Run the same check earlier: after
every edit an agent makes, and on every save in your editor.

!!! abstract "Applies if"
    - **Legal form, VAT status, method, filing period:** any. The check is
      `quints check`; [Diagnostics](../reference/diagnostics.md) says which
      codes appear in which case.
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

- `--review` also lists what is still to do: `!` flags, entries with no
  document, bank balances not asserted for a quarter.
- `quints explain <code>` prints what a code means and how to fix it, from
  the installed package: no network.
- `--fix` applies the fixes that need no judgement, then checks again.

After an edit, report only what the edit brought:

<!-- no-test: needs a git repository with a commit -->
```bash
quints check --review --strict --base-ref HEAD
```

`--base-ref HEAD` checks the books as committed and as they are now, and
reports the problems the commit doesn't have. `--strict` makes a new
warning exit 1 too. Books with fifty old problems still answer "you added
this one".

## 2. Claude Code: nothing to do

`quints init` writes `.claude/settings.json`. Claude Code then runs the
command of step 1 after each edit, and the new problems go back to the
model.[^hooks] For books scaffolded earlier, upgrade quints
(`uv lock --upgrade-package quints`: older releases lack the flags), then
add the file by hand:

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

Commit once the check is clean: the next edit is compared with that commit.

## 3. Other agents

`AGENTS.md` tells any agent that reads it to run the command of step 1
after each edit. Nothing else is needed.

OpenCode can also hand the model the problems from the language server of
step 4.[^opencode] In `opencode.json`:

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

The language server reports every problem, not only the new ones. On books
that already have many, prefer the command of step 1.

## 4. Your editor: `quints lsp`

`quints lsp` is a language server.[^lsp] It shows each problem at its file
and line when the editor opens the books, and again on every save. The code
links to its entry in [Diagnostics](../reference/diagnostics.md), and a
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
same: `uv run quints lsp`, in the directory that holds `main.bean`, for
beancount files.

## 5. Completion, formatting and the other files

`quints lsp` only reports problems. Three more servers run beside it:

- **Beancount files:** `beancount-language-server` completes accounts and
  payees and formats entries. It runs beancount with the books' `.venv`, so
  after `uv sync` it loads `quints.plugins.kmu` and reports a missing `kmu:`
  code in the editor.[^bls]
- **`quints.toml`:** its first line, `#:schema …/schema/quints.schema.json`,
  points Taplo (Even Better TOML) at the schema: an unknown key or a value
  of the wrong kind is underlined.[^taplo] `quints init` writes the line;
  add it to a `quints.toml` written earlier.
- **Invoice YAML:** each file under `invoicing/` carries a
  `yaml-language-server` modeline to its schema.

For an editor with no network, write the same schemas to
`invoicing/schema/`:

```bash
quints schema
```

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

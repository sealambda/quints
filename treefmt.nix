# treefmt-nix module (https://github.com/numtide/treefmt-nix). `flake.nix` uses
# it for `nix fmt` and for the `formatting` check. Each program formats a file
# or fails the check.
#
# No Python formatter: `make format` runs ruff at the version uv.lock pins.
# typos still reads the Python files (see _typos.toml).
{
  projectRootFile = "flake.nix";

  programs = {
    actionlint.enable = true;
    nixfmt.enable = true;
    shellcheck.enable = true;
    shfmt.enable = true;
    typos.enable = true;
    zizmor.enable = true;
  };

  # zizmor with its strictest persona.
  settings.formatter.zizmor.options = [ "--persona=pedantic" ];

  # The check action, and the CI that `quints init` scaffolds (in examples/).
  settings.formatter.zizmor.includes = [
    "actions/*/action.yml"
    "packages/quints/examples/.github/workflows/*.yml"
  ];
  settings.formatter.actionlint.includes = [
    "packages/quints/examples/.github/workflows/*.yml"
  ];

  settings.global.excludes = [
    "*.lock"
    ".basedpyright/*"
    "docs/schema/*"
  ];
}

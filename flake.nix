# Dev shells, formatter and checks from the Sealambda hub (sealambda/.github).
# Python stays with uv: `make check` is the gate for the code, and
# `nix flake check` adds the hub's formatting and tool-configuration checks.
{
  description = "quints: Swiss bookkeeping on Beancount";

  inputs = {
    hub.url = "github:sealambda/.github";
    nixpkgs.follows = "hub/nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      hub,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      eachSystem = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # No Python formatter here: ruff is `make format`'s job, at the version
      # uv.lock pins. typos still reads the Python files (see _typos.toml).
      treefmtFor = eachSystem (
        pkgs:
        hub.lib.mkTreefmt pkgs [
          {
            settings.global.excludes = [
              "*.lock"
              ".basedpyright/*"
              "docs/schema/*"
            ];
          }
        ]
      );
    in
    {
      devShells = eachSystem (
        pkgs:
        let
          inherit (pkgs.stdenv.hostPlatform) system;
        in
        {
          default = pkgs.mkShell {
            inputsFrom = [ hub.devShells.${system}.base ];
            packages = [
              pkgs.gnumake
              pkgs.uv
              treefmtFor.${system}.config.build.wrapper
            ];
            # uv builds the venv on the shell's Python, not a downloaded one.
            env = {
              UV_PYTHON = pkgs.python314.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
          };
          docs = hub.devShells.${system}.docs;
        }
      );

      formatter = eachSystem (pkgs: treefmtFor.${pkgs.stdenv.hostPlatform.system}.config.build.wrapper);

      checks = eachSystem (pkgs: {
        config-in-sync = hub.lib.mkConfigCheck pkgs self;
        formatting = treefmtFor.${pkgs.stdenv.hostPlatform.system}.config.build.check self;
      });
    };
}

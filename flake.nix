# Dev shell, formatter and format check. Python stays with uv: `make check` is
# the gate for the code. Nix adds the tools for everything else (CONTRIBUTING.md).
{
  description = "quints: Swiss bookkeeping on Beancount";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      treefmt-nix,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      eachSystem = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      treefmtFor = eachSystem (pkgs: treefmt-nix.lib.evalModule pkgs ./treefmt.nix);
    in
    {
      devShells = eachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.actionlint
            pkgs.gnumake
            pkgs.lychee
            pkgs.markdownlint-cli2
            pkgs.uv
            pkgs.zizmor
            treefmtFor.${pkgs.stdenv.hostPlatform.system}.config.build.wrapper
          ];
          # uv builds the venv on the shell's Python, not a downloaded one.
          env = {
            UV_PYTHON = pkgs.python314.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
          };
        };
      });

      formatter = eachSystem (pkgs: treefmtFor.${pkgs.stdenv.hostPlatform.system}.config.build.wrapper);

      checks = eachSystem (pkgs: {
        formatting = treefmtFor.${pkgs.stdenv.hostPlatform.system}.config.build.check self;
      });
    };
}

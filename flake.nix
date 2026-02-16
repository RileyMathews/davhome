{
  description = "davhome development shells";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowInsecurePredicate = pkg:
            builtins.elem (nixpkgs.lib.getName pkg) [ "python" ];
        };
      in {
        devShells.caldavtester = pkgs.mkShell {
          packages = with pkgs; [
            git
            python2
          ];

          shellHook = ''
            echo "Entered caldavtester shell (Python $(python2 --version 2>&1))."
            echo "Run: ./caldavtester-lab/bootstrap.sh"
          '';
        };
      });
}

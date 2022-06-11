with import <nixpkgs>{};

let
  pkgs = import (fetchTarball("channel:nixpkgs-unstable")) {};

in pkgs.mkShell {
  nativeBuildInputs = let
    env = pyPkgs : with pyPkgs; [
      poetry
    ];
  in with pkgs; [
    (python310.withPackages env)
  ];
  buildInputs = [
    pkgs.google-cloud-sdk
  ];
}


with import <nixpkgs>{};

let
  pkgs = import (fetchTarball("https://github.com/NixOS/nixpkgs/archive/nixos-22.11.tar.gz")) {};

in pkgs.mkShell {
  nativeBuildInputs = let
    env = pyPkgs : with pyPkgs; [
      poetry
    ];
  in with pkgs; [
    (python39.withPackages env)
  ];
  buildInputs = [
    pkgs.poetry
    pkgs.google-cloud-sdk
  ];
}


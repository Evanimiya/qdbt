{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.libreoffice
    pkgs.poppler_utils
    pkgs.sqlite
  ];
}

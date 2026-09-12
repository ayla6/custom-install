{
  lib,
  pkgs,
  python,
  callPackage,
  buildPythonApplication,
  fetchPypi,
  pyctr,
  pycryptodomex,
  pypng,
  tkinter,
  setuptools,
  events,
  zstandard,
  stdenv,
  save3ds,

  withGUI ? true,
}:

let
  save3ds_no_fuse = save3ds.override { withFUSE = false; };
in
buildPythonApplication rec {
  pname = "custominstall";
  version = "2.1";
  pyproject = true;

  src = builtins.path {
    path = ./.;
    name = "custominstall";
    filter =
      path: type:
      !(builtins.elem (baseNameOf path) [
        "build"
        "dist"
        "localtest"
        "__pycache__"
        "v"
        ".git"
        "_build"
        "custominstall.egg-info"
      ]);
  };

  doCheck = false;

  build-system = [ setuptools ];

  propagatedBuildInputs =
    [
      pyctr
      pycryptodomex
      setuptools
      events
      zstandard
    ]
    ++ lib.optionals (withGUI) [
      tkinter
    ];

  makeWrapperArgs = [ "--set CUSTOM_INSTALL_SAVE3DS_PATH ${save3ds_no_fuse}/bin/save3ds_fuse" ];

  preFixup = ''
    rm -r $out/lib/${python.libPrefix}/site-packages/custominstall/bin
    ${lib.optionalString (!withGUI) "rm $out/bin/custominstall-gui"}
  '';

  meta = with lib; {
    description = "Installs a title directly to an SD card for the Nintendo 3DS";
    homepage = "https://github.com/ihaveamac/custom-install";
    license = licenses.mit;
    platforms = platforms.unix;
    mainProgram = "custominstall";
  };
}

# Nix package for FrostByte (user-space tooling: GUI, daemon, CLI, keyd).
#
# NOTE: this packages the user-space tooling only. The vendored DKMS kernel
# driver (driver/acer-wmi-linuwu) and the two systemd units are NOT bundled —
# on NixOS they need a dedicated module (out-of-tree kernel module build).
# Without the driver the GUI/CLI run but cannot talk to the fans.
#
# Build with:  nix build
{ lib, stdenv, python3, makeWrapper }:

let
  py = python3.withPackages (ps: [ ps.pyqt6 ]);
in
stdenv.mkDerivation rec {
  pname = "frostbyte";
  version = "1.6.1";

  src = ./.;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    install -dm755 "$out/lib/frostbyte"
    install -m644 common/frostbyte_common.py common/frostbyte_gpu.py "$out/lib/frostbyte/"
    install -m755 daemon/frostbyte-daemon.py cli/frostctl.py cli/frostbyte-gpu-run \
      gui/frostbyte_gui.py keyd/frostbyte-keyd.py "$out/lib/frostbyte/"

    makeWrapper "${py}/bin/python3" "$out/bin/frostbyte" \
      --add-flags "$out/lib/frostbyte/frostbyte_gui.py"
    makeWrapper "${py}/bin/python3" "$out/bin/frostctl" \
      --add-flags "$out/lib/frostbyte/frostctl.py"
    makeWrapper "${py}/bin/python3" "$out/bin/frostbyte-keyd" \
      --add-flags "$out/lib/frostbyte/frostbyte-keyd.py"
    makeWrapper "${py}/bin/python3" "$out/bin/frostbyte-gpu-run" \
      --add-flags "$out/lib/frostbyte/frostbyte-gpu-run"

    install -Dm644 desktop/frostbyte.desktop \
      "$out/share/applications/frostbyte.desktop"
    install -Dm644 gui/frostbyte.svg \
      "$out/share/icons/hicolor/scalable/apps/frostbyte.svg"
    for sz in 16 24 32 48 64 96 128 256 512; do
      install -Dm644 packaging/icons/hicolor/''${sz}x''${sz}/apps/frostbyte.png \
        "$out/share/icons/hicolor/''${sz}x''${sz}/apps/frostbyte.png"
    done
    runHook postInstall
  '';

  meta = with lib; {
    description = "Open-source fan & thermal control for Acer Nitro/Predator laptops";
    homepage = "https://github.com/VanguardArts/frostbyte";
    license = licenses.gpl3Plus;
    mainProgram = "frostbyte";
    platforms = platforms.linux;
  };
}

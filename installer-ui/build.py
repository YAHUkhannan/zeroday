#!/usr/bin/env python3
"""Build the single-file installer from the backend and terminal UI assets."""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import tarfile


def archive(ui):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for relative in ("renderer.py", "disclaimer.txt", "assets/zeroday-disclaimer-frame.png",
                         "assets/zeroday-banner.png", "assets/zeroday-progress.png",
                         "assets/zeroday-banner-transparent.png", "assets/zeroday-progress-transparent.png",
                         "assets/sounds/move.wav", "assets/sounds/confirm.wav", "assets/sounds/cancel.wav"):
            data = (ui / relative).read_bytes()
            info = tarfile.TarInfo(relative)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(gzip.compress(buffer.getvalue(), mtime=0)).decode()


def main():
    parser = argparse.ArgumentParser(description="Build the single-file installer from its template and interface assets.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", type=Path)
    source.add_argument("--original", type=Path, help="Rebuild an already generated installer")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compatibility", type=Path, help="Validated client policy (for release maintenance and sandbox tests)")
    args = parser.parse_args()
    ui = Path(__file__).resolve().parent
    dist = ui.parent
    original = (args.template or args.original).read_text()
    marker = "# @ZERODAY_UI@"
    if marker not in original:
        prefix, rest = original.split("# -------------------------------------------------------------------- output", 1)
        _, body = rest.split("ui_floor_step()", 1)
        original = prefix + marker + "\n\nui_floor_step()" + body
    payload = archive(ui)
    encoded = "\n".join(payload[i:i+100] for i in range(0, len(payload), 100))
    embedded = ("# -------------------------------------------------------------------- output\n"
                "# Generated from installer/install.sh.in and installer-ui/.\n"
                'ui_unpack() { ui_archive_b64 | base64 -d | tar -xz -C "$1"; }\n'
                "ui_archive_b64() {\ncat <<'ZERODAY_UI_ARCHIVE'\n" + encoded +
                "\nZERODAY_UI_ARCHIVE\n}\n\n" + (ui / "terminal.sh").read_text())
    assert original.count(marker) == 1
    original = original.replace(marker, embedded)
    if "# @ZERODAY_COMPAT@" in original:
        policy = json.loads((args.compatibility or dist / "installer/steam-compatibility.json").read_text())
        checker = (dist / "installer/steam_client.py").read_text().replace(
            "POLICY = None  # @ZERODAY_STEAM_POLICY@", "POLICY = " + repr(policy))
        original = original.replace("# @ZERODAY_COMPAT@",
                                    "steam_compat() {\npython3 - \"$@\" <<'ZERODAY_STEAM_CHECKER'\n" + checker +
                                    "\nZERODAY_STEAM_CHECKER\n}\n")
    original = original.replace("@ZERODAY_VERSION@", (dist / "VERSION").read_text().strip())
    args.output.write_text(original)
    args.output.chmod(0o755)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Inspect Steam files and run its own updater before installing a compatible mod."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys

POLICY = None  # @ZERODAY_STEAM_POLICY@


def policy():
    if POLICY is not None:
        return POLICY
    return json.loads(Path(__file__).with_name("steam-compatibility.json").read_text())


def elf64(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
        return (len(header) == 20 and header[:6] == b"\x7fELF\x02\x01"
                and struct.unpack_from("<H", header, 18)[0] == 62)
    except OSError:
        return False


def digest(path):
    try:
        with path.open("rb") as stream:
            value = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
            return value.hexdigest()
    except OSError:
        return "missing"


def channel(root):
    beta = root / "package/beta"
    name = beta.read_text().strip() if beta.exists() else ""
    if name and not re.fullmatch(r"[a-z0-9_]{1,64}", name):
        raise ValueError("Unrecognized Steam update channel; select stable or beta in Steam first")
    return "steam_client_" + (name + "_" if name else "") + "steamrt64"


def manifest_version(text):
    # Valve's text manifest has its version directly inside the platform root.
    match = re.match(r'\s*"steamrt64"\s*\{\s*"version"\s*"([0-9]{1,12})"', text)
    if not match or int(match[1]) <= 0:
        raise ValueError("Steam returned an invalid native-client update manifest")
    return match[1]


def inspect(root):
    client, ui, exe = (root / "steamrt64" / name for name in ("steamclient.so", "steamui.so", "steam"))
    client_hash, ui_hash = digest(client), digest(ui)
    version = "unknown"
    selected = channel(root)
    for name in dict.fromkeys((selected, "steam_client_steamrt64")):
        try:
            version = manifest_version((root / "package" / (name + ".manifest")).read_text())
            break
        except (OSError, ValueError):
            pass
    supported = next((entry for entry in policy()["clients"]
                      if entry["steamclient_sha256"] == client_hash
                      and entry["steamui_sha256"] == ui_hash), None)
    compatible = bool(supported and all(elf64(path) for path in (client, ui, exe)) and os.access(exe, os.X_OK))
    return {"compatible": compatible, "build": version, "channel": selected,
            "steamclient_sha256": client_hash, "steamui_sha256": ui_hash,
            "validated_build": supported["build"] if compatible else None,
            "supported_builds": [entry["build"] for entry in policy()["clients"]]}


def plan(root, directory):
    installed = inspect(root)
    base = os.environ.get("ZERODAY_STEAM_UPDATE_URL", "https://cdn.steamstatic.com/client").rstrip("/")
    if not base.startswith("https://") and not re.match(r"http://(127\.0\.0\.1|localhost)(:|/)", base):
        raise ValueError("Steam update URL must use HTTPS (HTTP is allowed only for localhost tests)")
    result = subprocess.run(["curl", "-fsSL", "--connect-timeout", "10", "--max-time", "30",
                             "--max-filesize", "1048576", base + "/" + installed["channel"]],
                            capture_output=True, check=True, timeout=35)
    version = manifest_version(result.stdout.decode("utf-8"))
    if installed["build"] != "unknown" and int(version) < int(installed["build"]):
        raise ValueError("Available Steam is older than the installed client; refusing to downgrade")
    supported = any(entry["build"] == version for entry in policy()["clients"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "update-plan.json").write_text(json.dumps({"base": base, "build": version,
                                                           "channel": installed["channel"], "supported": supported}))
    (directory / "steam.manifest").write_bytes(result.stdout)
    print(version, int(supported), sep="\t")


def run_updater(root, directory):
    target = json.loads((directory / "update-plan.json").read_text())
    if channel(root) != target["channel"]:
        raise ValueError("Steam update channel changed after the compatibility check; run the installer again")
    cfg = root / "steam.cfg"
    cfg_backup = directory / "steam.cfg.before-update"
    had_cfg = cfg.exists()
    if had_cfg:
        import shutil
        shutil.copy2(cfg, cfg_backup)
    marker = root / ".steam-enable-steamrt64-client"
    had_marker = marker.exists()
    process = None

    def interrupted(signum, frame):
        raise InterruptedError("Steam update interrupted")

    old_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        # Keep all unrelated configuration; only the bootstrap inhibition is temporary.
        if had_cfg:
            cfg.write_text("\n".join(line for line in cfg.read_text().splitlines()
                                     if not re.match(r"\s*BootStrapper(?:InhibitAll|InhibitUpdateOnLaunch|ForceSelfUpdate)\s*=", line, re.I)) + "\n")
        marker.touch()
        launcher = root / "steam.sh"
        if launcher.is_file():
            command = ["bash", str(launcher)]
        else:
            launcher = next((root / name for name in ("steamrt64/steam", "ubuntu12_32/steam")
                             if os.access(root / name, os.X_OK)), None)
            if launcher is None:
                raise ValueError("No Steam bootstrapper found; repair Steam before installing")
            command = [str(launcher)]
        command += ["-forcesteamupdate", "-forcepackagedownload", "-overridepackageurl", target["base"], "-exitsteam"]
        env = {key: value for key, value in os.environ.items()
               if key not in ("LD_AUDIT", "LD_PRELOAD", "LD_LIBRARY_PATH", "STEAM_RUNTIME", "STEAM_RUNTIME_LIBRARY_PATH")}
        env["STEAM_FORCE_CLIENT"] = "steamrt64"
        process = subprocess.Popen(command, cwd=root, env=env, start_new_session=True,
                                   stdin=subprocess.DEVNULL, close_fds=True)
        status = process.wait(timeout=900)
        # The native bootstrapper's -exitsteam path returned 254 after a
        # complete real update in the isolated integration test. Success still
        # requires the checked build, native files and (when supported) hashes.
        if status not in (0, 254):
            raise ValueError(f"Steam updater exited with status {status}")
        print(f"Steam updater exited with status {status}; verifying installed files", flush=True)
    finally:
        if process is not None:
            # A restarting bootstrapper can leave descendants after its parent exits.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                finally:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if had_cfg:
            import shutil
            shutil.copy2(cfg_backup, cfg)
        else:
            cfg.unlink(missing_ok=True)
        if not had_marker:
            marker.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, old_term)
    actual = inspect(root)
    if actual["build"] != target["build"]:
        raise ValueError(f"Steam did not install checked build {target['build']} (found {actual['build']}); ZeroDay stays disabled")
    if not all(elf64(root / "steamrt64" / name) for name in ("steam", "steamclient.so", "steamui.so")):
        raise ValueError("Steam update did not produce a complete native client; ZeroDay stays disabled")
    if target["supported"] and not actual["compatible"]:
        raise ValueError("Updated Steam files do not match this release's validated client; ZeroDay stays disabled")
    print("Steam update verified:", json.dumps(actual), flush=True)


def validate_payload(root):
    for name, expected in policy()["payload"].items():
        path = root / name
        if not elf64(path) or digest(path) != expected:
            raise ValueError(f"{name} does not match this release's validated x86-64 payload")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "check", "plan", "update", "payload"))
    parser.add_argument("root", type=Path)
    parser.add_argument("directory", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "payload":
        validate_payload(args.root)
        return 0
    if args.action == "check":
        roots = (args.root / ".steam/steam", args.root / ".local/share/Steam",
                 args.root / ".var/app/com.valvesoftware.Steam/.local/share/Steam")
        root = next((path for path in roots if path.is_dir()), None)
        if root is None:
            raise ValueError("No Steam installation found")
        result = inspect(root)
        print(json.dumps(result, indent=2))
        return 0 if result["compatible"] else 3
    if args.action == "inspect":
        result = inspect(args.root)
        print(int(result["compatible"]), result["build"], sep="\t")
    elif args.action == "plan":
        plan(args.root, args.directory)
    else:
        run_updater(args.root, args.directory)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Steam compatibility check: {error}", file=sys.stderr)
        sys.exit(1)

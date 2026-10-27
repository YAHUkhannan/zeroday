# Installation

[← ZeroDay](../README.md)

## Requirements

- Linux x86-64 with Python 3 and curl.
- An installed Steam client with its bootstrapper available. ZeroDay uses
  the native 64-bit client; the installer can update Steam when necessary.
- Pillow for artwork. The installer works in text mode without it.

## Install or update

On Steam Deck, switch to Desktop Mode and open a terminal. Run:

```sh
curl -fsSL https://github.com/YAHUkhannan/zeroday/releases/latest/download/install.sh | bash
```

Choose **Install**. If administrator authentication is needed, it happens while
Steam is still running. The installer checks its download and client
compatibility, closes Steam, installs ZeroDay and starts Steam again.

Running the installer again updates an existing installation. Current config
values, keys and ticket cache are kept; missing settings are added. An existing
Decky Loader installation is reused.

If the installed Steam files match this release's validated client, Steam is
left unchanged and only ZeroDay is updated. If Steam is incompatible, the
installer checks Valve's update manifest for the selected channel. A supported
update is installed automatically with Steam's own bootstrapper, then checked
again before ZeroDay is installed.

If the available update is unsupported, choose **Update Steam anyway** or
**Cancel**. Cancel leaves Steam and the mod unchanged. Update anyway updates
Steam and leaves ZeroDay disabled, including an existing mod installation;
it does not install the new mod. Install a compatible ZeroDay release to
enable it again. `--yes` does not accept this separate choice.

Version 1.2.0 validates Steam build **1792618285** by checking the SHA-256 hashes
of both `steamrt64/steamclient.so` and `steamrt64/steamui.so`. A newer build is
not assumed compatible. Compatible installations need no update-manifest request.

For detailed artwork, use a SIXEL-capable terminal. Other truecolor terminals
use half-block artwork. Menu audio is optional.

## Options

Download the installer if you want to pass options:

```sh
curl -fL https://github.com/YAHUkhannan/zeroday/releases/latest/download/install.sh -o install.sh
bash install.sh --no-plugin
```

| Option | Effect |
| --- | --- |
| `--yes` | Skip the opening choice. Administrator authentication can still be required. |
| `--no-plugin` | Skip all Decky and plugin changes. |
| `--no-restart` | Leave Steam stopped after installation. |
| `--uninstall` | Remove ZeroDay and restore clean launcher backups when available. |
| `--check` | Print local Steam compatibility as JSON without installing or contacting a server. Exit 0 means compatible; 3 means unsupported. |
| `--version` | Print the installer version. |
| `--update-steam-anyway` | Explicitly accept an unsupported Steam update and leave ZeroDay disabled. |

## Remove

With the downloaded installer:

```sh
bash install.sh --uninstall
```

Configuration, keys and ticket cache remain available for a later installation.

## Recovery

A failed installation restores the previous files. If a file cannot be
restored, the installer keeps its recovery copies and prints their location.
The installation log is `~/.local/share/ZeroDay/install.log`.

An attempted Steam update can change Steam's own files, which the mod installer
cannot roll back. On failure or a changed update build, the existing mod files
and settings are restored and the mod stays disabled. Steam's update settings
are restored after the bootstrapper exits. The log records updater output and
the verification failure. Re-run a compatible release to recover.

[Troubleshooting](TROUBLESHOOTING.md)

# Installation

[← ZeroDay](../README.md)

## Requirements

- Linux x86-64 with Python 3 and curl.
- An installed native 64-bit Steam client: `steamrt64/steam` and
  `steamrt64/steamclient.so` in the Steam installation.
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

[Troubleshooting](TROUBLESHOOTING.md)

# Troubleshooting

[← ZeroDay](../README.md)

## Installation

Start with the message printed by the installer. Invalid downloads and update
compatibility choices are reported before Steam is closed. The installation log is
`~/.local/share/ZeroDay/install.log`.

If a restore fails, keep the recovery directory named in the error message.

Run `bash install.sh --check` for an offline compatibility report. Unsupported
clients return exit code 3. If a Steam update failed or an unsupported update
was explicitly chosen, `~/.local/share/ZeroDay/client-update-pending` keeps the
mod disabled through launcher repairs. Install a compatible release to clear
that state after verification. Do not delete the marker to force an unsupported
client to load the mod.

## Steam and games

The runtime log is `~/.ZeroDay.log`. It starts over each time Steam restarts,
so copy it before restarting if you need to preserve a failed launch.
A successful load reports `Loaded successfully` and `Hooks: 41 of 41 placed`.

On Steam Deck, the launcher preserves `-steamdeck` in Desktop Mode so Steam + X
can open the on-screen keyboard. A custom Steam launcher should preserve that
flag too.

## Report a problem

[Open an issue](https://github.com/YAHUkhannan/zeroday/issues) with:

- Your Linux distribution, desktop or Game Mode, and Steam client version.
- The action that failed, the exact error and any affected game.
- Relevant installation or runtime log lines from that attempt.

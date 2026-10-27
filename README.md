<p align="center">
  <img src="installer-ui/assets/zeroday-banner.png" alt="ZeroDay" width="100%">
</p>

<p align="center">
  <strong>A Steam mod for Linux and Steam Deck.</strong>
</p>

<p align="center">
  <a href="https://github.com/YAHUkhannan/zeroday/releases/latest">Download</a>
  &nbsp;·&nbsp;
  <a href="docs/INSTALL.md">Installation guide</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/YAHUkhannan/zeroday/issues">Report an issue</a>
</p>

## Install

Open a terminal in Desktop Mode and run:

```sh
curl -fsSL https://github.com/YAHUkhannan/zeroday/releases/latest/download/install.sh | bash
```

Choose **Install**. The installer handles setup and restarts Steam when it is
finished. Existing settings, keys and ticket cache are preserved.

**Requires:** Linux x86-64, Python 3, curl and the native 64-bit Steam client.
Pillow enables the artwork; a text interface is available without it.

## Included

- Steam library and download integration, with an optional Decky plugin.
- Cached launch tickets with background refresh.
- Steam Deck support in Desktop Mode and Game Mode.
- An animated installer with menu sounds, launcher repair and recovery on failure.

## First stable release

[**ZeroDay v1.1.8**](https://github.com/YAHUkhannan/zeroday/releases/latest)
is the starting point for this release line, with the complete installer and
native 64-bit client fixes. Get the installer from the latest release above.

## Documentation

[Install, update and remove](docs/INSTALL.md)
&nbsp;·&nbsp;
[Troubleshooting](docs/TROUBLESHOOTING.md)
&nbsp;·&nbsp;
[Build the installer](docs/DEVELOPMENT.md)

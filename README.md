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

**Requires:** Linux x86-64, Python 3, curl and an installed Steam client.
Pillow enables the artwork; a text interface is available without it.

Compatible Steam installations are kept unchanged. When Steam needs an update,
the installer checks the available build and updates automatically if it is
supported. An unsupported update offers **Update Steam anyway** or **Cancel**;
updating anyway leaves ZeroDay disabled.

## Included

- Steam library and download integration. GET GAME is on the store page. Remuvo and Remove game are in a game's Manage menu.
- Cached launch tickets with background refresh.
- Steam Deck support in Desktop Mode and Game Mode.
- An animated installer with menu sounds, launcher repair and recovery on failure.

## Latest release

[**ZeroDay v1.3.1**](https://github.com/YAHUkhannan/zeroday/releases/latest)
improves subprocess timeouts, file-watch recovery, numeric parsing and logging.
See the [release notes](docs/RELEASE-NOTES.md) for details and validation limits.
Steam build **1792618285** remains the validated client. The in-client store
and library menus remain included; the installer does not install Decky Loader.

## Documentation

[Install, update and remove](docs/INSTALL.md)
&nbsp;·&nbsp;
[Troubleshooting](docs/TROUBLESHOOTING.md)
&nbsp;·&nbsp;
[Build the installer](docs/DEVELOPMENT.md)

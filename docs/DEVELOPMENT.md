# Build the installer

[← ZeroDay](../README.md)

The installer backend is `installer/install.sh.in`. Its interface, artwork and
audio are in `installer-ui/`. Native libraries and the Decky plugin are
distributed in release assets. The launcher and its repair script are tracked
under `payload/`; the remaining payload files come from the validated release.

## Generate

From the repository root:

```sh
python3 installer-ui/build.py --template installer/install.sh.in --output install.sh
```

This produces the standalone installer with the interface assets embedded.
It also embeds `VERSION`, the Steam checker from `installer/steam_client.py`,
and the validated files in `installer/steam-compatibility.json`. The default
payload URL is pinned to that release's tag so an older installer cannot
silently install a newer payload with a different compatibility policy.

## Preview and interface checks

```sh
bash installer-ui/preview.sh
python3 installer-ui/test_ui.py
```

The preview runs the interface without installing ZeroDay. See the
[interface documentation](../installer-ui/README.md) for rendering and audio details.

## Installer checks and packaging

Unpack `ZeroDay-payload.tar.gz` from the current release in the repository root,
so `payload/` exists. Then run:

```sh
./make-release.sh --selftest
```

This generates the installer, checks the shell scripts, runs the isolated
installation tests and packages the release into `release/`. Tests use temporary
home directories, launcher paths and service stubs.

The packer and installer reject native libraries whose hashes differ from the
release policy. Refresh the ignored local `payload/` from the validated build
before packaging. Backend tests generate a separate installer with fixture
client hashes; production compatibility data is unchanged.

To run only the installation checks after generating `install.sh`:

```sh
python3 installer/test_install.py
```

## Validate a client update

Add a client build and its exact `steamclient.so` and `steamui.so` hashes to
`installer/steam-compatibility.json` only after the native signature, ABI and
feature regressions pass against those files. Build numbers alone do not prove
compatibility. Update the payload hashes when releasing a new native build.
Change `VERSION`, regenerate, and package into an explicit directory:

```sh
./make-release.sh --selftest --output release-1.2.0
bash release-1.2.0/install.sh --check
```

Steam updates use the current `package/beta` channel, or stable when it is empty.
`ZERODAY_STEAM_UPDATE_URL` can point at a local HTTP test server; production
overrides require HTTPS. The available build is checked before closing Steam,
and the installed files and build are verified again after the bootstrapper exits.
If the server advances during the update, the installer fails with the mod disabled.

# Build the installer

[← ZeroDay](../README.md)

The installer backend is `installer/install.sh.in`. Its interface, artwork and
audio are in `installer-ui/`. Native libraries and the Decky plugin are
distributed in release assets.

## Generate

From the repository root:

```sh
python3 installer-ui/build.py --template installer/install.sh.in --output install.sh
```

This produces the standalone installer with the interface assets embedded.

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

To run only the installation checks after generating `install.sh`:

```sh
python3 installer/test_install.py
```

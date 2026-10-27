#!/usr/bin/env bash
#
# Pack the ZeroDay release artifacts.
#
#   ./make-release.sh            build release/ (payload tarball + install.sh + SHA256SUMS)
#   ./make-release.sh --selftest also run install.sh end-to-end against a sandbox
#
# Upload everything in release/ to a GitHub release, then point people at:
#
#   curl -fsSL https://github.com/<user>/<repo>/releases/latest/download/install.sh | bash
#
# The payload tarball must keep `payload/` at its root — install.sh looks for
# $WORK/payload/... after unpacking.

set -euo pipefail

DIST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$DIST/release"
TS="$(date +%Y%m%d-%H%M%S)"

SELFTEST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --selftest) SELFTEST=1; shift ;;
        --output) [ -n "${2:-}" ] || { echo '--output needs a directory' >&2; exit 2; }; OUT=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

python3 "$DIST/installer-ui/build.py" --template "$DIST/installer/install.sh.in" --output "$DIST/install.sh"
bash -n "$DIST/install.sh" "$DIST/payload/zeroday-repatch.sh" "$DIST/payload/steam-jupiter.patched"
if [ "$SELFTEST" -eq 1 ]; then python3 "$DIST/installer/test_install.py"; fi

echo "==> packing"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

for f in ZeroDay.so library-inject.so steam-jupiter.patched zeroday-repatch.sh zeroday-steam.hook wudrm-proxy.py; do
	[ -f "$DIST/payload/$f" ] || { echo "missing payload/$f" >&2; exit 1; }
done

# The service key can mint, revoke and ban licences. It must never ship, so the
# packing stops here rather than publishing a payload that carries one.
if grep -rqIE 'service_role|sb_secret_|service_key' "$DIST/payload" 2>/dev/null; then
	echo "refusing to pack: something in payload/ looks like a licence service key" >&2
	grep -rIlIE 'service_role|sb_secret_|service_key' "$DIST/payload" >&2
	exit 1
fi
[ -d "$DIST/payload/ZeroDay-plugin" ] || { echo "missing payload/ZeroDay-plugin" >&2; exit 1; }
[ -f "$DIST/payload/res/config.yaml" ] || { echo "missing payload/res/config.yaml" >&2; exit 1; }

tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' --exclude='*.bak*' \
    -czf "$OUT/ZeroDay-payload.tar.gz" -C "$DIST" \
    payload/LICENSE payload/ZeroDay.so payload/library-inject.so \
    payload/steam-jupiter.patched payload/zeroday-repatch.sh payload/zeroday-steam.hook \
    payload/wudrm-proxy.py payload/res/config.yaml payload/ZeroDay-plugin
install -m 0755 "$DIST/install.sh" "$OUT/install.sh"

# Support tool, so a customer can be handed one curl command. Deliberately not
# part of the payload: the installer never runs it.
assets="install.sh ZeroDay-payload.tar.gz"
if [ -f "$DIST/../tools/zeroday-doctor.sh" ]; then
	install -m 0755 "$DIST/../tools/zeroday-doctor.sh" "$OUT/zeroday-doctor.sh"
	assets="$assets zeroday-doctor.sh"
fi

( cd "$OUT" && sha256sum $assets > SHA256SUMS )

echo
echo "==> release/ ($TS)"
ls -la "$OUT"
echo
cat "$OUT/SHA256SUMS"
echo
echo "Upload these as release assets:"
echo "  $assets"

#!/usr/bin/env bash
# Artwork preview and optional real download test. Never runs an installer.
set -euo pipefail
UI_SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OPT_YES=0
OPT_PREVIEW_URL=""
case "${1:-}" in
	--download)
		[ -n "${2:-}" ] && [ "$#" -eq 2 ] || { echo 'usage: preview.sh [--download URL]' >&2; exit 2; }
		OPT_PREVIEW_URL=$2 ;;
	-h|--help) echo 'usage: preview.sh [--download URL]'; exit 0 ;;
	'') ;;
	*) echo 'usage: preview.sh [--download URL]' >&2; exit 2 ;;
esac
ui_unpack() {
	cp -- "$UI_SOURCE/renderer.py" "$1/renderer.py"
	cp -R -- "$UI_SOURCE/assets" "$1/assets"
}
source "$UI_SOURCE/terminal.sh"
ui_preview

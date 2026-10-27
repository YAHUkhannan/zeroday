#!/bin/bash
# zeroday-repatch.sh — re-apply ZeroDay launcher patches after package updates,
# SteamOS image updates, or a crash that puts the stock steam-jupiter back.
# Idempotent. Runs as root from a pacman PostTransaction hook and from a login
# service. Always exits 0.
set -u

CANON="${ZERODAY_JUPITER_CANON:-/usr/local/share/zeroday/steam-jupiter.patched}"
JUP="${ZERODAY_JUPITER:-/usr/bin/steam-jupiter}"
BIN_STEAM="${ZERODAY_BIN_STEAM:-/usr/lib/steam/bin_steam.sh}"
AUDIT_LINE='export LD_AUDIT="$HOME/.local/share/ZeroDay/library-inject.so:$HOME/.local/share/ZeroDay/ZeroDay.so"; if [ -f "$HOME/.local/share/ZeroDay/client-update-pending" ]; then unset LD_AUDIT; fi; export STEAM_FORCE_CLIENT=steamrt64'
TS=$(date +%Y%m%d-%H%M%S)-$$

log() { echo "[zeroday-repatch] $*"; }

# SteamOS ships /usr read-only, so a repair after an image update has to ask for
# the write first. Only done when a write is actually needed, not on every run.
needs_write() {
	[ -f "$JUP" ] && [ -f "$CANON" ] && ! cmp -s "$JUP" "$CANON" && return 0
	[ -f "$BIN_STEAM" ] && ! grep -qxF "$AUDIT_LINE" "$BIN_STEAM" 2>/dev/null && return 0
	return 1
}

unlock_image() {
	# Probed beside the launcher itself rather than at a hardcoded /usr/bin: same
	# place in production, and it keeps the script testable against a sandbox.
	local probe; probe="$(dirname "$JUP")/.zeroday-write-probe.$$"
	touch "$probe" 2>/dev/null && { rm -f "$probe"; return 0; }
	command -v steamos-readonly >/dev/null 2>&1 || return 1
	log "asking SteamOS for a writable /usr"
	steamos-readonly disable >/dev/null 2>&1 || true
	touch "$probe" 2>/dev/null && { rm -f "$probe"; return 0; }
	return 1
}

if needs_write; then
	if ! unlock_image; then
		log "/usr is read-only and cannot be unlocked — nothing repaired"
		exit 0
	fi
fi

# 1. steam-jupiter: restore the patched build if the package or a crash replaced
#    it with the stock launcher (that one deletes the Steam install). Compared by
#    content against the canonical copy rather than by a revision string: the old
#    revision check named an older revision, so it always reported "not patched",
#    rewrote a good launcher and left a backup pile behind on every run.
if [ -f "$CANON" ] && [ -f "$JUP" ]; then
	if cmp -s "$JUP" "$CANON"; then
		log "jupiter already patched"
	else
		cp -p "$JUP" "$JUP.bak-syu-$TS"
		cp -p "$CANON" "$JUP"
		chmod 755 "$JUP"
		log "jupiter re-patched (rollback: $JUP.bak-syu-$TS)"
	fi
else
	log "canonical or jupiter missing — skipping jupiter patch"
fi

# 2. bin_steam.sh: restore the LD_AUDIT layer if the package rewrote it.
if [ -f "$BIN_STEAM" ]; then
	if ! grep -qxF "$AUDIT_LINE" "$BIN_STEAM" 2>/dev/null; then
		patched=$(mktemp "$(dirname "$BIN_STEAM")/.zeroday-repatch.XXXXXXXX") || exit 0
		if awk -v line="$AUDIT_LINE" '
			NR==1 {print; print line; next}
			/^[[:space:]]*export[[:space:]]+LD_AUDIT=.*(SLSsteam|ZeroDay)/ {next}
			{print}
		' "$BIN_STEAM" > "$patched" &&
			cp -p "$BIN_STEAM" "$BIN_STEAM.bak-syu-$TS" &&
			chmod --reference="$BIN_STEAM" "$patched" && mv -f "$patched" "$BIN_STEAM"; then
			log "bin_steam.sh re-patched (rollback: $BIN_STEAM.bak-syu-$TS)"
		else
			log "could not repair bin_steam.sh"
			rm -f "$patched"
		fi
	else
		log "bin_steam.sh already patched"
	fi
else
	log "bin_steam.sh absent — nothing to patch"
fi

exit 0

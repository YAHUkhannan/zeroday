# Shared terminal UI only. The caller supplies installer actions separately.
INTERACTIVE=0
UI_LIVE=0
UI_FD=1
UI_PCT=0
UI_PHASE=""
UI_WORK=0
UI_RESIZE=0
UI_ROWS=24
UI_COLS=80
UI_PID=""
UI_FETCH_PID=""
UI_FETCH_PART=""
UI_FETCH_HEADER=""
UI_TMP=""
UI_MODE="install"
UI_KEY=""
UI_SAVED_STTY=""
UI_BACKGROUND_OLD=""
c_reset=""; c_ok=""; c_warn=""; c_err=""; c_dim=""

# stdin may be a pipe containing the script; only /dev/tty supplies input.
if [ "${OPT_YES:-0}" -eq 0 ] && [ "${TERM:-dumb}" != dumb ] && command -v python3 >/dev/null 2>&1 && { : <>/dev/tty; } 2>/dev/null; then
	INTERACTIVE=1
	exec 3<>/dev/tty
	UI_FD=3
elif [ -t 1 ]; then
	c_reset=$'\033[0m'; c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_dim=$'\033[2m'
fi

ui() { printf "$@" >&"$UI_FD"; }

ui_send() {
	[ -n "$UI_PID" ] && kill -0 "$UI_PID" 2>/dev/null || return 0
	local separator="" value
	for value in "$@"; do
		value=${value//$'\036'/ }; value=${value//$'\037'/ }
		printf '%s%s' "$separator" "$value" >&5
		separator=$'\037'
	done
	printf '\036' >&5
}

ui_start() {
	[ "$INTERACTIVE" -eq 1 ] || return 0
	[ -z "$UI_PID" ] || return 0
	local da="" cell="" background="" cw=9 ch=18
	local -a graphics=()
	if [ -z "$UI_TMP" ]; then
		UI_TMP=$(mktemp -d "${TMPDIR:-/tmp}/zeroday-ui.XXXXXXXX")
		ui_unpack "$UI_TMP"
	fi
	UI_SAVED_STTY="$(stty -g <&3 2>/dev/null || true)"
	stty -echo -icanon min 1 time 0 <&3 2>/dev/null || true
	# Query rather than guessing from TERM. Unsupported terminals use truecolor
	# half-block art, and systems without Pillow still get a working text bar.
	ui '\033[c'
	IFS= read -rs -t 0.25 -d c da <&3 || true
	if [[ "${da};" == *';4;'* ]]; then
		ui '\033[16t'
		IFS= read -rs -t 0.25 -d t cell <&3 || true
		if [[ "$cell" =~ 6\;([0-9]+)\;([0-9]+) ]]; then
			ch=${BASH_REMATCH[1]}; cw=${BASH_REMATCH[2]}
			graphics=(--sixel)
		fi
	fi
	# Konsole reserves a background cell beyond a SIXEL's right edge. Match
	# that cell to the canvas, preserving the exact previous terminal color.
	ui '\033]11;?\a'
	IFS= read -rs -t 0.2 -d $'\a' background <&3 || true
	if [[ "$background" =~ (rgb:[0-9a-fA-F]+/[0-9a-fA-F]+/[0-9a-fA-F]+) ]]; then
		UI_BACKGROUND_OLD=${BASH_REMATCH[1]}
		ui '\033]11;#000000\a'
	fi
	rm -f "$UI_TMP/events" "$UI_TMP/screen-ready"
	mkfifo -m 600 "$UI_TMP/events"
	python3 "$UI_TMP/renderer.py" --fd "$UI_FD" --assets "$UI_TMP/assets" --ready-file "$UI_TMP/screen-ready" --cell-width "$cw" --cell-height "$ch" "${graphics[@]}" < "$UI_TMP/events" 2>"$UI_TMP/renderer.log" &
	UI_PID=$!
	exec 5>"$UI_TMP/events"
	trap 'UI_RESIZE=1' WINCH
}

ui_pause() {
	if [ -n "$UI_PID" ]; then
		# EOF also works when the renderer already exited. Writing a stop record
		# to a dead reader could raise SIGPIPE during terminal restoration.
		exec 5>&-
		wait "$UI_PID" 2>/dev/null || true
		UI_PID=""
	fi
	if [ -n "$UI_BACKGROUND_OLD" ]; then
		ui '\033]11;%s\a' "$UI_BACKGROUND_OLD"
		UI_BACKGROUND_OLD=""
	fi
	[ -z "$UI_SAVED_STTY" ] || stty "$UI_SAVED_STTY" <&"$UI_FD" 2>/dev/null || true
}

ui_restore() {
	if [ -n "$UI_FETCH_PID" ]; then
		kill "$UI_FETCH_PID" 2>/dev/null || true
		wait "$UI_FETCH_PID" 2>/dev/null || true
		UI_FETCH_PID=""
	fi
	[ -z "$UI_FETCH_PART" ] || rm -f -- "$UI_FETCH_PART"
	[ -z "$UI_FETCH_HEADER" ] || rm -f -- "$UI_FETCH_HEADER"
	UI_FETCH_PART=""; UI_FETCH_HEADER=""
	ui_pause
	trap - WINCH
	if [ -n "$UI_TMP" ]; then
		rm -rf -- "$UI_TMP"
		UI_TMP=""
	fi
}

ui_draw_progress() {
	[ "$INTERACTIVE" -eq 1 ] && [ "$UI_LIVE" -eq 1 ] || return 0
	ui_start
	local status=busy
	[ "$UI_PCT" -lt 1000 ] || status=done
	ui_send state "$UI_PHASE" "$UI_PCT" "" "$status"
}

ui_push() {
	[ "$INTERACTIVE" -eq 1 ] && [ "$UI_LIVE" -eq 1 ] || return 0
	ui_send log "$1" "$2"
}

ui_frames_build() { :; }

ui_key() {
	local key="" rest="" status
	while true; do
		if IFS= read -rsN1 -t 0.2 -u "$UI_FD" key; then break; else status=$?; fi
		# Timeouts leave room for signal traps. EOF closes the preview.
		if [ "$status" -le 128 ]; then UI_KEY=q; return 0; fi
	done
	case "$key" in
		$'\r'|$'\n') UI_KEY=$'\025' ;;
		$'\033') IFS= read -rsN2 -t 0.05 -u "$UI_FD" rest || true; UI_KEY=$rest ;;
		*) UI_KEY=$key ;;
	esac
}

ui_arm() {
	UI_MODE="${1:-install}"
	[ "$INTERACTIVE" -eq 1 ] || return 0
	ui_start
	local key selection=0
	rm -f "$UI_TMP/screen-ready"
	ui_send gate "$UI_MODE" "$selection"
	while true; do
		ui_key
		key=$UI_KEY
		case "$key" in
			'[C') selection=1 ;;
			$'\t') selection=$((1-selection)) ;;
			'[D') selection=0 ;;
			q|Q) ui_send sound cancel; return 1 ;;
			$' '|$'\025')
				if [ "$selection" -ne 0 ]; then ui_send sound cancel; return 1; fi
				UI_LIVE=1; UI_PCT=0; UI_PHASE="Getting ready"
				ui_draw_progress
				# Finish the visual transition before a password prompt or another
				# interactive installer can take ownership of the terminal.
				while [ ! -f "$UI_TMP/screen-ready" ]; do
					kill -0 "$UI_PID" 2>/dev/null || return 1
					sleep 0.05
				done
				return 0 ;;
		esac
		ui_send gate "$UI_MODE" "$selection"
	done
}

ui_fetch() {
	local dest="$1" url="$2" lo="${3:-0}" hi="${4:-1000}"
	local hdr partial total=0 sz=0 result=0 label
	# Download into a sibling temporary file. Errors leave the destination intact.
	partial=$(mktemp "${dest}.part.XXXXXXXX") || return 1
	hdr=$(mktemp) || { rm -f -- "$partial"; return 1; }
	UI_FETCH_PART=$partial; UI_FETCH_HEADER=$hdr
	label="Downloading ${dest##*/}"
	ui_send download "$label" 0 0 begin
	curl -fsSL --connect-timeout 20 --max-redirs 10 -D "$hdr" -o "$partial" "$url" &
	UI_FETCH_PID=$!
	while kill -0 "$UI_FETCH_PID" 2>/dev/null; do
		# Each redirect starts a new response. A Content-Length from a redirect
		# or proxy CONNECT must never become the size of the final response.
		total=$(awk '
			/^HTTP\// {n=0; chunked=0; final=($2 >= 200 && $2 < 300)}
			tolower($1)=="content-length:" && $2 ~ /^[0-9]+\r?$/ {gsub(/\r/,"",$2); n=$2}
			tolower($1)=="transfer-encoding:" && tolower($0) ~ /chunked/ {chunked=1}
			END {printf "%.0f\n", (final && !chunked) ? n : 0}' "$hdr")
		sz=$(stat -c%s "$partial" 2>/dev/null || echo 0)
		ui_send download "$label" "$sz" "$total" busy
		sleep 0.1
	done
	wait "$UI_FETCH_PID" || result=$?
	UI_FETCH_PID=""
	sz=$(stat -c%s "$partial" 2>/dev/null || echo 0)
	rm -f -- "$hdr"
	UI_FETCH_HEADER=""
	if [ "$result" -ne 0 ]; then
		ui_send download "$label" "$sz" "$total" error
		rm -f -- "$partial"
		UI_FETCH_PART=""
		return "$result"
	fi
	if ! mv -f -- "$partial" "$dest"; then
		ui_send download "$label" "$sz" "$total" error
		rm -f -- "$partial"
		UI_FETCH_PART=""
		return 1
	fi
	UI_PCT=$hi
	UI_FETCH_PART=""
	ui_send download "$label" "$sz" "$sz" done
}

ui_preview() {
	[ "$INTERACTIVE" -eq 1 ] || { printf 'Preview needs an interactive terminal.\n' >&2; return 2; }
	trap ui_restore EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM
	while true; do
	ui_arm preview || return 0
	if [ -n "${OPT_PREVIEW_URL:-}" ]; then
		ui_fetch "$UI_TMP/preview-download" "$OPT_PREVIEW_URL" 0 1000 || {
			ui_send log fail "Download failed; no installation was performed"
			ui_key >/dev/null
			return 1
		}
	else
		local i
		ui_send log log "Preview only — no installation changes"
		for ((i=0; i<=100; i++)); do
			ui_send state "Download appearance · preview" "$((i*10))" "Demonstration; no files are being downloaded" busy
			sleep 0.07
		done
	fi
	ui_send state "Preview complete" 1000 "R to replay / any other key to close" done
	ui_key >/dev/null
	case "$UI_KEY" in r|R) ;; *) return 0 ;; esac
	done
}

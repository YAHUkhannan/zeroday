#!/usr/bin/env python3
"""Offline tests: local HTTP downloads, terminal rendering and cleanup."""
import hashlib
import http.server
import importlib.util
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest import mock
import fcntl

os.environ["ZERODAY_UI_SOUND"] = "0"
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("renderer", ROOT / "renderer.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
BODY = b"ZeroDay UI download test.\n" * 32768


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/chunked")
            self.send_header("Content-Length", "987654321")
            self.end_headers()
            return
        if self.path == "/error":
            self.send_error(503)
            return
        self.send_response(200)
        if self.path == "/chunked":
            self.protocol_version = "HTTP/1.1"
            self.send_header("Transfer-Encoding", "chunked")
        elif self.path != "/unknown":
            self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        try:
            for i in range(0, len(BODY), 16384):
                data = BODY[i:i+16384]
                if self.path == "/chunked":
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()
                time.sleep(0.012)
            if self.path == "/chunked":
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


class Downloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def fetch(self, endpoint, initial=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            events = Path(tmp) / "events"
            if initial is not None:
                path.write_bytes(initial)
            command = '''set -euo pipefail
OPT_YES=1
source "$1/terminal.sh"
ui_send() { local sep="" arg; for arg in "$@"; do printf '%s%s' "$sep" "$arg" >> "$EVENTS"; sep=$'\\037'; done; printf '\\036' >> "$EVENTS"; }
ui_fetch "$2" "$3" 0 1000
'''
            proc = subprocess.run(["bash", "-c", command, "test", str(ROOT), str(path), self.base+endpoint],
                                  env={**os.environ, "EVENTS": str(events)}, capture_output=True, timeout=10)
            records = [r.split("\x1f") for r in events.read_text().split("\x1e") if r]
            data = path.read_bytes() if path.exists() else None
            leftovers = list(Path(tmp).glob("*.part.*"))
            return proc, records, data, leftovers

    def test_known_length_has_measured_monotonic_progress(self):
        proc, records, data, leftovers = self.fetch("/known")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data, BODY)
        busy = [r for r in records if r[-1] == "busy" and int(r[3]) > 0]
        self.assertGreater(len(busy), 2)
        values = [int(r[2]) for r in busy]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(int(r[3]) == len(BODY) for r in busy))
        self.assertEqual(records[-1][-1], "done")
        self.assertFalse(leftovers)

    def test_unknown_length_does_not_invent_a_total(self):
        proc, records, data, _ = self.fetch("/unknown")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data, BODY)
        self.assertTrue(all(r[3] == "0" for r in records if r[-1] == "busy"))

    def test_redirect_length_is_not_used_for_chunked_body(self):
        proc, records, data, _ = self.fetch("/redirect")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data, BODY)
        self.assertTrue(all(r[3] == "0" for r in records if r[-1] == "busy"))

    def test_error_keeps_existing_destination_and_cleans_partial(self):
        proc, records, data, leftovers = self.fetch("/error", b"previous file")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(data, b"previous file")
        self.assertEqual(records[-1][-1], "error")
        self.assertFalse(leftovers)
        self.assertFalse(any(r[-1] == "done" for r in records))

    def test_renderer_does_not_show_complete_before_curl_succeeds(self):
        screen = renderer.Screen(1, ROOT/"assets")
        screen.accept(b"download\x1ffile\x1f100\x1f100\x1fbusy")
        self.assertEqual(screen.progress, 999)
        screen.accept(b"download\x1ffile\x1f100\x1f100\x1fdone")
        self.assertEqual(screen.progress, 1000)

    def terminal_preview(self, cancel=False, sixel=False, early_enter=False):
        with tempfile.TemporaryDirectory() as tmp:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 110, 990, 648))
            before = termios.tcgetattr(slave)
            def controlling_terminal():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            env = {**os.environ, "HOME": tmp, "TMPDIR": tmp, "TERM": "xterm-256color"}
            proc = subprocess.Popen(["bash", str(ROOT/"preview.sh"), "--download", self.base+"/known"],
                                    stdin=subprocess.PIPE, stdout=slave, stderr=slave,
                                    pass_fds=(slave,), env=env, preexec_fn=controlling_terminal)
            # A piped stdin must not suppress the controlling-terminal UI.
            proc.stdin.close()
            data = bytearray()
            entered = stopped = False
            disclaimer_pages = set()
            startup_entered = False
            entered_at = 0
            deadline = time.monotonic() + 20
            try:
                while proc.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        block = os.read(master, 1048576)
                        data.extend(block)
                        if b"\x1b[c" in block:
                            if early_enter and not startup_entered:
                                # A user can press Enter while the terminal is
                                # answering DA, before the menu has painted.
                                os.write(master, b"\r")
                                startup_entered = True
                            os.write(master, b"\x1b[?62;4;22c" if sixel else b"\x1b[?1;2c")
                        if b"\x1b[16t" in block:
                            os.write(master, b"\x1b[6;18;9t")
                        if b"\x1b]11;?\x07" in block:
                            os.write(master, b"\x1b]11;rgb:2323/2626/2727\x07")
                    for ready in Path(tmp).glob("zeroday-ui.*/disclaimer-ready"):
                        try:
                            page = ready.read_text()
                        except FileNotFoundError:
                            continue
                        if page and page not in disclaimer_pages:
                            disclaimer_pages.add(page)
                            if cancel == "escape":
                                os.write(master, b"\x1b")
                                stopped = True
                            elif not (startup_entered and page == "0"):
                                os.write(master, b"\r")
                    if cancel == "disclaimer" and not stopped and b"ZeroDay" in data:
                        os.kill(proc.pid, signal.SIGTERM)
                        stopped = True
                    if cancel == "gate" and not stopped and b"INSTALLER PREVIEW" in data:
                        os.kill(proc.pid, signal.SIGTERM)
                        stopped = True
                    if cancel == "escape-gate" and not stopped and b"INSTALLER PREVIEW" in data:
                        os.write(master, b"\x1b")
                        stopped = True
                    if not entered and cancel not in ("gate", "disclaimer", "escape", "escape-gate") and b"INSTALLER PREVIEW" in data:
                        os.write(master, b"\r")
                        entered = True
                        entered_at = time.monotonic()
                    if cancel is True and not stopped and b"Downloading preview-download" in data:
                        os.kill(proc.pid, signal.SIGTERM)
                        stopped = True
                    elif not cancel and b"Preview complete" in data:
                        os.write(master, b"\r")
                self.assertIsNotNone(proc.poll(), "preview did not terminate")
                self.assertEqual(proc.returncode, 143 if cancel in (True, "gate", "disclaimer") else 0, data[-2000:])
                self.assertEqual(termios.tcgetattr(slave), before)
                self.assertFalse(list(Path(tmp).glob("zeroday-ui.*")))
                self.assertFalse((Path(tmp)/".local/share/ZeroDay").exists())
                self.assertIn(b"\x1b[?1049l", data)
                self.assertIn(b"\x1b]11;#000000\x07", data)
                self.assertIn(b"\x1b]11;rgb:2323/2626/2727\x07", data)
                if sixel:
                    self.assertIn(b"\x1bP0;1;0q", data)
                return data
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                os.close(master)
                os.close(slave)

    def test_piped_stdin_uses_tty_and_downloads_without_installing(self):
        data = self.terminal_preview()
        self.assertIn(b"Preview complete", data, data[-2000:])

    def test_sixel_terminal_negotiation_and_cleanup(self):
        self.terminal_preview(sixel=True)

    def test_startup_enter_advances_disclaimer_and_menu_still_requires_confirmation(self):
        data = self.terminal_preview(early_enter=True)
        self.assertIn(b"INSTALLER PREVIEW", data)
        self.assertIn(b"Preview complete", data)

    def test_cancellation_restores_terminal_and_removes_download(self):
        self.terminal_preview(cancel=True)

    def test_cancel_at_gate_does_not_wait_for_a_keypress(self):
        self.terminal_preview(cancel="gate")

    def test_cancel_during_disclaimer_restores_terminal_without_opening_menu(self):
        data = self.terminal_preview(cancel="disclaimer")
        self.assertNotIn(b"INSTALLER PREVIEW", data)

    def test_deck_b_escape_aborts_disclaimer_and_menu_without_installing(self):
        for stage in ("escape", "escape-gate"):
            with self.subTest(stage=stage):
                data = self.terminal_preview(cancel=stage)
                self.assertNotIn(b"Downloading preview-download", data)


class Disclaimer(unittest.TestCase):
    def test_disclaimer_continues_with_yes_once_without_paging(self):
        screen = renderer.Screen(1, ROOT/"assets", intro=True)
        screen.sounds = mock.Mock()
        screen.art = None
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((110, 36))):
            screen.render(10)
            screen.accept(b"disclaimer-back")
            screen.sounds.play.assert_not_called()
            screen.accept(b"disclaimer-next")
            self.assertIsNotNone(screen.disclaimer_leaving)
            screen.accept(b"disclaimer-next")  # Input during the fade is ignored.
            screen.accept(b"disclaimer-back")
            self.assertEqual(screen.sounds.play.call_args_list, [mock.call("confirm")])
            self.assertIn("A/Enter: continue", writes[-1])
            self.assertIn("B/Esc: abort", writes[-1])

    def test_single_card_contains_all_copy_in_smaller_text_and_stays_idle(self):
        for cols, rows in ((140, 43), (110, 36), (80, 24)):
            with self.subTest(size=(cols, rows)):
                screen = renderer.Screen(1, ROOT/"assets", use_sixel=True, intro=True)
                writes = []
                screen.send = writes.append
                with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((cols, rows))):
                    screen.render(10)
                    screen.render(12)
                    count = len(writes)
                    screen.render(13)
                    self.assertEqual(len(writes), count)
                    self.assertEqual(" ".join(" ".join(screen.disclaimer_card_lines).split()),
                                     " ".join(screen.disclaimer_text.split()))
                    width, height, *_ = screen.disclaimer_layout()
                    left, top, right, bottom = screen.disclaimer_card_bounds
                    self.assertGreaterEqual(left, round(width*.16))
                    self.assertGreaterEqual(top, round(height*.17))
                    self.assertLessEqual(right, width-round(width*.16))
                    self.assertLessEqual(bottom, round(height*.83))
                    self.assertLess(screen.disclaimer_font_size, screen.ch)
                    self.assertEqual(screen.disclaimer_scroll_limit, 0)
                    self.assertEqual(screen.disclaimer_offset, 0)
                    self.assertNotIn("INSTALLER PREVIEW", "".join(writes))

    def test_text_fallback_is_one_scrollable_document_with_all_copy_reachable(self):
        for cols, rows in ((110, 36), (80, 24), (40, 12), (24, 8)):
            with self.subTest(size=(cols, rows)):
                screen = renderer.Screen(1, ROOT/"assets", intro=True)
                screen.art = None
                screen.send = lambda value: None
                with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((cols, rows))):
                    screen.render(10)
                    collected = {}
                    for _ in range(250):
                        *_, capacity, framed, lines = screen.disclaimer_layout()
                        for i in range(screen.disclaimer_offset, min(len(lines), screen.disclaimer_offset+capacity)):
                            collected[i] = lines[i]
                        if screen.disclaimer_offset == screen.disclaimer_scroll_limit:
                            break
                        screen.accept(b"disclaimer-scroll")
                        screen.render(12)
                    else:
                        self.fail("scrolling never reached the end")
                    self.assertEqual(" ".join(" ".join(collected[i] for i in sorted(collected)).split()),
                                     " ".join(screen.disclaimer_text.split()))
                    self.assertIn("Or does it?", " ".join(collected.values()))
                    screen.accept(b"disclaimer-back")
                    self.assertLess(screen.disclaimer_offset, screen.disclaimer_scroll_limit)

    def test_resize_refits_single_card_without_creating_pages(self):
        screen = renderer.Screen(1, ROOT/"assets", use_sixel=True, intro=True)
        screen.send = lambda value: None
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 24))):
            screen.render(10)
            smaller = screen.disclaimer_font_size
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((110, 36))):
            screen.render(12)
            self.assertGreater(screen.disclaimer_font_size, smaller)
            screen.accept(b"disclaimer-scroll")
            screen.accept(b"disclaimer-back")
            self.assertEqual(screen.disclaimer_offset, 0)
            self.assertEqual(screen.disclaimer_scroll_limit, 0)

    def test_fades_disclaimer_out_then_banner_in_before_menu_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)/"screen-ready"
            screen = renderer.Screen(1, ROOT/"assets", use_sixel=True, ready_file=ready, intro=True)
            screen.mode = "preview"
            writes = []
            screen.send = writes.append
            with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((110, 36))):
                screen.render(10)
                self.assertFalse(ready.with_name("disclaimer-ready").exists())
                screen.render(11.3)
                self.assertTrue(ready.with_name("disclaimer-ready").exists())
                definitions = renderer.re.findall(r'#\d+;2;(\d+);(\d+);(\d+)', writes[-1])
                self.assertTrue(any(any(int(c)>0 for c in rgb) for rgb in definitions))
                with mock.patch.object(renderer.time, "monotonic", return_value=12):
                    screen.accept(b"disclaimer-next")
                screen.render(12.4)
                self.assertNotIn("\x1b[2J", writes[-1])
                screen.render(12.9)
                self.assertFalse(screen.disclaimer)
                self.assertNotIn("SERPENTS // DOVES", writes[-1])
                screen.render(13.5)
                self.assertNotIn("SERPENTS // DOVES", writes[-1])
                self.assertFalse(ready.with_name("gate-ready").exists())
                screen.render(14.3)
                self.assertIn("SERPENTS // DOVES", writes[-1])
                self.assertFalse(ready.with_name("gate-ready").exists())
                screen.render(15)
                self.assertTrue(ready.with_name("gate-ready").exists())
                self.assertIsNone(screen.opening_started)

class Render(unittest.TestCase):
    def test_black_theme_restores_original_copy_and_transparent_art(self):
        self.assertEqual(renderer.BG, (0, 0, 0))
        screen = renderer.Screen(1, ROOT/"assets")
        screen.cols, screen.rows = 110, 36
        words = screen.gate_words(15)
        for phrase in ("ZERODAY", "SERPENTS // DOVES",
                       "be ye therefore wise as serpents, and harmless as doves",
                       "Mattith'YAHU 10:16", "INSTALL", "ABORT"):
            self.assertIn(phrase, words)
        for mode in ("install", "uninstall", "preview"):
            screen.mode = mode
            words = screen.gate_words(15)
            for hint in ("A/Enter: confirm", "B/Esc: abort", "D-pad/arrows: choose"):
                self.assertIn(hint, words)
        for name in ("banner", "progress"):
            image = renderer.Image.open(ROOT/"assets"/f"zeroday-{name}-transparent.png")
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.size, (2048, 768))
            self.assertLessEqual(image.getpixel((0, 0))[3], 1)
            self.assertGreater(image.getchannel("A").histogram()[0], image.width*image.height//2)
        self.assertEqual(screen.art.banner.getpixel((0, 0)), (0, 0, 0))

    def test_bouncing_arrow_follows_selection_without_repainting_banner(self):
        screen = renderer.Screen(1, ROOT/"assets")
        screen.mode = "preview"
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((110, 36))):
            screen.render(10)
            original_location = screen.arrow_location
            screen.render(10.2)
            first = writes[-1]
            screen.render(10.4)
            self.assertNotEqual(writes[-1], first)
            self.assertNotIn("SERPENTS // DOVES", writes[-1])
            self.assertNotIn("\x1b[2J", writes[-1])
            screen.accept(b"gate\x1fpreview\x1f1")
            screen.render(10.5)
            self.assertEqual(screen.arrow_location[0], original_location[0])
            self.assertEqual(screen.arrow_location[1]-original_location[1], 15)
            self.assertNotIn("".join(screen.static["banner"]), writes[-1])

    def test_sixel_motion_uses_fixed_opaque_canvas_without_screen_clear(self):
        screen = renderer.Screen(1, ROOT/"assets", use_sixel=True)
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 30))):
            screen.render(10)
            deadline = time.monotonic()+6
            while len(screen.motion_frames)<6*renderer.MOTION_STEPS and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(len(screen.motion_frames), 6*renderer.MOTION_STEPS)
            with mock.patch.object(renderer.time, "monotonic", return_value=10):
                screen.accept(b"state\x1fInstalling\x1f0\x1f\x1fbusy")
            screen.render(10.9)
            writes.clear()
            for tick in (11, 11.3, 11.8, 12.4):
                screen.render(tick)
            self.assertNotIn("\x1b[2J", "".join(writes))
            sizes = []
            anchors = []
            for frame in writes:
                sizes.extend(renderer.re.findall(r'\x1bP0;1;0q"1;1;(\d+);(\d+)', frame))
                anchors.extend(renderer.re.findall(r'\x1b\[(\d+);(\d+)H\x1bP', frame))
            self.assertGreater(len(sizes), 2)
            self.assertEqual(len(set(sizes)), 1)
            self.assertEqual(len(set(anchors)), 1)

    def test_slow_motion_changes_coverage_between_whole_pixel_positions(self):
        screen = renderer.Screen(1, ROOT/"assets", use_sixel=True)
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 30))):
            screen.render(10)
            deadline = time.monotonic()+6
            while len(screen.motion_frames)<6*renderer.MOTION_STEPS and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(len(screen.motion_frames), 6*renderer.MOTION_STEPS)
            with mock.patch.object(renderer.time, "monotonic", return_value=10):
                screen.accept(b"state\x1fInstalling\x1f0\x1f\x1fbusy")
            screen.render(10.9)
            screen.render(11.7)
            first, position = writes[-1], screen.motion_offset
            screen.render(11.708)
            self.assertEqual(position//renderer.MOTION_STEPS, screen.motion_offset//renderer.MOTION_STEPS)
            self.assertNotEqual(writes[-1], first)
            self.assertNotIn("\x1b[2J", writes[-1])

    def test_smaller_bar_fades_after_motion_and_ready_waits_for_reveal(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)/"ready"
            screen = renderer.Screen(1, ROOT/"assets", use_sixel=True, ready_file=ready)
            writes = []
            screen.send = writes.append
            with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 30))):
                screen.render(10)
                banner_size = renderer.re.search(r'"1;1;(\d+);(\d+)', screen.static["banner"]).groups()
                bar_size = renderer.re.search(r'"1;1;(\d+);(\d+)', screen.static["bar"]).groups()
                for full, smaller in zip(banner_size, bar_size):
                    self.assertEqual(int(smaller), round(int(full)*.8))
                deadline = time.monotonic()+6
                while len(screen.motion_frames)<6*renderer.MOTION_STEPS and time.monotonic()<deadline:
                    time.sleep(.01)
                with mock.patch.object(renderer.time, "monotonic", return_value=10):
                    screen.accept(b"state\x1fInstalling\x1f0\x1f\x1fbusy")
                screen.render(10.9)
                screen.render(13.2)
                self.assertIsNone(screen.bar_reveal_started)
                self.assertNotIn(("bar-reveal", 0), screen.static)
                self.assertFalse(ready.exists())
                screen.render(13.4)
                self.assertEqual(screen.bar_reveal_started, 13.4)
                black = writes[-1]
                definitions = renderer.re.findall(r'#\d+;2;(\d+);(\d+);(\d+)', black)
                self.assertTrue(definitions)
                self.assertTrue(all(set(rgb)=={"0"} for rgb in definitions))
                self.assertFalse(ready.exists())
                with mock.patch.object(renderer, "sixel", side_effect=AssertionError("fade re-encoded its pixels")):
                    # A new percentage color may encode a tiny glyph; the bar
                    # must retain its cached pixel body during the fade.
                    with mock.patch.object(screen, "percentage", return_value=""):
                        screen.render(13.725)
                midpoint = writes[-1]
                self.assertNotEqual(midpoint, black)
                body = screen.static[("bar-reveal", 0)][3]
                self.assertIn(body, black)
                self.assertIn(body, midpoint)
                self.assertFalse(ready.exists())
                screen.render(14.1)
                self.assertIsNone(screen.bar_reveal_started)
                self.assertTrue(ready.exists())

    def test_cell_pixels_refresh_layout_after_zoom_even_with_same_row_count(self):
        master, slave = pty.openpty()
        try:
            screen = renderer.Screen(slave, ROOT/"assets", use_sixel=True)
            writes = []
            screen.send = writes.append
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 80, 720, 540))
            screen.render(10)
            first = screen.static["banner"]
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 80, 1050, 798))
            screen.render(10.1)
            self.assertEqual((screen.cw, screen.ch), (13, 26))
            self.assertNotEqual(screen.static["banner"], first)
            self.assertIn("\x1b[2J", writes[-1])
        finally:
            os.close(master)
            os.close(slave)

    def test_percentage_uses_small_glyph_canvas_and_clears_previous_digits(self):
        screen = renderer.Screen(1, ROOT/"assets", use_sixel=True)
        screen.cols, screen.rows = 110, 36
        first = screen.percentage(20, "9%", renderer.GOLD)
        second = screen.percentage(20, "100%", renderer.GOLD)
        self.assertIn("\x1b[2K", first)
        self.assertNotIn("\x1b[2K", second)
        self.assertNotEqual(first, second)
        sizes = [renderer.re.search(r'"1;1;(\d+);(\d+)', s).groups() for s in (first,second)]
        self.assertEqual(sizes[0], sizes[1])
        self.assertEqual(int(sizes[0][1]), screen.ch)
        screen.ch = 19
        zoomed = screen.percentage(20, "100%", renderer.GOLD)
        height = int(renderer.re.search(r'"1;1;\d+;(\d+)', zoomed)[1])
        self.assertLessEqual(height, screen.ch)
        self.assertEqual(height%6, 0)

    def test_sound_names_match_actions_without_repeating_for_progress(self):
        screen = renderer.Screen(1, ROOT/"assets")
        screen.sounds = mock.Mock()
        screen.accept(b"gate\x1fpreview\x1f0")
        screen.accept(b"gate\x1fpreview\x1f1")
        screen.accept(b"gate\x1fpreview\x1f1")
        screen.accept(b"gate\x1fpreview\x1f0")
        screen.accept(b"state\x1fGetting ready\x1f0\x1f\x1fbusy")
        screen.accept(b"state\x1fDownloading\x1f500\x1f\x1fbusy")
        screen.accept(b"sound\x1fcancel")
        self.assertEqual(screen.sounds.play.call_args_list,
                         [mock.call("move"),mock.call("move"),mock.call("confirm"),mock.call("cancel")])

    def test_first_sound_uses_warm_stream_and_reuses_player(self):
        # Capture the actual PCM pipe in a subprocess without emitting sound.
        # Verify a cold first click reaches it promptly and later clicks use
        # the same process; this is not a claim about hardware output latency.
        with tempfile.TemporaryDirectory() as tmp:
            raw, onset = Path(tmp)/"raw", Path(tmp)/"onset"
            helper = '''import os, sys, time
from pathlib import Path
with open(sys.argv[1], "wb", buffering=0) as output:
    while True:
        block = os.read(0, 3840)
        if not block: break
        output.write(block)
        if any(block) and not Path(sys.argv[2]).exists():
            Path(sys.argv[2]).write_text(str(time.monotonic()))
'''
            real_popen = subprocess.Popen
            def capture_player(*args, **kwargs):
                return real_popen([sys.executable, "-c", helper, str(raw), str(onset)], **kwargs)
            with mock.patch.dict(os.environ, {"ZERODAY_UI_SOUND": "1"}), \
                 mock.patch.object(renderer.shutil, "which", side_effect=lambda name: "/test/pw-play" if name == "pw-play" else None), \
                 mock.patch.object(renderer.subprocess, "Popen", side_effect=capture_player) as launch:
                sounds = renderer.Sounds(ROOT/"assets/sounds")
                try:
                    deadline = time.monotonic()+2
                    while (not raw.exists() or raw.stat().st_size < 7680) and time.monotonic()<deadline:
                        time.sleep(.005)
                    self.assertTrue(raw.exists())
                    self.assertFalse(any(raw.read_bytes()))
                    pressed = time.monotonic()
                    sounds.play("confirm")
                    deadline = pressed+1
                    while not onset.exists() and time.monotonic()<deadline:
                        time.sleep(.005)
                    self.assertTrue(onset.exists(), "first confirmation never reached the audio stream")
                    self.assertLess(float(onset.read_text())-pressed, .2)
                    sounds.play("move")
                    time.sleep(.04)
                    sounds.play("cancel")
                    time.sleep(.04)
                    sounds.play("confirm")
                finally:
                    sounds.close()
                self.assertEqual(launch.call_count, 1)
                self.assertFalse(sounds.thread.is_alive())
                self.assertIsNotNone(sounds.child.poll())
                self.assertIn(sounds.clips["confirm"], raw.read_bytes(),
                              "closing the UI cut off the final confirmation")

    def test_deck_desktop_button_keys_and_keyboard_arrows(self):
        command = '''source "$1"
UI_PENDING_INPUT=$'\\r'; ui_key; printf '%q\\n' "$UI_KEY"
UI_PENDING_INPUT=$'\\033'; ui_key; printf '%s\\n' "$UI_KEY"
UI_PENDING_INPUT=$'\\033[C'; ui_key; printf '%s\\n' "$UI_KEY"
UI_PENDING_INPUT=$'\\033[D'; ui_key; printf '%s\\n' "$UI_KEY"
UI_PENDING_INPUT=' '; ui_key; printf '<%s>\\n' "$UI_KEY"
'''
        result = subprocess.run(["bash", "-c", command, "test", str(ROOT/"terminal.sh")],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["$'\\025'", "q", "[C", "[D", "< >"])

    def test_startup_probe_keeps_interleaved_enter_and_arrows(self):
        master, slave = pty.openpty()
        try:
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~(termios.ECHO | termios.ICANON)
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            def answer():
                os.read(master, 1024)
                os.write(master, b"\n\x1b[?62;4;22c\x1b[C\x1b[6;18;9t\x1b]11;rgb:2323/2626/2727\x1b\\")
            thread = threading.Thread(target=answer)
            thread.start()
            result = renderer.probe_terminal(slave)
            thread.join(1)
            self.assertEqual(result, (9, 18, True, "rgb:2323/2626/2727", b"\n\x1b[C"))
        finally:
            os.close(master)
            os.close(slave)

    def test_late_captions_fade_without_restarting_on_rate_updates(self):
        screen = renderer.Screen(1, ROOT/"assets")
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 30))):
            screen.accept(b"state\x1fReady\x1f0\x1f\x1fdone")
            screen.render(10)
            screen.accept(b"state\x1fDownloading\x1f0\x1f1 KiB/s\x1fdone")
            screen.accept(b"log\x1finfo\x1fNew message")
            screen.render(11)
            self.assertEqual(screen.caption_ink("phase", renderer.GOLD, 11, 1), (0, 0, 0))
            screen.render(11.2)
            mid = screen.caption_ink("detail", renderer.MUTED, 11.2, 1)
            self.assertTrue(all(0<c<68 for c in mid))
            screen.accept(b"state\x1fDownloading\x1f0\x1f2 KiB/s\x1fdone")
            screen.render(11.25)
            self.assertEqual(screen.caption_fades["detail"], 11)
            screen.render(11.5)
            self.assertEqual(screen.caption_ink("log0", renderer.MUTED, 11.5, 1), renderer.MUTED)
            count = len(writes)
            screen.render(12)
            self.assertEqual(len(writes), count, "finished fade kept redrawing an idle screen")

    def test_install_fades_words_then_moves_banner_down_without_resizing(self):
        screen = renderer.Screen(1, ROOT/"assets")
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((110, 36))):
            screen.render(10)
            banner = screen.static["banner"]
            first = writes[-1]
            original_row = int(renderer.re.search(r"\x1b\[(\d+);\d+H", first).group(1))
            writes.clear()
            with mock.patch.object(renderer.time, "monotonic", return_value=10):
                screen.accept(b"state\x1fInstalling\x1f500\x1f\x1fbusy")
            screen.render(10.4)
            self.assertNotIn("\x1b[2J", writes[-1])
            self.assertIn("SERPENTS // DOVES", writes[-1])
            screen.render(10.9)
            screen.render(12)
            self.assertNotIn("\x1b[2J", "".join(writes))
            self.assertNotIn("SERPENTS // DOVES", writes[-1])
            self.assertNotIn("Installing", writes[-1])
            screen.render(13.4)
            self.assertIn("Installing", writes[-1])
            self.assertEqual(screen.static["banner"], banner)
            final_row = int(renderer.re.search(r"\x1b\[(\d+);\d+H", writes[-1]).group(1))
            self.assertGreater(final_row, original_row)

    def test_gate_redraws_only_arrow_and_finished_screen_stays_idle(self):
        screen = renderer.Screen(1, ROOT/"assets")
        writes = []
        screen.send = writes.append
        with mock.patch.object(renderer.os, "get_terminal_size", return_value=os.terminal_size((80, 24))):
            screen.render(1)
            count = len(writes)
            screen.render(2)
            self.assertEqual(len(writes), count+1)
            self.assertNotIn("\x1b[2J", writes[-1])
            self.assertNotIn("SERPENTS // DOVES", writes[-1])
            self.assertNotIn("".join(screen.static["banner"]), writes[-1])
            screen = renderer.Screen(1, ROOT/"assets")
            screen.send = writes.append
            screen.accept(b"state\x1fFinished\x1f1000\x1f\x1fdone")
            screen.render(3)
            count = len(writes)
            screen.render(4)
            self.assertEqual(len(writes), count)

    def test_original_assets_are_byte_identical(self):
        for name, expected in [
            ("zeroday-banner.png", "c76a4ce27916018c5d9733b7323b93a1de5791d2d4f7ad3dcbd103ba0c88c82e"),
            ("zeroday-progress.png", "4d5f11a9c544a0575e284ded6651a8dba3ef29d5c40abf641d662e0bb3c1a515"),
        ]:
            self.assertEqual(hashlib.sha256((ROOT/"assets"/name).read_bytes()).hexdigest(), expected)

    def test_artwork_fill_changes_only_inside_the_track(self):
        art = renderer.Artwork(ROOT/"assets")
        zero = art.bar(1024, 384, 0, 0, False)
        full = art.bar(1024, 384, 1000, 0, False)
        from PIL import ImageChops
        box = ImageChops.difference(zero, full).getbbox()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[0], 175)
        self.assertLessEqual(box[2], 835)
        self.assertGreaterEqual(box[1], 219)
        self.assertLessEqual(box[3], 247)

    def test_shimmer_moves_without_advancing_progress(self):
        art = renderer.Artwork(ROOT/"assets")
        a = art.bar(1024, 384, 500, 1.0, True)
        b = art.bar(1024, 384, 500, 1.4, True)
        from PIL import ImageChops
        box = ImageChops.difference(a, b).getbbox()
        self.assertIsNotNone(box)
        self.assertLessEqual(box[2], 510)

    def test_remote_status_cannot_inject_terminal_controls(self):
        self.assertNotIn("\x1b", renderer.clean("bad\x1b[2Jname\n"))

    def test_no_pillow_keeps_a_functional_text_bar(self):
        previous = renderer.Image
        try:
            renderer.Image = None
            screen = renderer.Screen(1, ROOT/"assets", use_sixel=True)
            self.assertIsNone(screen.art)
            self.assertFalse(screen.use_sixel)
        finally:
            renderer.Image = previous

    def test_unknown_total_has_no_numeric_percentage(self):
        screen = renderer.Screen(1, ROOT/"assets")
        screen.accept(b"download\x1ftest\x1f1234\x1f0\x1fbusy")
        self.assertEqual(screen.progress, -1)
        self.assertIn("size not reported", screen.detail)

    def test_pty_resize_stop_and_alternate_screen_cleanup(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 720, 432))
        proc = subprocess.Popen(["python3", str(ROOT/"renderer.py"), "--fd", str(slave)],
                                stdin=subprocess.PIPE, pass_fds=(slave,), stderr=subprocess.PIPE)
        captured = bytearray()
        def drain():
            if select.select([master], [], [], .1)[0]:
                captured.extend(os.read(master, 1048576))
        try:
            proc.stdin.write(b"state\x1fDownloading\x1f500\x1fTest transfer\x1fbusy\x1e")
            proc.stdin.flush()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                drain()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 40, 360, 216))
            deadline = time.monotonic() + .5
            while time.monotonic() < deadline:
                drain()
            proc.stdin.write(b"stop\x1e")
            proc.stdin.close()
            while proc.poll() is None:
                drain()
            drain()
            self.assertEqual(proc.returncode, 0, proc.stderr.read())
            self.assertIn(b"\x1b[?1049h", captured)
            self.assertIn(b"\x1b[?1049l", captured)
            self.assertIn(b"50%", captured)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stderr.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Terminal artwork and progress renderer. Contains no installation actions.

Records on stdin are separated by RS (0x1e); fields by US (0x1f).
  gate <install|uninstall|preview> <selection>
  disclaimer <install|uninstall|preview>
  disclaimer-next / disclaimer-back
  state <phase> <0..1000|-1> <detail> <busy|done|error>
  log <kind> <message>
  stop
Only this process writes the screen, including during blocking installer steps.
"""

import argparse
import fcntl
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import struct
import sys
import threading
import termios
import textwrap
import time
import wave

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = None

BG = (0, 0, 0)
INK = (168, 168, 168)
GOLD = (192, 142, 72)  # Original reference pixel (800, 450), not a replacement hue.
MUTED = (68, 68, 68)
TRACK = (36, 32, 26)
ESC = "\x1b"
MOTION_STEPS = 4  # Quarter-pixel coverage keeps a slow descent from stepping.
FRAME_PERIOD = 1 / 60
BAR_SCALE = 0.8
BAR_FADE_SECONDS = 0.65
TEXT_FADE_SECONDS = 0.4
INTRO_FADE_SECONDS = 1.2
OUTRO_FADE_SECONDS = 0.8
MENU_FADE_SECONDS = 0.8


class Sounds:
    """Preloaded cues on one warm, paced PCM stream; play() never does I/O."""
    def __init__(self, directory):
        self.child = None
        self.clips = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.drained = threading.Event()
        self.drained.set()
        self.current, self.offset = b"", 0
        self.thread = None
        if os.environ.get("ZERODAY_UI_SOUND", "1") == "0":
            return
        for name in ("move", "confirm", "cancel"):
            try:
                with wave.open(str(directory / f"{name}.wav"), "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) == (2, 2, 48000, "NONE"):
                        self.clips[name] = source.readframes(source.getnframes())
            except (OSError, EOFError, wave.Error):
                pass
        self.commands = []
        for name, args in (
            ("pw-play", ["--raw", "--format", "s16", "--rate", "48000", "--channels", "2", "--latency", "30ms", "--volume", "0.45", "-"]),
            ("paplay", ["--raw", "--format=s16le", "--rate=48000", "--channels=2", "--latency-msec=30", "--volume=29491"]),
            ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", "-f", "s16le", "-ar", "48000", "-ch_layout", "stereo", "-volume", "45", "-i", "pipe:0"]),
        ):
            if player := shutil.which(name):
                self.commands.append([player, *args])
        if self.commands and self.clips:
            self.thread = threading.Thread(target=self.pump, daemon=True, name="ui-audio")
            self.thread.start()

    def play(self, name):
        if name not in self.clips or not self.thread or not self.thread.is_alive():
            return
        with self.lock:
            self.current, self.offset = self.clips[name], 0
            self.drained.clear()

    def pump(self):
        # Feed 20 ms at a time, including silence while awaiting the first
        # choice. Sending silence without pacing would queue dead time ahead
        # of the cue. A single stream also avoids reconnecting for every key.
        chunk_size, period = 3840, 0.02
        silence = bytes(chunk_size)
        for command in self.commands:
            if self.stopped.is_set():
                break
            try:
                self.child = subprocess.Popen(command, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)
                os.set_blocking(self.child.stdin.fileno(), False)
                deadline = time.monotonic()
                while not self.stopped.is_set():
                    with self.lock:
                        clip, offset = self.current, self.offset
                        chunk = clip[offset:offset+chunk_size].ljust(chunk_size, b"\0") if clip else silence
                    remaining = memoryview(chunk)
                    while remaining and not self.stopped.is_set():
                        if self.child.poll() is not None:
                            raise BrokenPipeError
                        if select.select([], [self.child.stdin], [], .05)[1]:
                            try:
                                remaining = remaining[os.write(self.child.stdin.fileno(), remaining):]
                            except BlockingIOError:
                                continue
                    with self.lock:
                        if self.current is clip and self.offset == offset:
                            self.offset += chunk_size
                            if self.offset >= len(clip):
                                self.current, self.offset = b"", 0
                                self.drained.set()
                    deadline = max(deadline+period, time.monotonic())
                    self.stopped.wait(max(0, deadline-time.monotonic()))
            except (OSError, ValueError):
                pass
            finally:
                if self.child:
                    self.child.stdin.close()
                    try:
                        self.child.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait()
        self.drained.set()

    def close(self):
        if self.thread:
            self.drained.wait(1.3)
            self.stopped.set()
            self.thread.join(timeout=.5)


def probe_terminal(fd, timeout=.25):
    """Read capability replies without consuming keys typed during startup."""
    os.write(fd, b"\x1b[c\x1b[16t\x1b]11;?\x07")
    data = b""
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if select.select([fd], [], [], max(0, deadline-time.monotonic()))[0]:
            block = os.read(fd, 4096)
            if not block:
                break
            data += block
    da = re.search(rb"\x1b\[\?([0-9;]*)c", data)
    cell = re.search(rb"\x1b\[6;([0-9]+);([0-9]+)t", data)
    background = re.search(rb"\x1b\]11;(rgb:[0-9a-fA-F]+/[0-9a-fA-F]+/[0-9a-fA-F]+)(?:\x07|\x1b\\)", data)
    pending = data
    for match in (da, cell, background):
        if match:
            pending = pending.replace(match[0], b"", 1)
    cw, ch = (int(cell[2]), int(cell[1])) if cell else (9, 18)
    sixel_enabled = bool(da and b"4" in da[1].split(b";") and cell)
    return cw, ch, sixel_enabled, background[1].decode() if background else "", pending


def clean(text):
    """Do not let a remote filename or status message emit terminal commands."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)


def sixel(image, palette=None, parts=False):
    """Opaque SIXEL with per-band sparse masks and run length encoding."""
    if image.mode == "P":
        q = image
    else:
        rgb = image.convert("RGB")
        q = rgb.quantize(palette=palette, dither=Image.Dither.NONE) if palette else rgb.quantize(colors=128, dither=Image.Dither.NONE)
    palette = q.getpalette()
    width, height = q.size
    pixels = q.tobytes()
    # We emit every pixel. Transparent background mode avoids Konsole adding
    # a strip of its default background at the right edge of opaque SIXELs.
    out = [f'{ESC}P0;1;0q"1;1;{width};{height}']
    for c in sorted(set(pixels)):
        rgb = palette[c * 3:c * 3 + 3]
        # Register zero is transparent in some Konsole drawing paths. Reserve
        # it so an opaque black pixel really covers the previous picture.
        out.append(f"#{c+1};2;" + ";".join(str(round(v * 100 / 255)) for v in rgb))
    body_start = len(out)
    for y in range(0, height, 6):
        masks = {}
        for k in range(min(6, height - y)):
            for x, color in enumerate(pixels[(y + k) * width:(y + k + 1) * width]):
                if color not in masks:
                    masks[color] = bytearray(width)
                masks[color][x] |= 1 << k
        for color, vals in sorted(masks.items()):
            out.append(f"#{color+1}")
            end = len(vals.rstrip(b"\0"))
            start = 0
            while start < end:
                value = vals[start]
                j = start + 1
                while j < end and vals[j] == value:
                    j += 1
                n = j - start
                char = chr(value + 63)
                out.append(f"!{n}{char}" if n > 3 else char * n)
                start = j
            out.append("$")
        if y + 6 < height:
            out.append("-")
    out.append(f"{ESC}\\")
    if parts:
        return width, height, "".join(out[1:body_start]), "".join(out[body_start:])
    return "".join(out)


def halfblocks(image, cols, rows):
    image = image.resize((max(1, cols), max(2, rows * 2)), Image.Resampling.LANCZOS)
    px = image.load()
    result = []
    for y in range(0, image.height, 2):
        line = []
        previous = None
        for x in range(image.width):
            pair = px[x, y], px[x, y + 1]
            if pair != previous:
                fg, bg = pair
                line.append(f'{ESC}[38;2;{fg[0]};{fg[1]};{fg[2]};48;2;{bg[0]};{bg[1]};{bg[2]}m')
                previous = pair
            line.append("▀")
        result.append("".join(line) + f"{ESC}[0m")
    return result


class Artwork:
    def __init__(self, directory):
        def on_black(name):
            source = Image.open(directory / name).convert("RGBA")
            canvas = Image.new("RGBA", source.size, (*BG, 255))
            return Image.alpha_composite(canvas, source).convert("RGB")
        self.banner = on_black("zeroday-banner-transparent.png")
        self.disclaimer_frame = on_black("zeroday-disclaimer-frame.png")
        original = Image.open(directory / "zeroday-progress.png").convert("RGB")
        # Keep the serpent, dove, frame and the reference's actual metal texture.
        # Rebuild only the interior so the baked-in 60% fill becomes live data.
        self.empty = on_black("zeroday-progress-transparent.png")
        interior = Image.new("L", original.size)
        ImageDraw.Draw(interior).polygon(
            [(373, 446), (1644, 446), (1658, 460), (1644, 483), (373, 483), (358, 461)],
            fill=255,
        )
        self.mask = interior
        empty = Image.new("RGB", (1300, 38), TRACK)
        gold = original.crop((390, 446, 1060, 484)).resize((1300, 38))
        empty_canvas = self.empty.copy()
        empty_canvas.paste(empty, (358, 446))
        self.empty.paste(empty_canvas, mask=interior)
        self.full = self.empty.copy()
        gold_canvas = original.copy()
        gold_canvas.paste(gold, (358, 446))
        self.full.paste(gold_canvas, mask=interior)
        self.cache = {}
        self.palettes = {}

    def fit(self, width, height):
        key = (width, height)
        if key not in self.cache:
            # The banner and the smaller bar share assets at different sizes.
            # Retain both fits instead of resizing them again on every frame.
            if len(self.cache) >= 4:
                oldest = next(iter(self.cache))
                self.cache.pop(oldest)
                self.palettes.pop(oldest)
            resample = Image.Resampling.LANCZOS
            self.cache[key] = (
                self.banner.resize(key, resample), self.empty.resize(key, resample),
                self.full.resize(key, resample), self.mask.resize(key, resample),
            )
            # Reuse a palette sampled from the actual track. Re-running median
            # cut on every animation frame was the largest avoidable CPU cost.
            _, empty, full, _ = self.cache[key]
            box = (int(width * 358 / 2048), int(height * 446 / 768),
                   max(1, int(width * 1658 / 2048)), max(1, int(height * 484 / 768)))
            if box[2] > box[0] and box[3] > box[1]:
                sample = Image.new("RGB", (box[2]-box[0], (box[3]-box[1])*2))
                sample.paste(full.crop(box), (0, 0))
                sample.paste(empty.crop(box), (0, box[3]-box[1]))
                self.palettes[key] = sample.quantize(colors=128, dither=Image.Dither.NONE)
            else:
                self.palettes[key] = full.quantize(colors=128, dither=Image.Dither.NONE)
        self.palette = self.palettes[key]
        return self.cache[key]

    def bar(self, width, height, progress, tick, busy):
        _, empty, full, interior = self.fit(width, height)
        # Coordinates are fractions of the original 2048 x 768 artwork.
        left, right = width * 358 / 2048, width * 1658 / 2048
        if progress < 0:
            position = (1 - math.cos(tick * 1.5)) / 2
            span = (right - left) * 0.22
            begin = left + (right - left - span) * position
            end = begin + span
        else:
            begin, end = left, left + (right - left) * max(0, min(progress, 1000)) / 1000
        mask = Image.new("L", (width, height))
        if end > begin:
            ImageDraw.Draw(mask).rectangle((round(begin), 0, round(end) - 1, height), fill=255)
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, interior)
        frame = Image.composite(full, empty, mask)
        if busy and end > begin:
            # A low-opacity glint passes over the existing gold. It never
            # advances the fill or changes the amount reported as downloaded.
            head = left + ((tick * 0.27) % 1.25 - 0.12) * (right - left)
            glint = Image.new("RGBA", frame.size)
            brush = ImageDraw.Draw(glint)
            for size, alpha in ((0.035, 9), (0.018, 12), (0.006, 18)):
                radius = max(1, round(width * size))
                brush.rectangle((int(head - radius), 0, int(head + radius), height), fill=(249, 217, 161, alpha))
            glint.putalpha(ImageChops.multiply(glint.getchannel("A"), mask))
            frame = Image.alpha_composite(frame.convert("RGBA"), glint).convert("RGB")
        return frame


class Screen:
    def __init__(self, fd, assets, cell_width=9, cell_height=18, use_sixel=False, ready_file=None, intro=False):
        self.fd, self.cw, self.ch = fd, cell_width, cell_height
        self.use_sixel = use_sixel and Image is not None
        self.sounds = Sounds(assets / "sounds")
        self.art = Artwork(assets) if Image else None
        self.gate = True
        self.mode, self.selection = "install", 0
        self.phase, self.progress, self.detail, self.state = "Ready to install", 0, "", "busy"
        self.logs = []
        self.layout_key = None
        self.last_gate = None
        self.last_signature = None
        self.transition_started = None
        self.motion_started = None
        self.motion_key = None
        self.motion_frames = {}
        self.motion_offset = None
        self.bar_reveal_started = None
        self.work_painted = False
        self.footer_kind = None
        self.arrow_cache = {}
        self.arrow_location = None
        self.arrow_frame = None
        self.ready_file = ready_file
        self.static = {}
        self.caption_state = {}
        self.caption_fades = {}
        self.disclaimer = intro
        self.disclaimer_text = (assets.parent / "disclaimer.txt").read_text()
        self.disclaimer_offset = 0
        self.disclaimer_started = None
        self.disclaimer_leaving = None
        self.disclaimer_signature = None
        self.disclaimer_last = False
        self.disclaimer_step = 1
        self.opening_started = None

    def reading_ready(self, last):
        if self.ready_file:
            target = self.ready_file.with_name("disclaimer-ready")
            value = f"{self.disclaimer_offset}:{int(last)}"
            if not target.exists() or target.read_text() != value:
                target.write_text(value)

    def mark_ready(self):
        if self.disclaimer or self.opening_started is not None:
            return
        if self.ready_file:
            target = self.ready_file.with_name("gate-ready") if self.gate else self.ready_file
            if self.gate or (self.transition_started is None and self.bar_reveal_started is None):
                if not target.exists():
                    target.touch()

    def prepare_captions(self, tick):
        values = {"phase": self.phase, "detail": self.detail,
                  **{f"log{i}": log[1] for i, log in enumerate(self.logs[-2:])}}
        for key in ("phase", "detail", "log0", "log1"):
            value = values.get(key, "")
            previous = self.caption_state.get(key, "")
            # Numerical detail updates keep their current fade instead of
            # repeatedly vanishing. A newly named phase or log gets one fade.
            new = value and (not previous or (key != "detail" and previous != value))
            if new and self.bar_reveal_started is None and self.last_gate is False:
                self.caption_fades[key] = tick
            self.caption_state[key] = value
            if not value or tick-self.caption_fades.get(key, tick) >= TEXT_FADE_SECONDS:
                self.caption_fades.pop(key, None)

    def caption_ink(self, key, color, tick, reveal):
        start = self.caption_fades.get(key)
        if start is not None:
            t = min(1, max(0, (tick-start)/TEXT_FADE_SECONDS))
            reveal *= t*t*(3-2*t)
        return tuple(round(c*reveal) for c in color)

    def send(self, value):
        # Synchronized updates prevent a terminal painting half a moving image
        # while a long SIXEL write is still arriving.
        data = (f"{ESC}[?2026h" + value + f"{ESC}[?2026l").encode("utf-8")
        while data:
            n = os.write(self.fd, data)
            data = data[n:]

    def text(self, row, value, color=INK):
        if not 1 <= row <= self.rows:
            return ""
        value = clean(value)[:max(0, self.cols - 2)]
        col = max(1, (self.cols - len(value)) // 2 + 1)
        return (f"{ESC}[{row};1H{ESC}[48;2;{BG[0]};{BG[1]};{BG[2]}m{ESC}[2K"
                f"{ESC}[{row};{col}H{ESC}[38;2;{color[0]};{color[1]};{color[2]}m{value}{ESC}[0m")

    def percentage(self, row, value, color):
        if not self.use_sixel:
            return self.text(row, value, color)
        clear = self.text(row, "") if self.footer_kind != "percentage" else ""
        self.footer_kind = "percentage"
        size = max(8, round(self.ch*.72))
        key = ("percentage", self.cw, self.ch, value, color)
        if key not in self.static:
            font_path = next((p for p in (
                "/usr/share/fonts/TTF/Hack-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            ) if Path(p).is_file()), None)
            font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            # SIXEL encodes six-pixel bands. Keep the last band inside this
            # text row so the next caption cannot erase it after font zoom.
            frame = Image.new("RGB", (self.cw*8, max(6, self.ch//6*6)), BG)
            draw = ImageDraw.Draw(frame)
            box = draw.textbbox((0, 0), value, font=font)
            draw.text(((frame.width-box[2]+box[0])//2-box[0],
                       (frame.height-box[3]+box[1])//2-box[1]), value, font=font, fill=color)
            # Percentages have only 101 values; retain their encoded glyphs.
            self.static[key] = sixel(frame)
        col = max(1, (self.cols-8)//2+1)
        return clear + f"{ESC}[{row};{col}H" + self.static[key]

    def image(self, img, row, col, cache_key=None, palette=None):
        if cache_key in self.static:
            encoded = self.static[cache_key]
        elif self.use_sixel:
            encoded = sixel(img, palette)
        else:
            encoded = halfblocks(img, max(1, round(img.width / self.cw)), max(1, round(img.height / self.ch)))
        if cache_key is not None:
            self.static[cache_key] = encoded
        if isinstance(encoded, str):
            return f"{ESC}[{row};{col}H" + encoded
        return "".join(f"{ESC}[{row+i};{col}H{line}" for i, line in enumerate(encoded))

    def fade_image(self, img, row, col, opacity, cache_key):
        """Fade cached color definitions; never quantize the bar per frame."""
        if cache_key not in self.static:
            self.static[cache_key] = (sixel(img, parts=True) if self.use_sixel else
                halfblocks(img, max(1, round(img.width/self.cw)), max(1, round(img.height/self.ch))))
        encoded = self.static[cache_key]
        def color(match):
            return match[1] + ";".join(str(round(int(match[i])*opacity)) for i in (2, 3, 4))
        if self.use_sixel:
            width, height, definitions, data = encoded
            definitions = re.sub(r"(#\d+;2;)(\d+);(\d+);(\d+)", color, definitions)
            return f'{ESC}[{row};{col}H{ESC}P0;1;0q"1;1;{width};{height}' + definitions + data
        return "".join(f"{ESC}[{row+i};{col}H" +
            re.sub(r"((?:38|48);2;)(\d+);(\d+);(\d+)", color, line)
            for i, line in enumerate(encoded))

    def accept(self, record):
        fields = record.decode("utf-8", errors="replace").split("\x1f")
        if fields[0] == "stop":
            return False
        if fields[0] == "disclaimer" and len(fields) == 2:
            self.disclaimer, self.mode, self.gate = True, fields[1], True
            self.disclaimer_offset = 0
            self.disclaimer_started = self.disclaimer_leaving = None
            self.disclaimer_signature = self.layout_key = None
            self.opening_started = None
        elif fields[0] in ("disclaimer-next", "disclaimer-back"):
            if self.disclaimer and self.disclaimer_leaving is None:
                if fields[0] == "disclaimer-next" and self.disclaimer_last:
                    self.disclaimer_leaving = time.monotonic()
                else:
                    delta = self.disclaimer_step * (1 if fields[0] == "disclaimer-next" else -1)
                    self.disclaimer_offset = max(0, self.disclaimer_offset + delta)
                    self.disclaimer_signature = None
            return True
        elif fields[0] == "gate" and len(fields) == 3:
            if self.gate and int(fields[2]) != self.selection:
                self.sounds.play("move")
            if not self.gate:
                self.layout_key = None
                self.transition_started = self.motion_started = None
                self.bar_reveal_started = None
                self.caption_state.clear()
                self.caption_fades.clear()
                self.opening_started = time.monotonic()
            self.gate, self.mode = True, fields[1]
            self.selection = int(fields[2])
        elif fields[0] == "state" and len(fields) == 5:
            self.disclaimer = False
            if self.gate:
                self.sounds.play("confirm")
            if self.gate and self.last_gate is True:
                self.transition_started = time.monotonic()
                self.motion_started = None
            self.gate = False
            self.phase = fields[1]
            self.progress = max(-1, min(1000, int(fields[2])))
            self.detail, self.state = fields[3:5]
        elif fields[0] == "download" and len(fields) == 5:
            self.disclaimer = False
            if self.gate:
                self.sounds.play("confirm")
            if self.gate and self.last_gate is True:
                self.transition_started = time.monotonic()
                self.motion_started = None
            self.gate = False
            self.phase = fields[1]
            received, total = max(0, int(fields[2])), max(0, int(fields[3]))
            self.state = "busy" if fields[4] == "begin" else fields[4]
            now = time.monotonic()
            if fields[4] == "begin" or not hasattr(self, "download_started"):
                self.download_started = now
            elapsed = max(0.1, now - self.download_started)
            rate = received / elapsed
            self.progress = min(999, received * 1000 // total) if total else -1
            if self.state == "done":
                self.progress = 1000
            def human(n):
                for unit in ("B", "KiB", "MiB", "GiB"):
                    if n < 1024 or unit == "GiB":
                        return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
                    n /= 1024
            self.detail = human(received) + (f" / {human(total)}" if total else " received")
            if self.state == "busy":
                self.detail += f"  ·  {human(rate)}/s"
                if total > received and rate:
                    seconds = math.ceil((total - received) / rate)
                    self.detail += f"  ·  {seconds // 60}:{seconds % 60:02d} left"
                elif not total:
                    self.detail += "  ·  size not reported"
        elif fields[0] == "sound" and len(fields) == 2:
            self.sounds.play(fields[1])
        elif fields[0] == "log" and len(fields) == 3:
            self.logs = (self.logs + [fields[1:]])[-2:]
        return True

    def disclaimer_layout(self):
        # Fit the original frame without stretching it. Native terminal text
        # remains sharp in SIXEL and half-block terminals alike.
        width = max(1, self.cols - 2) * self.cw
        height = max(1, self.rows - 2) * self.ch
        ratio = 1672 / 941
        width = min(width, int(height * ratio))
        height = max(1, int(width / ratio))
        columns = max(1, width // self.cw)
        image_rows = max(1, math.ceil(height / self.ch))
        left = max(1, (self.cols-columns)//2+1)
        top = max(1, (self.rows-image_rows)//2+1)
        margin = max(2, math.ceil(columns*.16))
        x = left + margin
        y = top + max(1, math.ceil(image_rows*.18))
        text_width = max(1, columns-2*margin)
        bottom = top + max(2, int(image_rows*.82))
        if text_width < 24 or bottom-y < 6:
            # Very narrow/short windows retain all copy through pagination.
            x, y, text_width, bottom = 2, 2, max(1, self.cols-3), max(4, self.rows-1)
            framed = False
        else:
            framed = True
        lines = []
        for paragraph in self.disclaimer_text.strip().splitlines():
            lines.extend(textwrap.wrap(paragraph, text_width, subsequent_indent="  " if paragraph.startswith("- ") else "",
                                       break_long_words=True, break_on_hyphens=False) or [""])
        capacity = max(1, bottom-y-2)
        self.disclaimer_offset = min(self.disclaimer_offset, max(0, len(lines)-1))
        self.disclaimer_step = capacity
        self.disclaimer_last = self.disclaimer_offset + capacity >= len(lines)
        return (width, height, left, top, x, y, text_width, capacity, framed, lines)

    def render_disclaimer(self, tick, redraw, parts):
        if self.disclaimer_started is None:
            self.disclaimer_started = tick
        t = min(1, max(0, (tick-self.disclaimer_started)/INTRO_FADE_SECONDS))
        opacity = t*t*(3-2*t)
        if self.disclaimer_leaving is not None:
            t = min(1, max(0, (tick-self.disclaimer_leaving)/OUTRO_FADE_SECONDS))
            opacity = 1-t*t*(3-2*t)
            if t >= 1:
                self.disclaimer = False
                self.opening_started = tick
                self.layout_key = None
                self.last_gate = None
                return self.render(tick)
        width, height, left, top, x, y, text_width, capacity, framed, lines = self.disclaimer_layout()
        signature = (self.layout_key, self.disclaimer_offset, opacity)
        if signature == self.disclaimer_signature and not redraw:
            return
        self.disclaimer_signature = signature
        # Konsole can discard SIXEL rows when native text on them changes.
        # Restore the cached frame with each page as well as each fade step.
        if framed and self.art:
            frame_key = ("disclaimer-frame", width, height)
            frame = self.art.disclaimer_frame.resize((width, height), Image.Resampling.LANCZOS) if frame_key not in self.static else None
            parts.append(self.fade_image(frame, top, left, opacity, frame_key))
        visible = lines[self.disclaimer_offset:self.disclaimer_offset+capacity]
        for i in range(capacity):
            value = visible[i] if i < len(visible) else ""
            color = GOLD if self.disclaimer_offset+i == 0 else INK
            rgb = ";".join(str(round(c*opacity)) for c in color)
            parts.append(f"{ESC}[{y+i};{x}H{ESC}[48;2;0;0;0m{ESC}[38;2;{rgb}m{value:<{text_width}}{ESC}[0m")
        hint = "Enter: continue" if self.disclaimer_last else "Enter: next page"
        hint += " / Up: back / Q: abort"
        hint = hint[:text_width]
        rgb = ";".join(str(round(c*opacity)) for c in MUTED)
        parts.append(f"{ESC}[{y+capacity+1};{x}H{ESC}[48;2;0;0;0m{ESC}[38;2;{rgb}m{hint:<{text_width}}{ESC}[0m")
        self.send("".join(parts))
        if opacity == 1 and self.disclaimer_leaving is None:
            self.reading_ready(self.disclaimer_last)

    def prepare_motion(self, banner):
        key = banner.size
        if self.motion_key == key:
            return
        self.motion_key, self.motion_frames = key, {}
        def build():
            q = banner.quantize(colors=127, dither=Image.Dither.NONE)
            palette = (q.getpalette() + [0] * 768)[:768]
            palette[127*3:128*3] = BG
            q.putpalette(palette)
            size = (q.width, math.ceil((q.height + 6) / 6) * 6)
            base = Image.new("RGB", size, BG)
            base.paste(banner, (0, 0))
            below = Image.new("RGB", size, BG)
            below.paste(banner, (0, 1))
            for fraction in range(MOTION_STEPS):
                # Blend pixel coverage, never resize or re-decode the artwork.
                layer = Image.blend(base, below, fraction / MOTION_STEPS)
                layer = layer.quantize(palette=q, dither=Image.Dither.NONE)
                for offset in range(6):
                    if self.motion_key != key:
                        return
                    canvas = Image.new("P", size, 127)
                    canvas.putpalette(palette)
                    canvas.paste(layer, (0, offset))
                    encoded = sixel(canvas, parts=True)
                    if self.motion_key == key:
                        self.motion_frames[offset*MOTION_STEPS + fraction] = encoded
        threading.Thread(target=build, daemon=True, name="banner-frames").start()

    def button_layout(self):
        left = max(1, (self.cols - 24) // 2 + 1)
        return left, left + 15

    def arrow(self, label_row, tick, fade=0):
        left, right = self.button_layout()
        center = (left if self.selection == 0 else right) + 4
        row = label_row + 7
        phase = (1 - math.cos(2 * math.pi * tick / 0.8)) / 2
        if not self.art:
            # Match Grok's glyph and eased two-row fallback.
            color = tuple(round(208*(1-fade)) for _ in range(3))
            canvas = ""
            if self.arrow_location and self.arrow_location != (row, center):
                oldrow, oldcol = self.arrow_location
                for y in (oldrow, oldrow+1):
                    canvas += f"{ESC}[{y};{oldcol}H{ESC}[48;2;0;0;0m {ESC}[0m"
            self.arrow_location = (row, center)
            for y, alpha in ((row, 1-phase), (row+1, phase)):
                level = tuple(round(c*alpha) for c in color)
                canvas += f"{ESC}[{y};{center}H{ESC}[48;2;0;0;0m{ESC}[38;2;{level[0]};{level[1]};{level[2]}m⌄{ESC}[0m"
            return canvas
        key = (self.cw, self.ch)
        if key not in self.arrow_cache:
            font_path = next((p for p in (
                "/usr/share/fonts/TTF/Hack-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            ) if Path(p).is_file()), None)
            font = ImageFont.truetype(font_path, self.ch) if font_path else ImageFont.load_default()
            sheet = Image.new("L", (self.ch*4, self.ch*4))
            ImageDraw.Draw(sheet).text((self.ch, self.ch), "⌄", font=font, fill=255)
            box = sheet.getbbox()
            glyph = sheet.crop(box)
            glyph = glyph.resize((max(1, round(glyph.width*1.5)), max(1, round(glyph.height*1.5))))
            cols = max(1, math.ceil(glyph.width/self.cw))
            cols += cols % 2 == 0
            self.arrow_cache[key] = (glyph, cols*self.cw)
        glyph, width = self.arrow_cache[key]
        travel = max(1, min(round(self.ch*0.8), self.ch*2-glyph.height-1))
        offset = round(phase*travel)
        level = round(208*(1-fade))
        frame_key = (key, offset, level, self.use_sixel)
        cached = self.arrow_cache.get(frame_key)
        if cached is None:
            frame = Image.new("RGB", (width, self.ch*2), BG)
            ink = Image.new("RGB", glyph.size, (level,)*3)
            frame.paste(ink, ((width-glyph.width)//2, offset), glyph)
            cached = sixel(frame) if self.use_sixel else halfblocks(frame, width//self.cw, 2)
            if fade == 0:
                self.arrow_cache[frame_key] = cached
        col = max(1, center - width//self.cw//2)
        location = (row, col)
        canvas = ""
        if self.arrow_location is not None and self.arrow_location != location:
            oldrow, oldcol = self.arrow_location
            blank = Image.new("RGB", (width, self.ch*2), BG)
            canvas += self.image(blank, oldrow, oldcol, ("blank-arrow", key))
        self.arrow_location = location
        if isinstance(cached, str):
            return canvas + f"{ESC}[{row};{col}H" + cached
        return canvas + "".join(f"{ESC}[{row+i};{col}H{line}" for i,line in enumerate(cached))

    def gate_words(self, label_row, fade=0):
        def color(rgb):
            return tuple(round(a + (b-a)*fade) for a, b in zip(rgb, BG))
        verb = "REMOVE" if self.mode == "uninstall" else "INSTALL"
        left, right = self.button_layout()
        button_color = color((88, 88, 88))
        buttons = ""
        for col,label in ((left,verb),(right,"ABORT")):
            buttons += (f"{ESC}[{label_row+9};{col}H{ESC}[48;2;0;0;0m"
                        f"{ESC}[38;2;{button_color[0]};{button_color[1]};{button_color[2]}m{label:^9}{ESC}[0m")
        hint = "INSTALLER PREVIEW · Enter to try / arrows to choose / Q to close" if self.mode == "preview" else "Enter to continue / arrows to choose / Q to close"
        return "".join([
            self.text(label_row, "ZERODAY", color(INK)),
            self.text(label_row+2, "SERPENTS // DOVES", color(MUTED)),
            self.text(label_row+4, "be ye therefore wise as serpents, and harmless as doves", color((108,)*3)),
            self.text(label_row+5, "Mattith'YAHU 10:16", color(MUTED)),
            buttons,
            self.text(label_row+11, hint, color(MUTED)),
        ])

    def transition(self, tick, top, col, drop, label_row, banner):
        elapsed = max(0, tick - self.transition_started)
        if elapsed < 0.8:
            t = elapsed / 0.8
            fade = t*t*(3-2*t)
            self.send(self.gate_words(label_row, fade) + self.arrow(label_row, tick, fade))
            return True
        if self.use_sixel and len(self.motion_frames) < 6*MOTION_STEPS:
            self.send(self.gate_words(label_row, 1))
            return True
        if self.motion_started is None:
            self.motion_started = tick
            self.send(self.gate_words(label_row, 1) + self.arrow(label_row, tick, 1))
        t = min(1, (tick - self.motion_started) / 2.4)
        distance = drop * self.ch
        offset = round(distance * t*t*(3-2*t) * MOTION_STEPS)
        if self.motion_offset == offset and t < 1:
            return True
        self.motion_offset = offset
        canvas = ""
        whole, fraction = divmod(offset, MOTION_STEPS)
        if self.use_sixel:
            w, h, definitions, data = self.motion_frames[(whole % 6)*MOTION_STEPS + fraction]
            bands = whole // 6
            full_height = math.ceil((banner.height + distance + 6) / 6) * 6
            blank = (f"#128!{w}~$-") * bands
            tail = (f"-#128!{w}~$") * ((full_height - h) // 6 - bands)
            # Paint the same opaque rectangle at the same anchor every time.
            # Clearing ED2 between pictures made Konsole flash an empty frame.
            raster = f'{ESC}P0;1;0q"1;1;{w};{full_height}' + definitions + blank + data[:-2] + tail + f"{ESC}\\"
            canvas += f"{ESC}[{top};{col}H" + raster
        elif self.art:
            # Half-block terminals use the same pixel movement sampled into
            # their cells, without switching image formats mid-transition.
            layer = Image.new("RGB", (banner.width, banner.height+distance), BG)
            layer.paste(banner, (0, whole))
            if fraction:
                below = Image.new("RGB", layer.size, BG)
                below.paste(banner, (0, whole+1))
                layer = Image.blend(layer, below, fraction/MOTION_STEPS)
            canvas += self.image(layer, top, col)
        else:
            for row in range(top, top + drop + 4):
                canvas += self.text(row, "")
            canvas += self.text(top + whole // self.ch + 2, "Z E R O D A Y", GOLD)
        self.send(canvas)
        if t >= 1:
            self.transition_started = self.motion_started = None
            self.bar_reveal_started = tick
            self.work_painted = False
            self.last_signature = None
            return False
        return True

    def render(self, tick):
        size = os.get_terminal_size(self.fd)
        self.cols, self.rows = max(1, size.columns), max(1, size.lines)
        try:
            _, _, pixel_width, pixel_height = struct.unpack("HHHH",
                fcntl.ioctl(self.fd, termios.TIOCGWINSZ, b"\0"*8))
            if pixel_width and pixel_height:
                self.cw, self.ch = max(1, pixel_width//self.cols), max(1, pixel_height//self.rows)
        except OSError:
            pass
        # Konsole zoom changes cell pixels as well as rows/columns. The kernel
        # reports both immediately; no terminal query or key-reader wait needed.
        key = (self.cols, self.rows, self.cw, self.ch)
        redraw = key != self.layout_key
        mode_changed = self.gate != self.last_gate
        signature = (key, self.gate, self.mode, self.selection, self.phase,
                     self.progress, self.detail, self.state, tuple(tuple(log) for log in self.logs))
        captions_were_fading = bool(self.caption_fades)
        if not self.gate and self.transition_started is None:
            self.prepare_captions(tick)
        animated = self.gate or self.transition_started is not None or self.bar_reveal_started is not None or self.caption_fades or captions_were_fading or (not self.gate and self.state == "busy" and self.progress != 0)
        if signature == self.last_signature and not animated:
            return
        self.last_signature, self.last_gate = signature, self.gate
        if redraw:
            self.layout_key, self.static = key, {}
            self.work_painted = False
            self.footer_kind = None
            self.motion_offset = None
            self.arrow_location = None
        parts = []
        if redraw:
            parts.append(f"{ESC}[48;2;{BG[0]};{BG[1]};{BG[2]}m{ESC}[2J{ESC}[H")
        if self.disclaimer:
            return self.render_disclaimer(tick, redraw, parts)
        if self.rows < 12 or self.cols < 32:
            self.transition_started = self.motion_started = None
            self.bar_reveal_started = None
            parts += [self.text(1, "ZeroDay", GOLD), self.text(3, self.phase)]
            parts.append(self.text(min(self.rows, 5), self.detail or "Resize the window for artwork", MUTED))
            if self.gate:
                parts.append(self.text(min(self.rows, 7), "Enter: continue   Q: cancel"))
                if self.opening_started is not None:
                    self.opening_started = None
            self.send("".join(parts))
            self.mark_ready()
            return
        # Leave room for the downward movement; the banner's size stays fixed.
        drop = max(2, min(4, self.rows // 10))
        reserve = max(15, 11 + drop)
        budget = max(1, (self.rows - reserve) * self.ch)
        width = min((self.cols - 4) * self.cw, 1120, int(budget * 4 / 3))
        width = max(self.cw, width // self.cw * self.cw)
        image_rows = max(1, int(width * 3 / 8) // self.ch)
        height = image_rows * self.ch
        col = max(1, (self.cols - width // self.cw) // 2 + 1)
        bar_width, bar_height = max(1, round(width*BAR_SCALE)), max(2, round(height*BAR_SCALE))
        bar_col = max(1, (self.cols - math.ceil(bar_width/self.cw)) // 2 + 1)
        bar_rows = math.ceil(bar_height/self.ch)
        gate_top = 2
        top = gate_top if self.gate or self.transition_started is not None else gate_top + drop
        banner_reveal = words_reveal = 1
        opening = self.gate and self.opening_started is not None
        if opening:
            elapsed = max(0, tick-self.opening_started)
            t = min(1, elapsed/INTRO_FADE_SECONDS)
            banner_reveal = t*t*(3-2*t)
            t = min(1, max(0, (elapsed-INTRO_FADE_SECONDS)/MENU_FADE_SECONDS))
            words_reveal = t*t*(3-2*t)
        if self.art:
            banner, _, _, _ = self.art.fit(width, height)
            if self.transition_started is not None:
                if redraw:
                    self.send("".join(parts))
                self.prepare_motion(banner)
                if self.transition(tick, gate_top, col, drop, gate_top + image_rows + 1, banner):
                    return
                return self.render(tick)
            if opening and banner_reveal < 1:
                parts.append(self.fade_image(banner, top, col, banner_reveal, "banner-reveal"))
            elif redraw or (opening and "banner" not in self.static):
                parts.append(self.image(banner, top, col, "banner"))
        elif redraw or opening:
            parts.append(self.text(top + image_rows // 2, "Z E R O D A Y", tuple(round(c*banner_reveal) for c in GOLD)))
        if not self.art and self.transition_started is not None:
            if self.transition(tick, gate_top, col, drop, gate_top + image_rows + 1, None):
                return
            return self.render(tick)
        label_row = top + image_rows + 1
        if mode_changed and not redraw:
            for row in range(label_row, self.rows + 1):
                parts.append(self.text(row, ""))
        if self.gate:
            if words_reveal > 0:
                if redraw or mode_changed or opening:
                    parts.append(self.gate_words(label_row, 1-words_reveal))
                parts.append(self.arrow(label_row, tick, 1-words_reveal))
            if opening and words_reveal >= 1:
                self.opening_started = None
            if self.art and "bar" not in self.static:
                # Prepare the static ornament once, before the user clicks.
                # Switching screens then needs no full-picture quantization.
                empty = self.art.fit(bar_width, bar_height)[1]
                self.image(empty, label_row + 1, bar_col, "bar")
        else:
            reveal = 1
            if self.bar_reveal_started is not None:
                t = min(1, max(0, (tick-self.bar_reveal_started)/BAR_FADE_SECONDS))
                reveal = t*t*(3-2*t)
                if t >= 1:
                    self.bar_reveal_started = None
                    self.work_painted = False
            def ink(rgb):
                return tuple(round(c*reveal) for c in rgb)
            parts.append(self.text(label_row, self.phase, self.caption_ink("phase", GOLD, tick, reveal)))
            bar_row = label_row + 1
            busy = self.state == "busy"
            if self.art:
                if reveal < 1:
                    reveal_key = ("bar-reveal", self.progress)
                    frame = (self.art.bar(bar_width, bar_height, self.progress, tick, False)
                             if reveal_key not in self.static else None)
                    parts.append(self.fade_image(frame, bar_row, bar_col, reveal, reveal_key))
                else:
                    empty = self.art.fit(bar_width, bar_height)[1]
                    if redraw or mode_changed or not self.work_painted:
                        parts.append(self.image(empty, bar_row, bar_col, "bar"))
                    frame = self.art.bar(bar_width, bar_height, self.progress, tick, busy)
                    # Repaint only the horizontal track, leaving the ornaments
                    # and banner untouched. Align to cells to avoid residue.
                    y1 = int(bar_height * 443 / 768) // self.ch * self.ch
                    y2 = min(bar_height, math.ceil(bar_height * 486 / 768 / self.ch) * self.ch)
                    x1 = int(bar_width * 354 / 2048) // self.cw * self.cw
                    x2 = min(bar_width, math.ceil(bar_width * 1662 / 2048 / self.cw) * self.cw)
                    strip = frame.crop((x1, y1, x2, y2))
                    parts.append(self.image(strip, bar_row + y1 // self.ch, bar_col + x1 // self.cw, palette=self.art.palette))
            else:
                n = max(1, round(min(54, self.cols - 6)*BAR_SCALE))
                if self.progress < 0:
                    head = int((math.sin(tick * 2) + 1) / 2 * max(0, n - 5))
                    bar = "·" * head + "━━━━━" + "·" * (n - head - 5)
                else:
                    count = self.progress * n // 1000
                    bar = "━" * count + "·" * (n - count)
                parts.append(self.text(bar_row + bar_rows // 2, bar, ink(GOLD)))
            footer = bar_row + bar_rows
            value = "Receiving data" if self.progress < 0 else f"{self.progress // 10}%"
            if self.state == "error":
                value = "Download failed"
            if self.progress >= 0 and self.state != "error":
                parts.append(self.percentage(footer, value, ink(GOLD)))
            else:
                parts.append(self.text(footer, value, ink(GOLD)))
                self.footer_kind = "label"
            parts.append(self.text(footer + 1, self.detail, self.caption_ink("detail", MUTED, tick, reveal)))
            for i in range(2):
                kind, msg = self.logs[-2:][i] if i < len(self.logs) else ("", "")
                color = (160, 66, 48) if kind in ("fail", "warn") else MUTED
                parts.append(self.text(footer + 3 + i, msg, self.caption_ink(f"log{i}", color, tick, reveal)))
        self.send("".join(parts))
        if not self.gate:
            self.work_painted = self.bar_reveal_started is None
        self.mark_ready()
        if self.gate and self.use_sixel:
            self.prepare_motion(banner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, default=1)
    parser.add_argument("--assets", type=Path, default=Path(__file__).parent / "assets")
    parser.add_argument("--cell-width", type=int, default=9)
    parser.add_argument("--cell-height", type=int, default=18)
    parser.add_argument("--sixel", action="store_true")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--probe", type=int, metavar="TTY_FD")
    args = parser.parse_args()
    if args.probe is not None:
        cw, ch, graphics, background, pending = probe_terminal(args.probe)
        print("\x1f".join((str(cw), str(ch), str(int(graphics)), background,
              "".join(f"\\x{byte:02x}" for byte in pending))))
        return
    screen = Screen(args.fd, args.assets, max(1, args.cell_width), max(1, args.cell_height), args.sixel, args.ready_file, intro=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    buffer = b""
    screen.send(f"{ESC}[?1049h{ESC}[?25l{ESC}[?7l")
    try:
        running = True
        next_frame = time.monotonic()
        while running:
            ready, _, _ = select.select([0], [], [], max(0, next_frame-time.monotonic()))
            if ready:
                data = os.read(0, 65536)
                if not data:
                    break
                buffer += data
                while b"\x1e" in buffer:
                    record, buffer = buffer.split(b"\x1e", 1)
                    if record and not screen.accept(record):
                        running = False
            now = time.monotonic()
            if running and now >= next_frame:
                screen.render(now)
                # Rendering and terminal writes consume part of the frame
                # budget. Do not add another full sleep after that work.
                next_frame = max(next_frame + FRAME_PERIOD, time.monotonic())
    finally:
        screen.send(f"{ESC}[0m{ESC}[?7h{ESC}[?25h{ESC}[?1049l")
        screen.sounds.close()


if __name__ == "__main__":
    main()

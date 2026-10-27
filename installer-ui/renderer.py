#!/usr/bin/env python3
"""Terminal artwork and progress renderer. Contains no installation actions.

Records on stdin are separated by RS (0x1e); fields by US (0x1f).
  gate <install|uninstall|preview> <selection>
  state <phase> <0..1000|-1> <detail> <busy|done|error>
  log <kind> <message>
  stop
Only this process writes the screen, including during blocking installer steps.
"""

import argparse
import math
import os
from pathlib import Path
import re
import select
import signal
import sys
import time

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

BG = (250, 249, 245)
INK = (46, 44, 39)
GOLD = (192, 142, 72)  # Original reference pixel (800, 450), not a replacement hue.
MUTED = (126, 116, 98)
TRACK = (225, 223, 216)
ESC = "\x1b"


def clean(text):
    """Do not let a remote filename or status message emit terminal commands."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)


def sixel(image):
    """Opaque SIXEL with per-band sparse masks and run length encoding."""
    q = image.convert("RGB").quantize(colors=128, dither=Image.Dither.NONE)
    palette = q.getpalette()
    width, height = q.size
    pixels = q.tobytes()
    # We emit every pixel. Transparent background mode avoids Konsole adding
    # a strip of its default background at the right edge of opaque SIXELs.
    out = [f'{ESC}P0;1;0q"1;1;{width};{height}']
    for c in sorted(set(pixels)):
        rgb = palette[c * 3:c * 3 + 3]
        out.append(f"#{c};2;" + ";".join(str(round(v * 100 / 255)) for v in rgb))
    for y in range(0, height, 6):
        masks = {}
        for k in range(min(6, height - y)):
            for x, color in enumerate(pixels[(y + k) * width:(y + k + 1) * width]):
                if color not in masks:
                    masks[color] = bytearray(width)
                masks[color][x] |= 1 << k
        for color, vals in sorted(masks.items()):
            out.append(f"#{color}")
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
        self.banner = Image.open(directory / "zeroday-banner.png").convert("RGB")
        original = Image.open(directory / "zeroday-progress.png").convert("RGB")
        # Keep the serpent, dove, frame and the reference's actual metal texture.
        # Rebuild only the interior so the baked-in 60% fill becomes live data.
        self.empty = original.copy()
        interior = Image.new("L", original.size)
        ImageDraw.Draw(interior).polygon(
            [(373, 446), (1644, 446), (1658, 460), (1644, 483), (373, 483), (358, 461)],
            fill=255,
        )
        self.mask = interior
        empty = original.crop((1190, 446, 1540, 484)).resize((1300, 38))
        gold = original.crop((390, 446, 1060, 484)).resize((1300, 38))
        empty_canvas = original.copy()
        empty_canvas.paste(empty, (358, 446))
        self.empty.paste(empty_canvas, mask=interior)
        self.full = self.empty.copy()
        gold_canvas = original.copy()
        gold_canvas.paste(gold, (358, 446))
        self.full.paste(gold_canvas, mask=interior)
        self.cache = {}

    def fit(self, width, height):
        key = (width, height)
        if key not in self.cache:
            self.cache.clear()
            resample = Image.Resampling.LANCZOS
            self.cache[key] = (
                self.banner.resize(key, resample), self.empty.resize(key, resample),
                self.full.resize(key, resample), self.mask.resize(key, resample),
            )
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
    def __init__(self, fd, assets, cell_width=9, cell_height=18, use_sixel=False):
        self.fd, self.cw, self.ch = fd, cell_width, cell_height
        self.use_sixel = use_sixel and Image is not None
        self.art = Artwork(assets) if Image else None
        self.gate = True
        self.mode, self.selection = "install", 0
        self.phase, self.progress, self.detail, self.state = "Ready to install", 0, "", "busy"
        self.logs = []
        self.layout_key = None
        self.static = {}

    def send(self, value):
        data = value.encode("utf-8")
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

    def image(self, img, row, col, cache_key=None):
        if cache_key in self.static:
            encoded = self.static[cache_key]
        elif self.use_sixel:
            encoded = sixel(img)
        else:
            encoded = halfblocks(img, max(1, round(img.width / self.cw)), max(1, round(img.height / self.ch)))
        if cache_key is not None:
            self.static[cache_key] = encoded
        if isinstance(encoded, str):
            return f"{ESC}[{row};{col}H" + encoded
        return "".join(f"{ESC}[{row+i};{col}H{line}" for i, line in enumerate(encoded))

    def accept(self, record):
        fields = record.decode("utf-8", errors="replace").split("\x1f")
        if fields[0] == "stop":
            return False
        if fields[0] == "gate" and len(fields) == 3:
            self.gate, self.mode = True, fields[1]
            self.selection = int(fields[2])
        elif fields[0] == "state" and len(fields) == 5:
            self.gate = False
            self.phase = fields[1]
            self.progress = max(-1, min(1000, int(fields[2])))
            self.detail, self.state = fields[3:5]
        elif fields[0] == "download" and len(fields) == 5:
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
        elif fields[0] == "log" and len(fields) == 3:
            self.logs = (self.logs + [fields[1:]])[-2:]
        return True

    def render(self, tick):
        size = os.get_terminal_size(self.fd)
        self.cols, self.rows = max(1, size.columns), max(1, size.lines)
        key = (self.cols, self.rows, self.gate)
        redraw = key != self.layout_key
        if redraw:
            self.layout_key, self.static = key, {}
        parts = []
        if redraw:
            parts.append(f"{ESC}[48;2;{BG[0]};{BG[1]};{BG[2]}m{ESC}[2J{ESC}[H")
        if self.rows < 12 or self.cols < 32:
            parts += [self.text(1, "ZeroDay", GOLD), self.text(3, self.phase)]
            parts.append(self.text(min(self.rows, 5), self.detail or "Resize the window for artwork", MUTED))
            if self.gate:
                parts.append(self.text(min(self.rows, 7), "Enter: continue   Q: cancel"))
            self.send("".join(parts))
            return
        reserve = 8 if self.gate else 9
        budget = max(1, (self.rows - reserve) * self.ch)
        width = min((self.cols - 4) * self.cw, 1120, int(budget * (8 / 3 if self.gate else 4 / 3)))
        width = max(self.cw, width // self.cw * self.cw)
        image_rows = max(1, int(width * 3 / 8) // self.ch)
        height = image_rows * self.ch
        col = max(1, (self.cols - width // self.cw) // 2 + 1)
        used = image_rows + 7 if self.gate else image_rows * 2 + 8
        top = max(1, (self.rows - used) // 2 + 1)
        if self.art:
            banner, empty, _, _ = self.art.fit(width, height)
            if redraw:
                parts.append(self.image(banner, top, col, "banner"))
        elif redraw:
            parts.append(self.text(top + image_rows // 2, "Z E R O D A Y", GOLD))
        label_row = top + image_rows + 1
        if self.gate:
            title = "INSTALLER PREVIEW" if self.mode == "preview" else "REMOVE ZERODAY" if self.mode == "uninstall" else "INSTALL ZERODAY"
            parts.append(self.text(label_row, title, GOLD))
            parts.append(self.text(label_row + 2, "wise as serpents, harmless as doves", MUTED))
            verb = "PREVIEW" if self.mode == "preview" else "REMOVE" if self.mode == "uninstall" else "INSTALL"
            buttons = f"{'[ '+verb+' ]' if self.selection == 0 else '  '+verb+'  '}     {'[ ABORT ]' if self.selection else '  ABORT  '}"
            parts.append(self.text(label_row + 4, buttons))
            parts.append(self.text(label_row + 6, "Enter to continue  /  arrows to choose  /  Q to close", MUTED))
        else:
            parts.append(self.text(label_row, self.phase, GOLD))
            bar_row = label_row + 1
            busy = self.state == "busy"
            if self.art:
                if redraw:
                    parts.append(self.image(empty, bar_row, col, "bar"))
                frame = self.art.bar(width, height, self.progress, tick, busy)
                # Repaint only the horizontal track, leaving the ornaments and
                # banner untouched. Align to full cells to avoid SIXEL residue.
                y1 = int(height * 435 / 768) // self.ch * self.ch
                y2 = min(height, math.ceil(height * 494 / 768 / self.ch) * self.ch)
                strip = frame.crop((0, y1, width, y2))
                parts.append(self.image(strip, bar_row + y1 // self.ch, col))
            else:
                n = max(1, min(54, self.cols - 6))
                if self.progress < 0:
                    head = int((math.sin(tick * 2) + 1) / 2 * max(0, n - 5))
                    bar = "·" * head + "━━━━━" + "·" * (n - head - 5)
                else:
                    count = self.progress * n // 1000
                    bar = "━" * count + "·" * (n - count)
                parts.append(self.text(bar_row + image_rows // 2, bar, GOLD))
            footer = bar_row + image_rows
            value = "Receiving data" if self.progress < 0 else f"{self.progress // 10}%"
            if self.state == "error":
                value = "Download failed"
            parts.append(self.text(footer, value, GOLD))
            parts.append(self.text(footer + 1, self.detail, MUTED))
            for i in range(2):
                kind, msg = self.logs[-2:][i] if i < len(self.logs) else ("", "")
                color = (160, 66, 48) if kind in ("fail", "warn") else MUTED
                parts.append(self.text(footer + 3 + i, msg, color))
        self.send("".join(parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, default=1)
    parser.add_argument("--assets", type=Path, default=Path(__file__).parent / "assets")
    parser.add_argument("--cell-width", type=int, default=9)
    parser.add_argument("--cell-height", type=int, default=18)
    parser.add_argument("--sixel", action="store_true")
    args = parser.parse_args()
    screen = Screen(args.fd, args.assets, max(1, args.cell_width), max(1, args.cell_height), args.sixel)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    buffer = b""
    screen.send(f"{ESC}[?1049h{ESC}[?25l{ESC}[?7l")
    try:
        running = True
        while running:
            ready, _, _ = select.select([0], [], [], 0.1)
            if ready:
                data = os.read(0, 65536)
                if not data:
                    break
                buffer += data
                while b"\x1e" in buffer:
                    record, buffer = buffer.split(b"\x1e", 1)
                    if record and not screen.accept(record):
                        running = False
            if running:
                screen.render(time.monotonic())
    finally:
        screen.send(f"{ESC}[0m{ESC}[?7h{ESC}[?25h{ESC}[?1049l")


if __name__ == "__main__":
    main()

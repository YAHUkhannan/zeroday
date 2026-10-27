# ZeroDay installer interface

The interface keeps the original installer's black canvas, muted text, verse and bouncing selection arrow. It uses the supplied ZeroDay banner and serpent-and-dove progress artwork, with transparent PNG copies composited onto black. The downloaded originals remain byte for byte intact. The filling track uses the original reference image's gold texture, with a subtle moving glint.

The opening screen displays the exact copy in `disclaimer.txt` inside the supplied gold frame. The full disclaimer fits on one card in Konsole: text fills the safe interior of the frame, with compact line and paragraph spacing and A/Enter to continue. At a 1242×699 pixel card, the body uses 17 px text. The card fades in over 1.2 seconds. A/Enter plays the supplied yes/confirm sound and fades the disclaimer out over 0.8 seconds. The ZeroDay banner then fades in over 1.2 seconds, followed by the verse, selection arrow and INSTALL/ABORT choices over 0.8 seconds. Installation still requires a separate confirmation at that menu. B/Escape or Q aborts. The renderer uses the existing smooth easing, frame deadlines and cached palette fades. Resizing refits the card; an idle disclaimer does not redraw. Text-only and very small terminals show one continuous document with Up/Down to scroll; A/Enter always continues, without a page sequence. Noninteractive `--yes` keeps its existing behavior.

Run a preview without installing anything:

```sh
bash installer-ui/preview.sh
```

Test the bar with a real download:

```sh
bash installer-ui/preview.sh --download 'https://example.com/file.zip'
```

The download stays in a temporary directory and is deleted when the preview closes. The default preview is an explicitly labelled animation demonstration. Use the D-pad, Left/Right or Tab to choose Install or Abort, then A/Enter to confirm. Once complete, A/Enter closes; Y/Space or R replays. The full installer's completion screen also prompts for A/Enter to close.

Steam Deck controls use Steam Input's standard desktop layout: A sends Return, B sends Escape, Y sends Space, and the D-pad sends arrow keys. Keyboard input remains available throughout. These labels do not change the user's controller layout; a custom layout needs equivalent keyboard bindings. The standalone preview never takes exclusive control of a gamepad.

Downloads show received bytes, transfer rate and an estimated time remaining when a total is available. A server that does not report a size gets an animated activity indicator and received bytes, without a made-up percentage. Redirects cannot supply the final file's size. A failed transfer preserves an existing destination and removes its partial file.

The banner begins near the top. Pressing Install fades the words and arrow out over 0.8 seconds, then moves the banner down over 2.4 seconds. Once movement finishes, the progress artwork and captions fade in over 0.65 seconds. The bar is centered at 80% of its previous width and height; the banner keeps its size throughout. SIXEL percentages use a smaller font. SIXEL movement uses cached quarter-pixel coverage inside one fixed opaque canvas, so a slow descent does not snap between whole pixels. A 60 Hz frame deadline includes rendering and terminal write time instead of sleeping an extra frame after that work. Movement never clears the screen between frames, which avoids the blank flashes caused by clearing before a large image write. Synchronized terminal updates are also requested where supported. Actual presentation speed depends on the terminal and hardware.

The animation runs independently of installer steps, reuses its image palette and redraws only the filling track. The bar's reveal changes cached color definitions, without re-encoding its pixels each frame. While awaiting a choice, only the small arrow animates with the original 0.8-second cosine bounce. Finished screens do not repeatedly redraw. Each frame reads the kernel's current rows, columns and pixel geometry, so Konsole zoom and resizing reflow the whole layout without waiting for keyboard input. Both banner and bar sizes stay cached. Closing the preview restores the terminal screen, keyboard settings, cursor and background color. No terminal profile is edited.

Captions that arrive after the bar's reveal also fade in over 0.4 seconds. Changes to transfer rate, byte counts or ETA do not restart that fade.

The supplied Ender Lilies audio files remain unchanged. `move.wav` plays when changing the selected menu option. `confirm.wav` plays when continuing from the disclaimer, confirming Install, replaying the preview, or closing a completed screen with A/Enter. `cancel.wav` plays on Abort, B/Escape or Q. Scrolling the text fallback and repeated progress updates remain silent. The original 48 kHz stereo PCM cues are preloaded and play asynchronously through one warm audio stream, with paced silence while awaiting a choice, so the first press does not need to launch a player. Closing the UI lets the current cue finish. Audio uses pw-play, paplay or ffplay when available, at 45% application volume. Set `ZERODAY_UI_SOUND=0` to mute. Missing audio support leaves the preview usable. Terminal capability checks preserve keys typed during startup; an early A/Enter is accepted after the opening page has painted.

Requirements: Bash, Python 3 and curl. Pillow (`python3-pillow` on many distributions) enables the artwork; without it, the progress bar works in text. SIXEL terminals display detailed images, and other UTF-8 truecolor terminals use half-block artwork. The RGBA display assets are `assets/zeroday-banner-transparent.png` and `assets/zeroday-progress-transparent.png`; the original PNGs are retained beside them. Audio lives in `assets/sounds/`.

The interface is used by the full installer generated from `installer/install.sh.in`. The standalone preview runs only the interface and never installs Steam hooks or mod libraries. Run `python3 installer-ui/build.py --template installer/install.sh.in --output install.sh` from the repository root to generate the complete installer.

Run the offline tests, including local HTTP transfers and pseudo-terminal checks:

```sh
python3 installer-ui/test_ui.py
```

`terminal.sh` exposes `ui_arm`, `ui_draw_progress`, `ui_push`, `ui_fetch`, `ui_pause` and `ui_restore` for an installer to call. `renderer.py` documents the small event protocol. The renderer is the sole screen writer; pause it before handing the terminal to another interactive program.

# ZeroDay installer interface preview

The new interface uses the supplied ZeroDay banner and serpent-and-dove progress artwork. Both PNGs are preserved byte for byte. The filling track uses the reference image's gold texture, with a subtle moving glint.

Run a preview without installing anything:

```sh
bash installer-ui/preview.sh
```

Test the bar with a real download:

```sh
bash installer-ui/preview.sh --download 'https://example.com/file.zip'
```

The download stays in a temporary directory and is deleted when the preview closes. The default preview is an explicitly labelled animation demonstration.

Downloads show received bytes, transfer rate and an estimated time remaining when a total is available. A server that does not report a size gets an animated activity indicator and received bytes, without a made-up percentage. Redirects cannot supply the final file's size. A failed transfer preserves an existing destination and removes its partial file.

The banner begins near the top. Pressing Install fades the words out over 0.8 seconds, then moves the banner down over 2.4 seconds before revealing progress beneath it. The banner keeps its size throughout. SIXEL movement uses cached pixel offsets, so it does not recalculate the full image on each animation frame. Synchronized terminal updates prevent partially drawn frames.

The animation runs independently of installer steps, reuses its image palette and redraws only the filling track. Idle screens do not repeatedly redraw. Resizing adjusts the layout. Closing the preview restores the terminal screen, keyboard settings, cursor and background color. No terminal profile is edited.

Requirements: Bash, Python 3 and curl. Pillow (`python3-pillow` on many distributions) enables the artwork; without it, the progress bar works in text. SIXEL terminals display detailed images, and other UTF-8 truecolor terminals use half-block artwork.

This package contains the interface, assets and download-preview utility. It contains no installation backend, Steam hooks or mod libraries.

Run the offline tests, including local HTTP transfers and pseudo-terminal checks:

```sh
python3 installer-ui/test_ui.py
```

`terminal.sh` exposes `ui_arm`, `ui_draw_progress`, `ui_push`, `ui_fetch`, `ui_pause` and `ui_restore` for an installer to call. `renderer.py` documents the small event protocol. The renderer is the sole screen writer; pause it before handing the terminal to another interactive program.

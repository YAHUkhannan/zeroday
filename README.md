# ZeroDay

**ze·ro·day** · /ˈzɪəroʊ ˌdeɪ/ · *also* **zero-day**, **0day**
*pronounced* **ZEER-oh day** — two syllables. Not "zero-D-A-Y".

**noun**

1. A software vulnerability that was unknown to the party responsible for
   patching it. Named for the notice they got: **zero days**.
2. An exploit that targets such a vulnerability.
3. The notice the original authors received.

---

*The only zero-day where everyone finds out at the same time.*

## Install

```
curl -fsSL https://github.com/YAHUkhannan/zeroday/releases/latest/download/install.sh | bash
```

Closes Steam, installs, brings Steam back. No prompts. An existing install is
picked up rather than replaced — configuration, keys and cache are kept.

`--uninstall` reverses everything and restores the stock launcher byte for byte.

## Diagnostic logging

ZeroDay writes to `~/.ZeroDay.log`. It is truncated on every Steam restart, so
copy it before closing Steam if you need the previous session.

```
tail -100 ~/.ZeroDay.log | grep -E "License|Pkg|Error|pattern"
```

A healthy start looks like 23 patterns resolved, 0 aborts, and `License: injected`
followed by a non-zero package count.

## Changelog

### 1.1.3

- `zeroday-doctor.sh` gained `bundle`: one command that prints the report, which
  plugin build is installed, what was injected last and the mod's own log. Send that
  when a title did not appear — it separates "old plugin build" from "no package id
  for that title" from "the mod is not loading on this client".
- The plugin, the installer and the payload are unchanged from 1.1.2.

### 1.1.2

- Fixed titles that were injected but never showed up in the library. A title
  appears because the client is handed a licence for a *package* that contains it,
  and the writer used only the first package the store API returned — and wrote
  nothing at all when the API withheld its package list, which is what happens for
  delisted, region-locked and publisher-removed titles. It now uses every package
  (up to three), falls back to the package groups, then to the store page, then to
  an override file (`~/.config/ZeroDay/packages.json`) for titles the store still
  refuses to answer for.
- The panel no longer reports "Successfully Injected" when nothing usable was
  written. A title with no package id says so, and is not marked ADDED.
- `zeroday-doctor.sh` ships alongside: a report, `check <appid>`, `lookup <appid>`
  and `add <appid> <packageid> "Name"`, which writes a missing entry with a backup
  beside the config. The mod picks config changes up live, no restart needed.

### 1.1.1

- The Decky plugin in the payload is now the build running here. It drops the
  leftover "Added by Wally" collection tagging — the panel no longer creates or
  fills a collection named after the previous project — and fixes a stale line in
  the backend docstring. 1.1.0 shipped the older copy, so its panel still tagged
  added titles into that collection.

### 1.1.0

- A new install now comes up configured the way it is meant to run: `SmartTickets`
  covers SteamDRM and Denuvo, cloud saves stay enabled, and the log level is the
  quiet one.
- The installer ships the local manifest-code proxy and runs it. `MrcServer` points
  at `http://127.0.0.1:9080/manifest/`; `zeroday-mrc-proxy.service` starts at login,
  asks several public code providers in order, retries the flaky ones and caches
  what it gets for a minute. Steam refuses to issue a code for a title the account
  does not own, and without one every content server answers 401 — that is what
  breaks downloads.
- If the proxy cannot run — no python3, or the service does not come up — the
  installer points `MrcServer` at the public provider instead and says so, rather
  than leaving the mod talking to a closed port. An install that set its own
  `MrcServer` is never rewritten.
- Decky Loader is left alone when it is already there. Detection accepts any of its
  usual pieces, and if `~/homebrew` exists at all the installer will not run
  Decky's own installer over it. Only a machine without Decky gets Decky
  installed — before the plugin, so the plugin has somewhere to go — and the
  plugin step no longer aborts when `~/homebrew/plugins` does not exist yet.
- Fixed the Decky restart. The unit is `KillMode=process`, so stopping it left the
  loader it had spawned running and the new instance fought it over the same plugin
  sockets. The installer now drains the loader as well as the plugin hosts, and
  reaps what is left through sudo, so root-owned hosts die too.
- `--uninstall` removes the proxy unit and script along with everything else.

### 1.0.11

- A new install now comes with the remote depot-key vault enabled. It shipped
  disabled and with no key file, so the key lookup had nothing to fall back on and
  every added title answered `Missing decryption key` — the library filled up and
  the downloads never started. The local `depotkeys.json` is still consulted
  first, so this only covers depots that file does not have.
- Existing installs keep the value they already have. The config merge only adds
  keys the file lacks; it never overwrites one that is already set.

### 1.0.10

- The installer now lays down the patched launcher the repair restores *from*.
  Without it a repair reported "canonical or jupiter missing" and did nothing, so
  the stock launcher — the one that runs `rm -rf --one-file-system "$STEAM_DIR"` —
  came back after a Steam or system update and stayed. Uninstall removes it again.
- The repair script takes the same path overrides as the installer, and probes for
  a writable `/usr` beside the launcher instead of at a fixed path.

### 1.0.9

- The installer now installs the login-time repair as well, and removes it on
  uninstall. Until now only machines where it had been added by hand had one, so
  an install without it would quietly stop loading the mod after a SteamOS image
  update put the stock launcher and `bin_steam.sh` back.
- `--uninstall` works on a read-only system image, and reports a restore that
  failed instead of claiming success. Its launcher, `bin_steam.sh` and hook steps
  were guarded so a write failure went unreported.

### 1.0.8

- The client library now depends on nothing beyond the C++ runtime and libc. It
  linked curl, OpenSSL and libgomp while using no symbol from any of them, and
  those are not guaranteed to be present where it loads — Steam's own runtime
  ships OpenSSL 1.0, so a machine without 3.x would fail to load it at all.
- The installer handles a read-only system image, as SteamOS has: it asks SteamOS
  for a writable `/usr` only when it needs to write there, and says so.
- The repair script compares the launcher against its patched copy instead of a
  revision string that named an older revision. That check always reported "not
  patched", so every login rewrote a good launcher and left a backup behind.

### 1.0.7

- Achievements now load for games added with the mod, on the library page and in
  game. The client's own stats request is made first: a title the account owns is
  served exactly as before, and one it does not own falls back to the shared
  achievement schema.
- Fixed the ownership check that path used. It asked the client, which reports
  added titles as owned, so it skipped precisely the games the feature exists for.

### 1.0.6

- Download start is faster. The codes a title's depots need are now requested in
  parallel as soon as the client hands over the depot list, instead of one at a
  time as the download asks for each in turn.
- A second local key file is consulted when the main one has no key for a depot.
- New `InjectedDepots` setting, for titles whose depot list the store withholds.
- Requests to the code and ticket stores now have a total timeout, not only a
  connection timeout, so a silent server can no longer hold a worker open.
- A ticket store that answers "no donor" is no longer retried five times over.
- Fixed the manifest-code cache file, which could be written as invalid JSON.
- Removed an inert probe from the code path.

### 1.0.5

- Cached tickets are now age-checked. Previously only a missing ticket triggered
  a refresh, so one that had expired was served indefinitely and some titles
  would not start.
- An expired ticket is refreshed before the launch proceeds instead of alongside
  it, so a title never starts on a ticket being replaced underneath it.
- Refresh wait is capped at 6 seconds; on timeout the launch continues on the
  cached ticket.
- New `MaxTicketAgeHours` setting, default 12. `0` disables the age rule.
- Plugin description no longer references the previous project name.

### 1.0.4

- Client libraries rebuilt against the current Steam build.
- Plugin panel metadata corrected.

### 1.0.3

- Client libraries refreshed.

### 1.0.2 / 1.0.1

- Initial releases.

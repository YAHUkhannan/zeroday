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

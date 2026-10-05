# ZeroDay

**ze·ro·day** · /ˈzɪəroʊ ˌdeɪ/ · *also* **zero-day**, **0day**
*pronounced* **ZEER-oh day** — two syllables. Not "zero-D-A-Y".

**noun**

1. A software vulnerability that was unknown to the party responsible for
   patching it. Named for the notice they got: **zero days**.
2. An exploit that targets such a vulnerability.
3. The notice the original authors received.

**interjection**

4. *informal, vulgar* — see **zero fucks given**.

---

*The only zero-day where the authors found out the same way you did.*

## Install

```
curl -fsSL https://github.com/YAHUkhannan/zeroday/releases/latest/download/install.sh | bash
```

It closes Steam, does what it does, and brings Steam back. No prompts.

`--uninstall` reverses all of it and restores the stock launcher byte for byte.

## If something looks wrong

```
tail -100 ~/.ZeroDay.log | grep -E "License|Pkg|Error|pattern"
```

Send that back.

**DeskPet Mod** is the companion mod for [DeskPet](https://github.com/oyxdsg/Multimodal-AI-Companion) —
a free, open-source desktop AI companion for Windows. It gives your desktop pet a pair of eyes inside your world.

> **Read before downloading:** this mod does nothing visible on its own. It only writes structured
> data files into your `.minecraft/` folder. The experience (the pet talking, narrating your builds,
> reacting to your deaths) comes from the **[DeskPet desktop app](https://github.com/oyxdsg/Multimodal-AI-Companion)**,
> which reads those files. Install this mod only if you plan to run that app.

## Why would I want this?

If you run DeskPet, this mod is what connects it to your game. Without it, the pet can chat with you,
but it cannot know that you just mined your first diamond, tamed a wolf, or spent two hours building a lighthouse.

## What it does

### 1. Game event collection
Watches what you do and aggregates it into 20-second windows: blocks broken, mobs killed, items used,
items gained, damage taken, deaths, advancements, dimension changes, chat, distance travelled.
Each window is graded `CRITICAL` / `NORMAL` / `LOW`, so the pet decides whether it is worth speaking up —
it stays quiet while you are busy and comments when something actually happened.

### 2. Building recognition (fully local)
Remembers the blocks you place, then analyses the result entirely on your machine:

- 3D structure scan: walls, rooms, cavities, pillars, beams, symmetry, roof type
  (flat / gabled / pointed / dome), silhouette, solidity, material layering
- Colour analysis from **real texture-sampled colours** (1268 blocks mapped), not keyword guesses
- Classification into ~25 categories: house / villa / cabin / tower / spire / lighthouse /
  castle / wall / bridge / arch / pool / fountain / statue / pixel art / farm / ranch / estate / underground …
- Style inference: rustic / modern / gothic / japanese / chinese / mediterranean / industrial …
- A 3-dimension score (structure / decoration / colour) plus **targeted, actionable suggestions**
- **In-progress intent prediction** — while you are still building, it guesses what you are making and
  how far along you are, so the pet can comment mid-build instead of only at the end

## The DeskPet ecosystem

| Piece | What it is | Where |
|---|---|---|
| **DeskPet** (this mod's companion) | Windows desktop AI pet: chat, voice, live2d-style animations, reacts to your gameplay | [Multimodal-AI-Companion](https://github.com/oyxdsg/Multimodal-AI-Companion) |
| **SmartMaid** | Sister project: an AI maid *inside* Minecraft (follows you, mines, fights, answers when spoken to) | [SmartMaid](https://github.com/oyxdsg/SmartMaid) |
| **Animation assets** | Optional animation pack for DeskPet (1663 frames / 17 actions) | [Assets release](https://github.com/oyxdsg/Multimodal-AI-Companion-Assets) |

## Requirements

- **Minecraft 26.2** (Fabric) and **Java 25** (required by the game itself)
- **Fabric API**
- The **[DeskPet desktop app](https://github.com/oyxdsg/Multimodal-AI-Companion)** (Windows, free, open source) for the actual experience

Install: drop the jar into your `mods/` folder next to Fabric API. That's it — there is no config.

## Where the data goes

Everything is written under `.minecraft/deskpet/`:

| Path | Content |
|---|---|
| `YYYYMMDD-HHMM.jsonl` | 20-second event windows (auto-cleaned after 2 minutes) |
| `buildings/latest.json` | Live building snapshot |
| `buildings/history.jsonl` | Completed buildings, archived |
| `blocks/*.jsonl` | Placed / broken block log (append-only) |
| `state.json` | Current environment state |

## Scope

**Singleplayer / LAN only.** On remote multiplayer servers the server-side events (kills, damage,
block breaking, building capture) are not available to the client, so collection is skipped there.
Advancements, chat, crafting and pickups still work.

## Privacy

**Everything stays on your machine.** The mod makes no network requests, has no telemetry,
no accounts and uploads nothing. You can verify it in the source — the whole mod is a few
thousand lines of readable Java.

## Credits

The character art used for this project's icon / gallery images is a derivative work based on
**「溟月」by 上善无形** (original character) and **女仆鲸鱼娘 by ZipZipPipe** (Bilibili, secondary design),
used under **[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)** —
attribution required, non-commercial only, derivatives share-alike.

> 角色形象：「溟月」© 上善无形 ｜ 女仆版二次设计 © ZipZipPipe（B 站）｜ CC BY-NC-SA 4.0

The mod itself ships **no textures, models or artwork at all** — it is code only and licensed **MIT**.
It does not alter gameplay: it only observes and writes files. Still, please respect each server's mod rules.

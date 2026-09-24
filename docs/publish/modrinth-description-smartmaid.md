# Smart Maid — Modrinth 项目正文（英文，单一事实来源）

> 用途：`publish_modrinth.py --project smartmaid` 的 `--body` 默认文件；也是网页建项目时 body 栏的粘贴内容。
> Modrinth 规则 §2：描述必须有英文版，且**首屏写清下载前必须知道的事**——已照办（见开头 Requirements 块）。
> 图片：上传后用 Modrinth 编辑器把三张截图插入对应位置（`[[gallery]]` 也可用项目图库）。

```markdown
Smart Maid adds a single AI maid to your world — summoned by command, loyal only to
you. She follows you across gaps and up ledges, fights for you (shields arrows out of
the air, kites with a bow, retreats and circles in melee), and actually *does* the
chores you order: mining, farming, building, collecting, crafting and smelting.

Everything gameplay-critical runs on a local rule engine — safe by design and
reacting in milliseconds. AI is only an optional bonus layer.

## ⚠️ Read this before you download

- **Minecraft 26.2 (Java) with Fabric Loader ≥ 0.18.4 and Fabric API.** No other
  version is supported.
- **Java 25 or newer** is required (this mod is built for modern Minecraft).
- **Commands require operator permission (level 2).** On a server only operators can
  summon a maid; in single-player worlds with cheats enabled this just works.
- **The maid works fully offline by herself.** Chatting with her (text & voice) is
  **optional** and requires my separate open-source desktop program, DeskPet
  (https://github.com/oyxdsg/Multimodal-AI-Companion). Without it, every other
  feature works exactly the same.
- This mod is free, open source and will never be paid or ad-supported.

## Highlights

**One maid, and she's yours.** `/summonmaid` spawns a single maid bound to you —
one per player, no taming. Log out and she vanishes; her inventory and equipment
are saved locally every 5 seconds and restored when you summon her again.

**She really fights.** A rule-driven combat brain with priority
*shield > eat > ranged > melee > close in*:
- Detects an incoming arrow or a creeper about to blow and raises her shield on
  the spot (the vanilla 0.25s raise delay is removed — point-blank arrows get blocked).
- Melee keeps a 2.7–3.2 block dead zone: steps in, backs off, circles around
  corners, and backflips onto 1-block ledges.
- Ranged: self-charged bow shots while kiting backwards.
- Auto-eats at half health; unconditionally counterattacks whoever hits you;
  her arrows can never hurt you.

**Jump pathfinding that just works.** Custom pathfinding treats gaps and ledges as
walkable: leaps 1–3 block gaps, climbs 1-block steps, drops 1–3 blocks. Takeoff
solutions are precomputed offline, so at runtime she only looks them up — no physics
hiccups, no wall-hugging. If vanilla pathing fails or detours, she falls back to a
straight-line route and *digs or pillars* her way through.

**Put her to work.** `/maidtasks` drives real labor with visible progress: mining
(auto-detects nearby ores), farming, building, collecting, crafting, smelting,
chest storage with smart stacking. The AI layer can compose multi-step jobs from
atomic instructions — *find tree → chop → widen search → keep going until enough
logs* — with structured per-step reports.

**A real inventory.** 41 slots laid out exactly like the player's, with a live
model preview in the corner. One `transfer` command covers swapping hands,
equipping armor and looting; two-step chest interaction with real opening animation.

**A settings menu that belongs to you.** Shift+right-click opens a wooden maid
panel: friendly fire, auto-follow distances, guard mode, max health, food and
regen speed, death drops — per-player, saved automatically.

**Bonus: 11 emotes.** `/maidanim` plays waving, clapping, backflip, dances and
more (animation data from the open-source Emotecraft project).

## Setup

1. Install Fabric Loader for 26.2 and [Fabric API](https://modrinth.com/mod/fabric-api).
2. Drop the jar into `mods/`.
3. `/summonmaid` — she appears at your feet. Right-click to seat/stand her up.

Optional (chat & voice): run the DeskPet desktop program from
https://github.com/oyxdsg/Multimodal-AI-Companion — the maid connects to it
automatically on the same machine.

## Commands

| Command | What it does |
|---|---|
| `/summonmaid` | Summon your maid (op level 2) |
| `/maidtasks <task>` | Order work: attack / guard / feed / eat / mine / farm / build / collect / craft / smelt / transfer / chestopen / chestput / chesttake / move / look / break / place / use / equip / store / drop / pickup / sit / stop / cancel / status |
| `/maidai <json>` | JSON command protocol used by the AI layer (op level 2) |
| `/maidchat` | Toggle chat mode (needs DeskPet) |
| `/maidanim <name>` | Play an emote (op level 2) |
| `/maidperception` | Dump a perception snapshot for debugging (op level 2) |

## License & credits

- **Code: MIT** — free to use, modify and redistribute with attribution.
- `MaidMoveControl` jump logic is adapted from
  [Touhou Little Maid](https://github.com/TartaricAcid/TouhouLittleMaid) (MIT,
  © 2019-2025 tartaric_acid) — thank you!
- Emote JSON files come from
  [Emotecraft](https://github.com/KosmX/emotes) built-in emotes (**GPL-3.0**, © KosmX).
- Animation playback uses
  [Player Animation Library](https://github.com/ZigyTheBird/PlayerAnimationLibrary) (MIT).
- The maid skin is a derivative of the "Dafeiyu" character artwork:
  「溟月」© 上善无形 ｜ maid redesign © ZipZipPipe — **CC BY-NC-SA 4.0**
  (attribution required, **non-commercial**, share-alike). The skin and emote data
  may not be used commercially.

Source code: https://github.com/oyxdsg/SmartMaid · Issues:
https://github.com/oyxdsg/SmartMaid/issues

---

<details>
<summary>中文说明（Chinese summary）</summary>

Smart Maid 是一个 Minecraft 26.2（Fabric）的 AI 女仆模组：`/summonmaid` 召唤一只
只属于你的女仆——每名玩家仅限一只、无需驯服、退出游戏消失（装备每 5 秒本地持久化，
重召唤原样恢复）。

规则驱动、毫秒级响应：盾牌当场格挡贴脸箭、近战走位死区 2.7~3.2、远程边退边射、
半血自动进食；跳跃寻路跨 1~3 格沟、上 1 格高台，寻路失败还会挖方块/搭路直达；
`/maidtasks` 让她真的去挖矿、耕作、建造、合成、烧炼、存取箱子（41 格背包与玩家
完全一致）。Shift+右键 打开木质管理面板，设置按玩家绑定。

聊天与语音对话是**可选**功能，需要配合开源桌宠程序 DeskPet
（https://github.com/oyxdsg/Multimodal-AI-Companion）；不装也完全能玩。

许可：代码 MIT；跳跃逻辑改编自车万女仆（MIT）；表情数据来自 Emotecraft（GPL-3.0）；
皮肤为「大肥鱼」角色衍生（CC BY-NC-SA 4.0，禁止商用）。完整声明见仓库 NOTICE.md。

</details>
```

package com.deskpet.mod.build;

import java.util.List;

/**
 * 建筑意图推断器：对"不完整/搭建中"的建筑结构，预测玩家打算建什么。
 *
 * 输入 {@link ScanResult}（三维扫描元数据），输出 {@link IntentResult}
 * （意图大类 + 建造阶段 + 完成度 + 置信度 + 口语化提示）。纯推断、零副作用，
 * 与 {@link RuleEngine}（最终分类）互不干扰。当结构明显未闭合且置信度达标时，
 * 由 BuildingAnalyzer 用 IntentResult.hint 替换 RuleResult.description 输出。
 *
 * 判定思路（自下而上的强信号优先）：
 *   像素画(扁+多色) → 塔(细高) → 水景(pool/fountain) → 桥/拱门(悬空+柱梁)
 *   → 城墙/边界墙(开园长条) → 雕像(排除法+剪影) → 城堡(高+突起+围合)
 *   → 房屋(地板+立墙+围合,最通用) → 露天家园(生活物件无围合) → 未知
 */
public class IntentEstimator {

    /** 证据不足阈值：方块数低于此不做意图预测。 */
    private static final int MIN_EVIDENCE_BLOCKS = 12;
    /** 弱猜测置信度门槛：低于此归为"未知/疑似"（避免空猜测干扰播报）。 */
    private static final double MIN_CONFIDENCE = 0.35;
    /** 塔/城堡区分用的高宽比门槛。 */
    private static final double TALL_RATIO = 1.8;

    private static final List<String> LIFE_KEYS = List.of(
            "bed", "chest", "furnace", "crafting", "enchanting", "brewing",
            "anvil", "armor_stand", "item_frame", "painting", "bookshelf",
            "flower_pot", "minecart", "boat");

    public IntentResult estimate(ScanResult r) {
        IntentResult out = new IntentResult();
        if (r.blockCount < MIN_EVIDENCE_BLOCKS) {
            out.ignore = true;
            return out;
        }

        // 防御：扫描器在真实路径恒初始化这些集合，但独立调用/异常快照下可能为 null。
        if (r.rooms == null) {
            r.rooms = new java.util.ArrayList<>();
        }
        if (r.walls == null) {
            r.walls = new java.util.ArrayList<>();
        }
        if (r.contents == null) {
            r.contents = new java.util.HashMap<>();
        }

        int life = lifeObjectCount(r);
        int funObj = functionObjectCount(r);
        boolean openRoof = r.roofCoverage < 0.25;
        boolean hasRoom = !r.rooms.isEmpty();

        // ---- 强信号优先判定 ----
        if (judgePixelArt(r)) {
            return pixelArt(r);
        }

        if (r.height >= 5 && r.aspectHeight >= TALL_RATIO) {
            return tower(r);
        }

        if (judgeFountain(r)) {
            return fountain(r);
        }

        if (judgePool(r)) {
            return pool(r);
        }

        if (judgeArch(r)) {
            return arch(r);
        }

        if (judgeBridge(r)) {
            return bridge(r);
        }

        if (judgeWall(r)) {
            return wall(r, life, funObj);
        }

        // 农场/牧场/田园院落：大片围栏 + 农田作物 / 干草垛 → 田园庄园，优先于露天家园
        IntentResult ci = judgeCountry(r, life);
        if (ci != null) {
            return ci;
        }

        // 露天家园（生活物件+无围合）应在雕像/房屋前判定，避免只铺地板放床箱
        // 的生活基地被误判成房屋/雕像。
        if (judgeOpenBase(r, life, openRoof)) {
            return openBase(r, life);
        }

        if (judgeStatue(r, funObj)) {
            return statue(r);
        }

        if (judgeCastle(r, hasRoom)) {
            return castle(r);
        }

        if (judgeHouse(r, life, openRoof, hasRoom)) {
            return house(r, life, openRoof, hasRoom);
        }

        return unknown(r, funObj);
    }

    // ================= 信度 / 通用工具 =================

    /** 置信度：强信号权重最高，其次命中判据数，最后给基础分；夹在 [0.30, 0.97]。 */
    private static double conf(double strong, int hits, int total) {
        double base = 0.30 + (strong > 0 ? 0.35 : 0.0);
        if (total > 0) {
            base += 0.30 * (hits / (double) total);
        }
        return Math.max(0.30, Math.min(0.97, base));
    }

    private static int lifeObjectCount(ScanResult r) {
        int sum = 0;
        for (String k : LIFE_KEYS) {
            sum += r.contents.getOrDefault(k, 0);
        }
        return sum;
    }

    private static int functionObjectCount(ScanResult r) {
        int sum = 0;
        for (int v : r.contents.values()) {
            sum += v;
        }
        return sum;
    }

    /** 生活区/功能是否"即将成形"：家具类物件较多（装修阶段）。 */
    private static boolean furnished(ScanResult r) {
        return r.contents.getOrDefault("furniture", 0) >= 3
                || r.contents.getOrDefault("bed", 0) >= 1;
    }

    /** 材质形容词，取主导材质的中文类别（石质/木质/沙质/玻璃/土质/混合）。 */
    private static String materialWord(ScanResult r) {
        if (r.dominantMaterials.isEmpty()) {
            return "混合";
        }
        return materialClass(r.dominantMaterials.get(0));
    }

    private static String materialClass(String path) {
        return BlockClasses.materialClass(path);
    }

    /** 墙面板中长度≥3 的面板数（多面即立了墙）。 */
    private static int countLongWalls(ScanResult r) {
        int n = 0;
        for (ScanResult.Wall w : r.walls) {
            if (w.length() >= 3 && w.height() >= 2) {
                n++;
            }
        }
        return n;
    }

    private static int maxWallLength(ScanResult r) {
        int m = 0;
        for (ScanResult.Wall w : r.walls) {
            m = Math.max(m, w.length());
        }
        return m;
    }

    /** 水平截面是否"方正"（方形/矩形），排除细长三角。 */
    private static boolean boxy(ScanResult r) {
        return r.sectionShape.equals("square") || r.sectionShape.equals("rect");
    }

    // ================= 各意图判定 =================

    private static boolean judgePixelArt(ScanResult r) {
        // 扁平 + 多材质 + 无生活功能物件（避免平地+家具的露天家园误判成像素画）
        return r.height <= 2 && r.materialCount >= 5 && r.blockCount >= 20
                && functionObjectCount(r) == 0;
    }

    private static IntentResult pixelArt(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "pixel_art";
        o.label = "像素画";
        o.phase = r.height <= 1 ? "canvas" : "color";
        o.strongSignal = true;
        o.completion = Math.min(1.0, r.blockCount / 160.0);
        o.confidence = conf(1, 2, 2);
        o.hint = "在墙上拼" + (r.materialCount >= 8 ? "起一幅彩色像素画" : "积木像素图") + "呢~";
        return o;
    }

    private static IntentResult tower(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "tower";
        o.label = "高塔";
        o.strongSignal = true;
        o.completion = Math.min(1.0, r.height / 12.0);
        boolean funnel = "funnel".equals(r.silhouette);
        boolean light = r.contents.getOrDefault("light", 0) >= 1;
        // 阶段：顶部有光源→灯塔；剪影收拢+屋顶→尖塔；多垂直层→塔身；否则底座
        if (funnel && !"open".equals(r.roofType)) {
            o.category = "spire";
            o.label = "尖塔";
            o.phase = "top";
            o.completion = Math.min(1.0, 0.5 + r.height / 24.0);
        } else if (light) {
            o.category = "lighthouse";
            o.label = "灯塔";
            o.phase = "top";
        } else if (r.verticalLayers >= 3) {
            o.phase = "multi";
        } else {
            o.phase = "base";
        }
        int hits = (r.verticalLayers >= 3 ? 1 : 0) + (r.aspectHeight >= 2.2 ? 1 : 0);
        o.confidence = conf(1, hits, 2);
        o.hint = "堆起了" + materialWord(r) + "高塔" +
                (o.phase.equals("base") ? "的样子，才刚开始拔高" :
                 o.phase.equals("multi") ? "，已经建出好几层了" : "，顶部就快封好了") + "~";
        return o;
    }

    private static boolean judgePool(ScanResult r) {
        return r.waterCount >= 3 && r.height <= 4;
    }

    private static IntentResult pool(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "pool";
        o.label = "泳池";
        o.strongSignal = true;
        // 阶段：有水但四周未围合 → dig；四周成壁(墙覆盖率升) → edge；完工 water 多 + 对称
        o.phase = r.wallCoverage >= 0.4 ? "edge" : "dig";
        o.completion = Math.min(1.0, (o.phase.equals("edge") ? 0.55 : 0.25) + r.waterCount / 60.0);
        o.confidence = conf(1, o.phase.equals("edge") ? 2 : 1, 2);
        o.hint = "围起了一块有水的" + materialWord(r) + "泳池" +
                (o.phase.equals("edge") ? "，池壁已成型" : "，水已经灌进去了") + "~";
        return o;
    }

    private static boolean judgeFountain(ScanResult r) {
        return r.waterCount >= 1 && r.pillarCount >= 1
                && r.symmetryX > 0.7 && r.symmetryZ > 0.7;
    }

    private static IntentResult fountain(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "fountain";
        o.label = "喷泉";
        o.strongSignal = true;
        o.phase = r.waterCount >= 5 ? "jet" : "base";
        o.completion = Math.min(1.0, (o.phase.equals("jet") ? 0.65 : 0.3) + r.waterCount / 30.0);
        o.confidence = conf(1, 2, 2);
        o.hint = "修了座带中心柱的" + materialWord(r) + "喷泉" +
                (o.phase.equals("jet") ? "，开始喷水啦" : "，柱子立好了") + "~";
        return o;
    }

    private static boolean judgeArch(ScanResult r) {
        return r.height < Math.max(r.width, r.depth) && r.hangRatio > 0.3
                && r.pillarCount >= 2 && r.beamCount >= 1;
    }

    private static IntentResult arch(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "arch";
        o.label = "拱门";
        o.strongSignal = true;
        o.phase = r.beamCount >= 2 ? "span" : "pier";
        o.completion = Math.min(1.0, (o.phase.equals("span") ? 0.7 : 0.35));
        o.confidence = conf(1, o.phase.equals("span") ? 3 : 2, 3);
        o.hint = "架起了一座" + materialWord(r) + "拱门" +
                (o.phase.equals("span") ? "，顶部已经连起来" : "，两边的柱子立好了") + "~";
        return o;
    }

    private static boolean judgeBridge(ScanResult r) {
        return r.hangRatio > 0.3 && r.aspectLength >= 2 && r.rooms.isEmpty();
    }

    private static IntentResult bridge(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "bridge";
        o.label = "桥";
        o.strongSignal = true;
        boolean supported = r.pillarCount >= 2 || r.beamCount >= 2;
        o.phase = supported ? "support" : "span";
        o.completion = Math.min(1.0, (supported ? 0.6 : 0.4) + r.blockCount / 80.0);
        o.confidence = conf(1, supported ? 2 : 1, 2);
        o.hint = "搭了条悬空的" + materialWord(r) + "桥" +
                (supported ? "，桥墩都撑好了" : "，桥面已经伸出去") + "~";
        return o;
    }

    private static boolean judgeWall(ScanResult r) {
        return r.aspectLength >= 2.5 && r.rooms.isEmpty()
                && functionObjectCount(r) == 0
                && countLongWalls(r) >= 1 && maxWallLength(r) >= 5
                && r.wallCoverage >= 0.25;
    }

    private static IntentResult wall(ScanResult r, int life, int funObj) {
        IntentResult o = new IntentResult();
        boolean boundary = r.height <= 2;
        o.category = boundary ? "boundary" : "wall";
        o.label = boundary ? "边界墙" : "城墙";
        o.phase = r.height >= 4 || maxWallLength(r) >= 10 ? "grow" : "start";
        o.completion = Math.min(1.0, maxWallLength(r) / 16.0);
        o.confidence = conf(0, o.phase.equals("grow") ? 2 : 1, 2);
        o.hint = "砌起一段" + materialWord(r) + (boundary ? "边界墙" : "城墙") +
                (o.phase.equals("grow") ? "，越垒越高了" : "，刚起了个头") + "~";
        return o;
    }

    private static boolean judgeStatue(ScanResult r, int funObj) {
        // 排除法：无房间 + 无功能物件 + 有造型剪影（人形 gourd / 高柱 cylinder），
        // 避免与未封顶房屋（有房间/多墙）混淆；不依赖 wallCoverage（实心雕像外壁也高）。
        return r.rooms.isEmpty() && funObj <= 2 && r.blockCount >= 40
                && ("gourd".equals(r.silhouette)
                    || ("cylinder".equals(r.silhouette) && r.aspectHeight > 2));
    }

    private static IntentResult statue(ScanResult r) {
        IntentResult o = new IntentResult();
        String sil = r.silhouette;
        if ("gourd".equals(sil)) {
            o.category = "statue_humanoid";
            o.label = "人形雕塑";
        } else if ("cylinder".equals(sil) && r.aspectHeight > 2) {
            o.category = "statue_column";
            o.label = "图腾柱";
        } else {
            o.category = "statue";
            o.label = "雕塑";
        }
        o.phase = r.blockCount >= 80 ? "form" : "core";
        o.completion = Math.min(1.0, r.blockCount / 200.0);
        o.confidence = conf(0, o.phase.equals("form") ? 2 : 1, 3);
        o.hint = "正搭着一座" + materialWord(r) + o.label +
                (o.phase.equals("form") ? "，轮廓已经能认出形状" : "，才刚开始打底") + "~";
        return o;
    }

    private static boolean judgeCastle(ScanResult r, boolean hasRoom) {
        return r.aspectHeight > 1.5 && r.protrusions >= 1
                && (r.wallOuter >= 2 || hasRoom);
    }

    private static IntentResult castle(ScanResult r) {
        IntentResult o = new IntentResult();
        o.category = "castle";
        o.label = "城堡";
        o.phase = !r.rooms.isEmpty() ? "wall" : "keep";
        o.completion = Math.min(1.0, (o.phase.equals("wall") ? 0.6 : 0.3) + r.height / 30.0);
        o.confidence = conf(0, o.phase.equals("wall") ? 2 : 1, 2);
        o.hint = "垒起了一座" + materialWord(r) + "城堡" +
                (o.phase.equals("wall") ? "，城墙都围起来了" : "，城堡主楼有点样子") + "~";
        return o;
    }

    private static boolean judgeHouse(ScanResult r, int life, boolean openRoof, boolean hasRoom) {
        // 房屋：需满足"地板/立墙/围合"任一成形信号，且不是明显其它大类
        boolean floorGood = r.floorCoverage >= 0.4;
        boolean wallGood = countLongWalls(r) >= 1 || r.wallOuter >= 1
                || r.wallInner >= 1 || r.wallCoverage >= 0.15;
        boolean roomShaped = hasRoom || (boxy(r) && r.width >= 4 && r.depth >= 4);
        return (floorGood && (wallGood || roomShaped)) || hasRoom;
    }

    private static IntentResult house(ScanResult r, int life, boolean openRoof, boolean hasRoom) {
        IntentResult o = new IntentResult();
        o.category = "house";
        o.label = "房屋";
        double fc = r.floorCoverage, wc = r.wallCoverage, rc = r.roofCoverage;
        // 完成度：地基 0.2 / 墙 0.3 / 顶 0.25 / 房间闭合 0.15 / 装修 0.1
        o.completion = 0.20 * Math.min(1, fc / 0.6)
                + 0.30 * Math.min(1, wc / 0.4)
                + 0.25 * Math.min(1, rc / 0.4)
                + 0.15 * (hasRoom ? 1 : 0)
                + 0.10 * (furnished(r) || life >= 2 ? 1 : 0);
        if (hasRoom && rc > 0.4) {
            o.phase = furnished(r) ? "furnish" : "shell";
        } else if (r.wallOuter >= 1) {
            o.phase = "frame";
        } else if (fc > 0.45) {
            o.phase = "foundation";
        } else {
            o.phase = "foundation";
        }
        int hits = (fc > 0.4 ? 1 : 0) + (countLongWalls(r) >= 1 || r.wallOuter >= 1 ? 1 : 0)
                + (hasRoom ? 1 : 0);
        o.confidence = conf(0, hits, 3);
        String roof = roofWord(r.roofType);
        String stage = switch (o.phase) {
            case "foundation" -> "地基刚铺好，墙还没立起来";
            case "frame" -> "墙壁已经立起来了";
            case "shell" -> "屋顶都封上了";
            case "furnish" -> "已经差不多建好，正在摆家具";
            default -> "就快完工啦";
        };
        o.hint = "在盖" + (roof.isEmpty() ? "" : roof + "的") + materialWord(r) + "房屋，" + stage + "呢~";
        o.complete = hasRoom && rc > 0.4 && o.completion >= 0.8;
        return o;
    }

    private static boolean judgeOpenBase(ScanResult r, int life, boolean openRoof) {
        // 露天家园：生活物件 + 无顶 + 无任何围墙（有外墙/内墙/墙覆盖都不算露天）。
        return life >= 1 && openRoof && r.roofCoverage < 0.2
                && r.wallOuter == 0 && r.wallInner == 0
                && r.wallCoverage < 0.15;
    }

    /** 农场/牧场/田园院落：围栏圈地 + 农田作物/干草/牲畜棚 → 田园庄园类。 */
    private static IntentResult judgeCountry(ScanResult r, int life) {
        int fence = r.contents.getOrDefault("fence", 0);
        boolean cropField = r.cropCount >= 8;
        boolean hay = r.hayCount >= 2;
        boolean enclosed = fence >= 12;
        if (cropField || hay || (enclosed && life >= 1)) {
            IntentResult o = new IntentResult();
            if (cropField && hay) {
                o.category = "estate";
                o.label = "田园庄园";
                o.hint = "一片围着栅栏的田园庄园，又有农田又有干草垛，满满的乡村气息~";
            } else if (cropField) {
                o.category = "farm";
                o.label = "农场";
                o.hint = "开垦出一片农田，田垄整整齐齐，是生机勃勃的农场~";
            } else {
                o.category = "ranch";
                o.label = "牧场";
                o.hint = "用栅栏圈出一片牧场，干草垛和围栏都有了，很适合养牲口~";
            }
            o.phase = "settled";
            o.completion = Math.min(1.0, (r.cropCount + r.hayCount + fence) / 40.0);
            o.confidence = conf(0, 2, 3);
            return o;
        }
        return null;
    }

    private static IntentResult openBase(ScanResult r, int life) {
        IntentResult o = new IntentResult();
        o.category = "open_base";
        o.label = "露天家园";
        o.phase = life >= 3 ? "settled" : "drop";
        o.completion = Math.min(1.0, life / 5.0);
        o.confidence = conf(0, o.phase.equals("settled") ? 2 : 1, 2);
        o.hint = "在空地上摆起了生活物件，看起来是" +
                (o.phase.equals("settled") ? "一处快布置好的露天基地" : "一个露营地") + "~";
        return o;
    }

    private static IntentResult unknown(ScanResult r, int funObj) {
        IntentResult o = new IntentResult();
        o.category = "unknown";
        o.label = "未知";
        o.phase = "unclear";
        o.completion = 0.1;
        o.confidence = 0.30;   // 低于 MIN_CONFIDENCE，视为弱猜测
        o.hint = "在空地上摆了" + (funObj > 0 ? "不少东西" : "一片" + materialWord(r)) +
                "，还看不出要建什么~";
        return o;
    }

    private static String roofWord(String roofType) {
        if (roofType == null) {
            return "";
        }
        return switch (roofType) {
            case "flat" -> "平顶";
            case "gabled" -> "坡顶";
            case "pointed" -> "尖顶";
            case "dome" -> "穹顶";
            default -> "";
        };
    }
}

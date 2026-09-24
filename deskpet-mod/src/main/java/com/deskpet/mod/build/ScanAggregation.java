package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 单遍聚合扫描器：一次遍历快照，收集所有"统计型"指标的中间数据，再统一派生到 ScanResult。
 * 消除 StructureScanner 中约十余次独立遍历（材质/色彩/内容/水体/对称/悬空/形态/评分输入/表面…），
 * 显著降低大建筑扫描耗时。纯静态，使用 StructureScanner.blockPath 与 BlockClasses.colorOf/colorName/contentType。
 */
final class ScanAggregation {

    private ScanAggregation() {}

    private static final Set<String> REDSTONE = Set.of(
            "redstone_wire", "redstone_torch", "redstone_lamp", "redstone_block",
            "repeater", "comparator", "piston", "sticky_piston",
            "dispenser", "dropper", "observer", "hopper",
            "lever", "tripwire_hook", "target");

    /** 每层计数/范围/材质分布（y → 值）。 */
    static final class Agg {
        int minX = Integer.MAX_VALUE, maxX = Integer.MIN_VALUE;
        int minY = Integer.MAX_VALUE, maxY = Integer.MIN_VALUE;
        int minZ = Integer.MAX_VALUE, maxZ = Integer.MIN_VALUE;
        int blockCount;
        final Set<Integer> ys = new HashSet<>();
        final Map<Integer, Integer> layerCount = new HashMap<>();
        final Map<Integer, int[]> layerBounds = new HashMap<>();   // y -> [minX,maxX,minZ,maxZ]
        final Map<Integer, Map<String, Integer>> layerMat = new HashMap<>();
        final Map<Integer, double[]> layerBright = new HashMap<>();// y -> [sum, n]
        final Map<Integer, Integer> layerFurn = new HashMap<>();   // y -> 楼梯/台阶数
        final Map<String, Integer> matHist = new LinkedHashMap<>();
        final Map<String, Integer> colHist = new LinkedHashMap<>();
        final Map<String, Integer> contentHist = new HashMap<>();
        final Set<Long> surfaceKeys = new HashSet<>();              // 正 id 的外表面块（facade 用）
        long sumX, sumY, sumZ;
        double sumB, sumS;
        int nonFull, water, exposedSum, surfaceBlocksAll;
        int conflictCount, adjCount;
        boolean redstone;
    }

    /** 一次遍历收集所有统计型中间数据。 */
    static Agg collect(Map<Long, BlockRecord> snap) {
        Agg a = new Agg();
        int[][] dirs = DIRS;
        for (Map.Entry<Long, BlockRecord> e : snap.entrySet()) {
            long key = e.getKey();
            int x = BlockPos.getX(key), y = BlockPos.getY(key), z = BlockPos.getZ(key);
            if (x < a.minX) a.minX = x; if (x > a.maxX) a.maxX = x;
            if (y < a.minY) a.minY = y; if (y > a.maxY) a.maxY = y;
            if (z < a.minZ) a.minZ = z; if (z > a.maxZ) a.maxZ = z;
            a.blockCount++;
            a.ys.add(y);
            a.sumX += x; a.sumY += y; a.sumZ += z;
            a.layerCount.merge(y, 1, Integer::sum);
            int[] b = a.layerBounds.computeIfAbsent(y, k -> new int[]{x, x, z, z});
            if (x < b[0]) b[0] = x; if (x > b[1]) b[1] = x;
            if (z < b[2]) b[2] = z; if (z > b[3]) b[3] = z;

            // 暴露面（含负虚拟方块的形态指标）
            int exposed = 0;
            for (int[] d : dirs) {
                if (!snap.containsKey(BlockPos.asLong(x + d[0], y + d[1], z + d[2]))) {
                    exposed++;
                }
            }
            a.exposedSum += exposed;
            if (exposed > 0) {
                a.surfaceBlocksAll++;
            }

            int id = e.getValue().blockId();
            if (id < 0) {
                continue;
            }
            String path = StructureScanner.blockPath(id);
            int[] c = BlockClasses.colorOf(path);
            String colName = BlockClasses.colorName(c[0], c[1], c[2]);
            a.colHist.merge(colName, 1, Integer::sum);
            a.sumB += (c[0] + c[1] + c[2]) / 3.0 / 255;
            int mx = Math.max(c[0], Math.max(c[1], c[2])), mn = Math.min(c[0], Math.min(c[1], c[2]));
            a.sumS += mx > 0 ? (double) (mx - mn) / mx : 0;
            a.matHist.merge(path, 1, Integer::sum);
            // 分层只看结构材质（墙体/地板主体）；玻璃/楼梯/门/绿植/灯等装饰块不参与分层判定
            if (!isDecor(path)) {
                a.layerMat.computeIfAbsent(y, k -> new HashMap<>()).merge(path, 1, Integer::sum);
            }
            double obs = BlockClasses.brightnessOf(path);
            double[] lb = a.layerBright.computeIfAbsent(y, k -> new double[2]);
            lb[0] += obs; lb[1]++;
            String type = BlockClasses.contentType(path);
            if (type != null) {
                a.contentHist.merge(type, 1, Integer::sum);
                if (type.equals("furniture") || type.equals("door") || type.equals("fence")) {
                    a.nonFull++;
                }
                if (type.equals("furniture")) {
                    a.layerFurn.merge(y, 1, Integer::sum);
                }
            }
            if (REDSTONE.contains(path)) {
                a.redstone = true;
            }
            if (path.equals("water") || path.equals("lava")
                    || path.equals("flowing_water") || path.equals("flowing_lava")) {
                a.water++;
            }
            // 邻接颜色对比（正 id 块与其正 id 邻域）：通透采光（玻璃/水/冰）豁免，
            // 只对真正突兀的高对比（阈值 0.6）判冲突，避免"有玻璃采光的房子"被误扣配色分。
            for (int[] d : dirs) {
                BlockRecord nb = snap.get(BlockPos.asLong(x + d[0], y + d[1], z + d[2]));
                if (nb == null || nb.blockId() < 0) {
                    continue;
                }
                a.adjCount++;
                String nbPath = StructureScanner.blockPath(nb.blockId());
                if (isClear(path) || isClear(nbPath)) {
                    continue;
                }
                if (Math.abs(obs - BlockClasses.brightnessOf(nbPath)) > 0.6) {
                    a.conflictCount++;
                }
            }
            if (exposed > 0) {
                a.surfaceKeys.add(key);
            }
        }
        return a;
    }

    // 冲突/邻接计数
    static final class Agg2 {
    }

    /** 由 Agg 派生到 ScanResult（基础/形态/色彩/材质/内容/评分输入 + 对称/悬空/立面起伏）。 */
    /** 由 Agg 派生到 ScanResult（基础/形态/色彩/材质/内容/评分输入 + 对称/悬空/立面起伏）。 */
    static void apply(Agg a, ScanResult r, Map<Long, BlockRecord> snap, ServerLevel level) {
        r.blockCount = a.blockCount;
        r.width = a.maxX - a.minX + 1;
        r.depth = a.maxZ - a.minZ + 1;
        r.height = a.maxY - a.minY + 1;
        r.minY = a.minY;
        r.maxY = a.maxY;
        r.verticalLayers = a.ys.size();
        long volume = (long) r.width * r.depth * r.height;
        r.solidity = volume > 0 ? (double) a.blockCount / volume : 0;
        r.aspectHeight = Math.max(r.width, r.depth) > 0 ? (double) r.height / Math.max(r.width, r.depth) : 0;
        r.aspectLength = Math.min(r.width, r.depth) > 0 ? (double) Math.max(r.width, r.depth) / Math.min(r.width, r.depth) : 0;

        // 层面积数组（minY..maxY）
        int h = r.height;
        List<Integer> layerAreas = new ArrayList<>(h);
        for (int y = a.minY; y <= a.maxY; y++) {
            layerAreas.add(a.layerCount.getOrDefault(y, 0));
        }
        r.layerAreas = layerAreas;

        // 材质
        r.materialCount = a.matHist.size();
        List<Map.Entry<String, Integer>> mats = new ArrayList<>(a.matHist.entrySet());
        mats.sort((p1, p2) -> Integer.compare(p2.getValue(), p1.getValue()));
        r.dominantMaterials = new ArrayList<>();
        for (int i = 0; i < Math.min(2, mats.size()); i++) {
            r.dominantMaterials.add(mats.get(i).getKey());
        }
        int topCount = mats.isEmpty() ? 0 : mats.get(0).getValue();
        double topRatio = a.blockCount > 0 ? (double) topCount / a.blockCount : 0;
        r.materialComplexity = r.materialCount <= 3 ? "monochrome"
                : (r.materialCount >= 8 && topRatio <= 0.3) ? "rich" : "medium";

        // 色彩
        List<Map.Entry<String, Integer>> cols = new ArrayList<>(a.colHist.entrySet());
        cols.sort((p1, p2) -> Integer.compare(p2.getValue(), p1.getValue()));
        r.colorCount = cols.size();
        r.colorRichness = cols.size() <= 3 ? "monochrome" : (cols.size() >= 8 ? "rich" : "medium");
        r.dominantColor = cols.isEmpty() ? "" : cols.get(0).getKey();
        r.secondaryColor = cols.size() > 1 ? cols.get(1).getKey() : "";
        r.palette = new ArrayList<>();
        for (int i = 0; i < Math.min(5, cols.size()); i++) {
            r.palette.add(cols.get(i).getKey());
        }
        Set<String> warm = Set.of("红", "橙", "黄", "棕");
        int warmSum = 0;
        for (Map.Entry<String, Integer> e : a.colHist.entrySet()) {
            if (warm.contains(e.getKey())) {
                warmSum += e.getValue();
            }
        }
        int colorN = a.colHist.values().stream().mapToInt(Integer::intValue).sum();
        r.warmRatio = colorN > 0 ? (double) warmSum / colorN : 0;
        r.brightness = colorN > 0 ? a.sumB / colorN : 0;
        r.saturation = colorN > 0 ? a.sumS / colorN : 0;

        // 功能物件 / 水 / 红石
        r.contents = a.contentHist;
        r.waterCount = a.water;
        r.hasRedstone = a.redstone;
        r.nonFullCount = a.nonFull;
        // 自然绿植装饰（树叶/藤蔓/苔藓/草/花/杜鹃/竹）
        int nature = 0;
        int crop = 0, hay = 0;
        for (Map.Entry<String, Integer> e : a.matHist.entrySet()) {
            String p = e.getKey();
            if (p.contains("leaves") || p.contains("vine") || p.contains("moss")
                    || p.contains("grass") || p.contains("flower")
                    || p.contains("azalea") || p.contains("bamboo")) {
                nature += e.getValue();
            }
            // 作物（农田）/ 干草垛（牧场、田园）
            if (p.contains("wheat") || p.contains("carrots") || p.contains("potatoes")
                    || p.contains("beetroot") || p.contains("sweet_berry")
                    || p.contains("torchflower") || p.contains("pitcher_crop")
                    || p.contains("crop") || p.contains("nether_wart")) {
                crop += e.getValue();
            }
            if (p.contains("hay_block")) {
                hay += e.getValue();
            }
        }
        r.natureCount = nature;
        r.cropCount = crop;
        r.hayCount = hay;

        // 形态：暴露面 / 表面 / 质心
        r.exposedSurface = a.exposedSum;
        r.roughness = a.blockCount > 0 ? (double) a.exposedSum / a.blockCount : 0;
        r.surfaceRatio = a.blockCount > 0 ? (double) a.surfaceBlocksAll / a.blockCount : 0;
        double cx = (double) a.sumX / a.blockCount;
        double cy = (double) a.sumY / a.blockCount;
        double cz = (double) a.sumZ / a.blockCount;
        r.centroidDX = (cx - (a.minX + a.maxX) / 2.0) / Math.max(1, a.maxX - a.minX);
        r.centroidDY = (cy - (a.minY + a.maxY) / 2.0) / Math.max(1, a.maxY - a.minY);
        r.centroidDZ = (cz - (a.minZ + a.maxZ) / 2.0) / Math.max(1, a.maxZ - a.minZ);

        // 层面积方差 / 截面形状 / 剪影 / 突起
        double maxA = 0;
        for (int v : layerAreas) {
            maxA = Math.max(maxA, v);
        }
        double[] norm = new double[h];
        for (int i = 0; i < h; i++) {
            norm[i] = maxA > 0 ? layerAreas.get(i) / maxA : 0;
        }
        double sq = 0;
        for (int i = 0; i < h; i++) {
            sq += (layerAreas.get(i) - maxA / 2) * (layerAreas.get(i) - maxA / 2);
        }
        r.layerAreaVar = maxA > 0 ? sq / h / (maxA * maxA) : 0;

        Map<String, Integer> shapes = new HashMap<>();
        for (int y = a.minY; y <= a.maxY; y++) {
            int[] b = a.layerBounds.get(y);
            if (b == null) {
                continue;
            }
            int wl = b[1] - b[0] + 1, dl = b[3] - b[2] + 1;
            int cnt = a.layerCount.getOrDefault(y, 0);
            double fill = wl * dl > 0 ? (double) cnt / (wl * dl) : 0;
            double ratio = Math.max(wl, dl) / Math.max(1, Math.min(wl, dl));
            if (ratio < 1.3 && fill > 0.7) {
                shapes.merge("square", 1, Integer::sum);
            } else if (ratio >= 3) {
                shapes.merge("elongated", 1, Integer::sum);
            } else if (ratio >= 1.3) {
                shapes.merge("rect", 1, Integer::sum);
            } else {
                shapes.merge("irregular", 1, Integer::sum);
            }
        }
        r.sectionShape = shapes.entrySet().stream()
                .max((p1, p2) -> Integer.compare(p1.getValue(), p2.getValue()))
                .map(Map.Entry::getKey).orElse("irregular");
        if (h >= 3) {
            double top = norm[h - 1], mid = norm[h / 2], bot = norm[0];
            double mnD = Double.MAX_VALUE, mxD = -1;
            for (double v : norm) {
                mnD = Math.min(mnD, v);
                mxD = Math.max(mxD, v);
            }
            if (mxD - mnD < 0.15) {
                r.silhouette = "cylinder";
            } else if (mid > bot * 1.2 && mid > top * 1.2) {
                r.silhouette = "gourd";
            } else if (bot > top * 1.5 && mid > top) {
                r.silhouette = "funnel";
            } else if (top > bot * 1.5 && mid > bot) {
                r.silhouette = "trumpet";
            } else {
                r.silhouette = "irregular";
            }
        } else {
            r.silhouette = "flat";
        }
        int prot = 0;
        for (int i = 1; i < h; i++) {
            int pa = layerAreas.get(i - 1), ca = layerAreas.get(i);
            if (pa > 0 && (ca < pa * 0.5 || ca > pa * 2)) {
                prot++;
            }
        }
        r.protrusions = prot;

        // 层主材质 / 分层切换
        r.topMaterial = dominantLayerMat(a.layerMat.get(a.maxY));
        r.baseMaterial = dominantLayerMat(a.layerMat.get(a.minY));
        String prev = null;
        int changes = 0;
        for (int y = a.minY; y <= a.maxY; y++) {
            String cur = dominantLayerMat(a.layerMat.get(y));
            if (!cur.isEmpty()) {
                if (prev != null && !prev.equals(cur)) {
                    changes++;
                }
                prev = cur;
            }
        }
        r.materialLayerChanges = changes;

        // 屋顶
        r.roofType = roofType(a, layerAreas, r.width, r.depth);

        // 对称（镜）
        int mirrorX = 0, mirrorZ = 0;
        for (long key : snap.keySet()) {
            int x = BlockPos.getX(key), y = BlockPos.getY(key), z = BlockPos.getZ(key);
            if (snap.containsKey(BlockPos.asLong(a.minX + a.maxX - x, y, z))) {
                mirrorX++;
            }
            if (snap.containsKey(BlockPos.asLong(x, y, a.minZ + a.maxZ - z))) {
                mirrorZ++;
            }
        }
        r.symmetryX = a.blockCount > 0 ? (double) mirrorX / a.blockCount : 0;
        r.symmetryZ = a.blockCount > 0 ? (double) mirrorZ / a.blockCount : 0;

        // 悬空
        double hang = 0;
        int bottom = 0, hangC = 0;
        for (long key : snap.keySet()) {
            int x = BlockPos.getX(key), y = BlockPos.getY(key), z = BlockPos.getZ(key);
            if (y != a.minY) {
                continue;
            }
            bottom++;
            if (!snap.containsKey(BlockPos.asLong(x, y - 1, z))
                    && level != null && level.getBlockState(new BlockPos(x, y - 1, z)).isAir()) {
                hangC++;
            }
        }
        r.hangRatio = bottom > 0 ? (double) hangC / bottom : 0;

        // 立面起伏（外表面距质心标准差）
        double sum = 0, sumSq = 0;
        int surfaceN = 0;
        for (long key : a.surfaceKeys) {
            int x = BlockPos.getX(key), y = BlockPos.getY(key), z = BlockPos.getZ(key);
            double dx = x - cx, dy = y - cy, dz = z - cz;
            double dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
            sum += dist;
            sumSq += dist * dist;
            surfaceN++;
        }
        double mean = surfaceN > 0 ? sum / surfaceN : 0;
        double variance = surfaceN > 0 ? Math.max(0, sumSq / surfaceN - mean * mean) : 0;
        r.facadeStd = surfaceN > 0 ? Math.sqrt(variance) : 0;
        r.conflictPairs = a.conflictCount;
        r.adjacentPairs = a.adjCount;
    }

    /** 通透/采光类方块：不参与颜色冲突判定（玻璃/水/冰/灯）。 */
    private static boolean isClear(String p) {
        return p.contains("glass") || p.contains("water") || p.contains("ice")
                || p.contains("flowing") || p.contains("lantern")
                || p.contains("torch") || p.contains("sea_lantern");
    }

    /** 装饰/功能/绿植类方块：不参与结构材质分层判定（玻璃/楼梯/门/灯/花/绿植/地毯）。 */
    private static boolean isDecor(String p) {
        if (BlockClasses.contentType(p) != null) {
            return true;
        }
        return p.contains("leaves") || p.contains("vine") || p.contains("moss")
                || p.contains("flower") || p.contains("azalea") || p.contains("bamboo")
                || p.contains("carpet");
    }

    private static String dominantLayerMat(Map<String, Integer> hist) {
        if (hist == null || hist.isEmpty()) {
            return "";
        }
        return hist.entrySet().stream()
                .max((p1, p2) -> Integer.compare(p1.getValue(), p2.getValue()))
                .map(Map.Entry::getKey).orElse("");
    }

    private static String roofType(Agg a, List<Integer> layerAreas, int w, int d) {
        int h = layerAreas.size();
        int ceil = -1;
        for (int i = h - 1; i >= 0; i--) {
            if (layerAreas.get(i) >= w * d * 0.5) {
                ceil = i;
                break;
            }
        }
        if (ceil < 0) {
            return "open";
        }
        if (ceil == h - 1) {
            return "flat";
        }
        int first = layerAreas.get(ceil + 1), last = layerAreas.get(h - 1);
        int roofY0 = a.minY + ceil + 1;
        int roofBlocks = 0;
        for (int i = ceil + 1; i < h; i++) {
            roofBlocks += layerAreas.get(i);
        }
        int stairs = 0;
        for (int y = roofY0; y <= a.maxY; y++) {
            stairs += a.layerFurn.getOrDefault(y, 0);
        }
        double stairRatio = roofBlocks > 0 ? (double) stairs / roofBlocks : 0;
        if (last < first * 0.5) {
            return stairRatio > 0.2 ? "pointed" : "flat";
        }
        return stairRatio > 0.3 ? "gabled" : "flat";
    }

    private static final int[][] DIRS = {
            {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}
    };
}

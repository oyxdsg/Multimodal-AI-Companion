package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 房间/空腔扫描器：RLE 空气段 + Union-Find 连通分量（替代洪水填充）。
 * 从 StructureScanner 拆出，降低单类复杂度；纯静态，同包共享工具。
 */
final class RoomSegmenter {
    private RoomSegmenter() {}

    /** 一个水平空气段（RLE：同层同行连续空气格）。 */
    private record AirSeg(int y, int z, int xStart, int xEnd, boolean touchBoundary) {}

    /**
     * 房间判定（RLE 游程段 + 并查集连通分量）：
     * 1. 每层每行把连续空气编码为段（AirSeg），O(空气格)；
     * 2. 段间用 Union-Find 合并（同层 z 邻接 + 跨层 y 邻接，区间重叠即连通）；
     * 3. 接触 AABB 边界的分量 = 外部；其余 = 内部空腔；
     * 4. 空腔边界有门 → 房间；无门 → 空腔。
     * 段级合并仅 O(段数)，比逐体素 BFS 常数小、免 visited 大数组。
     */
    static void computeRooms(Map<Long, BlockRecord> snap, ScanResult r,
                             int minX, int maxX, int minY, int maxY,
                             int minZ, int maxZ, int w, int d) {
        int h = r.height;
        long volume = (long) w * d * h;
        if (volume > 1_000_000L || w < 3 || d < 3 || h < 3) {
            return;
        }
        List<AirSeg> segs = new ArrayList<>();
        for (int y = minY; y <= maxY; y++) {
            for (int z = minZ; z <= maxZ; z++) {
                int x = minX;
                while (x <= maxX) {
                    if (snap.containsKey(BlockPos.asLong(x, y, z))) {
                        x++;
                        continue;
                    }
                    int start = x;
                    while (x <= maxX && !snap.containsKey(BlockPos.asLong(x, y, z))) {
                        x++;
                    }
                    int end = x - 1;
                    boolean touch = y == minY || y == maxY || z == minZ || z == maxZ
                            || start == minX || end == maxX;
                    segs.add(new AirSeg(y, z, start, end, touch));
                }
            }
        }
        int n = segs.size();
        if (n == 0) {
            return;
        }
        int[] parent = new int[n];
        for (int i = 0; i < n; i++) {
            parent[i] = i;
        }
        Map<Long, List<Integer>> byRow = new HashMap<>();
        for (int i = 0; i < n; i++) {
            AirSeg s = segs.get(i);
            byRow.computeIfAbsent((long) (s.y - minY) << 16 | (s.z - minZ),
                    k -> new ArrayList<>()).add(i);
        }
        for (Map.Entry<Long, List<Integer>> e : byRow.entrySet()) {
            long key = e.getKey();
            unionRow(byRow, key, key + 1, segs, parent);          // 同层 z+1
            unionRow(byRow, key, key + (1L << 16), segs, parent); // 跨层 y+1
        }
        boolean[] touched = new boolean[n];
        boolean[] hasDoor = new boolean[n];
        long[] vol = new long[n];
        int[] minAirY = new int[n];
        int[] maxAirY = new int[n];
        for (int i = 0; i < n; i++) {
            AirSeg s = segs.get(i);
            int root = find(parent, i);
            if (s.touchBoundary()) {
                touched[root] = true;
            }
            if (segmentHasDoor(s, snap)) {
                hasDoor[root] = true;
            }
            vol[root] += s.xEnd() - s.xStart() + 1;
            minAirY[root] = Math.min(minAirY[root] == 0 ? Integer.MAX_VALUE : minAirY[root], s.y());
            maxAirY[root] = Math.max(maxAirY[root], s.y());
        }
        r.rooms = new ArrayList<>();
        int cavities = 0;
        for (int i = 0; i < n; i++) {
            if (find(parent, i) != i) {
                continue;
            }
            if (touched[i]) {
                continue;   // 外部空气
            }
            if (hasDoor[i]) {
                r.rooms.add(new ScanResult.Room((int) vol[i], minAirY[i], maxAirY[i], ""));
            } else {
                cavities++;
            }
        }
        r.cavities = cavities;
    }

    /** 合并两行中 x 区间重叠的段（双指针，行内按 xStart 有序）。 */
    private static void unionRow(Map<Long, List<Integer>> byRow, long k1, long k2,
                                 List<AirSeg> segs, int[] parent) {
        List<Integer> l1 = byRow.get(k1), l2 = byRow.get(k2);
        if (l1 == null || l2 == null) {
            return;
        }
        int i = 0, j = 0;
        while (i < l1.size() && j < l2.size()) {
            AirSeg a = segs.get(l1.get(i)), b = segs.get(l2.get(j));
            if (a.xEnd() < b.xStart()) {
                i++;
            } else if (b.xEnd() < a.xStart()) {
                j++;
            } else {
                union(parent, l1.get(i), l2.get(j));
                if (a.xEnd() <= b.xEnd()) {
                    i++;
                } else {
                    j++;
                }
            }
        }
    }

    private static int find(int[] p, int a) {
        while (p[a] != a) {
            p[a] = p[p[a]];
            a = p[a];
        }
        return a;
    }

    private static void union(int[] p, int a, int b) {
        int ra = find(p, a), rb = find(p, b);
        if (ra != rb) {
            p[ra] = rb;
        }
    }

    /** 段边界 solid 邻居是否存在门。 */
    private static boolean segmentHasDoor(AirSeg s, Map<Long, BlockRecord> snap) {
        if (isDoorAt(snap, s.xStart() - 1, s.y(), s.z())
                || isDoorAt(snap, s.xEnd() + 1, s.y(), s.z())) {
            return true;
        }
        for (int x = s.xStart(); x <= s.xEnd(); x++) {
            if (isDoorAt(snap, x, s.y() - 1, s.z())
                    || isDoorAt(snap, x, s.y() + 1, s.z())
                    || isDoorAt(snap, x, s.y(), s.z() - 1)
                    || isDoorAt(snap, x, s.y(), s.z() + 1)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isDoorAt(Map<Long, BlockRecord> snap, int x, int y, int z) {
        BlockRecord rec = snap.get(BlockPos.asLong(x, y, z));
        return rec != null && rec.blockId() >= 0
                && "door".equals(BlockClasses.contentType(StructureScanner.blockPath(rec.blockId())));
    }
}

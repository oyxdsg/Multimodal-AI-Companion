package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 墙段扫描器：从快照提取竖状连续同材质面片并聚类成"墙面"（标注内外）。
 * 从 StructureScanner 拆出，降低单类复杂度；纯静态，使用 StructureScanner.blockPath 与
 * BlockClasses.contentType（同包共享）。
 */
final class WallSegmenter {
    /** 水平 4 邻域方向（墙片段聚类用）。 */
    private static final int[][] HORIZ_DIRS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
    /** 墙片段最小高度（连续堆叠格数）。 */
    private static final int WALL_MIN_H = 2;

    private WallSegmenter() {}

    /** 一个垂直墙片段（竖状连续同材质堆叠）。 */
    private record VSeg(int x, int z, int yStart, int yEnd, int blockId) {}

    /** 提取垂直墙片段：每个 (x,z) 列沿 Y 找连续同 blockId 的段。 */
    private static List<VSeg> extractVerticalSegments(Map<Long, BlockRecord> snap,
                                                      int minY, int maxY) {
        Map<Long, List<VSeg>> byColumn = new HashMap<>();
        for (long key : snap.keySet()) {
            int x = BlockPos.getX(key), z = BlockPos.getZ(key);
            byColumn.computeIfAbsent(BlockPos.asLong(x, 0, z), k -> new ArrayList<>());
        }
        List<VSeg> all = new ArrayList<>();
        for (Map.Entry<Long, List<VSeg>> e : byColumn.entrySet()) {
            int x = BlockPos.getX(e.getKey()), z = BlockPos.getZ(e.getKey());
            int y = minY;
            while (y <= maxY) {
                BlockRecord rec = snap.get(BlockPos.asLong(x, y, z));
                if (rec == null || rec.blockId() < 0) {
                    y++;
                    continue;
                }
                // 功能物件（床/箱/门/家具等）不是建筑墙，不参与墙检测
                if (BlockClasses.contentType(StructureScanner.blockPath(rec.blockId())) != null) {
                    y++;
                    continue;
                }
                int blockId = rec.blockId();
                int start = y;
                while (y <= maxY) {
                    BlockRecord r2 = snap.get(BlockPos.asLong(x, y, z));
                    if (r2 == null || r2.blockId() != blockId) {
                        break;
                    }
                    y++;
                }
                int end = y - 1;
                if (end - start + 1 >= WALL_MIN_H) {
                    all.add(new VSeg(x, z, start, end, blockId));
                }
            }
        }
        return all;
    }

    /** 墙片段聚类成面（相邻列 + 同材质 + 高度接近），并标注内外。 */
    static void computeWalls(Map<Long, BlockRecord> snap, ScanResult r,
                             int minX, int maxX, int minY, int maxY,
                             int minZ, int maxZ) {
        List<VSeg> segs = extractVerticalSegments(snap, minY, maxY);
        if (segs.isEmpty()) {
            return;
        }
        // 列索引：(x,z) → 该列所有片段索引，BFS 只查邻域列，避免 O(S²) 全对全比较
        Map<Long, List<Integer>> byColumn = new HashMap<>();
        for (int i = 0; i < segs.size(); i++) {
            VSeg s = segs.get(i);
            byColumn.computeIfAbsent(BlockPos.asLong(s.x(), 0, s.z()),
                    k -> new ArrayList<>()).add(i);
        }
        boolean[] visited = new boolean[segs.size()];
        for (int i = 0; i < segs.size(); i++) {
            if (visited[i]) {
                continue;
            }
            Deque<Integer> queue = new ArrayDeque<>();
            queue.add(i);
            visited[i] = true;
            List<VSeg> cluster = new ArrayList<>();
            while (!queue.isEmpty()) {
                int cur = queue.poll();
                VSeg s = segs.get(cur);
                cluster.add(s);
                for (int[] d : HORIZ_DIRS) {
                    List<Integer> nbrs = byColumn.get(
                            BlockPos.asLong(s.x() + d[0], 0, s.z() + d[1]));
                    if (nbrs == null) {
                        continue;
                    }
                    for (int j : nbrs) {
                        if (!visited[j] && sameMaterialAdjacent(s, segs.get(j))) {
                            visited[j] = true;
                            queue.add(j);
                        }
                    }
                }
            }
            if (cluster.size() < 2) {
                continue;   // 单列 → 柱，不是墙
            }
            int cMinX = Integer.MAX_VALUE, cMaxX = Integer.MIN_VALUE;
            int cMinZ = Integer.MAX_VALUE, cMaxZ = Integer.MIN_VALUE;
            int maxH = 0;
            int edgeCols = 0;
            for (VSeg s : cluster) {
                cMinX = Math.min(cMinX, s.x);
                cMaxX = Math.max(cMaxX, s.x);
                cMinZ = Math.min(cMinZ, s.z);
                cMaxZ = Math.max(cMaxZ, s.z);
                maxH = Math.max(maxH, s.yEnd - s.yStart + 1);
                if (s.x == minX || s.x == maxX || s.z == minZ || s.z == maxZ) {
                    edgeCols++;
                }
            }
            int lenX = cMaxX - cMinX + 1;
            int lenZ = cMaxZ - cMinZ + 1;
            int length = Math.max(lenX, lenZ);
            String dir = lenZ > lenX ? "z" : "x";
            String type = edgeCols >= cluster.size() / 2.0 ? "outer" : "inner";
            String mat = StructureScanner.blockPath(cluster.get(0).blockId());
            r.walls.add(new ScanResult.Wall(mat, maxH, length, dir, type));
            if (type.equals("outer")) {
                r.wallOuter++;
            } else {
                r.wallInner++;
            }
        }
    }

    /** 两墙片段是否同一面：相邻列（曼哈顿≤1）+ 同材质 + 高度重叠或间隔≤1。 */
    private static boolean sameMaterialAdjacent(VSeg a, VSeg b) {
        if (a.blockId() != b.blockId()) {
            return false;
        }
        if (Math.abs(a.x() - b.x()) + Math.abs(a.z() - b.z()) != 1) {
            return false;
        }
        return Math.max(a.yStart(), b.yStart()) <= Math.min(a.yEnd(), b.yEnd()) + 1;
    }
}

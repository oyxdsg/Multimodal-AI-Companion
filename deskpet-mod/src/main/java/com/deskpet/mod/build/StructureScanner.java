package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Block;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 三维扫描器：从 BlockStore 快照提取结构元 + 美学元。
 *
 * 核心：连续同材质面片提取（按轴参数化）
 * - 墙：竖状（Y 向连续同材质堆叠），相邻列聚类成面
 * - 地板/天花板：水平（X/Z 向连续同材质），同层聚类成水平面
 * 房间 = 墙 + 地板 + 天花板围合空间，边界有门 → 房间，无门 → 空腔。
 */
public class StructureScanner {
    /** 6 邻域方向。 */
    private static final int[][] DIRS = {
            {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}
    };
    /** 水平 4 邻域方向（墙片段聚类用）。 */
    private static final int[][] HORIZ_DIRS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
    /** 水平面片 / 覆盖层判定的最小覆盖率。 */
    private static final double COVER_LAYER_RATIO = 0.35;
    /** 墙片段最小高度（连续堆叠格数）。 */
    private static final int WALL_MIN_H = 2;

    private static final Set<String> REDSTONE_PARTS = Set.of(
            "redstone_wire", "redstone_torch", "redstone_lamp", "redstone_block",
            "repeater", "comparator", "piston", "sticky_piston",
            "dispenser", "dropper", "observer", "hopper",
            "lever", "tripwire_hook", "target");



    public ScanResult scan(Map<Long, BlockRecord> snap, ServerLevel level) {
        ScanResult r = new ScanResult();
        if (snap.isEmpty()) {
            return r;
        }

        // ---- 单遍聚合：一次遍历收集所有统计型指标，并派生到 r ----
        ScanAggregation.Agg agg = ScanAggregation.collect(snap);
        ScanAggregation.apply(agg, r, snap, level);

        int w = r.width, d = r.depth;
        int minX = agg.minX, maxX = agg.maxX, minY = agg.minY, maxY = agg.maxY;
        int minZ = agg.minZ, maxZ = agg.maxZ;

        // ---- 连通集群 ----
        r.clusterCount = countClusters(snap);

        // ---- 密闭性（MC isSolid 读 world）----
        computeEnclosure(snap, level, r, minX, maxX, minY, maxY, minZ, maxZ, w, d);

        // ---- 柱 / 梁 ----
        computePillarsBeams(snap, r, minX, maxX, minY, maxY, minZ, maxZ);

        // ---- 墙检测（连续同材质竖状面片）----
        r.walls = new ArrayList<>();
        WallSegmenter.computeWalls(snap, r, minX, maxX, minY, maxY, minZ, maxZ);

        // ---- 房间/空腔（墙+地板+天花板围合 + 门判定）----
        r.rooms = new ArrayList<>();
        RoomSegmenter.computeRooms(snap, r, minX, maxX, minY, maxY, minZ, maxZ, w, d);

        // ---- 地表高度 ----
        r.groundY = computeGroundY(level, (minX + maxX) / 2, (minZ + maxZ) / 2, maxY);

        return r;
    }

    // ================= 连通集群 =================

    /** blockId → 注册表 path（缓存，避免每块每段重复 byId+getKey）。 */
    private static final Map<Integer, String> PATH_CACHE = new java.util.concurrent.ConcurrentHashMap<>();

    static String blockPath(int blockId) {
        String p = PATH_CACHE.get(blockId);
        if (p == null) {
            Block b = BuiltInRegistries.BLOCK.byId(blockId);
            p = BuiltInRegistries.BLOCK.getKey(b).getPath();
            PATH_CACHE.put(blockId, p);
        }
        return p;
    }

    private static int countClusters(Map<Long, BlockRecord> snap) {
        Set<Long> visited = new HashSet<>();
        int clusters = 0;
        for (Long key : snap.keySet()) {
            if (!visited.add(key)) {
                continue;
            }
            clusters++;
            Deque<Long> queue = new ArrayDeque<>();
            queue.add(key);
            while (!queue.isEmpty()) {
                long cur = queue.poll();
                int x = BlockPos.getX(cur), y = BlockPos.getY(cur), z = BlockPos.getZ(cur);
                for (int[] dir : DIRS) {
                    long nk = BlockPos.asLong(x + dir[0], y + dir[1], z + dir[2]);
                    if (snap.containsKey(nk) && visited.add(nk)) {
                        queue.add(nk);
                    }
                }
            }
        }
        return clusters;
    }

    // ================= 密闭性 =================

    private static void computeEnclosure(Map<Long, BlockRecord> snap, ServerLevel level,
                                         ScanResult r,
                                         int minX, int maxX, int minY, int maxY,
                                         int minZ, int maxZ, int w, int d) {
        int floorCells = w * d;
        int floorCount, roofCount, wallCount;
        if (level != null) {
            floorCount = roofCount = 0;
            for (int x = minX; x <= maxX; x++) {
                for (int z = minZ; z <= maxZ; z++) {
                    if (isSolid(level, x, minY, z)) {
                        floorCount++;
                    }
                    if (isSolid(level, x, maxY, z)) {
                        roofCount++;
                    }
                }
            }
            wallCount = 0;
            for (int x = minX; x <= maxX; x++) {
                for (int y = minY; y <= maxY; y++) {
                    if (isSolid(level, x, y, minZ)) {
                        wallCount++;
                    }
                    if (isSolid(level, x, y, maxZ)) {
                        wallCount++;
                    }
                }
            }
            for (int z = minZ + 1; z <= maxZ - 1; z++) {
                for (int y = minY; y <= maxY; y++) {
                    if (isSolid(level, minX, y, z)) {
                        wallCount++;
                    }
                    if (isSolid(level, maxX, y, z)) {
                        wallCount++;
                    }
                }
            }
        } else {
            floorCount = roofCount = wallCount = 0;
            for (long key : snap.keySet()) {
                int x = BlockPos.getX(key), y = BlockPos.getY(key), z = BlockPos.getZ(key);
                if (y == minY) {
                    floorCount++;
                }
                if (y == maxY) {
                    roofCount++;
                }
                if (x == minX || x == maxX || z == minZ || z == maxZ) {
                    wallCount++;
                }
            }
        }
        r.floorCoverage = floorCells > 0 ? (double) floorCount / floorCells : 0;
        r.roofCoverage = floorCells > 0 ? (double) roofCount / floorCells : 0;
        int totalWallCells = Math.max(1, 2 * w * r.height + 2 * d * r.height - 4 * r.height);
        r.wallCoverage = totalWallCells > 0 ? (double) wallCount / totalWallCells : 0;
        r.enclosureLevel = classifyEnclosure(r.wallCoverage, r.roofCoverage, r.floorCoverage);
    }

    private static String classifyEnclosure(double wall, double roof, double floor) {
        if (wall > 0.9 && roof > 0.9 && floor > 0.9) {
            return "完全密闭";
        }
        if (wall >= 0.6 && roof >= 0.3 && floor > 0.8) {
            return "部分密闭";
        }
        if (wall >= 0.3 && roof < 0.4 && floor > 0.5) {
            return "半开放";
        }
        if (wall < 0.3 && roof < 0.2 && floor > 0.4) {
            return "开放平台";
        }
        if (wall < 0.1 && roof < 0.1) {
            return "完全露天";
        }
        return "半开放";
    }

    private static boolean isSolid(ServerLevel level, int x, int y, int z) {
        return level.getBlockState(new BlockPos(x, y, z)).isSolid();
    }

    // ================= 柱梁 / 对称 / 悬空 =================

    private static void computePillarsBeams(Map<Long, BlockRecord> snap, ScanResult r,
                                            int minX, int maxX, int minY, int maxY,
                                            int minZ, int maxZ) {
        Set<Long> xzSet = new HashSet<>();
        for (long key : snap.keySet()) {
            xzSet.add(BlockPos.asLong(BlockPos.getX(key), 0, BlockPos.getZ(key)));
        }
        for (Long xz : xzSet) {
            int x = BlockPos.getX(xz), z = BlockPos.getZ(xz);
            int run = 0, best = 0;
            for (int y = minY; y <= maxY; y++) {
                if (snap.containsKey(BlockPos.asLong(x, y, z))) {
                    best = Math.max(best, ++run);
                } else {
                    run = 0;
                }
            }
            if (best >= 4) {
                r.pillarCount++;
            }
        }
        Set<Long> yzSet = new HashSet<>();
        for (long key : snap.keySet()) {
            yzSet.add(BlockPos.asLong(0, BlockPos.getY(key), BlockPos.getZ(key)));
        }
        for (Long yz : yzSet) {
            int y = BlockPos.getY(yz), z = BlockPos.getZ(yz);
            int run = 0, best = 0;
            for (int x = minX; x <= maxX; x++) {
                if (snap.containsKey(BlockPos.asLong(x, y, z))) {
                    best = Math.max(best, ++run);
                } else {
                    run = 0;
                }
            }
            if (best >= 5) {
                r.beamCount++;
            }
        }
        Set<Long> xySet = new HashSet<>();
        for (long key : snap.keySet()) {
            xySet.add(BlockPos.asLong(BlockPos.getX(key), BlockPos.getY(key), 0));
        }
        for (Long xy : xySet) {
            int x = BlockPos.getX(xy), y = BlockPos.getY(xy);
            int run = 0, best = 0;
            for (int z = minZ; z <= maxZ; z++) {
                if (snap.containsKey(BlockPos.asLong(x, y, z))) {
                    best = Math.max(best, ++run);
                } else {
                    run = 0;
                }
            }
            if (best >= 5) {
                r.beamCount++;
            }
        }
    }


    private static int computeGroundY(ServerLevel level, int cx, int cz, int maxY) {
        if (level == null) {
            return maxY;
        }
        for (int y = maxY; y >= -64; y--) {
            if (!level.getBlockState(new BlockPos(cx, y, cz)).isAir()) {
                return y;
            }
        }
        return maxY;
    }
}
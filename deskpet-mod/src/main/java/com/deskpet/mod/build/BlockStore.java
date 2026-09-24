package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 记忆模块：稀疏哈希表追踪玩家放置/破坏的方块。
 *
 * 主线程只做 O(1) 写入/查询（方案：单次 &lt;100ns 目标）；
 * 扫描/分析在后台线程通过 {@link #snapshot()} 取快照，避免遍历时并发修改。
 */
public class BlockStore {
    /** 压缩坐标 (BlockPos.asLong) -> 方块记录。 */
    private final Map<Long, BlockRecord> store = new HashMap<>();
    private int minX = Integer.MAX_VALUE, maxX = Integer.MIN_VALUE;
    private int minY = Integer.MAX_VALUE, maxY = Integer.MIN_VALUE;
    private int minZ = Integer.MAX_VALUE, maxZ = Integer.MIN_VALUE;

    public void put(int x, int y, int z, BlockRecord rec) {
        store.put(BlockPos.asLong(x, y, z), rec);
        expandTo(x, y, z);
    }

    public void put(long key, BlockRecord rec) {
        store.put(key, rec);
        expandTo(BlockPos.getX(key), BlockPos.getY(key), BlockPos.getZ(key));
    }

    public boolean contains(int x, int y, int z) {
        return store.containsKey(BlockPos.asLong(x, y, z));
    }

    public boolean contains(long key) {
        return store.containsKey(key);
    }

    public BlockRecord get(int x, int y, int z) {
        return store.get(BlockPos.asLong(x, y, z));
    }

    /** 破坏玩家方块：仅删除玩家放置的（自然方块破坏不做反应，由调用方判断）。 */
    public BlockRecord remove(int x, int y, int z) {
        return store.remove(BlockPos.asLong(x, y, z));
    }

    public int size() {
        return store.size();
    }

    public boolean isEmpty() {
        return store.isEmpty();
    }

    public void clear() {
        store.clear();
        minX = Integer.MAX_VALUE;
        maxX = Integer.MIN_VALUE;
        minY = Integer.MAX_VALUE;
        maxY = Integer.MIN_VALUE;
        minZ = Integer.MAX_VALUE;
        maxZ = Integer.MIN_VALUE;
    }

    /** 点是否在 AABB 膨胀 margin 格内（空间归属判断）。 */
    public boolean nearAabb(int x, int y, int z, int margin) {
        if (isEmpty()) {
            return false;
        }
        return x >= minX - margin && x <= maxX + margin
                && y >= minY - margin && y <= maxY + margin
                && z >= minZ - margin && z <= maxZ + margin;
    }

    /** 点是否在 AABB 内。 */
    public boolean insideAabb(int x, int y, int z) {
        if (isEmpty()) {
            return false;
        }
        return x >= minX && x <= maxX && y >= minY && y <= maxY && z >= minZ && z <= maxZ;
    }

    private void expandTo(int x, int y, int z) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
        minZ = Math.min(minZ, z);
        maxZ = Math.max(maxZ, z);
    }

    /** 快照副本（后台分析线程使用，避免 ConcurrentModificationException）。 */
    public Map<Long, BlockRecord> snapshot() {
        return new HashMap<>(store);
    }

    /** 全部坐标 key 快照。 */
    public List<Long> keys() {
        return new ArrayList<>(store.keySet());
    }
}
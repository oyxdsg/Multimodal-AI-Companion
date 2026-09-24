package com.deskpet.mod.build;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.player.Player;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/**
 * 建筑模式生命周期状态机（MVP：IDLE / ACTIVE 两态，休眠由 Analyzer 驱动）。
 *
 * - IDLE：行为元监控——记录玩家放置的方块，每次放置后检查该方块所在的
 *   连通集群（6 邻域面接触）大小，达到 ≥4 格即触发 ACTIVE（如 2x2 地板）。
 * - ACTIVE：放置/破坏写入 BlockStore（O(1)），记录锚点与世界分区键，
 *   由 Analyzer 按 20s/60s 阈值触发窗口输出与休眠。
 */
public class BuildingTracker {
    public enum State { IDLE, ACTIVE }

    /** 触发阈值：连通集群方块数 ≥ 该值激活。 */
    private static final int TRIGGER_CLUSTER = 4;
    /** IDLE 临时积累的清理间隔：超过该时长未放置则清空，避免零星方块跨时误触发。 */
    private static final long IDLE_RESET_MS = 60_000;

    private static final int[][] DIRS = {
            {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}
    };

    private State state = State.IDLE;
    private final BlockStore store = new BlockStore();
    private UUID projectId;          // 当前项目唯一标识（激活时生成）
    private UUID playerUuid;         // 项目级玩家（不随方块存储）
    private long[] anchor;           // 触发时玩家脚部坐标 [x, y, z]
    private String worldPartitionKey = "";
    private long lastActivityMs = 0; // 最近一次玩家操作时间
    private ServerLevel levelRef;    // 当前项目所在世界（悬空检测用）

    /** 放置方块（主线程调用）。 */
    public void onBlockPlace(ServerLevel level, int x, int y, int z,
                             int blockId, Player player, long nowMs) {
        long key = BlockPos.asLong(x, y, z);
        if (state == State.IDLE) {
            // 距上次放置过久则清空临时积累，避免零星方块跨长时间误触发
            if (!store.isEmpty() && nowMs - lastActivityMs > IDLE_RESET_MS) {
                store.clear();
            }
            store.put(key, new BlockRecord(blockId, nowMs, true));
            lastActivityMs = nowMs;
            // 该方块所在连通集群（面接触）≥4 格 → 激活（如 2x2 地板）
            if (clusterSize(key) >= TRIGGER_CLUSTER) {
                activate(level, x, y, z, player, nowMs);
            }
            return;
        }
        // ACTIVE：O(1) 写入
        store.put(key, new BlockRecord(blockId, nowMs, true));
        lastActivityMs = nowMs;
    }

    /** 破坏方块（主线程调用）：只对玩家放置的方块有反应。 */
    public void onBlockBreak(ServerLevel level, int x, int y, int z, long nowMs) {
        if (store.remove(x, y, z) != null) {
            lastActivityMs = nowMs;
        }
    }

    /** 激活建筑项目：设置锚点 + 世界分区键（store 已含触发阶段的方块）。 */
    private void activate(ServerLevel level, int x, int y, int z, Player player, long nowMs) {
        state = State.ACTIVE;
        projectId = UUID.randomUUID();
        playerUuid = player != null ? player.getUUID() : null;
        anchor = new long[]{player.getBlockX(), player.getBlockY(), player.getBlockZ()};
        worldPartitionKey = level.getSeed() + "_" + level.dimension().identifier();
        levelRef = level;
        lastActivityMs = nowMs;
    }

    /** 该方块所在连通集群的方块数（6 邻域面接触 BFS）。 */
    private int clusterSize(long start) {
        Set<Long> visited = new HashSet<>();
        Deque<Long> queue = new ArrayDeque<>();
        visited.add(start);
        queue.add(start);
        while (!queue.isEmpty()) {
            long cur = queue.poll();
            int x = BlockPos.getX(cur), y = BlockPos.getY(cur), z = BlockPos.getZ(cur);
            for (int[] d : DIRS) {
                long nk = BlockPos.asLong(x + d[0], y + d[1], z + d[2]);
                if (store.contains(nk) && visited.add(nk)) {
                    queue.add(nk);
                }
            }
        }
        return visited.size();
    }

    /** 该位置是否归属当前项目：在项目 AABB 膨胀 4 格内即视为同一建筑
     * （容忍门廊/塔身等 1~4 格空隙，避免误切；真正离开才开新项目）。 */
    public boolean connectedToCurrent(int x, int y, int z) {
        if (store.isEmpty()) {
            return true;
        }
        return store.nearAabb(x, y, z, 4);
    }

    /** 返回当前空闲时长（毫秒）；非 ACTIVE 返回 -1。 */
    public long idleMillis(long nowMs) {
        return state == State.ACTIVE ? nowMs - lastActivityMs : -1;
    }

    public boolean isActive() {
        return state == State.ACTIVE;
    }

    /** 休眠/退出：释放内存，回到 IDLE（仅当前会话，不做磁盘持久化）。 */
    public void reset() {
        store.clear();
        state = State.IDLE;
        projectId = null;
        playerUuid = null;
        anchor = null;
        worldPartitionKey = "";
        levelRef = null;
    }

    public BlockStore store() {
        return store;
    }

    public UUID projectId() {
        return projectId;
    }

    public long[] anchor() {
        return anchor;
    }

    public String worldPartitionKey() {
        return worldPartitionKey;
    }

    public ServerLevel level() {
        return levelRef;
    }
}
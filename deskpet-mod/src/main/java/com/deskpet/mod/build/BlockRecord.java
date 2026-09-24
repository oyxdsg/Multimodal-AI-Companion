package com.deskpet.mod.build;

/**
 * 单个方块记录（记忆模块的最小单元）。
 * 玩家身份由项目级 BuildingTracker 持有，不随每个方块存 UUID（省内存）。
 */
public record BlockRecord(
        int blockId,          // 方块注册表 ID（实体放置为负数虚拟 id）
        long placedTick,      // 放置时的游戏时间戳（毫秒）
        boolean placedByPlayer  // 是否玩家放置（MVP 恒为 true，预留区分自然地形）
) {}
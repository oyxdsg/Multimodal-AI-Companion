package com.deskpet.mod;

/** 一次玩家操作。 */
public record PlayerAction(
        String player,   // 玩家名
        ActionType type, // 操作类型
        String target,   // 方块/物品/实体名（可为 null）
        String detail,   // 死亡原因 / 进度名 / 聊天文本 / 移动距离（可为 null）
        int[] pos,       // [x, y, z]（可为 null）
        long ts          // 事件时间戳（毫秒）
) {}
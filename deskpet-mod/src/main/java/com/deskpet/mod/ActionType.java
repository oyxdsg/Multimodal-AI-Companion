package com.deskpet.mod;

/** 玩家操作类型。 */
public enum ActionType {
    JOIN,          // 进入世界
    LEAVE,         // 离开世界
    DEATH,         // 玩家死亡
    ADVANCEMENT,   // 获得进度
    DIMENSION,     // 跨维度（下界/末地）
    BREAK,         // 破坏方块
    USE_ITEM,      // 使用物品
    KILL,          // 击杀生物（target 为生物名）
    ITEM_GAIN,     // 获得物品（target 为物品名，detail 为合成/拾取）
    DAMAGE,        // 受到伤害（target 为伤害来源）
    CHAT,          // 玩家聊天
    MOVE,          // 聚合移动距离（detail 为米）
    BROADCAST      // 服务器广播消息（多人时由客户端广播解析器产生，detail 为完整文本）
}
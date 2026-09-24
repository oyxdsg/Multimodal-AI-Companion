package com.deskpet.mod.build;

/**
 * 本地规则引擎判定结果。
 */
public class RuleResult {
    public String category;      // tower/wall/bridge/pixel_art/statue/house/... 
    public String description;   // 中文模板描述
    public boolean needsAi;      // 雕像/复杂结构 → 桌宠端调 AI 润色
    public boolean ignore;       // 规模过小，不输出
    public String scale;         // 小屋/房屋/大宅/大型建筑
    public int standAloneWalls;  // 独立墙数（无围合长墙）
    public String style;         // 建筑风格（哥特/现代/和风/地中海/乡村/中式/中世纪/冰雪/沙漠/奇幻/工业/传统）
}
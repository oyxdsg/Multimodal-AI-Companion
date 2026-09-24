package com.deskpet.mod.build;

/**
 * 建筑评分结果：三大维度（结构/装饰/配色）各 0~100 分 + 总分 + 等级 + 评语。
 * 子项分保留供调试 / 桌宠端展示。
 */
public class ScoreResult {
    // 三大维度
    public double structure;   // 结构（体态 / 空心率 / 支撑）
    public double decorate;    // 装饰（雕工指数 / 外立面起伏）
    public double color;       // 配色（材质丰富 / 垂向分层 / 临近冲突）
    public double total;       // 总分 0~100
    public String grade;       // S / A / B / C / D
    public String comment;     // 中文评语

    // 子项分（便于调优）
    public double proportion;  // 体态比例
    public double solidity;    // 空心率
    public double support;     // 支撑逻辑
    public double detail;      // 雕工指数
    public double facade;      // 外立面起伏度
    public double material;    // 材质丰富度
    public double layer;       // 垂向分层
    public double conflict;    // 邻近颜色对比度

    /** 程序从建筑优化知识库挑出的针对性建议（不含分数，交给 AI 组织语言）。 */
    public java.util.List<String> suggestions = new java.util.ArrayList<>();
}

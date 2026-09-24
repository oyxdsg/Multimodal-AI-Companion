package com.deskpet.mod.build;

/**
 * 建筑意图推断结果：预测"不完整建筑"玩家打算建什么。
 *
 * 与 RuleResult 互补：RuleResult 是对"最终快照"的硬分类（完整建筑），
 * IntentResult 是对"正在搭建中结构"的意图猜测（大类 + 建造阶段 + 完成度 + 置信度）。
 * 二者由 BuildingAnalyzer.emit 在"是否进行中"时择一输出到感知包。
 */
public class IntentResult {
    /** 意图大类（house/tower/pool/fountain/bridge/arch/wall/boundary/castle/statue/pixel_art/open_base/unknown）。 */
    public String category;
    /** 意图中文大类（房屋/高塔/泳池/…/未知）。 */
    public String label;
    /** 建造阶段（foundation 地基 / frame 框架 / shell 封顶 / furnish 装修 / complete 完成 / base·multi·top 塔类阶段 / 其它）。 */
    public String phase;
    /** 相对该意图的完成度 0~1。 */
    public double completion;
    /** 意图置信度 0~1（低于 BUILDING 门槛视为弱猜测）。 */
    public double confidence;
    /** 桌宠可直接播放/播报的中文小句（含阶段 + 意图 + 材质）。 */
    public String hint;
    /** 结构是否已闭合（有房间/顶满/特征齐全）。 */
    public boolean complete;
    /** 是否命中强独特信号（含水/细高/扁平多材质），用于提升置信度。 */
    public boolean strongSignal;
    /** 证据不足（方块过少），不做意图预测。 */
    public boolean ignore;
}

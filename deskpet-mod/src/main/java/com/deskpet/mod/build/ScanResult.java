package com.deskpet.mod.build;

import java.util.List;
import java.util.Map;

/**
 * 三维扫描分析结果（本地规则引擎的输入，仅含结构/美学摘要，无行为元数据）。
 */
public class ScanResult {
    /** 一面墙（连续同材质面片，竖状）。 */
    public record Wall(String material, int height, int length, String dir, String type) {}

    /** 一个房间（墙+地板+天花板围合，边界有门）。 */
    public record Room(int volume, int floorY, int ceilY, String purpose) {}

    public int blockCount;
    public int clusterCount;
    public int width;            // AABB X 跨度
    public int depth;            // AABB Z 跨度
    public int height;           // AABB Y 跨度
    public int minY;             // AABB 最低 Y
    public int maxY;             // AABB 最高 Y
    public int verticalLayers;   // 不同 Y 层数
    public double solidity;      // 实心度 = 方块数 / (W*D*H)
    public double aspectHeight;  // 高宽比 H / max(W,D)
    public double aspectLength;  // 长宽比 max(W,D) / min(W,D)
    public double hangRatio;     // 底部悬空比例（下方悬空判定桥梁）
    public double floorCoverage; // 地面覆盖率
    public double wallCoverage;  // 墙壁覆盖率
    public double roofCoverage;  // 顶部覆盖率
    public String enclosureLevel; // 完全密闭/部分密闭/半开放/开放平台/完全露天
    public int pillarCount;
    public int beamCount;
    public double symmetryX;
    public double symmetryZ;
    public int materialCount;
    public String materialComplexity;  // monochrome / medium / rich
    public List<String> dominantMaterials;  // 前 2 名方块 identifier path
    public Map<String, Integer> contents;   // bed/chest/furnace/... -> 数量
    public boolean hasRedstone;

    // ---- P1 新增：墙 / 房间 / 露天 / 环境 ----
    public List<Wall> walls;        // 墙面板（含内外标注）
    public int wallOuter;           // 外墙数
    public int wallInner;           // 内墙/隔断数
    public int standAloneWall;      // 独立墙数（无围合长墙，RuleEngine 或扫描标注）
    public List<Room> rooms;        // 有门的围合空间
    public int cavities;            // 无门的密闭空间
    public int waterCount;          // 水/熔岩方块数
    public int groundY;             // AABB 中心处地表高度（最高实体方块）
    public List<Integer> layerAreas; // 每 Y 层方块数（minY..maxY，debug/屋顶用）
    public String roofType;         // flat/gabled/pointed/dome/open（open=无顶，雕塑等不适用）

    // ---- P2：形态 / 表面 / 材质位置特征 ----
    public String sectionShape;     // square/rect/elongated/irregular（水平截面主形状）
    public String silhouette;       // cylinder/funnel/trumpet/gourd/irregular/flat（垂直剪影）
    public double roughness;        // 平均暴露面（表面起伏）
    public double surfaceRatio;     // 表面方块占比
    public int exposedSurface;      // 暴露面总数
    public double centroidDX, centroidDY, centroidDZ;  // 质心偏移（相对 AABB 归一化）
    public double layerAreaVar;     // 层面积方差（波动度）
    public String topMaterial;      // 顶部主导材质
    public String baseMaterial;     // 底部主导材质
    public int materialLayerChanges;// 材质分层切换次数
    public int protrusions;         // 层面积骤变次数（突起指标）

    // ---- P3：色彩特征（判断建筑风格） ----
    public int colorCount;          // 颜色丰富度（独特颜色数）
    public String colorRichness;    // monochrome / medium / rich
    public String dominantColor;    // 主色（白/灰/黑/红/橙/黄/棕/蓝/绿/青/紫/粉）
    public String secondaryColor;   // 次色
    public double warmRatio;        // 暖色占比（红/橙/黄/棕）
    public double brightness;       // 平均明度 0~1
    public double saturation;       // 平均饱和度 0~1
    public List<String> palette;    // 调色板（前 5 主色）

    // ---- P4：建筑评分输入（结构/装饰/配色） ----
    public int nonFullCount;        // 非完整方块数（台阶/楼梯/门/活板门/栅栏等），雕工指数分母
    public int natureCount;         // 自然绿植装饰方块数（树叶/藤蔓/草/花等），装饰加分项
    public int cropCount;           // 作物方块数（小麦/胡萝卜/土豆/甜菜/浆果等）——农田特征
    public int hayCount;            // 干草垛方块数——牧场/田园特征
    public double facadeStd;        // 外表面方块到质心距离标准差（外立面起伏度）
    public int conflictPairs;       // 冲突邻接对数（冷暖/种类突兀组合）
    public int adjacentPairs;       // 有效邻接对总数（相邻两方块）
    public List<Double> layerBrightness;  // 每层平均明度 0~1（垂向分层/渐变检测）
    public List<String> layerMaterials;   // 每层主导材质 path
    public List<Double> layerPurity;      // 每层主导材质占比（0~1，色带集中度）
}
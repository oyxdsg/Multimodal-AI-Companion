package com.deskpet.mod.build;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 本地规则引擎：规模过滤 → 分层判定（特殊形状 → 水景 → 露天家园 → 墙 →
 * 地下 → 雕像 → 建筑细分）→ 规模分级 → 中文模板描述。
 * 简单结构直接输出（零成本）；雕像/复杂结构标记 needsAi，交由桌宠端调 AI 润色。
 */
public class RuleEngine {
    /** 忽略规模阈值：方块数少于该值不输出。 */
    private static final int MIN_BLOCKS = 30;
    /** 雕像最小造型规模。 */
    private static final int STATUE_MIN_BLOCKS = 50;

    public RuleResult evaluate(ScanResult r) {
        RuleResult out = new RuleResult();
        if (r.blockCount < MIN_BLOCKS) {
            // 小型生活场景（露天基地雏形：有床/箱等生活物件）也输出，
            // 纯散落方块才忽略
            if (functionObjectCount(r) == 0) {
                out.ignore = true;
                return out;
            }
            out.category = "open_base";
            out.scale = "小屋";
            out.needsAi = false;
            out.description = "一处露天家园，摆着" + functionWords(r);
            return out;
        }

        out.scale = scaleOf(r.blockCount);
        String material = materialAdjective(r);
        String category = classify(r);

        out.category = category;
        out.needsAi = category.equals("statue") || category.equals("complex");
        out.standAloneWalls = countStandAlone(r, category);
        out.style = inferStyle(r, category);

        String func = functionWords(r);
        out.description = describe(r, category, material, func, r.roofType, out.style);
        return out;
    }

    /** 建筑风格推断：用色彩（暖/明/饱和/主色）+ 结构（屋顶/材质/对称/物件）特征。 */
    private static String inferStyle(ScanResult r, String category) {
        String d = r.dominantColor;
        String mat = materialAdjective(r);
        String roof = r.roofType == null ? "open" : r.roofType;
        double warm = r.warmRatio, bri = r.brightness, sat = r.saturation;
        boolean glass = r.contents.getOrDefault("glass", 0) >= 1;
        boolean cyanPalette = r.palette.contains("青");
        int glassN = r.contents.getOrDefault("glass", 0);
        boolean nature = r.natureCount >= 8;
        // 0 通透采光 + 绿植 → 现代田园；通透采光为主 → 现代（玻璃落地窗/采光窗显通透现代）
        if (glassN >= 6 && nature) {
            return "田园";
        }
        if (glassN >= 6 && bri > 0.5) {
            return "现代";
        }
        // 1 现代：玻璃（青）+ 低饱和
        if (cyanPalette && sat < 0.4) {
            return "现代";
        }
        // 2 冰雪：白 + 冷 + 亮
        if ("白".equals(d) && warm < 0.3 && bri > 0.7) {
            return "冰雪";
        }
        // 3 沙漠：沙质 + 暖
        if ("沙质".equals(mat) && warm > 0.7) {
            return "沙漠";
        }
        // 4 奇幻：紫/粉 + 悬空
        if (("紫".equals(d) || "粉".equals(d)) && r.hangRatio > 0.3) {
            return "奇幻";
        }
        // 5 哥特：暗 + 深灰/黑 + 尖顶或高耸
        if (bri < 0.4 && ("灰".equals(d) || "黑".equals(d))
                && ("pointed".equals(roof) || r.aspectHeight > 2)) {
            return "哥特";
        }
        // 6 地中海：暖 + 亮 + 平顶
        if (warm > 0.6 && bri > 0.6 && "flat".equals(roof)) {
            return "地中海";
        }
        // 7 和风：木质 + 坡/尖顶 + 对称
        if ("木质".equals(mat) && ("gabled".equals(roof) || "pointed".equals(roof))
                && r.symmetryX > 0.6) {
            return "和风";
        }
        // 8 中式：红/棕 + 暖 + 对称
        if (("红".equals(d) || "棕".equals(d)) && warm > 0.7 && r.symmetryX > 0.7) {
            return "中式";
        }
        // 9 乡村：木/土 + 暖 + 坡顶或露天家园
        if (("木质".equals(mat) || "土质".equals(mat)) && warm > 0.5
                && ("gabled".equals(roof) || "open_base".equals(category))) {
            return "乡村";
        }
        // 10 工业：灰 + 低饱和 + 红石
        if ("灰".equals(d) && sat < 0.4 && r.hasRedstone) {
            return "工业";
        }
        // 11 中世纪：城堡分类
        if ("castle".equals(category)) {
            return "中世纪";
        }
        // 默认：按材质给传统基调
        if ("木质".equals(mat)) {
            return "传统木构";
        }
        if ("石质".equals(mat)) {
            return "传统石构";
        }
        return "混合";
    }

    /** 分层判定：特殊形状 → 水景 → 露天家园 → 墙 → 地下 → 雕像 → 建筑细分。 */
    private static String classify(ScanResult r) {
        int extent = Math.max(r.width, r.depth);
        // 1. 像素画：扁平 + 材质多样
        if (r.height <= 2 && r.materialCount > 5) {
            return "pixel_art";
        }
        // 2. 高塔：细高 + 多层
        if (r.aspectHeight > 2.5 && r.verticalLayers >= 3) {
            // 顶部有光源 → 灯塔；funnel 剪影+尖顶 → 尖塔
            if (r.contents.getOrDefault("light", 0) >= 1) {
                return "lighthouse";
            }
            if ("funnel".equals(r.silhouette)) {
                return "spire";
            }
            return "tower";
        }
        // 3. 桥：底部大面积悬空 + 长条
        if (r.hangRatio > 0.5 && extent > 4) {
            return "bridge";
        }
        // 4. 拱门：宽>高 + 悬空 + 柱梁支撑
        if (r.height < extent && r.hangRatio > 0.3
                && r.pillarCount >= 2 && r.beamCount >= 1) {
            return "arch";
        }
        // 5. 泳池：矮 + 含水多
        if (r.height <= 3 && r.waterCount >= 10) {
            return "pool";
        }
        // 6. 喷泉：含水 + 中心柱 + 对称
        if (r.waterCount >= 1 && r.pillarCount >= 1
                && r.symmetryX > 0.7 && r.symmetryZ > 0.7) {
            return "fountain";
        }
        // 7. 露天家园：无围合但摆着功能物件（新手玩家露天家）
        if (r.floorCoverage > 0.5 && functionObjectCount(r) >= 1
                && r.roofCoverage < 0.3 && r.wallCoverage < 0.3) {
            return "open_base";
        }
        // 8. 城墙/边界墙：开放长条（无围合、无功能物件）
        if (r.rooms.isEmpty() && r.aspectLength >= 3
                && functionObjectCount(r) == 0) {
            if (r.height >= 2 && r.height <= 10 && r.hangRatio < 0.5) {
                return "wall";
            }
            if (r.height <= 2) {
                return "boundary";
            }
        }
        // 9. 地下建筑：整体在地表下（最高层低于地表 2 格以上）
        if (r.groundY > -64 && r.maxY < r.groundY - 2) {
            return "underground";
        }
        // 10. 雕像（排除法）：无房间 + 无功能物件 + 造型规模足
        //     （不限实心/空心/顶形态，由剪影细分人形/柱状/抽象）
        if (r.rooms.isEmpty() && functionObjectCount(r) <= 3
                && r.blockCount >= STATUE_MIN_BLOCKS) {
            // 细化：剪影区分人形 / 柱状 / 抽象
            if ("gourd".equals(r.silhouette)) {
                return "statue_humanoid";
            }
            if ("cylinder".equals(r.silhouette) && r.aspectHeight > 2) {
                return "statue_column";
            }
            return "statue";
        }
        // 11. 建筑细分（有围合）
        if (!r.rooms.isEmpty()) {
            if (r.aspectHeight > 1.5 && r.protrusions >= 1) {
                return "castle";
            }
            // 别墅：规模较大 + 讲究（多房间/材质丰富/装饰多/对称高）
            if (r.blockCount >= 300
                    && (r.rooms.size() >= 2 || r.materialCount >= 4
                        || functionObjectCount(r) >= 4
                        || (r.symmetryX > 0.7 && r.symmetryZ > 0.7))) {
                return "villa";
            }
            if (r.rooms.size() >= 2) {
                return "multi_story";
            }
            if (r.aspectHeight < 0.6 && r.blockCount > 300) {
                return "barn";
            }
            return classifyHouse(r);
        }
        return "complex";
    }

    /** 居住建筑细分：火柴盒 / 平房 / 别墅 / 小屋 / 普通房屋。 */
    private static String classifyHouse(ScanResult r) {
        // 火柴盒：方正空心屋 + 几乎无装饰（仅门）+ 普通材质（木/石/土，最朴素形态优先）
        if (r.rooms.size() >= 1
                && r.aspectLength <= 1.3
                && r.aspectHeight >= 0.4 && r.aspectHeight <= 1.3
                && functionObjectCount(r) <= 2
                && r.materialCount <= 2
                && isPlainMaterial(r)) {
            return "matchbox";
        }
        // 平房：单层 + 矮 + 横向延展
        if (singleStory(r) && r.aspectHeight <= 0.5 && r.aspectLength >= 1.5) {
            return "bungalow";
        }
        // 别墅：规模较大 + 讲究（多房间 / 材质丰富 / 装饰多 / 对称高）
        if (r.blockCount >= 300
                && (r.rooms.size() >= 2 || r.materialCount >= 4
                    || functionObjectCount(r) >= 4
                    || (r.symmetryX > 0.7 && r.symmetryZ > 0.7))) {
            return "villa";
        }
        // 小屋：木质 + 规模小 + 单房间
        if (r.blockCount < 200 && "木质".equals(materialAdjective(r))) {
            return "cabin";
        }
        return "house";
    }

    /** 是否普通建材（木/石/土）——火柴盒等朴素建筑限定，排除沙/玻璃/羊毛等特殊材质。 */
    private static boolean isPlainMaterial(ScanResult r) {
        if (r.dominantMaterials.isEmpty()) {
            return false;
        }
        String cls = materialClass(r.dominantMaterials.get(0));
        return cls.equals("木质") || cls.equals("石质") || cls.equals("土质");
    }

    /** 是否单层：房间 Y 跨度 ≤3，或（无房间时）高度 ≤4。 */
    private static boolean singleStory(ScanResult r) {
        if (r.rooms.isEmpty()) {
            return r.height <= 4;
        }
        int minF = Integer.MAX_VALUE, maxC = Integer.MIN_VALUE;
        for (ScanResult.Room rm : r.rooms) {
            minF = Math.min(minF, rm.floorY());
            maxC = Math.max(maxC, rm.ceilY());
        }
        return maxC - minF <= 3;
    }

    private static String describe(ScanResult r, String category, String material, String func, String roofType, String style) {
        String roof = roofWord(roofType);
        String roofAdj = roof.isEmpty() ? "" : roof + "的";
        String base = baseFoundation(r);
        String baseAdj = base.isEmpty() ? "" : base + "、";
        String styleAdj = (style == null || style.isEmpty()) ? "" : style + "的";
        return switch (category) {
            case "tower" -> "一座" + styleAdj + material + (roof.isEmpty() ? "" : "（" + roof + "）") + "高塔";
            case "spire" -> "一座" + styleAdj + material + "尖塔";
            case "lighthouse" -> "一座" + styleAdj + material + "灯塔";
            case "wall" -> "一段" + styleAdj + material + "城墙";
            case "boundary" -> "一段" + styleAdj + material + "边界墙";
            case "bridge" -> "一座" + styleAdj + material + "桥梁";
            case "arch" -> "一座" + styleAdj + material + "拱门";
            case "pixel_art" -> "一幅彩色像素画";
            case "pool" -> "一座" + styleAdj + material + "泳池";
            case "fountain" -> "一座" + styleAdj + material + "喷泉";
            case "open_base" -> "一处露天家园，摆着" + (func.isEmpty() ? "家当" : func);
            case "underground" -> "一处" + styleAdj + material + "地下建筑" + (func.isEmpty() ? "" : "，有" + func);
            case "castle" -> "一座" + styleAdj + baseAdj + roofAdj + material + "城堡" + (func.isEmpty() ? "" : "，带" + func);
            case "multi_story" -> "一栋" + styleAdj + baseAdj + roofAdj + material + "多层楼房" + (func.isEmpty() ? "" : "，带" + func);
            case "barn" -> "一座" + styleAdj + baseAdj + material + "谷仓";
            case "bungalow" -> "一间" + styleAdj + baseAdj + roofAdj + material + "平房";
            case "matchbox" -> "一个" + styleAdj + material + "火柴盒";
            case "villa" -> "一座" + styleAdj + baseAdj + material + "别墅" + (func.isEmpty() ? "" : "，带" + func);
            case "cabin" -> "一间" + styleAdj + material + "小屋";
            case "statue_humanoid" -> "一座" + styleAdj + material + "人形雕塑";
            case "statue_column" -> "一根" + styleAdj + material + "图腾柱";
            case "statue" -> "一座" + styleAdj + material + "雕塑";
            case "house" -> "一间" + styleAdj + baseAdj + roofAdj + (func.isEmpty() ? "" : "带" + func + "的") + material + "房屋";
            default -> "一座" + styleAdj + "结构复杂的" + material + "建筑";
        };
    }

    /** 基座描述：材质分层且底材质与主材质不同（如石基木墙）。 */
    private static String baseFoundation(ScanResult r) {
        if (r.materialLayerChanges >= 1 && !r.baseMaterial.isEmpty()
                && !r.baseMaterial.equals(r.dominantMaterials.isEmpty()
                        ? "" : r.dominantMaterials.get(0))) {
            String b = materialClass(r.baseMaterial);
            if (!b.equals("混合")) {
                return "带" + b + "地基";
            }
        }
        return "";
    }

    /** 屋顶类型 → 中文（open/未知不输出）。 */
    private static String roofWord(String roofType) {
        if (roofType == null) {
            return "";
        }
        return switch (roofType) {
            case "flat" -> "平顶";
            case "gabled" -> "坡顶";
            case "pointed" -> "尖顶";
            case "dome" -> "穹顶";
            default -> "";
        };
    }

    /** 建筑规模分级。 */
    private static String scaleOf(int blocks) {
        if (blocks < 200) {
            return "小屋";
        }
        if (blocks < 1000) {
            return "房屋";
        }
        if (blocks < 5000) {
            return "大宅";
        }
        return "大型建筑";
    }

    /** 功能物件总数（含实体/家具）。 */
    private static int functionObjectCount(ScanResult r) {
        int sum = 0;
        for (int v : r.contents.values()) {
            sum += v;
        }
        return sum;
    }

    /** 独立墙数：无围合时，扫描到的长墙（长度≥5）算独立城墙。 */
    private static int countStandAlone(ScanResult r, String category) {
        if (category.equals("wall") || category.equals("boundary")
                || category.equals("bridge") || category.equals("arch")) {
            int n = 0;
            for (ScanResult.Wall w : r.walls) {
                if (w.length() >= 5) {
                    n++;
                }
            }
            return n;
        }
        return 0;
    }

    /** 功能物件判定 → 功能词列表。 */
    private static String functionWords(ScanResult r) {
        Map<String, Integer> c = r.contents;
        List<String> parts = new ArrayList<>();
        if (c.getOrDefault("bed", 0) >= 1) {
            parts.add("卧室");
        }
        if (r.natureCount >= 10) {
            parts.add("绿植");
        }
        int chests = c.getOrDefault("chest", 0);
        if (chests >= 5) {
            parts.add("大型储物");
        } else if (chests >= 1) {
            parts.add("储物");
        }
        if (c.getOrDefault("crafting", 0) >= 1 && c.getOrDefault("furnace", 0) >= 1) {
            parts.add("工坊");
        }
        if (c.getOrDefault("enchanting", 0) >= 1) {
            parts.add("附魔室");
        }
        if (c.getOrDefault("brewing", 0) >= 1) {
            parts.add("炼药间");
        }
        if (c.getOrDefault("anvil", 0) >= 1) {
            parts.add("修复间");
        }
        if (r.hasRedstone) {
            parts.add("红石自动化");
        }
        if (c.getOrDefault("armor_stand", 0) >= 1) {
            parts.add("盔甲架");
        }
        if (c.getOrDefault("item_frame", 0) >= 1) {
            parts.add("展示框");
        }
        if (c.getOrDefault("painting", 0) >= 1) {
            parts.add("挂画");
        }
        if (c.getOrDefault("minecart", 0) >= 1) {
            parts.add("矿车");
        }
        if (c.getOrDefault("boat", 0) >= 1) {
            parts.add("船");
        }
        if (c.getOrDefault("fence", 0) >= 1) {
            parts.add("栅栏");
        }
        if (c.getOrDefault("flower_pot", 0) >= 1) {
            parts.add("花盆");
        }
        if (c.getOrDefault("furniture", 0) >= 3) {
            parts.add("家具结构");
        }
        if (c.getOrDefault("glass", 0) >= 1) {
            parts.add("采光");
        }
        if (c.getOrDefault("bookshelf", 0) >= 1) {
            parts.add("书架");
        }
        return String.join("、", parts);
    }

    /** 主导材质的形容词（石质/木质/沙质/土质/玻璃/羊毛/混合）。 */
    private static String materialAdjective(ScanResult r) {
        if (r.dominantMaterials.isEmpty()) {
            return "混合";
        }
        String cls = materialClass(r.dominantMaterials.get(0));
        if (cls.equals("混合") && r.materialCount >= 3) {
            return "混合";
        }
        return cls;
    }

    private static String materialClass(String path) {
        return BlockClasses.materialClass(path);
    }
}
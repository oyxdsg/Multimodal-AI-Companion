package com.deskpet.mod.build;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 方块视觉/物理分类共享工具：材质大类、颜色 RGB、亮度。
 * 表驱动（规则列表）替代各处的长 if-else 分支，并提供路径级缓存，
 * 消除 RuleEngine / IntentEstimator / StructureScanner 三处重复实现。
 */
public final class BlockClasses {
    private BlockClasses() {}

    // ---- 材质大类规则（顺序敏感：sand 先于 stone 等） ----
    private static final List<String[]> MATERIAL_RULES = List.of(
            new String[]{"沙质", "sand"},
            new String[]{"石质", "stone", "cobblestone", "deepslate", "bricks", "basalt",
                    "tuff", "blackstone", "granite", "diorite", "andesite", "calcite", "concrete"},
            new String[]{"木质", "log", "planks", "_wood", "oak", "spruce", "birch", "jungle",
                    "acacia", "dark_oak", "cherry", "mangrove", "bamboo", "warped", "crimson",
                    "fence", "trapdoor"},
            new String[]{"土质", "dirt", "grass", "gravel", "clay", "mud", "podzol"},
            new String[]{"玻璃", "glass"},
            new String[]{"羊毛", "wool", "carpet"});

    // ---- 颜色规则（顺序敏感） ----
    private static final List<String[]> COLOR_RULES = List.of(
            // ---- 木头系：按具体木种精确配色（优先于通用 log/planks 兜底） ----
            new String[]{"cherry", "244,160,168"},      // 樱花木（粉）
            new String[]{"acacia", "214,148,90"},       // 金合欢（橙）
            new String[]{"birch", "222,209,185"},       // 白桦（浅白）
            new String[]{"warped", "58,116,130"},       // 诡异（青）
            new String[]{"crimson", "122,48,58"},       // 绯红（暗红）
            new String[]{"mangrove", "138,80,54"},      // 红树（红棕）
            new String[]{"dark_oak", "78,58,44"},       // 深色橡木（深棕）
            new String[]{"spruce", "100,82,60"},        // 云杉（深棕）
            new String[]{"jungle", "150,110,60"},       // 丛林（棕）
            new String[]{"bamboo", "214,196,130"},      // 竹（黄绿）
            new String[]{"pink", "244,177,190"},
            new String[]{"sand", "216,192,122"},
            // 通用木质兜底（oak 等默认棕）
            new String[]{"log", "150,110,70"}, new String[]{"planks", "160,128,80"},
            new String[]{"wood", "139,90,43"}, new String[]{"fence", "139,90,43"},
            new String[]{"trapdoor", "139,90,43"}, new String[]{"stair", "139,90,43"},
            // ---- 石系：按具体石种配色（优先于通用 stone 兜底） ----
            new String[]{"calcite", "222,220,214"},      // 方解石（白）
            new String[]{"blackstone", "45,45,50"},      // 黑石（黑）
            new String[]{"deepslate", "66,66,72"},       // 深板岩（深灰黑）
            new String[]{"tuff", "110,114,116"},         // 凝灰岩（灰）
            new String[]{"stone", "128,128,128"}, new String[]{"cobble", "128,128,128"},
            new String[]{"brick", "128,128,128"}, new String[]{"granite", "128,128,128"},
            new String[]{"dirt", "122,74,42"}, new String[]{"mud", "122,74,42"},
            new String[]{"grass", "106,176,76"}, new String[]{"leaves", "96,161,67"},
            new String[]{"vine", "96,161,67"}, new String[]{"moss", "96,161,67"},
            new String[]{"glass", "207,232,239"},
            new String[]{"water", "63,118,228"},
            new String[]{"lava", "255,107,0"},
            new String[]{"bed", "240,240,240"}, new String[]{"wool", "240,240,240"},
            new String[]{"carpet", "240,240,240"},
            new String[]{"chest", "139,90,43"}, new String[]{"door", "139,90,43"},
            new String[]{"lantern", "255,220,130"}, new String[]{"torch", "255,220,130"},
            new String[]{"glowstone", "255,220,130"},
            new String[]{"obsidian", "21,10,33"},
            new String[]{"gold", "252,212,20"},
            new String[]{"iron", "216,216,216"},
            new String[]{"diamond", "74,237,217"},
            new String[]{"redstone", "200,30,30"}, new String[]{"netherrack", "200,30,30"});

    /** path → 材质大类（优先查映射表；未命中用规则兜底 + 缓存）。 */
    public static String materialClass(String path) {
        if (path == null) {
            return "混合";
        }
        String t = MAT_TABLE.get(path);
        if (t != null) {
            return t;
        }
        String cached = MAT_CACHE.get(path);
        if (cached != null) {
            return cached;
        }
        String cls = "混合";
        for (String[] rule : MATERIAL_RULES) {
            for (int i = 1; i < rule.length; i++) {
                if (path.contains(rule[i])) {
                    cls = rule[0];
                    break;
                }
            }
            if (!cls.equals("混合")) {
                break;
            }
        }
        MAT_CACHE.put(path, cls);
        return cls;
    }

    /** path → RGB int[3]（优先查映射表=真实纹理色；未命中用规则兜底 + 缓存）。 */
    public static int[] colorOf(String path) {
        if (path == null) {
            return new int[]{140, 140, 140};
        }
        int[] t = RGB_TABLE.get(path);
        if (t != null) {
            return t;
        }
        int[] cached = COLOR_CACHE.get(path);
        if (cached != null) {
            return cached;
        }
        int[] rgb = new int[]{140, 140, 140};
        for (String[] rule : COLOR_RULES) {
            if (path.contains(rule[0])) {
                String[] parts = rule[1].split(",");
                rgb[0] = Integer.parseInt(parts[0]);
                rgb[1] = Integer.parseInt(parts[1]);
                rgb[2] = Integer.parseInt(parts[2]);
                break;
            }
        }
        COLOR_CACHE.put(path, rgb);
        return rgb;
    }

    /** path → 亮度 0~1（基于 colorOf）。 */
    public static double brightnessOf(String path) {
        int[] c = colorOf(path);
        return (c[0] + c[1] + c[2]) / 3.0 / 255.0;
    }

    /** RGB → 颜色名（分布直方图，鲁棒分类）。 */
    public static String colorName(int r, int g, int b) {
        int mx = Math.max(r, Math.max(g, b)), mn = Math.min(r, Math.min(g, b));
        if (mx - mn < 24) {
            return mx > 200 ? "白" : (mx < 60 ? "黑" : "灰");
        }
        if (b >= r && b >= g) {
            return (g > 150 && b - Math.max(r, g) < 80) ? "青" : "蓝";
        }
        if (g >= r && g >= b) {
            if (r > 140) {
                return "黄";
            }
            if (b > 170) {
                return "青";
            }
            return "绿";
        }
        if (g > 150 && b < 150) {
            return "黄";
        }
        if (r > 200 && g > 140 && b > 140) {
            return "粉";
        }
        if (r > 200 && g < 110 && b < 110) {
            return "红";
        }
        if (g > 110) {
            return b < 120 ? "橙" : "粉";
        }
        if (g > 55) {
            return "棕";
        }
        return "红";
    }

    private static final java.util.Set<String> LIGHT_SOURCES = java.util.Set.of(
            "torch", "lantern", "glowstone", "sea_lantern", "shroomlight",
            "campfire", "soul_campfire", "jack_o_lantern", "redstone_lamp");

    /** 功能物件分类（bed/chest/furnace/…/furniture/light），非功能物件返回 null。属于方块词典，共享给扫描器。 */
    public static String contentType(String path) {
        if (path == null) {
            return null;
        }
        String tt = TYPE_TABLE.get(path);
        if (tt != null) {
            return tt;
        }
        if (path.endsWith("_bed") || path.equals("bed")) {
            return "bed";
        }
        if (path.endsWith("_chest") || path.equals("chest") || path.equals("barrel")) {
            return "chest";
        }
        if (path.endsWith("furnace") || path.equals("smoker")) {
            return "furnace";
        }
        if (path.equals("crafting_table")) {
            return "crafting";
        }
        if (path.equals("enchanting_table")) {
            return "enchanting";
        }
        if (path.equals("brewing_stand")) {
            return "brewing";
        }
        if (path.endsWith("anvil")) {
            return "anvil";
        }
        if (path.endsWith("_door") || path.equals("door")
                || path.endsWith("_trapdoor") || path.equals("trapdoor")
                || path.endsWith("_gate") || path.equals("fence_gate")) {
            return "door";
        }
        if (path.endsWith("_fence") || path.equals("fence")) {
            return "fence";
        }
        if (path.equals("flower_pot") || path.startsWith("potted_")) {
            return "flower_pot";
        }
        if (path.endsWith("_slab") || path.endsWith("_stairs") || path.endsWith("_stair")) {
            return "furniture";
        }
        if (path.contains("glass")) {
            return "glass";
        }
        if (path.equals("bookshelf")) {
            return "bookshelf";
        }
        if (LIGHT_SOURCES.contains(path)) {
            return "light";
        }
        return null;
    }

    private static final Map<String, String> MAT_CACHE = new HashMap<>();
    private static final Map<String, int[]> COLOR_CACHE = new HashMap<>();

    // ===== 权威映射表（registry/blocks.json）：颜色/材质/功能统一查表，规则仅兜底 =====
    private static final Map<String, int[]> RGB_TABLE = new HashMap<>();
    private static final Map<String, String> MAT_TABLE = new HashMap<>();
    private static final Map<String, String> TYPE_TABLE = new HashMap<>();

    /** 从 registry/blocks.json 加载映射表（数组格式：{id,name,material,rgb,type}），供查表优先。 */
    public static void loadFrom(java.nio.file.Path p) {
        if (p == null || !java.nio.file.Files.isRegularFile(p)) {
            return;
        }
        try {
            com.google.gson.JsonElement root = com.google.gson.JsonParser
                    .parseString(new String(java.nio.file.Files.readAllBytes(p), java.nio.charset.StandardCharsets.UTF_8));
            if (root.isJsonArray()) {
                for (com.google.gson.JsonElement e : root.getAsJsonArray()) {
                    com.google.gson.JsonObject o = e.getAsJsonObject();
                    String name = o.has("name") ? o.get("name").getAsString() : null;
                    if (name == null) {
                        continue;
                    }
                    if (o.has("material")) {
                        MAT_TABLE.put(name, o.get("material").getAsString());
                    }
                    if (o.has("rgb")) {
                        com.google.gson.JsonArray rgb = o.getAsJsonArray("rgb");
                        if (rgb.size() >= 3) {
                            RGB_TABLE.put(name, new int[]{rgb.get(0).getAsInt(), rgb.get(1).getAsInt(), rgb.get(2).getAsInt()});
                        }
                    }
                    if (o.has("type")) {
                        TYPE_TABLE.put(name, o.get("type").getAsString());
                    }
                }
            }
        } catch (java.io.IOException | RuntimeException ignored) {
        }
    }
}

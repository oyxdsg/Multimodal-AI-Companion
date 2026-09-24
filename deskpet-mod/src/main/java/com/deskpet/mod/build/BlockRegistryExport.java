package com.deskpet.mod.build;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.Block;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

/**
 * 全量方块映射表导出：遍历 MC 注册表，为每个方块生成
 * {id, name(path), material(材质大类), rgb, brightness, color_name, type(功能类别)}。
 *
 * 输出到 {gameDir}/registry/blocks.json，供离线分析脚本：
 *  - 把日志救回的方块数字 id 补全成完整信息（材质/颜色/功能）；
 *  - 颜色/材质/功能判定与 mod 端 BlockClasses 完全一致。
 * 同一 MC 版本 id 稳定，导出一次即可复用。
 */
public final class BlockRegistryExport {
    private static final Logger LOGGER = LoggerFactory.getLogger("deskpet-registry");

    private BlockRegistryExport() {}

    /** 导出到 <gameDir>/deskpet/registry/blocks.json，返回条数；失败返回 -1。
     *  颜色从真实的纹理采样表 block_colors.json 读取（若存在），否则用 colorOf 规则兜底。 */
    public static int export(Path gameDir) {
        try {
            // 真实纹理采样色（name -> [r,g,b]），来自客户端贴图，最准确
            Map<String, int[]> realColor = new HashMap<>();
            Path bc = gameDir.resolve("registry").resolve("block_colors.json");
            if (Files.isRegularFile(bc)) {
                JsonObject root = com.google.gson.JsonParser
                        .parseString(new String(Files.readAllBytes(bc), StandardCharsets.UTF_8))
                        .getAsJsonObject();
                for (String name : root.keySet()) {
                    JsonArray c = root.getAsJsonArray(name);
                    realColor.put(name, new int[]{c.get(0).getAsInt(), c.get(1).getAsInt(), c.get(2).getAsInt()});
                }
            }
            JsonArray arr = new JsonArray();
            for (Block b : BuiltInRegistries.BLOCK) {
                JsonObject o = new JsonObject();
                int id = BuiltInRegistries.BLOCK.getId(b);
                String path = BuiltInRegistries.BLOCK.getKey(b).getPath();
                o.addProperty("id", id);
                o.addProperty("name", path);
                o.addProperty("material", BlockClasses.materialClass(path));
                int[] rgb = realColor.getOrDefault(path, BlockClasses.colorOf(path));
                JsonArray c = new JsonArray();
                c.add(rgb[0]);
                c.add(rgb[1]);
                c.add(rgb[2]);
                o.add("rgb", c);
                o.addProperty("bright", Math.round(rgb[0] / 3.0 / 255 * 1000) / 1000.0);
                o.addProperty("color_name", BlockClasses.colorName(rgb[0], rgb[1], rgb[2]));
                String type = BlockClasses.contentType(path);
                if (type != null) {
                    o.addProperty("type", type);
                }
                arr.add(o);
            }
            Path dir = gameDir.resolve("registry");
            Files.createDirectories(dir);
            Files.write(dir.resolve("blocks.json"),
                    arr.toString().getBytes(StandardCharsets.UTF_8));
            LOGGER.info("[registry] exported {} blocks -> {}", arr.size(), dir.resolve("blocks.json"));
            return arr.size();
        } catch (IOException e) {
            LOGGER.error("[registry] export failed", e);
            return -1;
        }
    }
}

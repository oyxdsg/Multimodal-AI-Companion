package com.deskpet.mod.build;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.Block;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 建筑识别调度入口：
 * - 接收放置/破坏事件（主线程，O(1) 写入 BlockStore）
 * - 服务端 tick：20 秒窗口输出感知包（边建边播报，去重防刷屏）
 * - 60 秒无操作：输出最终感知包并休眠释放内存
 * 扫描在独立后台线程执行（不阻塞服务端 tick），感知包写入 deskpet/buildings/latest.json。
 */
public class BuildingAnalyzer {
    private static final Logger LOGGER = LoggerFactory.getLogger("deskpet-build");
    private static final long WINDOW_MS = 20_000;   // 播报窗口
    private static final long DORMANT_MS = 60_000;  // 休眠阈值
    /** 扫描护栏：方块数超过此值时跳过结构分析（避免极端超大建筑拖慢），仅保留原始数据。 */
    private static final int MAX_SCAN_BLOCKS = 80_000;
    /** 放置日志节流：每 N 块打一条。 */
    private static final int PLACE_LOG_EVERY = 50;

    private long placeLogCount = 0;

    private final BuildingTracker tracker = new BuildingTracker();
    private final StructureScanner scanner = new StructureScanner();
    private final RuleEngine ruleEngine = new RuleEngine();
    private final IntentEstimator intentEstimator = new IntentEstimator();
    private final BuildingScore scoreEngine = new BuildingScore();
    private final BlockRecorder blockRecorder;
    private final Path buildingsDir;
    private final Path historyFile;
    /** 后台扫描线程（单线程 daemon，不阻塞主线程）。 */
    private final ExecutorService scanPool = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "deskpet-build-scan");
        t.setDaemon(true);
        return t;
    });

    private long lastWindowMs = 0;
    private long lastEmittedBlocks = 0;
    private int window = 0;

    public BuildingAnalyzer(Path gameDir) {
        this.buildingsDir = gameDir.resolve("buildings");
        this.historyFile = buildingsDir.resolve("history.jsonl");
        this.blockRecorder = new BlockRecorder(gameDir.resolve("blocks"));
        // 加载权威映射表：颜色/材质/功能统一查 registry/blocks.json（未导出时规则兜底）
        BlockClasses.loadFrom(gameDir.resolve("registry").resolve("blocks.json"));
    }

    public void onBlockPlace(ServerLevel level, int x, int y, int z,
                             int blockId, Player player, long nowMs) {
        // 空间分离的新建筑：新放置与当前项目完全不相连 → 先完成当前项目再开新
        if (tracker.isActive() && !tracker.connectedToCurrent(x, y, z)) {
            window++;
            emit("dormant");
            tracker.reset();
        }
        tracker.onBlockPlace(level, x, y, z, blockId, player, nowMs);
        // 玩家放置方块持久化（只存 id+坐标，查表派生材质/颜色/功能，省资源）
        blockRecorder.record(nowMs, worldKey(level), projectId(), x, y, z, blockId, "place");
        // 逐方块日志（节流：每 50 块打一条汇总，避免高频刷日志）
        if (++placeLogCount % PLACE_LOG_EVERY == 0) {
            LOGGER.info("[building] place {} at ({},{},{}) id={} ({}th)",
                    playerName(player), x, y, z, blockId, placeLogCount);
        }
    }

    public void onBlockBreak(ServerLevel level, int x, int y, int z, long nowMs) {
        // 记录被玩家破坏的方块（保留历史：破坏前 store 里还有该块）
        BlockRecord rec = tracker.store().get(x, y, z);
        int blockId = rec != null ? rec.blockId() : -1;
        triggerBlockBreak(level, x, y, z, blockId, nowMs);
    }

    /** 供子类/扩展调用：记录破坏事件 + 从内存 store 移除。 */
    void triggerBlockBreak(ServerLevel level, int x, int y, int z, int blockId, long nowMs) {
        blockRecorder.record(nowMs, worldKey(level), projectId(), x, y, z, blockId, "break");
        tracker.onBlockBreak(level, x, y, z, nowMs);
    }

    // ---------- 方块数据持久化辅助 ----------

    /** 世界分区键：seed + 维度 id（与 BuildingTracker.worldPartitionKey 同格式）。 */
    private String worldKey(ServerLevel level) {
        return level.getSeed() + "_" + level.dimension().identifier();
    }

    private String projectId() {
        return tracker.projectId() != null ? tracker.projectId().toString() : "";
    }

    private String playerName(Player p) {
        return p != null ? p.getName().getString() : "";
    }

    /** 服务器停止时调用：把排队中的方块数据全部落盘并释放资源。 */
    public void close() {
        blockRecorder.close();
    }

    /** 服务端每 tick 调用（主线程）。 */
    public void onServerTick(long nowMs) {
        if (!tracker.isActive()) {
            return;
        }
        // 扫描护栏：超出上限只保留原始数据，不再全量扫描/评分（防极端超大建筑拖慢）
        int size = tracker.store().size();
        if (size > MAX_SCAN_BLOCKS) {
            blockRecorder.flush();
            return;
        }
        if (nowMs - lastWindowMs >= WINDOW_MS) {
            lastWindowMs = nowMs;
            if (size > lastEmittedBlocks) {
                lastEmittedBlocks = size;
                window++;
                emit("progress");
            }
        }
        if (tracker.idleMillis(nowMs) >= DORMANT_MS) {
            window++;
            emit("dormant");
            tracker.reset();
            lastEmittedBlocks = 0;
            lastWindowMs = 0;
        }
    }

    /** 弱猜测置信度门槛：意图置信度低于此不大幅替换最终描述。 */
    private static final double MIN_CONFIDENCE = 0.35;

    /** 主线程快照 store，后台线程扫描+规则+写文件。 */
    private void emit(String reason) {
        // 唤醒方块记录器，把已入队的玩家方块数据尽快落盘
        blockRecorder.flush();
        Map<Long, BlockRecord> snap = tracker.store().snapshot();
        ServerLevel level = tracker.level();
        int window = this.window;
        String projectId = tracker.projectId() != null ? tracker.projectId().toString() : "";
        long[] anchor = tracker.anchor();
        String world = tracker.worldPartitionKey();
        scanPool.execute(() -> {
            ScanResult sr = scanner.scan(snap, level);
            RuleResult rr = ruleEngine.evaluate(sr);
            IntentResult ir = intentEstimator.estimate(sr);
            // 是否进行中：progress 窗口（边建边播），或结构明显未闭合（无房间+顶覆盖率低）。
            // 进行中的建筑用"意图预测"描述，避免硬分类对不完整结构误判（如火柴盒/边界墙）。
            boolean inProgress = "progress".equals(reason)
                    || (sr.rooms.isEmpty() && sr.roofCoverage < 0.3);
            boolean useIntent = !ir.ignore && ir.confidence >= MIN_CONFIDENCE && inProgress;
            if (rr.ignore && !useIntent) {
                LOGGER.info("[building] window#{} {} skip (blocks={})", window, reason, sr.blockCount);
                return;
            }
            // 进行中但意图置信度低 → 仍播报弱信息（hint），保证"边建边有回应"不静默
            String classification = useIntent ? ir.hint : (rr.ignore ? ir.hint : rr.description);
            String category = useIntent || rr.ignore ? ir.category : rr.category;
            boolean needsAi = (useIntent || rr.ignore) ? false : rr.needsAi;
            ScoreResult sc = scoreEngine.calculate(sr, category);
            LOGGER.info("[building] window#{} {} => {} (blocks={}, category={}, intent={}/{}, conf={})",
                    window, reason, classification, sr.blockCount, category,
                    ir.category, ir.phase, String.format("%.2f", ir.confidence));
            JsonObject json = buildJson(sr, rr, ir, reason, window, projectId, anchor, world,
                    classification, category, needsAi, inProgress, sc);
            write(json);
            // 项目完成（dormant/切换）时归档整栋感知包：每栋独立留存，不再被下一个项目覆盖
            if ("dormant".equals(reason)) {
                archive(json);
            }
        });
    }

    /** 把完成的整栋感知包追加到 buildings/history.jsonl（可按 project 与 blocks 关联，永不覆盖）。 */
    private void archive(JsonObject json) {
        try {
            Files.createDirectories(buildingsDir);
            Files.write(historyFile, (json.toString() + "\n").getBytes(StandardCharsets.UTF_8),
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException ignored) {
        }
    }

    private JsonObject buildJson(ScanResult sr, RuleResult rr, IntentResult ir, String reason,
                                 int window, String projectId, long[] anchor, String world,
                                 String classification, String category, boolean needsAi,
                                 boolean inProgress, ScoreResult sc) {
        JsonObject o = new JsonObject();
        o.addProperty("type", "building");
        o.addProperty("window", window);
        o.addProperty("ts", System.currentTimeMillis());
        o.addProperty("reason", reason);
        o.addProperty("project_id", projectId);
        JsonArray an = new JsonArray();
        if (anchor != null) {
            for (long v : anchor) {
                an.add(v);
            }
        }
        o.add("anchor", an);
        o.addProperty("world", world);

        JsonObject dim = new JsonObject();
        dim.addProperty("width", sr.width);
        dim.addProperty("depth", sr.depth);
        dim.addProperty("height", sr.height);
        dim.addProperty("block_count", sr.blockCount);
        dim.addProperty("min_y", sr.minY);
        dim.addProperty("max_y", sr.maxY);
        o.add("dimensions", dim);

        o.addProperty("classification", classification);
        o.addProperty("category", category);
        o.addProperty("scale", rr.scale);
        o.addProperty("style", rr.style);
        o.addProperty("roof_type", sr.roofType);
        o.addProperty("needs_ai", needsAi);
        o.addProperty("state", inProgress ? "in_progress" : "complete");
        o.add("intent", intentJson(ir));
        o.add("score", scoreJson(sc));

        JsonObject enc = new JsonObject();
        enc.addProperty("wall", sr.wallCoverage);
        enc.addProperty("roof", sr.roofCoverage);
        enc.addProperty("floor", sr.floorCoverage);
        enc.addProperty("level", sr.enclosureLevel);
        o.add("enclosure", enc);

        // 墙
        JsonObject walls = new JsonObject();
        walls.addProperty("outer", sr.wallOuter);
        walls.addProperty("inner", sr.wallInner);
        walls.addProperty("standalone", rr.standAloneWalls);
        JsonArray wallList = new JsonArray();
        for (ScanResult.Wall w : sr.walls) {
            JsonObject wo = new JsonObject();
            wo.addProperty("material", w.material());
            wo.addProperty("height", w.height());
            wo.addProperty("length", w.length());
            wo.addProperty("dir", w.dir());
            wo.addProperty("type", w.type());
            wallList.add(wo);
        }
        walls.add("list", wallList);
        o.add("walls", walls);

        // 房间 / 空腔
        JsonArray rooms = new JsonArray();
        for (ScanResult.Room rm : sr.rooms) {
            JsonObject ro = new JsonObject();
            ro.addProperty("volume", rm.volume());
            ro.addProperty("floor_y", rm.floorY());
            ro.addProperty("ceil_y", rm.ceilY());
            if (rm.purpose() != null && !rm.purpose().isEmpty()) {
                ro.addProperty("purpose", rm.purpose());
            }
            rooms.add(ro);
        }
        o.add("rooms", rooms);
        o.addProperty("cavities", sr.cavities);
        o.addProperty("pillars", sr.pillarCount);
        o.addProperty("beams", sr.beamCount);
        JsonObject sym = new JsonObject();
        sym.addProperty("x", sr.symmetryX);
        sym.addProperty("z", sr.symmetryZ);
        o.add("symmetry", sym);

        JsonObject contents = new JsonObject();
        for (Map.Entry<String, Integer> e : sr.contents.entrySet()) {
            contents.addProperty(e.getKey(), e.getValue());
        }
        o.add("contents", contents);
        o.addProperty("redstone", sr.hasRedstone);
        o.addProperty("water", sr.waterCount);
        o.addProperty("ground_y", sr.groundY);

        JsonArray mats = new JsonArray();
        for (String m : sr.dominantMaterials) {
            mats.add(m);
        }
        o.add("materials", mats);
        o.addProperty("material_complexity", sr.materialComplexity);
        o.addProperty("solidity", sr.solidity);
        o.addProperty("hang_ratio", sr.hangRatio);
        o.addProperty("aspect_height", sr.aspectHeight);
        o.addProperty("aspect_length", sr.aspectLength);
        // ---- P2 形态/表面/材质位置特征 ----
        o.addProperty("section_shape", sr.sectionShape);
        o.addProperty("silhouette", sr.silhouette);
        o.addProperty("roughness", sr.roughness);
        o.addProperty("surface_ratio", sr.surfaceRatio);
        o.addProperty("exposed_surface", sr.exposedSurface);
        JsonObject cen = new JsonObject();
        cen.addProperty("dx", sr.centroidDX);
        cen.addProperty("dy", sr.centroidDY);
        cen.addProperty("dz", sr.centroidDZ);
        o.add("centroid_offset", cen);
        o.addProperty("layer_area_var", sr.layerAreaVar);
        o.addProperty("top_material", sr.topMaterial);
        o.addProperty("base_material", sr.baseMaterial);
        o.addProperty("material_layer_changes", sr.materialLayerChanges);
        o.addProperty("protrusions", sr.protrusions);
        o.addProperty("non_full_count", sr.nonFullCount);
        o.addProperty("nature_count", sr.natureCount);
        // ---- P3 色彩特征 ----
        o.addProperty("color_count", sr.colorCount);
        o.addProperty("color_richness", sr.colorRichness);
        o.addProperty("dominant_color", sr.dominantColor);
        o.addProperty("secondary_color", sr.secondaryColor);
        o.addProperty("warm_ratio", sr.warmRatio);
        o.addProperty("brightness", sr.brightness);
        o.addProperty("saturation", sr.saturation);
        JsonArray palette = new JsonArray();
        for (String c : sr.palette) {
            palette.add(c);
        }
        o.add("palette", palette);
        return o;
    }

    /** 评分结果 → JSON（结构/装饰/配色 + 总分/等级/评语 + 子项分）。 */
    private JsonObject scoreJson(ScoreResult sc) {
        JsonObject o = new JsonObject();
        o.addProperty("structure", round2(sc.structure));
        o.addProperty("decorate", round2(sc.decorate));
        o.addProperty("color", round2(sc.color));
        o.addProperty("total", round2(sc.total));
        o.addProperty("grade", sc.grade);
        o.addProperty("comment", sc.comment);
        JsonArray sugg = new JsonArray();
        for (String sg : sc.suggestions) {
            sugg.add(sg);
        }
        o.add("suggestions", sugg);
        JsonObject sub = new JsonObject();
        sub.addProperty("proportion", round2(sc.proportion));
        sub.addProperty("solidity", round2(sc.solidity));
        sub.addProperty("support", round2(sc.support));
        sub.addProperty("detail", round2(sc.detail));
        sub.addProperty("facade", round2(sc.facade));
        sub.addProperty("material", round2(sc.material));
        sub.addProperty("layer", round2(sc.layer));
        sub.addProperty("conflict", round2(sc.conflict));
        o.add("sub", sub);
        return o;
    }

    private static double round2(double v) {
        return Math.round(v * 100.0) / 100.0;
    }

    /** 意图预测 → JSON 对象（进行中建筑的关键信息，供桌宠端播报/去重）。 */
    private JsonObject intentJson(IntentResult ir) {
        JsonObject o = new JsonObject();
        o.addProperty("category", ir.category);
        o.addProperty("label", ir.label);
        o.addProperty("phase", ir.phase);
        o.addProperty("completion", ir.completion);
        o.addProperty("confidence", ir.confidence);
        o.addProperty("hint", ir.hint);
        o.addProperty("complete", ir.complete);
        o.addProperty("strong_signal", ir.strongSignal);
        return o;
    }

    private void write(JsonObject json) {
        try {
            Files.createDirectories(buildingsDir);
            Files.write(buildingsDir.resolve("latest.json"),
                    json.toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException ignored) {
        }
    }
}
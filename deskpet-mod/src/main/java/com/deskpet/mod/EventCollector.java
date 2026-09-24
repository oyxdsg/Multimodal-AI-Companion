package com.deskpet.mod;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 事件收集与聚合：收集所有 PlayerAction，每 20 秒聚合成分级窗口写入文件。
 *
 * 聚合规则：
 * - DEATH / ADVANCEMENT / DIMENSION / JOIN / LEAVE：单独保留 → CRITICAL
 * - CHAT：保留玩家原始发言（最多最近 5 条）→ CRITICAL
 * - BREAK / ATTACK：按目标计数 → 汇总成 summary
 * - MOVE：累计距离 → 汇总进 summary
 */
public class EventCollector {
    private final List<PlayerAction> actions =
            Collections.synchronizedList(new ArrayList<>());
    private final AtomicLong moveDistance = new AtomicLong();
    private final WindowSink sink;

    public EventCollector(WindowSink sink) {
        this.sink = sink;
    }

    public void add(PlayerAction action) {
        actions.add(action);
    }

    /** 累计移动距离（米）。 */
    public void addMove(long meters) {
        moveDistance.addAndGet(meters);
    }

    /** 获得物品聚合计数（按 物品|来源 合并）。 */
    private record ItemGainCount(String player, int count) {}

    /** 受伤聚合计数（按 伤害来源 合并）。 */
    private record DamageCount(String player, int count) {}

    /** 20 秒窗口到点：聚合并输出。 */
    public void flush() {
        List<PlayerAction> snapshot;
        synchronized (actions) {
            snapshot = new ArrayList<>(actions);
            actions.clear();
        }
        long dist = moveDistance.getAndSet(0);

        Map<String, Integer> breaks = new HashMap<>();
        Map<String, Integer> kills = new HashMap<>();
        Map<String, Integer> uses = new HashMap<>();
        Map<String, DamageCount> damages = new HashMap<>();
        Map<String, ItemGainCount> gains = new HashMap<>();
        List<PlayerAction> criticals = new ArrayList<>();
        List<String> chats = new ArrayList<>();

        for (PlayerAction a : snapshot) {
            switch (a.type()) {
                case BREAK -> breaks.merge(a.target(), 1, Integer::sum);
                case KILL -> kills.merge(a.target(), 1, Integer::sum);
                case USE_ITEM -> uses.merge(a.target(), 1, Integer::sum);
                case DAMAGE -> {
                    DamageCount cur = damages.get(a.target());
                    damages.put(a.target(), cur == null
                            ? new DamageCount(a.player(), 1)
                            : new DamageCount(cur.player(), cur.count() + 1));
                }
                case ITEM_GAIN -> {
                    String key = a.target() + "\u0001" + (a.detail() == null ? "" : a.detail());
                    ItemGainCount cur = gains.get(key);
                    gains.put(key, cur == null
                            ? new ItemGainCount(a.player(), 1)
                            : new ItemGainCount(cur.player(), cur.count() + 1));
                }
                case CHAT -> {
                    if (chats.size() < 5) {
                        chats.add(a.player() + ": " + a.detail());
                    }
                }
                default -> criticals.add(a);
            }
        }

        JsonArray hl = new JsonArray();
        for (PlayerAction a : criticals) {
            JsonObject o = new JsonObject();
            o.addProperty("type", a.type().name().toLowerCase());
            if (a.player() != null) o.addProperty("player", a.player());
            if (a.target() != null) o.addProperty("target", a.target());
            if (a.detail() != null && !a.detail().isEmpty()) {
                o.addProperty("detail", a.detail());
            }
            hl.add(o);
        }
        for (String chat : chats) {
            JsonObject o = new JsonObject();
            o.addProperty("type", "chat");
            o.addProperty("text", chat);
            hl.add(o);
        }
        for (Map.Entry<String, ItemGainCount> e : gains.entrySet()) {
            String key = e.getKey();
            int sep = key.indexOf('\u0001');
            String target = sep >= 0 ? key.substring(0, sep) : key;
            String how = sep >= 0 ? key.substring(sep + 1) : "";
            JsonObject o = new JsonObject();
            o.addProperty("type", "item_gain");
            o.addProperty("player", e.getValue().player());
            o.addProperty("target", target);
            if (!how.isEmpty()) {
                o.addProperty("detail", how);
            }
            if (e.getValue().count() > 1) {
                o.addProperty("count", e.getValue().count());
            }
            hl.add(o);
        }
        for (Map.Entry<String, DamageCount> e : damages.entrySet()) {
            JsonObject o = new JsonObject();
            o.addProperty("type", "damage");
            o.addProperty("player", e.getValue().player());
            o.addProperty("target", e.getKey());
            if (e.getValue().count() > 1) {
                o.addProperty("count", e.getValue().count());
            }
            hl.add(o);
        }

        StringBuilder summary = new StringBuilder();
        if (!breaks.isEmpty()) {
            summary.append("挖掘:").append(joinCounts(breaks)).append("; ");
        }
        if (!kills.isEmpty()) {
            summary.append("击杀:").append(joinCounts(kills)).append("; ");
        }
        if (!uses.isEmpty()) {
            summary.append("使用:").append(joinCounts(uses)).append("; ");
        }
        if (dist > 100) {
            summary.append("移动:").append(dist).append("米; ");
        }
        if (summary.length() > 0) {
            JsonObject o = new JsonObject();
            o.addProperty("type", "summary");
            o.addProperty("detail", summary.toString().trim());
            hl.add(o);
        }

        JsonObject win = new JsonObject();
        win.addProperty("window", 20);
        win.addProperty("ts", System.currentTimeMillis());
        win.addProperty("importance",
                criticals.isEmpty() && chats.isEmpty() && damages.isEmpty()
                        ? (hl.size() > 0 ? "NORMAL" : "LOW")
                        : "CRITICAL");
        win.add("highlights", hl);

        try {
            sink.write(win);
        } catch (IOException ignored) {
        }
    }

    private static String joinCounts(Map<String, Integer> map) {
        return map.entrySet().stream()
                .map(e -> e.getValue() + " " + e.getKey())
                .reduce((a, b) -> a + "," + b)
                .orElse("");
    }
}
package com.deskpet.mod.build;

import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * 玩家方块数据持久化记录器（供未来优化使用，**不会随日志清理而丢失**）。
 *
 * - 直接拦截"放置/破坏"事件落盘（不依赖易轮转清理的 latest.log）。
 * - 追加写入 deskpet/blocks/YYYYMMDD.jsonl，**按天分片、永不删除**。
 * - 每条记录含过滤标记 by_player=true、动作(place/break)、坐标、方块、时间、世界、项目。
 * - 写入在独立 daemon 线程 + 有界队列异步批量落盘，主线程只 offer（O(1)），不阻塞服务端 tick。
 */
public class BlockRecorder {
    private static final Logger LOGGER = LoggerFactory.getLogger("deskpet-blocks");
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final int QUEUE_CAP = 100_000;

    private final Path dir;
    private final Deque<String> queue = new ArrayDeque<>();
    private final Thread flushThread;
    private volatile boolean running = true;
    private volatile String currentDay = "";
    private volatile BufferedWriter out = null;

    public BlockRecorder(Path dir) {
        this.dir = dir;
        this.flushThread = new Thread(this::loop, "deskpet-block-recorder");
        this.flushThread.setDaemon(true);
        this.flushThread.start();
    }

    /** 记录一个玩家方块事件（主线程调用，O(1)，不阻塞）。
     *  只存核心字段：时间/世界/项目/坐标/方块id/动作；材质、颜色、功能由 registry/blocks.json 按 id 查表派生。 */
    public void record(long ts, String world, String projectId,
                       int x, int y, int z, int blockId, String action) {
        JsonObject o = new JsonObject();
        o.addProperty("ts", ts);
        o.addProperty("world", world);
        o.addProperty("project", projectId == null || projectId.isEmpty() ? "" : projectId);
        o.addProperty("x", x);
        o.addProperty("y", y);
        o.addProperty("z", z);
        o.addProperty("id", blockId);
        o.addProperty("action", action);
        o.addProperty("by_player", true);   // 过滤标记：玩家放置/破坏，长期保留
        String line = o.toString();
        synchronized (queue) {
            if (queue.size() < QUEUE_CAP) {
                queue.add(line);
            } else {
                LOGGER.warn("[blocks] queue full, dropping 1 block record");
            }
        }
        synchronized (this) {
            this.notifyAll();
        }
    }

    /** 唤醒落盘线程（20s/休眠窗口调用，确保数据及时落盘）。 */
    public void flush() {
        synchronized (this) {
            this.notifyAll();
        }
    }

    public void close() {
        running = false;
        synchronized (this) {
            this.notifyAll();
        }
        try {
            flushThread.join(2000);
        } catch (InterruptedException ignored) {
        }
        closeWriter();
    }

    private void loop() {
        while (running) {
            try {
                ensureWriter(LocalDate.now().format(DAY));
                while (true) {
                    String line;
                    synchronized (queue) {
                        line = queue.poll();
                    }
                    if (line == null) {
                        break;
                    }
                    out.write(line);
                    out.newLine();
                }
                if (out != null) {
                    out.flush();
                }
                synchronized (this) {
                    this.wait(5000);
                }
            } catch (InterruptedException ignored) {
            } catch (IOException e) {
                LOGGER.error("[blocks] write error", e);
                closeWriter();
                try {
                    Thread.sleep(2000);
                } catch (InterruptedException ignored) {
                }
            }
        }
    }

    private void ensureWriter(String day) throws IOException {
        if (!day.equals(currentDay)) {
            closeWriter();
            currentDay = day;
            Files.createDirectories(dir);
            Path p = dir.resolve(day + ".jsonl");
            out = Files.newBufferedWriter(p, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND,
                    StandardOpenOption.WRITE);
        }
    }

    private void closeWriter() {
        if (out != null) {
            try {
                out.close();
            } catch (IOException ignored) {
            }
            out = null;
        }
    }
}

package com.deskpet.mod;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.stream.Stream;

/**
 * 窗口事件输出：按分钟分片写入独立目录 deskpet/（不与游戏 logs/ 混用）。
 * 每分钟轮转一个新文件；每分钟清理 2 分钟前的旧文件（保留当前 + 上一分钟）。
 */
public class WindowSink {
    private static final DateTimeFormatter FMT =
            DateTimeFormatter.ofPattern("yyyyMMdd-HHmm");

    private final Path dir;
    private String currentMinute = "";
    private Writer writer;

    public WindowSink(Path dir) {
        this.dir = dir;
    }

    /** 追加写入一个窗口 JSON。 */
    public synchronized void write(JsonObject window) throws IOException {
        String minute = LocalDateTime.now().format(FMT);
        if (!minute.equals(currentMinute)) {
            close();
            currentMinute = minute;
            Files.createDirectories(dir);
            writer = Files.newBufferedWriter(
                    dir.resolve(minute + ".jsonl"),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            cleanup();
        }
        writer.write(window.toString());
        writer.write('\n');
        writer.flush();
    }

    public synchronized void close() {
        if (writer != null) {
            try {
                writer.close();
            } catch (IOException ignored) {
            }
            writer = null;
        }
    }

    /** 删除 2 分钟前的 .jsonl 文件。 */
    private void cleanup() {
        long cutoff = System.currentTimeMillis() - 2 * 60 * 1000L;
        try (Stream<Path> paths = Files.list(dir)) {
            paths.filter(p -> p.toString().endsWith(".jsonl"))
                 .filter(p -> {
                     try {
                         return Files.getLastModifiedTime(p).toMillis() < cutoff;
                     } catch (IOException e) {
                         return false;
                     }
                 })
                 .forEach(p -> {
                     try {
                         Files.deleteIfExists(p);
                     } catch (IOException ignored) {
                     }
                 });
        } catch (IOException ignored) {
        }
    }
}
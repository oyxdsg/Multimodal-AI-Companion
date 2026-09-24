package com.deskpet.mod;

import com.google.gson.JsonObject;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.PlayerChatMessage;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.levelgen.FlatLevelSource;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 客户端入口：监听玩家聊天 + 服务器广播 + 采集环境状态（覆盖单机 + 多人服务器）。
 *
 * 多人时服务端类不会被客户端加载，服务端事件采集不到，因此：
 * - CHAT：玩家聊天（标准聊天包），sender 可能为 null（离线/未签名）；
 * - GAME：服务器广播的系统消息（击杀/死亡/获得物品等），做广播解析：
 *   去除颜色码 → 超长/装饰/噪声消息过滤 → 按关键词分类保留；
 * - 环境状态：连接服务器名、Hypixel 小游戏模式、单机世界类型（超平坦等），
 *   周期写入 deskpet/state.json，供桌宠组装 AI 消息时注入上下文。
 * 由客户端 tick 周期 flush 落盘（多人无内嵌服务器，服务端 tick 不触发）。
 */
public class DeskpetModClient implements ClientModInitializer {

    // 常见聊天显示格式：<玩家名> 内容 / 玩家名: 内容（Hypixel 等服务器聊天格式）
    private static final Pattern CHAT_LINE = Pattern.compile("^<([^<>]+)>[ ]?(.*)$");
    private static final Pattern NAME_COLON = Pattern.compile(
            "^([A-Za-z0-9_]{1,16})\\s*[:：]\\s*(.*)$");
    // 颜色码（§0-§f、§k-§o、§r），去除后得到纯文本
    private static final Pattern COLOR_CODES = Pattern.compile("§[0-9a-fk-orA-FK-OR]");
    // 超长消息（分隔线 / Reward Summary / 游戏说明等噪声）直接过滤
    private static final int MAX_MSG_LEN = 150;
    // 纯装饰分隔线（如 ▬▬▬▬▬），去色码后只剩重复符号
    private static final Pattern DECOR_LINE = Pattern.compile(
            "^([^\\p{L}\\p{N}])\\1{5,}$");
    // 噪声关键词：token奖励 / 广告 / 提示开关 / 进大厅 / 加入离开 / 倒计时 / 排行等无价值消息直接丢弃
    private static final Pattern NOISE_RE = Pattern.compile(
            "硬币|\\btokens?\\b|Network Booster|store\\.hypixel\\.net|点击这里|尚未领取|点击查看|"
            + "将在\\d+秒后|会在\\d+秒后|\\d+秒后爆炸|下个灾难|灾难将在\\d+秒后|进入了大厅|"
            + "正在前往|加入了游戏|离开了游戏|togglehints|banned|watchdog|blacklisted|"
            + "成为杀手几率|成为侦探几率|\\d+秒后?将你传送|传送至大厅|已自动排队|点击红色小床|"
            + "使用/[a-z]+以禁用|第一名|第二名|第三名|你拥有\\d+x|\\bleft\\.|\\brejoined\\.",
            Pattern.CASE_INSENSITIVE);
    // 短时间内重复广播去重（Hypixel 会把同一条提示连续广播多次）
    private final java.util.Map<String, Long> recentBroadcast = new java.util.HashMap<>();
    private static final long DEDUP_MS = 20_000L;

    // 广播分类（中英文，Hypixel 常见句式）
    private static final Pattern KILL_RE = Pattern.compile(
            "被\\s*\\S+\\s*(?:击杀|杀死|终结|击败)|\\b(?:was killed|was slain|killed by)\\b",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern DEATH_RE = Pattern.compile(
            "摔落|摔了下来|摔下|落地过猛|坠入|坠落|被绊倒|炸死|烧死|淹死|窒息|炸|死|"
            + "\\b(?:died|drowned|fell from|blew up|suffocated|burned to death)\\b",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern GAIN_RE = Pattern.compile(
            "获得|得到|拿到|收到|获取|\\b(?:earned|received|obtained|gained)\\b",
            Pattern.CASE_INSENSITIVE);

    // ---- 环境状态（写入 deskpet/state.json 供桌宠注入 AI 上下文） ----
    private String currentServer = "";
    private String currentMode = "";
    private String currentWorldType = "";
    private String currentGame = "";   // 当前小游戏/主模式（sidebar 或消息识别）

    // sidebar 标题 → 标准游戏 key（对应桌宠 config.HYPIXEL_GAMES）
    private static final String[][] SIDEBAR_GAME_MAP = {
            {"bedwars", "BEDWARS"}, {"skywars", "SKYWARS"}, {"murder mystery", "MURDER_MYSTERY"},
            {"arcade", "ARCADE"}, {"uhc", "UHC"}, {"arena", "ARENA"},
            {"build battle", "BUILD_BATTLE"}, {"cops and crims", "COPS_AND_CRIMS"},
            {"duels", "DUELS"}, {"mega walls", "MEGA_WALLS"}, {"paintball", "PAINTBALL"},
            {"quakecraft", "QUAKECRAFT"}, {"blitz", "BLITZ"}, {"smash heroes", "SMASH_HEROES"},
            {"tnt games", "TNT_GAMES"}, {"tnt", "TNT_GAMES"}, {"turbo kart", "TURBO_KART"},
            {"vampirez", "VAMPIREZ"}, {"the walls", "WALLS"}, {"warlords", "WARLORDS"},
            {"skyblock", "SKYBLOCK"},
    };
    // 消息关键词 → 具体小游戏/模式（比 sidebar 更精确，命中时覆盖）
    private static final String[][] GAME_KEYWORDS = {
            {"灾难模拟器", "DISASTERS"}, {"空岛战争", "SKYWARS"}, {"起床战争", "BEDWARS"},
            {"床战", "BEDWARS"}, {"密室杀手", "MURDER_MYSTERY"}, {"街机", "ARCADE"},
            {"派对游戏", "PARTY_GAMES"}, {"僵尸", "ZOMBIES"}, {"躲猫猫", "HIDE_AND_SEEK"},
            {"足球", "FOOTBALL"}, {"赏金猎人", "BOUNTY_HUNTERS"}, {"建筑大赛", "BUILD_BATTLE"},
            {"决斗", "DUELS"}, {"巨型城墙", "MEGA_WALLS"}, {"彩弹战争", "PAINTBALL"},
            {"雷神之锤", "QUAKECRAFT"}, {"闪电生存", "BLITZ"}, {"超级英雄", "SMASH_HEROES"},
            {"吸血鬼", "VAMPIREZ"}, {"卡丁车", "TURBO_KART"}, {"战争领主", "WARLORDS"},
            {"空岛生存", "SKYBLOCK"}, {"警察与罪犯", "COPS_AND_CRIMS"}, {"城墙", "WALLS"},
            {"UHC", "UHC"},
            {"party games", "PARTY_GAMES"}, {"murder mystery", "MURDER_MYSTERY"},
            {"skywars", "SKYWARS"}, {"bedwars", "BEDWARS"},
            {"build battle", "BUILD_BATTLE"}, {"skyblock", "SKYBLOCK"},
    };

    @Override
    public void onInitializeClient() {
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> {
            ServerData sd = client.getCurrentServer();
            currentServer = sd != null ? sd.name + " (" + sd.ip + ")" : "单机";
            currentMode = "";
            updateEnvironment();
        });
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
            currentServer = "";
            currentMode = "";
            currentWorldType = "";
            writeState();
        });

        ClientReceiveMessageEvents.CHAT.register((message, signedMessage, sender, boundChatType, timestamp) -> {
            if (DeskpetMod.INSTANCE == null) {
                return;
            }
            String content = null;
            if (signedMessage != null) {
                content = signedMessage.signedContent();
            }
            if (content == null || content.isEmpty()) {
                content = message.getString();
            }
            String player = sender != null ? sender.name() : "";
            // 无签名玩家：尝试从显示文本提取 <玩家名>
            if (player.isEmpty()) {
                String[] parsed = extractChatLine(content);
                if (parsed != null) {
                    player = parsed[0];
                    content = parsed[1];
                }
            }
            recordChat(player, content);
        });

        // 广播解析：服务器系统消息（击杀/死亡/获得等），去除颜色码 + 长度过滤后分类保留
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> {
            if (DeskpetMod.INSTANCE == null) {
                return;
            }
            String text = COLOR_CODES.matcher(message.getString())
                    .replaceAll("")
                    .replace("\\n", "")
                    .replace("\n", "")
                    .trim();
            if (text.isEmpty()) {
                return;
            }
            // 玩家闲聊（<名字> 内容 / 名字: 内容）：无条件保留，不走广播过滤
            String[] chatLine = extractChatLine(text);
            if (chatLine != null) {
                recordChat(chatLine[0], chatLine[1]);
                return;
            }
            if (text.length() > MAX_MSG_LEN) {
                return;   // 超长噪声消息（分隔线 / 奖励汇总 / 玩法说明）过滤
            }
            if (DECOR_LINE.matcher(text).matches()) {
                return;   // 纯装饰分隔线（▬▬▬▬▬ 等）
            }
            if (NOISE_RE.matcher(text).find()) {
                return;   // 噪声关键词（token/广告/奖励/进大厅/加入离开/公告）过滤
            }
            if (isDup(text)) {
                return;   // 同一消息短时间内重复广播，只保留一次
            }
            // 从消息识别当前小游戏/主模式（进游戏欢迎语等），比 sidebar 更精确
            detectGameFromMessage(text);
            String player = "";
            String[] parsed = extractChatLine(text);
            if (parsed != null) {
                player = parsed[0];
                text = parsed[1];
            }
            String kind = "chat";
            if (KILL_RE.matcher(text).find()) {
                kind = "kill";
            } else if (DEATH_RE.matcher(text).find()) {
                kind = "death";
            } else if (GAIN_RE.matcher(text).find()) {
                kind = "item_gain";
            }
            DeskpetMod.INSTANCE.onBroadcast(player, text, kind);
        });

        // 周期 flush + 环境状态更新：多人无内嵌服务器时服务端 tick 不触发，
        // 由客户端 tick 每 20 秒把采集到的事件落盘、刷新环境信息
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (DeskpetMod.INSTANCE != null) {
                DeskpetMod.INSTANCE.onClientTick();
                updateEnvironment();
            }
        });
    }

    /** 每 20 秒：刷新世界类型（单机超平坦检测）+ 当前游戏，并写环境状态文件。 */
    private void updateEnvironment() {
        Minecraft mc = Minecraft.getInstance();
        // server 兜底：JOIN 事件若未及时设置（getCurrentServer 为 null），这里补上
        if (currentServer.isEmpty()) {
            ServerData sd = mc.getCurrentServer();
            if (sd != null) {
                currentServer = sd.name + " (" + sd.ip + ")";
            }
        }
        if (mc.isLocalServer()) {            // 单机（内嵌服务器）才能拿到生成器
            IntegratedServer server = mc.getSingleplayerServer();
            if (server != null) {
                ServerLevel world = server.overworld();
                if (world != null) {
                    currentWorldType = world.getChunkSource().getGenerator()
                            instanceof FlatLevelSource ? "超平坦" : "普通";
                }
            }
        }
        updateGameFromScoreboard();
        writeState();
    }

    /** 从 Hypixel 侧边记分板标题识别当前主游戏（如 SKYWARS / Arcade）。 */
    private void updateGameFromScoreboard() {
        try {
            Minecraft mc = Minecraft.getInstance();
            if (mc.level == null) {
                return;
            }
            net.minecraft.world.scores.Objective obj = mc.level.getScoreboard()
                    .getDisplayObjective(net.minecraft.world.scores.DisplaySlot.SIDEBAR);
            if (obj == null) {
                return;
            }
            String title = COLOR_CODES.matcher(obj.getDisplayName().getString())
                    .replaceAll("").trim().toLowerCase();
            if (title.isEmpty()) {
                return;
            }
            String game = "";
            for (String[] m : SIDEBAR_GAME_MAP) {
                if (title.contains(m[0])) {
                    game = m[1];
                    break;
                }
            }
            if (!game.isEmpty() && !game.equals(currentGame)) {
                currentGame = game;
            }
        } catch (Exception ignored) {
        }
    }

    /** 消息里出现具体小游戏名时，覆盖为更精确的游戏（优先于 sidebar 的主游戏）。 */
    private void detectGameFromMessage(String text) {
        for (String[] m : GAME_KEYWORDS) {
            if (text.contains(m[0])) {
                if (!currentGame.equals(m[1])) {
                    currentGame = m[1];
                    writeState();
                }
                return;
            }
        }
    }

    private void writeState() {
        try {
            Path dir = FabricLoader.getInstance().getGameDir().resolve("deskpet");
            Files.createDirectories(dir);
            JsonObject o = new JsonObject();
            o.addProperty("server", currentServer);
            o.addProperty("mode", currentMode);
            o.addProperty("world_type", currentWorldType);
            o.addProperty("game", currentGame);
            Files.write(dir.resolve("state.json"),
                    o.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) {
        }
    }

    /** 从聊天行提取 (玩家名, 内容)；支持 <名字> 内容 与 名字: 内容；非聊天格式返回 null。 */
    private static String[] extractChatLine(String text) {
        if (text == null || text.isEmpty()) {
            return null;
        }
        Matcher m = CHAT_LINE.matcher(text);
        if (m.matches()) {
            return new String[]{m.group(1), m.group(2)};
        }
        Matcher m2 = NAME_COLON.matcher(text);
        if (m2.matches()) {
            return new String[]{m2.group(1), m2.group(2)};
        }
        return null;
    }

    /** 去重：同一文本在 DEDUP_MS 内只保留一次（Hypixel 提示/广播常连续重复多次）。 */
    private boolean isDup(String text) {
        long now = System.currentTimeMillis();
        recentBroadcast.entrySet().removeIf(e -> now - e.getValue() > DEDUP_MS);
        if (recentBroadcast.containsKey(text)) {
            return true;
        }
        recentBroadcast.put(text, now);
        return false;
    }

    private static void recordChat(String player, String content) {
        if (content == null || content.isEmpty()) {
            return;
        }
        DeskpetMod.INSTANCE.onPlayerChat(player, content);
    }
}
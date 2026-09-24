package com.deskpet.mod;

import com.deskpet.mod.build.BlockRegistryExport;
import com.deskpet.mod.build.BuildingAnalyzer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.entity.event.v1.ServerEntityLevelChangeEvents;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.UseItemCallback;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * DeskPet Mod 入口：注册事件监听，把玩家行为聚合输出到 .minecraft/deskpet/。
 */
public class DeskpetMod implements ModInitializer {
    public static final Logger LOGGER = LoggerFactory.getLogger("deskpet-mod");
    public static DeskpetMod INSTANCE;

    private EventCollector collector;
    private BuildingAnalyzer buildingAnalyzer;
    private int tick = 0;
    private final Map<UUID, Vec3> lastMovePos = new HashMap<>();
    private final Map<UUID, Long> moveAcc = new HashMap<>();

    @Override
    public void onInitialize() {
        INSTANCE = this;
        Path dataDir = FabricLoader.getInstance().getGameDir().resolve("deskpet");
        collector = new EventCollector(new WindowSink(dataDir));
        buildingAnalyzer = new BuildingAnalyzer(dataDir);
        LOGGER.info("DeskPet mod initialized, data dir: {}", dataDir);
        // 导出全量方块映射表（id/材质/颜色/功能），供离线分析脚本复用
        BlockRegistryExport.export(FabricLoader.getInstance().getGameDir().resolve("deskpet"));

        registerConnectionEvents();
        registerBlockEvents();
        registerCombatEvents();
        registerProgressEvents();
        registerMoveAndFlush();
        // 服务器停止/关闭时，把排队中的玩家方块数据全部落盘（不随日志清理而丢失）
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
            if (buildingAnalyzer != null) {
                buildingAnalyzer.close();
            }
        });
    }

    private void registerConnectionEvents() {
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) ->
                collector.add(new PlayerAction(name(handler.getPlayer()), ActionType.JOIN,
                        null, null, pos(handler.getPlayer()), now())));
        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) ->
                collector.add(new PlayerAction(name(handler.getPlayer()), ActionType.LEAVE,
                        null, null, pos(handler.getPlayer()), now())));
    }

    private void registerBlockEvents() {
        PlayerBlockBreakEvents.AFTER.register((world, player, blockPos, state, blockEntity) -> {
            collector.add(new PlayerAction(name(player), ActionType.BREAK,
                    state.getBlock().getName().getString(), null,
                    pos(player), now()));
            if (world instanceof ServerLevel sl) {
                buildingAnalyzer.onBlockBreak(sl, blockPos.getX(), blockPos.getY(),
                        blockPos.getZ(), now());
            }
        });
        UseItemCallback.EVENT.register((player, world, hand) -> {
            if (!player.getItemInHand(hand).isEmpty()) {
                collector.add(new PlayerAction(name(player), ActionType.USE_ITEM,
                        player.getItemInHand(hand).getItem().getName(player.getItemInHand(hand)).getString(),
                        null, pos(player), now()));
            }
            return InteractionResult.PASS;
        });
    }

    private void registerCombatEvents() {
        // 玩家死亡 / 玩家击杀生物：AFTER_DEATH 统一处理
        ServerLivingEntityEvents.AFTER_DEATH.register((entity, source) -> {
            if (entity instanceof Player p) {
                collector.add(new PlayerAction(name(p), ActionType.DEATH,
                        null, source.getLocalizedDeathMessage(p).getString(), pos(p), now()));
            } else {
                Entity attacker = source.getEntity();
                if (attacker instanceof Player killer) {
                    collector.add(new PlayerAction(name(killer), ActionType.KILL,
                            entity.getDisplayName().getString(), null,
                            pos(killer), now()));
                }
            }
        });
        // 玩家受到伤害：标注伤害来源（ALLOW_DAMAGE 在每次伤害请求时触发）
        ServerLivingEntityEvents.ALLOW_DAMAGE.register((entity, source, amount) -> {
            if (entity instanceof Player p) {
                collector.add(new PlayerAction(name(p), ActionType.DAMAGE,
                        damageSourceText(source), null, pos(p), now()));
            }
            return true;   // 不阻止伤害
        });
        ServerEntityLevelChangeEvents.AFTER_PLAYER_CHANGE_LEVEL.register(
                (player, origin, destination) -> {
                    collector.add(new PlayerAction(name(player), ActionType.DIMENSION,
                            null, destination.dimension().identifier().toString(),
                            pos(player), now()));
                });
    }

    /** 伤害来源文字：优先用攻击者名字，否则用来源描述（熔岩/坠落/魔法等）。 */
    private static String damageSourceText(DamageSource source) {
        Entity attacker = source.getEntity();
        if (attacker != null) {
            return attacker.getDisplayName().getString();
        }
        return source.getMsgId();
    }

    private void registerProgressEvents() {
        // 进度获得通过 mixin（PlayerAdvancementsMixin / ClientAdvancementsMixin）回调 onPlayerAdvancement
    }

    /** 每 tick：每秒累计玩家移动距离；每 20 秒把移动距离交给 collector（写入由客户端 tick flush）。
     * 同时驱动建筑识别调度（服务端 tick 单机/内嵌服务器触发）。 */
    private void registerMoveAndFlush() {
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            tick++;
            buildingAnalyzer.onServerTick(System.currentTimeMillis());
            if (tick % 20 == 0) {          // 每秒
                for (var p : server.getPlayerList().getPlayers()) {
                    Vec3 nowPos = p.getPosition(0.0F);
                    Vec3 last = lastMovePos.get(p.getUUID());
                    if (last != null) {
                        long meters = Math.round(last.distanceTo(nowPos));
                        moveAcc.merge(p.getUUID(), meters, Long::sum);
                    }
                    lastMovePos.put(p.getUUID(), nowPos);
                }
            }
            if (tick % 400 == 0) {         // 20 秒
                long total = 0;
                for (long d : moveAcc.values()) {
                    total += d;
                }
                moveAcc.clear();
                collector.addMove(total);
            }
        });
    }

    private int clientTick = 0;

    /** 由客户端入口（DeskpetModClient）的客户端 tick 周期调用。
     * 多人服务器没有内嵌服务器，服务端 tick 不触发，必须由客户端 tick
     * 周期 flush 把已采集的事件（聊天等客户端事件）落盘。 */
    public void onClientTick() {
        if (++clientTick % 400 == 0) {     // 20 秒
            collector.flush();
        }
    }

    /** 由聊天 mixin 调用。 */
    public void onPlayerChat(String player, String text) {
        collector.add(new PlayerAction(player, ActionType.CHAT, null,
                text, null, now()));
    }

    /** 由获得物品 mixin（拾取/合成）调用。 */
    public void onItemGain(String player, String itemName, String how) {
        collector.add(new PlayerAction(player, ActionType.ITEM_GAIN,
                itemName, how, null, now()));
    }

    /** 由客户端广播解析器（DeskpetModClient）调用：把服务器广播的消息
     * （击杀/死亡/获得物品/玩家聊天等系统消息）作为单独事件保留。
     * 单人时服务端事件已覆盖这些场景，广播只出现在多人服务器。 */
    public void onBroadcast(String player, String text, String kind) {
        collector.add(new PlayerAction(player, ActionType.BROADCAST,
                kind, text, null, now()));
    }

    /** 由进度 mixin（PlayerAdvancementsMixin / ClientAdvancementsMixin）调用。 */
    public void onPlayerAdvancement(String player, String title) {
        collector.add(new PlayerAction(player, ActionType.ADVANCEMENT, null,
                title, null, now()));
    }

    /** 由 BlockItemPlaceMixin 调用（仅服务端）：喂给建筑识别调度。 */
    public void onBlockPlaced(ServerLevel level, BlockPos pos, BlockState state, Player player) {
        int blockId = BuiltInRegistries.BLOCK.getId(state.getBlock());
        buildingAnalyzer.onBlockPlace(level, pos.getX(), pos.getY(), pos.getZ(),
                blockId, player, now());
    }

    /** 由实体/液体 mixin 调用（仅服务端）：记录虚拟 id（盔甲架/展示框/船等）。 */
    public void onBlockPlacedVirtual(ServerLevel level, BlockPos pos, int virtualId, Player player) {
        buildingAnalyzer.onBlockPlace(level, pos.getX(), pos.getY(), pos.getZ(),
                virtualId, player, now());
    }

    private static String name(Player p) {
        return p.getGameProfile().name();
    }

    private static int[] pos(Entity e) {
        return new int[]{e.getBlockX(), e.getBlockY(), e.getBlockZ()};
    }

    private static long now() {
        return System.currentTimeMillis();
    }
}
package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.state.BlockState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 监听玩家放置方块（服务端）：26.2 的 fabric-api 无 PlayerBlockPlacementEvents，
 * 注入 BlockItem.place（放置实际入口），仅 ServerLevel 记录（单机/局域网内嵌服务器；
 * 多人远程服务器的客户端预测不会记录）。
 */
@Mixin(BlockItem.class)
public abstract class BlockItemPlaceMixin {
    private static final Logger LOGGER = LoggerFactory.getLogger("deskpet-build");

    /** 构造方法注入：确认 mixin 已应用。 */
    @Inject(method = "<init>", at = @At("TAIL"))
    private void deskpet$init(CallbackInfo ci) {
        LOGGER.info("[building] BlockItemPlaceMixin applied");
    }

    @Inject(method = "place", at = @At("RETURN"))
    private void deskpet$afterPlace(BlockPlaceContext context,
                                    CallbackInfoReturnable<InteractionResult> cir) {
        InteractionResult result = cir.getReturnValue();
        if (result != InteractionResult.SUCCESS
                && result != InteractionResult.SUCCESS_SERVER
                && result != InteractionResult.CONSUME) {
            return;
        }
        Level level = context.getLevel();
        if (!(level instanceof ServerLevel serverLevel)) {
            return;   // 客户端预测/纯客户端不记录
        }
        Player player = context.getPlayer();
        if (player == null) {
            return;
        }
        // 26.2 中 BlockPlaceContext.getClickedPos() 即实际放置位置
        // （placeBlock 内部 setBlock(getClickedPos())，构造/updatePlacementContext 已偏移好），
        // 不要再 relative(clickedFace) 二次偏移
        BlockPos placed = context.getClickedPos();
        BlockState state = level.getBlockState(placed);
        if (state.isAir()) {
            LOGGER.info("[building] place skipped: {} at {} is air",
                    player.getName().getString(), placed);
            return;
        }
        if (DeskpetMod.INSTANCE != null) {
            DeskpetMod.INSTANCE.onBlockPlaced(serverLevel, placed, state, player);
            // 双格方块（门）：上半格由放置自动生成、无独立事件，补记保证方块集完整
            if (state.getBlock() instanceof DoorBlock) {
                BlockPos up = placed.above();
                BlockState upState = serverLevel.getBlockState(up);
                if (!upState.isAir()) {
                    DeskpetMod.INSTANCE.onBlockPlaced(serverLevel, up, upState, player);
                }
            }
        }
    }
}
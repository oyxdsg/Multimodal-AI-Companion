package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BucketItem;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.LiquidBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 水/熔岩桶：非 BlockItem，use(右键) 倒出液体。放置成功（点击位置非液体 =
 * 倒水场景）时记录新生成的液体方块。
 */
@Mixin(BucketItem.class)
public abstract class BucketItemMixin {

    @Inject(method = "use", at = @At("RETURN"))
    private void deskpet$afterUse(Level level, Player player, InteractionHand hand,
                                  CallbackInfoReturnable<InteractionResult> cir) {
        InteractionResult r = cir.getReturnValue();
        if (r != InteractionResult.SUCCESS
                && r != InteractionResult.SUCCESS_SERVER
                && r != InteractionResult.CONSUME) {
            return;
        }
        if (!(level instanceof ServerLevel sl)) {
            return;
        }
        if (player == null || DeskpetMod.INSTANCE == null) {
            return;
        }
        HitResult hit = player.pick(player.blockInteractionRange(), 0.0F, false);
        if (!(hit instanceof BlockHitResult bhr)
                || bhr.getType() != HitResult.Type.BLOCK) {
            return;
        }
        BlockPos clicked = bhr.getBlockPos();
        // 点击位置本身是液体 → 装水（非放置），跳过
        if (sl.getBlockState(clicked).getBlock() instanceof LiquidBlock) {
            return;
        }
        // 倒水：检查点击位置沿点击面处是否有新液体
        BlockPos cand = clicked.relative(bhr.getDirection());
        BlockState candState = sl.getBlockState(cand);
        if (candState.getBlock() instanceof LiquidBlock) {
            DeskpetMod.INSTANCE.onBlockPlaced(sl, cand, candState, player);
        }
    }
}
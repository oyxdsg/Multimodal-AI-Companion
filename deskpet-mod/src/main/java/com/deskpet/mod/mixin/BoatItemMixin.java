package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import com.deskpet.mod.build.BuildingVirtualIds;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BoatItem;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 船：非 BlockItem，use(右键水面) 生成实体。放置成功时按玩家点击目标记录位置。
 */
@Mixin(BoatItem.class)
public abstract class BoatItemMixin {

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
        BlockPos pos = (hit instanceof BlockHitResult bhr
                && bhr.getType() == HitResult.Type.BLOCK)
                ? bhr.getBlockPos() : player.blockPosition();
        DeskpetMod.INSTANCE.onBlockPlacedVirtual(sl, pos,
                BuildingVirtualIds.BOAT, player);
    }
}
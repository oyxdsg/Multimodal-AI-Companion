package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import com.deskpet.mod.build.BuildingVirtualIds;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.MinecartItem;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.level.Level;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 矿车：非 BlockItem，useOn 在轨道上生成实体。记录放置位置。
 */
@Mixin(MinecartItem.class)
public abstract class MinecartItemMixin {

    @Inject(method = "useOn", at = @At("RETURN"))
    private void deskpet$afterUseOn(UseOnContext context,
                                    CallbackInfoReturnable<InteractionResult> cir) {
        InteractionResult r = cir.getReturnValue();
        if (r != InteractionResult.SUCCESS
                && r != InteractionResult.SUCCESS_SERVER
                && r != InteractionResult.CONSUME) {
            return;
        }
        Level level = context.getLevel();
        if (!(level instanceof ServerLevel sl)) {
            return;
        }
        Player player = context.getPlayer();
        if (player == null || DeskpetMod.INSTANCE == null) {
            return;
        }
        DeskpetMod.INSTANCE.onBlockPlacedVirtual(sl, context.getClickedPos(),
                BuildingVirtualIds.MINECART, player);
    }
}
package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import com.deskpet.mod.build.BuildingVirtualIds;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ArmorStandItem;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.level.Level;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 盔甲架：非 BlockItem，useOn 直接生成实体（不经过 BlockItem.place）。
 * 放置成功时记录位置（实体通常放在点击方块上方）。
 */
@Mixin(ArmorStandItem.class)
public abstract class ArmorStandItemMixin {

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
        DeskpetMod.INSTANCE.onBlockPlacedVirtual(sl, context.getClickedPos().above(),
                BuildingVirtualIds.ARMOR_STAND, player);
    }
}
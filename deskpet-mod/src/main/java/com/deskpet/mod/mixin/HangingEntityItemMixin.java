package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import com.deskpet.mod.build.BuildingVirtualIds;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.HangingEntityItem;
import net.minecraft.world.item.ItemFrameItem;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.level.Level;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 物品展示框 / 画：继承 HangingEntityItem，useOn 生成挂墙实体（不经过
 * BlockItem.place）。放置成功时按实际物品区分记录，位置 = 点击位置沿点击面。
 */
@Mixin(HangingEntityItem.class)
public abstract class HangingEntityItemMixin {

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
        int virtualId = ((Object) this) instanceof ItemFrameItem
                ? BuildingVirtualIds.ITEM_FRAME : BuildingVirtualIds.PAINTING;
        DeskpetMod.INSTANCE.onBlockPlacedVirtual(sl,
                context.getClickedPos().relative(context.getClickedFace()), virtualId, player);
    }
}
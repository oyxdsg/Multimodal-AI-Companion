package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * 监听玩家拾取掉落物：注入 ItemEntity.playerTouch（服务端拾取逻辑）。
 * 成功拾取时实体被移除（isRemoved），此时记录「获得物品·拾取」。
 */
@Mixin(ItemEntity.class)
public abstract class ItemPickupMixin {

    @Inject(method = "playerTouch", at = @At("TAIL"))
    private void deskpet_onPickup(Player player, CallbackInfo ci) {
        ItemEntity self = (ItemEntity) (Object) this;
        if (DeskpetMod.INSTANCE != null
                && self.isRemoved()
                && !self.getItem().isEmpty()) {
            DeskpetMod.INSTANCE.onItemGain(
                    player.getGameProfile().name(),
                    self.getItem().getItem().getName(self.getItem()).getString(),
                    "拾取");
        }
    }
}
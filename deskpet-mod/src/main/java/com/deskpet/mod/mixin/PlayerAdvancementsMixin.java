package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.minecraft.advancements.Advancement;
import net.minecraft.advancements.AdvancementHolder;
import net.minecraft.resources.Identifier;
import net.minecraft.server.PlayerAdvancements;
import net.minecraft.server.level.ServerPlayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 监听玩家获得进度（服务端，单机/内嵌服务器）：Fabric API 无此事件，
 * 用 mixin 注入 PlayerAdvancements.award。
 * 过滤纯配方进度（recipes/*）：价值低且刷屏，不记录。
 */
@Mixin(PlayerAdvancements.class)
public abstract class PlayerAdvancementsMixin {

    @Shadow
    private ServerPlayer player;

    @Inject(method = "award", at = @At("HEAD"))
    private void deskpet_onAward(AdvancementHolder advancement,
                                 String criterionKey,
                                 CallbackInfoReturnable<Boolean> cir) {
        if (DeskpetMod.INSTANCE == null) {
            return;
        }
        // 跳过纯配方进度（解锁配方类），保留真正的进度/成就/挑战
        Identifier advId = advancement.id();
        if (advId.getNamespace().equals("minecraft")
                && advId.getPath().startsWith("recipes/")) {
            return;
        }
        String title = Advancement.name(advancement).getString();
        DeskpetMod.INSTANCE.onPlayerAdvancement(
                player.getGameProfile().name(), title);
    }
}
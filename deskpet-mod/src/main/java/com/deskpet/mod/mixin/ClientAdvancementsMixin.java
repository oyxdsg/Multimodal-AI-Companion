package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.fabricmc.api.EnvType;
import net.fabricmc.api.Environment;
import net.minecraft.advancements.Advancement;
import net.minecraft.advancements.AdvancementHolder;
import net.minecraft.advancements.AdvancementProgress;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientAdvancements;
import net.minecraft.resources.Identifier;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * 客户端进度监听（兼容多人服务器 + 单机）。
 * 多人客户端不加载服务端类 PlayerAdvancements，原服务端 mixin 不会触发；
 * 本 mixin 混入客户端等价物 ClientAdvancements，在 update 全量同步后
 * 按已完成进度 diff 判断新增（首次同步只建立基线，不上报历史进度）。
 * 自动过滤纯配方进度（recipes/*）。
 */
@Environment(EnvType.CLIENT)
@Mixin(ClientAdvancements.class)
public abstract class ClientAdvancementsMixin {

    @Shadow
    @Final
    private Map<AdvancementHolder, AdvancementProgress> progress;

    private final Set<Identifier> deskpet_done = new HashSet<>();
    private boolean deskpet_initial = true;

    @Inject(method = "update", at = @At("TAIL"))
    private void deskpet_afterUpdate(CallbackInfo ci) {
        if (DeskpetMod.INSTANCE == null) {
            return;
        }
        // 首次全量同步：建立已完成基线，不上报历史进度
        if (deskpet_initial) {
            deskpet_initial = false;
            for (Map.Entry<AdvancementHolder, AdvancementProgress> e
                    : progress.entrySet()) {
                if (e.getValue() != null && e.getValue().isDone()) {
                    deskpet_done.add(e.getKey().id());
                }
            }
            return;
        }
        for (Map.Entry<AdvancementHolder, AdvancementProgress> e
                : progress.entrySet()) {
            if (e.getValue() == null || !e.getValue().isDone()) {
                continue;
            }
            Identifier id = e.getKey().id();
            if (!deskpet_done.add(id)) {
                continue;
            }
            // 跳过纯配方进度（解锁配方类），价值低且刷屏
            if (id.getNamespace().equals("minecraft")
                    && id.getPath().startsWith("recipes/")) {
                continue;
            }
            String title = Advancement.name(e.getKey()).getString();
            String player = "";
            if (Minecraft.getInstance().player != null) {
                player = Minecraft.getInstance().player
                        .getGameProfile().name();
            }
            DeskpetMod.INSTANCE.onPlayerAdvancement(player, title);
        }
    }
}
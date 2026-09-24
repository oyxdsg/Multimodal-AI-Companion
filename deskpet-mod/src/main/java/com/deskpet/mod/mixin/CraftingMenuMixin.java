package com.deskpet.mod.mixin;

import com.deskpet.mod.DeskpetMod;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.CraftingContainer;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.ResultContainer;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.RecipeHolder;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * 监听工作台（3x3）合成：注入 CraftingMenu.slotChangedCraftingGrid（服务端合成结果计算）。
 * 结果槽出现非空物品时记录「获得物品·合成」。
 */
@Mixin(CraftingMenu.class)
public abstract class CraftingMenuMixin {

    @Inject(method = "slotChangedCraftingGrid", at = @At("TAIL"))
    private static void deskpet_onCraftResult(
            AbstractContainerMenu handler, ServerLevel world, Player player,
            CraftingContainer input, ResultContainer result,
            RecipeHolder<CraftingRecipe> recipe, CallbackInfo ci) {
        if (DeskpetMod.INSTANCE == null || world.isClientSide()) {
            return;
        }
        ItemStack stack = result.getItem(0);
        if (!stack.isEmpty()) {
            DeskpetMod.INSTANCE.onItemGain(
                    player.getGameProfile().name(),
                    stack.getItem().getName(stack).getString(),
                    "合成");
        }
    }
}
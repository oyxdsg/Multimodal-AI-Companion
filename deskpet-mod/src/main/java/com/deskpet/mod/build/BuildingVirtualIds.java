package com.deskpet.mod.build;

/**
 * 非方块放置物（实体/挂墙物等）的虚拟方块 id 约定。
 * 这些放置不经过 BlockItem.place（无真实方块），用负数 id 标记，
 * 扫描时映射为功能物件分类。
 */
public final class BuildingVirtualIds {
    public static final int ARMOR_STAND = -1;
    public static final int ITEM_FRAME = -2;
    public static final int PAINTING = -3;
    public static final int MINECART = -4;
    public static final int BOAT = -5;

    private BuildingVirtualIds() {
    }
}
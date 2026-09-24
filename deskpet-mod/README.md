# DeskPet Mod（Fabric · Minecraft 26.2）

为桌面宠物（DeskPet）提供两大能力：
1. **游戏事件采集**：监听玩家行为 → 每 20 秒聚合成分级窗口写入 `.minecraft/deskpet/`，供桌宠联动 AI 互动。
2. **建筑识别系统**：记忆玩家放置的方块 → 三维结构分析 → 本地规则引擎分类 → 输出建筑感知包，供桌宠理解玩家建造了什么。

## 一、游戏事件采集

### 功能
- 监听：进入/离开世界、破坏方块、使用物品、击杀生物、获得物品（合成/拾取）、受到伤害（标注来源）、死亡、获得进度、跨维度、玩家聊天、移动距离
- 聚合：破坏/击杀/使用/移动按目标计数 → `summary`；获得物品/受伤按目标**合并计数为独立条目**；死亡/进度/维度/聊天单独保留
- 分级：有高价值事件（死亡/进度/维度/聊天/**受伤**）→ `CRITICAL`；只有普通活动 → `NORMAL`；空 → `LOW`
- 输出：按分钟分片 JSONL（`deskpet/YYYYMMDD-HHMM.jsonl`），每分钟自动清理 2 分钟前的文件

### 特殊放置采集（非 BlockItem 方块）
| 类型 | 实现 | 记录 |
|---|---|---|
| 放置方块 | mixin `BlockItem.place`（仅 ServerLevel） | 坐标 + 方块 id |
| 门（双格） | 补记 `placed.above()` | 上下两格 |
| 水/熔岩 | mixin `BucketItem.use` | 液体方块 |
| 盔甲架 | mixin `ArmorStandItem.useOn` | 虚拟 id（负数） |
| 物品展示框/画 | mixin `HangingEntityItem.useOn` | 虚拟 id |
| 矿车 | mixin `MinecartItem.useOn` | 虚拟 id |
| 船 | mixin `BoatItem.use` | 虚拟 id |

## 二、建筑识别系统

### 架构流程

```
玩家放置/破坏 ──→ 记忆模块 BlockStore（稀疏哈希，O(1)）
                      │  空间分离（AABB 膨胀 4 格，分离建筑自动切项目）
                      │  20s 窗口 / 60s 休眠
                      ▼
          单遍聚合 ScanAggregation（一次遍历收集全部统计指标）
                      │  结构元 + 美学元 + 评分输入
                      ▼
          三维扫描 + 意图推断 + 规则引擎 + 建筑评分
                      │  (WallSegmenter/RoomSegmenter/IntentEstimator/RuleEngine/BuildingScore)
                      ▼
          感知包 → latest.json(实时) + history.jsonl(完成归档)
          玩家方块 → blocks/*.jsonl(只记 id+坐标，查表还原)
          映射表   → registry/blocks.json + block_colors.json(真实纹理色)
```

### 生命周期
- **触发**：连通集群 ≥4 格（面接触）激活
- **空间分离**：新放置与当前项目 AABB 膨胀 4 格内 → 归同建筑；真正离开 → 完成当前项目开新项目
- **播报**：20s 窗口（有新方块）输出感知包；60s 无操作输出最终包并休眠释放内存

### 特征提取（扫描器）
| 类别 | 特征 |
|---|---|
| 基础 | AABB、体量比例、垂直分层、实心度、连通集群、层面积 |
| 结构 | **墙检测**（竖状连续同材质堆叠 → 相邻列聚类成面 → 内外标注）、柱、梁、对称度、密闭性（MC `isSolid` 读 world）、悬空率、水体 |
| 房间 | **RLE 段 + Union-Find 连通分量**（替代洪水填充）：内部空腔边界有门 → 房间；无门 → 空腔 |
| 屋顶 | 满铺平面层定位天花板 → flat / gabled（楼梯）/ pointed / dome / open（不适用） |
| 形态 | 水平截面形状（square/rect/elongated）、垂直剪影（cylinder/funnel/gourd/trumpet）、表面起伏、质心偏移、层面积方差、突起 |
| 材质 | 主导材质、复杂度、顶部/底部材质、材质分层切换 |
| 色彩 | 颜色丰富度、主/次色调、暖色占比、明度、饱和度、调色板（颜色来自真实纹理采样映射表 `registry/block_colors.json`；按木种/石种精确配色，樱花木=粉、黑石=黑等） |
| 物件 | 床/箱/熔炉/工作台/附魔台/酿造台/铁砧/门/栅栏/花盆/家具/玻璃/书架/光源/红石 + 实体类 |
| 农田/牧场 | 作物(crop)计数、干草垛(hay)计数、栅栏围合——农庄/田园特征 |
| 绿植/装饰 | 树叶/藤蔓/苔藓/花草(natureCount) 计作自然装饰；非完整方块(nonFullCount) 计作雕工 |

### 分类体系（本地规则引擎，分层判定）
```
像素画 → 高塔/尖塔/灯塔 → 拱门/桥 → 泳池/喷泉 → 农场/牧场/田园庄园 → 露天家园 → 城墙/边界墙
→ 地下建筑 → 雕像(人形/图腾柱/抽象) → 城堡/别墅/多层楼/谷仓/平房/小屋/火柴盒/房屋 → 复杂
```

### 风格推断（色彩 + 结构特征）
| 风格 | 判据 |
|---|---|
| 田园 | 玻璃采光(≥6) + 绿植(≥8)；或暖木 + 坡顶/露天 + 田园气息 |
| 现代 | 玻璃采光(≥6，明快)；或青色系 + 低饱和 |
| 冰雪 | 白 + 冷 + 亮 |
| 沙漠 | 沙质 + 暖 |
| 奇幻 | 紫/粉 + 悬空 |
| 哥特 | 暗 + 深灰/黑 + 尖顶/高耸 |
| 地中海 | 暖 + 亮 + 平顶 |
| 和风 | 木质 + 坡/尖顶 + 对称 |
| 中式 | 红/棕 + 暖 + 对称 |
| 乡村 | 木/土 + 暖 + 坡顶/露天 |
| 工业 | 灰 + 低饱和 + 红石 |
| 中世纪 | 城堡分类 |
| 默认 | 传统木构 / 传统石构 |

| 分类 | 判据 |
|---|---|
| 高塔 tower | 高宽比>2.5 + 分层≥3 |
| 尖塔 spire | 高塔 + 剪影 funnel（顶部收窄） |
| 灯塔 lighthouse | 高塔 + 顶部光源 |
| 城墙 wall / 边界墙 boundary | 长条(aspectLength≥3) + 无围合 + 高度 2~10 / ≤2 |
| 桥 bridge | 悬空率>0.5 + 长条 |
| 拱门 arch | 两柱+顶梁 + 悬空 |
| 泳池 pool / 喷泉 fountain | 含水 + 矮 / 中心柱 + 对称 |
| 像素画 pixel_art | 扁平(高≤2) + 材质多样 |
| 露天家园 open_base | 无围合(wall<0.15, roof<0.2) + 有生活物件（无任何外墙/内墙） |
| 农场 farm / 牧场 ranch / 庄园 estate | 围栏圈地 + 农田作物(≥8) / 干草垛(≥2) / 围栏≥12；农田+牧场→庄园 |
| 雕像 statue | 排除法（无房间+无功能+规模≥50），剪影细分：gourd→**人形**、cylinder细高→**图腾柱**、其他→抽象 |
| 地下建筑 underground | 最高层低于地表 2 格以上 |
| 城堡 castle | 高 + 突起（角塔）+ 房间 |
| 别墅 villa | 规模≥300 + 讲究（多房间/材质≥4/装饰≥4/对称） |
| 多层楼 multi_story | 房间≥2 |
| 谷仓 barn | 矮宽 + 大 |
| 平房 bungalow | 单层 + 矮 + 横向延展 |
| 小屋 cabin | 木质 + 规模<200 + 单房间 |
| 火柴盒 matchbox | 方正空心屋 + 仅门 + 普通建材（木/石/土）+ 材质单调 |
| 房屋 house | 兜底 |

### 感知包格式（`deskpet/buildings/latest.json`）
```json
{
  "type": "building", "window": 3, "reason": "progress",
  "project_id": "...", "anchor": [x,y,z], "world": "<seed>_minecraft:overworld",
  "dimensions": {"width":8,"depth":9,"height":13,"block_count":282,"min_y":-63,"max_y":-51},
  "classification": "一座哥特的木质尖塔",
  "category": "spire", "scale": "房屋", "style": "哥特",
  "roof_type": "pointed", "needs_ai": false,
  "state": "complete",
  "non_full_count": 164, "nature_count": 60,
  "intent": {
    "category": "tower", "label": "高塔", "phase": "top",
    "completion": 0.92, "confidence": 0.85,
    "hint": "堆起了石质高塔，顶部就快封好了~", "complete": true, "strong_signal": true
  },
  "score": {
    "structure": 53.0, "decorate": 61.6, "color": 77.9,
    "total": 63.0, "grade": "B", "comment": "基本成型；结构是短板",
    "suggestions": ["…针对性建议…", "…进阶点缀…"],
    "sub": {"proportion": 40, "solidity": 66, "support": 100,
            "detail": 70, "facade": 55, "material": 100, "layer": 75, "conflict": 90}
  },
  "enclosure": {"wall":0.8,"roof":0.9,"floor":1.0,"level":"完全密闭"},
  "walls": {"outer":4,"inner":2,"standalone":0,"list":[{"material":"stripped_oak_log","height":5,"length":8,"dir":"x","type":"outer"}]},
  "rooms": [{"volume":240,"floor_y":-58,"ceil_y":-52,"purpose":""}],
  "cavities": 0, "pillars": 5, "beams": 8,
  "symmetry": {"x":0.9,"z":0.8},
  "contents": {"bed":2,"chest":4,"door":2},
  "redstone": false, "water": 0, "ground_y": -61,
  "materials": ["stripped_oak_log","oak_stairs"], "material_complexity": "medium",
  "solidity": 0.4, "hang_ratio": 0, "aspect_height": 1.6, "aspect_length": 1.2,
  "section_shape": "rect", "silhouette": "funnel", "roughness": 2.1,
  "surface_ratio": 0.9, "exposed_surface": 616,
  "centroid_offset": {"dx":0.0,"dy":0.1,"dz":-0.1},
  "layer_area_var": 0.1, "top_material": "oak_stairs", "base_material": "stripped_oak_log",
  "material_layer_changes": 2, "protrusions": 3,
  "color_count": 2, "color_richness": "medium",
  "dominant_color": "棕", "secondary_color": "灰",
  "warm_ratio": 0.8, "brightness": 0.4, "saturation": 0.5,
  "palette": ["棕", "灰"]
}
```

### 资源占用
- 内存：BlockStore ~80B/方块（去 UUID，long 键）；房间 RLE 免大数组，仅 O(段数)；映射表/表加载后进缓存。
- CPU：主线程放置 O(1)；后台扫描**单遍聚合**（快照主遍历从 ~13 次降到 3 次，含对称/悬空/立面），5 万方块 <100ms；20s 窗口一次，占用 ≈0.5% 单核。
- 扫描护栏：方块数 >8 万时跳过分析，只记原始数据（防极端超大拖慢）。
- 线程：后台扫描线程 + 方块记录 daemon（后台批量落盘）；服务端 tick 零阻塞。
- 磁盘：`latest.json` 覆盖写；`history.jsonl` 完成时追加（每栋完整归档）；`blocks/*.jsonl` 追加、永不清理；`registry/*.json` 映射表。

### 性能优化（GitHub 算法借鉴 + 本轮重构）
- **墙聚类 O(S²)→O(n)**：列索引 + 邻域查询，替代片段全对全比较。
- **房间检测 RLE + Union-Find CCL**：空气段游程编码 + 并查集合并，替代逐体素 BFS，免 `visited` 大数组。
- **单遍聚合**：`ScanAggregation.collect` 一次遍历收集全部统计指标，`apply` 统一派生，消除十余次独立遍历。
- **缓存解析**：`blockId→path`（`ConcurrentHashMap`）、`材质/颜色/亮度`（表驱动 + 缓存），消灭重复 `byId()+getKey()`。
- **日志节流**：`[building] place` 每 50 块打一条；颜色对比判据区分"通透采光"，玻璃/水/冰/灯豁免冲突。
- **数据结构**：`blocks/` 只存 id+坐标，材质/颜色/功能一律查映射表，省磁盘与 CPU。

## 三、环境要求

- **JDK 25**（26.2 必需，fabric-api 0.158.0+26.2 要求 `java>=25`）。
  注意：**JDK 17 不能用来构建本模组**，必须 25。
  没有的话，Minecraft 官方启动器 / HMCL 自带的运行时里通常就有一份可用的 JDK 25，位置形如：
  ```
  %APPDATA%\.hmcl\java\windows-x86_64\<runtime-name>
  ```
- 建筑识别仅服务端采集（单机/局域网内嵌服务器）；多人远程服务器自动不采集。

## 四、构建与安装

用仓库自带的 Gradle wrapper（9.7.1，无需系统 gradle），并**让 Gradle 用 Java 25**：

```powershell
# 指向你机器上的 JDK 25（示例：HMCL 自带运行时）
$env:JAVA_HOME = "$env:APPDATA\.hmcl\java\windows-x86_64\<runtime-name>"
.\gradlew.bat clean build --console=plain
```

产物在 `build/libs/deskpet-mod-2.0.0.jar`，复制到 `.minecraft/mods/` 即可。数据写入 `.minecraft/deskpet/`。

### 构建约定（对维护者 / AI 助手，必须遵守）

- **长耗时操作（gradle 构建等）禁止用"设置超时等它结束"的简单方式**：超时被掐断会丢进度、误判为卡死。
- 正确做法：**后台启动 + 输出重定向到日志文件 + 实时轮询日志**。轮询时检查进程是否存活、日志尾部进度、是否出现 `BUILD SUCCESSFUL` / `BUILD FAILED`。
- 依赖大多已在本地缓存（`~/.gradle`），增量构建十几秒；若提示下载，先确认镜像配置，不要怀疑卡死就反复重跑。

## 五、版本对应（gradle.properties）

| 项目 | 值 |
|---|---|
| minecraft | 26.2 |
| fabric-loader | 0.19.3 |
| fabric-api | 0.158.0+26.2 |
| loom | 1.17-SNAPSHOT（gradle wrapper 9.7.1） |
| mappings | 无（26.2 起官方停止发布映射，直接引用官方类名） |

## 六、与桌宠程序对接

桌宠程序「设置 → 游戏」：
- 日志路径指向你的 `.minecraft`（自动推导 `deskpet/` 数据目录）
- 数据源选「自动（模组优先）」或「仅模组数据」
- 打开游戏模式即可

**建筑感知包**（`deskpet/buildings/latest.json`）：桌宠端读取后，`state=complete` 且 `needs_ai=true` 的复杂结构（雕像/复杂）调 DeepSeek/千问润色描述，其余用模板描述播报；`state=in_progress`（进行中/不完整建筑）直接用 `intent.hint` 口语提示播报（不占 AI），并按「意图+阶段」去重，阶段推进才再播报一次。

### 建筑评分系统（BuildingScore）
对每个感知包打三维评分，写入 `score` 字段（`comment` 评价 + `suggestions` 建议可给播报用；子项分 `sub` 供调试）：

| 维度(权重) | 子项(权重) | 依据指标 | 良好判据 |
|---|---|---|---|
| **结构** 0.4 | 体态比例 0.35 | `aspectHeight/aspectLength` | 接近 1:1.6:1（高:长:宽）或横向舒展 1:2:1；细针/地砖扣半 |
| | 空心率 0.35 | `solidity`(=方块数÷边界框体积) | 0.3~0.6 最佳；<0.2 空心薄皮不及格；>0.8 实心敷衍 |
| | 支撑逻辑 0.30 | `hangRatio`(底部悬空/柱脚) | 有柱脚/阶梯地基加分；全部直接铺地扣分 |
| **装饰** 0.3 | 雕工指数 0.45 | `nonFullCount`÷方块数。**半砖/楼梯/栅栏/活板门/门等特殊方块是明确加分项** | 5%~15% 细节丰富；<5% 火柴盒低分 |
| | 外立面起伏度 0.55 | `facadeStd`(外表面到质心距离标准差) | 越大越凹凸有致；接近 0 豆腐块 |
| **配色** 0.3 | 材质丰富度 0.35 | `materialCount` | 4~8 种协调佳作；1~2 单调；>12 杂乱 |
| | 垂向分层 0.25 | 层主材质带数(=1+`materialLayerChanges`) | 2~4 层带=明显分带；>6 随机混杂（轻量，无渐变/纯度重计算） |
| | 邻近颜色对比度 0.40 | 邻接方块明度差>0.45 的占比 | 高对比邻接越少越和谐（岩浆邻木/钻石邻泥等高对比自然被捕获） |

- 维度内按权重加权 → 结构/装饰/配色各 0~100；总分 = 0.4·结构 + 0.3·装饰 + 0.3·配色；等级 S/A/B/C/D。
- **评分隐式**：`comment`（总体评价，无分数）+ `suggestions`（程序从内置建筑优化知识库按最弱子项挑出的针对性建议，无分数）。桌宠端只把这俩交给 AI 组织语言，**不向玩家报具体分/等级**。
- 评分器 `BuildingScore.java` 纯计算，`StructureScanner` 采集元数据（非完整方块数、外立面起伏、颜色对比、层主材质带）。设计取向：低算量（一次性 6 邻域遍历，无渐变梯度等二次扫描）。

### 建筑优化知识库（BuildingAdviceKb）
`suggestions` 由 `BuildingAdviceKb` 内置知识库生成，程序按「当前建筑类别 + 最弱子项」组合抽 2~3 条，交给桌宠 AI 组织语言（**隐式，不含分数**）：

- **类型专属设计**：覆盖各结构类型（房屋/别墅/小屋/火柴盒/塔/尖塔/灯塔/城堡/城墙/边界墙/桥/拱门/泳池/喷泉/人形雕塑/图腾柱/像素画/露天家园/地下），每种给出"这类建筑怎么做才好看"（如塔瘦高收束+台阶腰线、城堡角塔+垛口+门楼、桥要有桥墩/拱肋/栏杆等）。
- **通用优化**：按最弱子项给可操作手法——材质丰富（梁柱/墙/地板材质分开三区三层）、特殊方块少（半砖+活板门=窗框/苗圃、楼梯=屋檐/门廊/坡顶、栅栏=栏杆）、空心率（夹层楼板划分/加厚实体）、垂向分层（底深-中浅-顶亮三层带）、颜色对比高（亮暗色只做点缀）、支撑贴地（柱脚/阶梯地基）、立面平板（壁柱/窗沿/飞檐）、体态异常（细高加基座/过扁加高）。
- **锦上添花**：整体评分都不错时，给类型相关的进阶点缀（立体窗框+窗台花盆、屋顶窗 Dormer、藤蔓/树叶自然点缀、檐口灯带）。

> 内容来源：Minecraft 中文 Wiki 建筑教程（屋顶类型/建造指南）、公开建筑教程与社区设计要点（窗户/墙面/雕花）。知识库为「设计方案 + 针对性优化」结构，后续可继续扩充条目。

### 评分亮点（针对真实建筑修正）
- **通透采光豁免**：玻璃/水/冰/灯邻接不算颜色冲突（否则"有玻璃采光的房子"配色会被误扣）——配色分不再虚低。
- **自然绿植加分**：树叶/藤蔓/花草(natureCount)是装饰加分项（≥12 块 +8 分）；垂向分层只看"结构材质"，玻璃/楼梯/绿植等装饰块不干扰分层。
- **半开放式田园/院落加分**：墙覆盖<0.5 且田园生活化（围栏/农田/干草/绿植）→ 结构 +8（半开放是浪漫/度假的**优点**，不是结构缺陷）。
- **颜色来自真实映射表**：材质/颜色/功能统一查 `registry/blocks.json`（颜色为真实纹理采样），樱花木=粉、黑石=黑、白桦=浅白等，不再按关键词笼统判色。

### 不完整建筑意图推断（IntentEstimator）
`RuleEngine` 只对**完整快照**做硬分类；玩家边建边播时结构不完整，直接用硬分类会误判（如火柴盒/边界墙）。`IntentEstimator` 在**搭建中**预测玩家打算建什么：输出「意图大类 + 建造阶段 + 完成度 + 置信度 + 口语提示」。强信号优先：像素画 → 塔 → 水景(pool/fountain) → 桥/拱门 → 城墙/边界墙 → **农场/牧场/庄园** → 雕像 → 城堡 → 房屋 → 露天家园 → 未知；方块 <12 不做预测，置信度 <0.35 归弱猜测。`BuildingAnalyzer.emit` 在「进行中（`reason=progress` 或结构明显未闭合）」且置信度达标时，用 `intent.hint` 替换 `classification`，并写 `state` / `intent` 字段。

### 方块数据持久化 + 全量映射表
- **持久化**：玩家放置/破坏 → `blocks/*.jsonl`（`BlockRecorder`，只记 `ts/world/project/x/y/z/id/action/by_player`，**追加、永不清理**，不依赖易轮转的 `latest.log`）。`by_player=true` 过滤标记，供未来优化（材质统计/建筑复盘/重建建议）。
- **全量映射表**：`registry/blocks.json`（`BlockRegistryExport` 导出，id→name/material/rgb/type）+ `registry/block_colors.json`（从 MC 客户端纹理逐方块采样真实主色，覆盖 1268 个）。颜色/材质/功能**统一查表**，规则仅兜底。
- **项目归档**：建筑完成(dormant)时整栋感知包追加到 `buildings/history.jsonl`（含识别/评分/建议），结合 `blocks` 的 project 坐标可 1:1 收藏；`latest.json` 只做实时预览。

## 七、目录结构

```
src/main/java/com/deskpet/mod/
├── DeskpetMod.java          # 入口 + 服务端事件注册 + 建筑识别接入
├── DeskpetModClient.java    # 客户端入口（聊天/广播/环境状态）
├── EventCollector.java      # 事件聚合 + 分级 + 输出
├── WindowSink.java          # 分片文件写入
├── PlayerAction.java / ActionType.java
├── build/                   # 建筑识别系统
│   ├── BlockRecord.java     #   方块记录（blockId/tick/placedByPlayer）
│   ├── BlockStore.java      #   稀疏哈希表 + AABB（惰性扩展）
│   ├── BlockRecorder.java   #   玩家方块数据持久化（blocks/*.jsonl，永不清理）
│   ├── BlockRegistryExport.java # 全量方块映射表导出（id→材质/颜色/功能）
│   ├── BlockClasses.java    #   共享词典：材质/颜色/亮度/功能，映射表优先/规则兜底
│   ├── BuildingTracker.java #   状态机 IDLE/ACTIVE + 触发 + 空间分离
│   ├── BuildingAnalyzer.java#   调度（20s 窗口/60s 休眠/后台线程）+ 感知包输出 + 项目归档
│   ├── ScanAggregation.java #   单遍聚合（一次遍历收集全部统计指标，含农田/绿植/颜色对比）
│   ├── StructureScanner.java#   三维扫描编排（形态/材质/色彩/密闭/柱梁/地表）
│   ├── WallSegmenter.java   #   墙段检测（竖状连续同材质面片聚类）
│   ├── RoomSegmenter.java   #   房间/空腔（RLE 空气段 + Union-Find CCL）
│   ├── IntentEstimator.java #   进行中建筑意图预测（含 farm/ranch/estate）
│   ├── BuildingScore.java   #   三维评分（结构/装饰/配色 + 半开放/绿植/通透采光修正）
│   ├── BuildingAdviceKb.java#   建筑优化知识库（类型建议 + 通用优化 + 锦上添花）
│   ├── ScoreResult.java / RuleEngine.java / RuleResult.java / ScanResult.java
│   └── BuildingVirtualIds.java
└── mixin/
    ├── BlockItemPlaceMixin.java      # 放置方块（服务端）
    ├── ArmorStandItemMixin.java      # 盔甲架
    ├── HangingEntityItemMixin.java   # 展示框/画
    ├── MinecartItemMixin.java / BoatItemMixin.java
    ├── BucketItemMixin.java          # 水/熔岩
    ├── PlayerAdvancementsMixin.java / ClientAdvancementsMixin.java
    ├── ItemPickupMixin.java / CraftingMenuMixin.java
```

## 八、已知问题与待办

- **多人采集**：多人时服务端事件（击杀/受伤/破坏/建筑采集）客户端拿不到；进度/聊天/合成/拾取可正常采集。
- **建筑识别接口已实现**：读取 `buildings/latest.json`，`in_progress` 用 intent.hint 直接播报（按意图+阶段去重），`complete` 的复杂结构调 AI 润色；`score` 的 comment/suggestions（隐式，不含分）交给 AI 组织语言。
- **庄园/建筑群聚合（待办）**：别墅 + 农场/牧场目前可能因空间分离(AABB 膨胀 4 格)被切成独立项目；"庄园"整体识别与聚合规划中。
- **坡顶 vs 尖顶**：白色楼梯铺坡的坡顶偶尔被判 `pointed`（尖顶），坡顶/尖顶判别仍可优化。
- **多人服务器**：反作弊不拦截纯观察型 mod，但请遵守各服务器 mod 规则。
- **建筑识别边界**：拱门可能判为桥（窄桥近似）；平顶城堡（无突起角塔）可能判为房屋。

## 九、数据格式示例（事件窗口）

每行一个 20 秒窗口 JSON，如：

```json
{"window":20,"ts":... ,"importance":"CRITICAL","highlights":[
  {"type":"damage","player":"arcoyx","target":"史莱姆","count":2},
  {"type":"item_gain","player":"arcoyx","target":"橡木原木","detail":"拾取","count":3},
  {"type":"summary","detail":"挖掘:2 橡木原木; 击杀:1 史莱姆; 使用:1 面包; 移动:120米;"}
]}
```

## 十、环境状态文件（state.json）

客户端入口周期写入 `deskpet/state.json`，供桌宠组装 AI 消息时注入环境上下文：

```json
{"server":"Hypixel (mc.hypixel.net)","mode":"","world_type":"","game":"PARTY_GAMES"}
```
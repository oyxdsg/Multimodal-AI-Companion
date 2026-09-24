package com.deskpet.mod.build;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 建筑优化知识库：程序据此挑针对性建议，交给桌宠端 AI 组织语言（隐式，不含分数）。
 *
 * 两部分：
 * - 类型专属设计（byType）：按建筑类型(castle/tower/bridge/pool/…)给出"这类建筑怎么建才好看"。
 * - 通用优化（byWeakness）：按评分子项(材质/雕工/空心率/分层/对比/支撑/立面/体态)给出可操作手法。
 * 由 BuildingScore.buildAdvice 按"当前类别 + 最弱子项"抽取。
 */
public final class BuildingAdviceKb {

    private BuildingAdviceKb() {}

    /** 类型 → 该类型建筑的优秀设计要点（1 条，作为风格指引）。 */
    private static final Map<String, String> TYPE_ADVICE = Map.ofEntries(
            Map.entry("house", "一间好住的屋子要靠立面说话：窗户配立体窗框（半砖错位+活板门做窗台窗眉），"
                    + "进门做个小门廊（楼梯台阶+立柱+栅栏扶手），比光板墙耐看多了。"),
            Map.entry("villa", "大房子要破开单调的大面：加壁柱、阳台、三段式窗（窗沿+窗眉+窗台），"
                    + "屋顶用复斜/折线形（Gambrel/Mansard）扩大阁楼，再开两个屋顶窗采光。"),
            Map.entry("multi_story", "多楼层用楼梯间串联，半砖做楼板沿口，楼层界线更清楚；"
                    + "每层窗对齐、层间加一圈腰线，立面立刻有节奏。"),
            Map.entry("barn", "谷仓胜在'大而通透'：大坡顶（楼梯铺坡）+ 门洞，顶部开天窗/气窗，"
                    + "墙面用木板搭石基。"),
            Map.entry("bungalow", "平房要横向舒展：宽檐口（挑出的楼梯/半砖）、大玻璃窗，"
                    + "屋角用砖柱包边，低矮也显规整。"),
            Map.entry("matchbox", "火柴盒的救星是立面层次：把窗、门、屋檐全换成半砖/活板门/楼梯组合，"
                    + "墙上错位嵌倒置楼梯做凸凹，平脸立刻变生动。"),
            Map.entry("cabin", "小屋走质感：原木+去皮原木混搭、石基、小坡顶；"
                    + "窗边挂花盆、檐下垂藤，温暖自然。"),
            Map.entry("tower", "塔要瘦高挺拔：越往上越收一点（漏斗剪影），顶部加尖顶/悬崖顶；"
                    + "每隔几层加一圈台阶腰线，打破垂直单调。"),
            Map.entry("spire", "尖塔着重收束：塔身递减、顶部用楼梯铺尖避免空隙，底做宽基座才稳。"),
            Map.entry("lighthouse", "灯塔顶部加一圈栏杆+发光源（海灯/萤石/火把），"
                    + "塔身做红白条纹（羊毛/混凝土交替），晚上很出片。"),
            Map.entry("castle", "城堡要'角塔+城墙+门楼'三件套：墙顶做垛口（楼梯交替），"
                    + "角塔高于城墙，城门用石拱+吊桥感觉。"),
            Map.entry("wall", "长墙别光溜溜：顶部加垛口（楼梯交替），底部用石砖/苔石斑驳过渡，"
                    + "每隔一段加一座小塔或箭窗。"),
            Map.entry("boundary", "边界墙低而规整即可：顶部半砖压边、转角立柱，"
                    + "中间嵌栅栏/铁栏更轻盈。"),
            Map.entry("bridge", "桥要看'支撑'：桥墩、拱肋、栏杆都不能少；石桥用楼梯做拱肋、"
                    + "半砖做栏板，悬空才有味道。"),
            Map.entry("arch", "拱门两柱加基座、顶部用楼梯合龙，可垂一点藤蔓；"
                    + "拱内嵌玻璃或灯，人往下穿很有仪式感。"),
            Map.entry("pool", "泳池边缘用深色石砖+半砖嵌边+一级矮梯下去；"
                    + "旁边配躺椅（楼梯/半块）和遮阳伞（杆+羊毛顶）。"),
            Map.entry("fountain", "喷泉做三层递减水盘：中心柱、中层盘、底层池，盘沿用半砖；"
                    + "四周对称铺砖，夜里点海灯很妙。"),
            Map.entry("statue_humanoid", "人形雕像要有'基座+主体+点缀'：深板岩基座衬托主体，"
                    + "加肩膀、飘带（楼梯/半砖）更有动感。"),
            Map.entry("statue_column", "图腾柱层层收窄（圆柱剪影），每层用不同石料/换色一圈，"
                    + "顶上加个尖墩点亮。"),
            Map.entry("statue", "雕塑胜在整体轮廓：基座、主体、顶部渐变，别让细碎块破坏剪影。"),
            Map.entry("pixel_art", "像素画在立面上：用羊毛/混凝土色块，中间深色描边、边缘半砖收边，"
                    + "画面更清晰。"),
            Map.entry("open_base", "露天家园要'围而不合'：四周矮栅栏/花坛围一圈，"
                    + "中间摆床、工作台、箱阵，生活气十足。"),
            Map.entry("underground", "地下空间注意采光：墙面嵌灯（萤石/火把）、苔石斑驳，"
                    + "顶部开口做采光井，别闷成黑窑。")
    );

    /** 整体不错时的锦上添花建议（通用）。 */
    private static final List<String> POLISH = List.of(
            "可以在窗外做立体窗框+窗台花盆，或给屋顶开个屋顶窗(Dormer)。",
            "墙角或屋檐垂点藤蔓、树叶、杜鹃，建筑立刻添了自然气。",
            "夜里用萤石/海灯/火把做檐口灯带，白天看材质、晚上看光影。",
            "给地坪做一圈矮苗圃（活板门围边+草/花），窗下也不空。"
    );

    static String typeAdvice(String category) {
        if (category == null) {
            return "";
        }
        return TYPE_ADVICE.getOrDefault(category, "");
    }

    static List<String> polish() {
        return new ArrayList<>(POLISH);
    }

    /** 按类别聚合成大类（villa 归 house、spire 归 tower 等），查不到返回原类别。 */
    static String normalize(String category) {
        if (category == null) {
            return "";
        }
        return switch (category) {
            case "villa", "multi_story", "barn", "bungalow", "matchbox", "cabin" -> "house";
            case "spire", "lighthouse" -> "tower";
            case "boundary" -> "wall";
            case "arch" -> "bridge";
            case "statue_column" -> "statue_humanoid";
            default -> category;
        };
    }
}

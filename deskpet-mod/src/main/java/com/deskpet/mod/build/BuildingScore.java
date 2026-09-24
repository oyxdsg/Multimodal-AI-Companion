package com.deskpet.mod.build;

import java.util.ArrayList;
import java.util.List;

/**
 * 建筑评分器：对扫描结果做三大维度打分（结构 / 装饰 / 配色），每维 0~100。
 *
 * 结构 体态比例(0.35) + 空心率(0.35) + 支撑逻辑(0.30)
 * 装饰 雕工指数(0.45) + 外立面起伏度(0.55)
 * 配色 材质丰富度(0.40) + 垂向分层(0.35) + 邻近冲突(0.25)
 *
 * 各部分基于 ScanResult 提供的元数据（aspectHeight/aspectLength、solidity、hangRatio、
 * nonFullCount、facadeStd、materialCount、layerBrightness、conflictPairs 等）。
 * 分数为启发式：给出相对优劣与改进方向，供桌宠播报与玩家参考。
 */
public class BuildingScore {
    // 三围权重
    private static final double W_STRUCTURE = 0.4;
    private static final double W_DECORATE = 0.3;
    private static final double W_COLOR = 0.3;

    public ScoreResult calculate(ScanResult r, String category) {
        ScoreResult s = new ScoreResult();
        s.proportion = scoreProportion(r);
        s.solidity = scoreSolidity(r);
        s.support = scoreSupport(r);
        s.structure = clamp(0.35 * s.proportion + 0.35 * s.solidity + 0.30 * s.support);
        // 半开放式田园/院落住宅：开放但生活化（围栏院落/农田/牧场/绿植）是浪漫田园的优点，
        // 不该因"墙覆盖/围合低"而被当成结构缺陷——给结构加分。
        if (r.wallCoverage < 0.5 && countryLife(r)) {
            s.structure = clamp(s.structure + 8);
        }

        s.detail = scoreDetail(r);
        s.facade = scoreFacade(r);
        s.decorate = clamp(0.45 * s.detail + 0.55 * s.facade);

        s.material = scoreMaterial(r);
        s.layer = scoreLayer(r);
        s.conflict = scoreConflict(r);
        // 配色：材质丰富 0.35 + 垂向分层 0.25 + 邻近颜色对比度 0.40（强调视觉和谐）
        s.color = clamp(0.35 * s.material + 0.25 * s.layer + 0.40 * s.conflict);

        s.total = clamp(W_STRUCTURE * s.structure + W_DECORATE * s.decorate + W_COLOR * s.color);
        s.grade = grade(s.total);
        s.comment = comment(s);
        s.suggestions = buildAdvice(r, s, category);
        return s;
    }

    private static double scoreProportion(ScanResult r) {
        double p = 100;
        double ah = r.aspectHeight;
        double al = r.aspectLength;
        // 高远大于长宽（细针）或远小于（地砖）→ 扣半
        if (ah > 2.2 || ah < 0.2) {
            p *= 0.5;
        } else if (ah > 1.8) {
            p *= 0.75;
        } else if (ah >= 0.5 && ah <= 1.0) {
            p += 3;   // 接近 1:1.6:1 的黄金感微奖
        }
        // 长横舒展
        if (al > 3.5) {
            p *= 0.7;     // 过窄长条
        } else if (al > 2.5) {
            p *= 0.85;
        } else if (al >= 1.0 && al <= 2.0) {
            p += 2;       // 横向舒展 1:2:1 微奖
        }
        return clamp(p);
    }

    private static double scoreSolidity(ScanResult r) {
        double vol = Math.max(1, (double) r.width * r.depth * r.height);
        double solid = (double) r.blockCount / vol;   // 实际方块数 ÷ 边界框体积（空心率）
        if (solid >= 0.3 && solid <= 0.6) {
            return 100;   // 最佳：有内部空间且墙有厚度
        }
        if (solid < 0.2) {
            return 40;    // 空心薄皮
        }
        if (solid > 0.8) {
            return 55;    // 实心巨石（敷衍）
        }
        if (solid < 0.3) {
            return 60 + 40 * (solid - 0.2) / 0.1;                 // 0.2~0.3 线性上升
        }
        return 100 - 45 * (solid - 0.6) / 0.2;                    // 0.6~0.8 从 100 降到 55
    }

    private static double scoreSupport(ScanResult r) {
        double h = r.hangRatio;   // 底部悬空/柱脚比例
        if (h >= 0.05 && h <= 0.5) {
            return 100;   // 有柱脚 / 阶梯地基 / 悬挑 → 力学落地感
        }
        if (h < 0.05) {
            return 40 + 300 * h;   // 全贴地 → 扣分（越贴越低）
        }
        return 100 - 80 * (h - 0.5);   // 过度腾空（整桥悬空）略降
    }

    private static double scoreDetail(ScanResult r) {
        double d = r.blockCount > 0 ? (double) r.nonFullCount / r.blockCount : 0;   // 非完整方块占比
        double score;
        if (d >= 0.15) {
            score = 100;   // >15% 细节丰富
        } else if (d >= 0.05) {
            score = 70 + 30 * (d - 0.05) / 0.10;   // 0.05~0.15 → 70~100
        } else {
            score = 30 + 40 * d / 0.05;             // 0~5% 火柴盒 → 30~70
        }
        // 自然绿植（树叶/藤蔓/花草/苔藓/竹）是明确的装饰性加分项
        if (r.natureCount >= 12) {
            score = Math.min(100, score + 8);
        } else if (r.natureCount >= 1) {
            score = Math.min(100, score + 4);
        }
        return score;
    }

    private static double scoreFacade(ScanResult r) {
        double std = r.facadeStd;   // 外表面方块到质心距离标准差（起伏度）
        if (std >= 2.0) {
            return 100;
        }
        if (std >= 0.8) {
            return 75 + 25 * (std - 0.8) / 1.2;
        }
        if (std >= 0.35) {
            return 50 + 25 * (std - 0.35) / 0.45;
        }
        return 20 + 30 * std / 0.35;   // 接近 0 的豆腐块 → 低分
    }

    private static double scoreMaterial(ScanResult r) {
        int m = r.materialCount;
        if (m >= 4 && m <= 8) {
            return 100;   // 协调佳作
        }
        if (m <= 1) {
            return 30;
        }
        if (m == 2) {
            return 45;
        }
        if (m == 3) {
            return 60;
        }
        if (m <= 12) {
            return 100 - 30 * (m - 8) / 4.0;   // 9~12 → 100~70
        }
        return 55;   // >12 番茄炒蛋式杂乱
    }

    private static double scoreLayer(ScanResult r) {
        // 垂向分层（轻量，无渐变/纯度计算）：层主材质带数量 = 1 + 层切换次数。
        // 2~4 层带 = 明显"下深中浅上亮"分带（好）；>6 频繁切换 = 随机混杂（差）。
        double bands = 1 + r.materialLayerChanges;
        if (bands >= 2 && bands <= 4) {
            return 100;
        }
        if (bands <= 6) {
            return 100 - 20 * (bands - 4) / 2.0;   // 5~6 带 → 100~80
        }
        return 45;
    }

    /** 邻近颜色对比度：高对比邻接占比越低越好（视觉和谐）。 */
    private static double scoreConflict(ScanResult r) {
        if (r.adjacentPairs <= 0) {
            return 100;   // 无邻接关系
        }
        double ratio = (double) r.conflictPairs / r.adjacentPairs;   // 高对比邻接占比
        return clamp(100 - ratio * 100);   // ratio 0% → 100；100% → 0
    }

    /** 田园/院落生活特征：围栏院落、农田、牧场(干草)、绿植较丰富——浪漫/田园系。 */
    private static boolean countryLife(ScanResult r) {
        int fence = r.contents.getOrDefault("fence", 0);
        return fence >= 12 || (r.cropCount + r.hayCount) >= 4 || r.hayCount >= 2;
    }

    private static String grade(double t) {
        if (t >= 85) {
            return "S";
        }
        if (t >= 70) {
            return "A";
        }
        if (t >= 55) {
            return "B";
        }
        if (t >= 40) {
            return "C";
        }
        return "D";
    }

    private static String comment(ScoreResult s) {
        StringBuilder sb = new StringBuilder();
        double[] dims = {s.structure, s.decorate, s.color};
        String[] names = {"结构", "装饰", "配色"};
        int weak = 0;
        for (int i = 1; i < 3; i++) {
            if (dims[i] < dims[weak]) {
                weak = i;
            }
        }
        if (s.total >= 85) {
            sb.append("精品杰作，空间、体量、配色俱佳");
        } else if (s.total >= 70) {
            sb.append("建成的水准相当不错");
        } else if (s.total >= 55) {
            sb.append("基本成型，仍有打磨空间");
        } else {
            sb.append("略显粗糙，像临时堆起来的");
        }
        if (dims[weak] < 60) {
            sb.append("，" + names[weak] + "可以再打磨");
        }
        return sb.toString();
    }

    /**
     * 从内置建筑优化知识库挑针对性建议（不含分数）。组合三部分：
     * 1. 类型专属设计（按当前建筑类别，如城堡/塔/桥该怎么做才好看）；
     * 2. 通用优化（按最弱子项：材质/雕工/空心率/分层/对比/支撑/立面/体态）；
     * 3. 整体不错时给锦上添花（polish）。
     * 交给桌宠 AI 组织语言，不吐露分数。
     */
    private static List<String> buildAdvice(ScanResult r, ScoreResult s, String category) {
        List<String> out = new ArrayList<>();
        double solid = r.blockCount / (double) Math.max(1, (long) r.width * r.depth * r.height);
        double detailRatio = r.blockCount > 0 ? (double) r.nonFullCount / r.blockCount : 0;

        // 1. 类型专属设计（建筑类别指引）
        String type = BuildingAdviceKb.typeAdvice(BuildingAdviceKb.normalize(category));
        if (!type.isEmpty()) {
            out.add(type);
        }

        // 2. 按最弱子项目的通用优化（每项 1 条，避开明显是"好方向"的项）
        if (s.material < 60) {
            out.add("材质丰富度可以更立体：把承重柱、墙、地板的主材质分开——柱用深色原木或石砖、"
                    + "墙用浅木板、地板深板/地毯，三区三层，层次感立刻出来。");
        } else if (s.detail < 60 && detailRatio < 0.05) {
            out.add("几乎没用特殊方块——半砖搭活板门能拼窗框和窗台、楼梯能挑屋檐和门廊、"
                    + "栅栏做栏杆，加上这些立面马上有细节。");
        } else if (s.detail < 60) {
            out.add("细节还能再多：窗沿用半砖、墙角用楼梯、门洞用活板门做门槛，层次会更丰富。");
        }
        if (s.solidity < 55) {
            if (solid > 0.6) {
                out.add("建筑偏实心，内部结构没做出来——加承重柱、楼梯，用半砖做夹层楼板，"
                        + "把大空间分成立体的小房间，留挑空/天井。");
            } else {
                out.add("墙身太薄显得空心，可以加厚墙体、留承重柱和横梁，让结构更扎实。");
            }
        }
        if (s.layer < 55) {
            out.add("材质分布有点乱，试试'底深-中浅-顶亮'三层：石基/深色底座、浅色主体、亮或深色屋顶，"
                    + "过渡用楼梯/半砖做窄混搭带。");
        }
        if (s.conflict < 55) {
            out.add("相邻方块颜色对比有点高——把亮暗/高饱和色当点缀局部用（窗框、檐线、门楣），"
                    + "主体用同明度过渡色衔接。");
        }
        if (s.support < 55 && r.natureCount == 0) {
            out.add("地基直接铺地显得单薄，围一圈柱脚或做成阶梯式地基，建筑更稳更有落地感。");
        }
        if (s.facade < 55) {
            out.add("墙面太扁平平，加壁柱、窗沿、飞檐，或让方块凹凸交错放，立体感就来了。");
        }
        if (s.proportion < 55) {
            if (r.aspectHeight > 1.8) {
                out.add("塔身有点过细，加个宽基座、横向腰线或平台舒展一下，别像细针。");
            } else {
                out.add("建筑有点太扁，加高、加塔楼，或上坡顶/复斜顶再挑出檐口。");
            }
        }

        // 3. 保证 2~3 条、类型丰富：不足时补"进阶/锦上添花（polish）"，避免建议只一条且单一
        List<String> pol = BuildingAdviceKb.polish();
        for (String p : pol) {
            if (out.size() >= 3) {
                break;
            }
            out.add(p);
        }
        if (out.size() > 3) {
            out = new ArrayList<>(out.subList(0, 3));
        }
        return out;
    }

    private static double clamp(double v) {
        return Math.max(0, Math.min(100, v));
    }
}

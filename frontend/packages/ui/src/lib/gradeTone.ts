import type { BadgeProps } from "../components/Badge";

/**
 * §2.4「案件等级」——等级 → 徽章变体的**唯一来源**。
 *
 * 规范给的是**颜色**（`S`=`--gold-500` 金 / `A`=`--danger-500` 朱砂 /
 * `B`=`--brand-600` 墨蓝 / `C`=`--ink-400` 中性），本表把它落到组件层变体上；
 * 变体名与令牌前缀一一对应（`gold` / `danger` / `brand` / `ink`）。
 *
 * ⚠️ 此前这段映射在 4 个端里**各写一份**（共 6 份），且颜色与 §2.4 不符
 *（S→`danger`、A→`pending`、B→`info`、C→`neutral`）⇒ 由 `verify_grade_badges.py`
 * 的 **G1**（渲染层底色）+ **G2**（来源数）双判据逮到。收归到 `packages/ui` 后
 * 只有一份定义，改色只需改这里。
 *
 * 双通道：颜色 + **字母**。字母通道（徽章文本含 `S`/`A`/`B`/`C`）由 G3 判，本表不涉及。
 */
export const GRADE_TONE: Record<string, BadgeProps["variant"]> = {
  S: "gold",
  A: "danger",
  B: "brand",
  C: "ink",
};

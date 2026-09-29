import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * 本项目的**语义字号档**（与 `frontend/tailwind.preset.ts` 的 `fontSize` 语义档一一对应）。
 *
 * 🚨 不登记会怎样（2026-09-24 实测）：
 * tailwind-merge 只认识 Tailwind 标准档（`text-sm` / `text-base` / `text-lg` …），
 * **不认识** `text-body-sm` 这类语义档 ⇒ 把它与 `text-<颜色>` 判成**同一组**，
 * 同组后者赢 ⇒ **字号类被删掉**，字号静默回落到继承值。
 * 最常见的「字号 + 颜色」组合必中：
 *   `cn("text-body-sm", "text-ink-900")` → `"text-ink-900"`（`text-body-sm` 没了）
 *   `cn("text-h2", "text-ink-900")`      → `"text-ink-900"`（同上）
 *
 * 判据侧同步读这个数组（`evidence/verify_typography.py:cn_font_size_tokens`），
 * 因此**改这里就能同时改变产品行为与门禁期望值**，不会出现「修好了门禁照红」。
 */
const FONT_SIZE_TOKENS = [
  "caption",
  "label",
  "body-sm",
  "body",
  "body-lg",
  "h4",
  "h3",
  "h2",
  "h1",
  "display",
];

/** 把语义字号档登记进 `font-size` 组，其余行为与库默认完全一致。 */
const merge = extendTailwindMerge({
  extend: { classGroups: { "font-size": [{ text: FONT_SIZE_TOKENS }] } },
});

/**
 * 合并 Tailwind 类名：clsx 处理条件类，tailwind-merge 消解冲突类。
 */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}

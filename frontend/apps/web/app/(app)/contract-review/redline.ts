/* ============================================================================
 * 合同审查 · redline（CR-04「一键采纳」）
 * ----------------------------------------------------------------------------
 * 纯函数，无副作用、无依赖，便于逐行审阅。
 *
 * ## 两条不可违背的约束
 *
 * 1. **不得对原文做 trim / 规范化**。
 *    后端保证 `original === source_text[char_start:char_end]`（逐字一致），
 *    一旦在渲染或切片前对文本做任何规范化（去首尾空白、合并连续空白、
 *    NFC/NFKC 归一化），字符下标就会整体错位，高亮会落在错误的句子上——
 *    而「定位错」比「不定位」更糟：用户会据此以为风险在别处。
 *
 * 2. **只替换 `suggestion_text`**（在 `types.suggestionSpec` 中收敛）。
 *    区间重叠时保留靠前的一条并显式报告冲突，绝不静默丢弃——
 *    用户点了「采纳」却什么都没发生，是最难排查的一类缺陷。
 * ========================================================================== */

import { suggestionSpec, type ReviewFinding } from "./types";

export interface Range {
  /** findings 数组下标 */
  index: number;
  start: number;
  end: number;
}

export interface Segment {
  text: string;
  /** 非 null 表示该段是一个风险区间（已采纳时 text 为改写文本） */
  findingIndex: number | null;
}

export interface RedlineResult {
  /** 覆盖全文的连续分段（含已替换段） */
  segments: Segment[];
  /** 拼接后的修订后全文，可直接复制 */
  plain: string;
  /** 已成功采纳并替换的条目下标 */
  applied: number[];
  /** 因区间重叠而未能采纳的条目下标 */
  conflicts: number[];
  /** 采纳了但原文区间无效（下标越界/空区间）的条目下标 */
  invalidRanges: number[];
}

/** 从 finding 中取出合法的原文区间；越界或空区间一律判为无效。 */
export function collectRanges(
  text: string,
  findings: ReviewFinding[],
): { ranges: Range[]; invalid: number[] } {
  const ranges: Range[] = [];
  const invalid: number[] = [];

  findings.forEach((finding, index) => {
    const start = finding.char_start;
    const end = finding.char_end;
    const ok =
      typeof start === "number" &&
      typeof end === "number" &&
      Number.isFinite(start) &&
      Number.isFinite(end) &&
      start >= 0 &&
      end > start &&
      end <= text.length;

    if (ok) ranges.push({ index, start: start as number, end: end as number });
    else invalid.push(index);
  });

  return { ranges, invalid };
}

/** 按起点排序，丢弃与前一条重叠的区间（保留靠前的一条）。 */
function dedupeOverlaps(ranges: Range[]): { kept: Range[]; dropped: number[] } {
  const sorted = [...ranges].sort((a, b) => a.start - b.start || a.end - b.end);
  const kept: Range[] = [];
  const dropped: number[] = [];
  let cursor = 0;

  for (const r of sorted) {
    if (r.start < cursor) {
      dropped.push(r.index);
      continue;
    }
    kept.push(r);
    cursor = r.end;
  }

  return { kept, dropped };
}

export interface HighlightResult {
  segments: Segment[];
  /** 因重叠未参与高亮的条目（仍会出现在风险清单里） */
  dropped: number[];
  /** 区间无效的条目 */
  invalid: number[];
}

/** 原文视图：把全部风险区间切成「普通文本 / 高亮段」交替的分段。 */
export function buildHighlights(text: string, findings: ReviewFinding[]): HighlightResult {
  const { ranges, invalid } = collectRanges(text, findings);
  const { kept, dropped } = dedupeOverlaps(ranges);

  const segments: Segment[] = [];
  let pos = 0;
  for (const r of kept) {
    if (r.start > pos) segments.push({ text: text.slice(pos, r.start), findingIndex: null });
    segments.push({ text: text.slice(r.start, r.end), findingIndex: r.index });
    pos = r.end;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos), findingIndex: null });

  return { segments, dropped, invalid };
}

/**
 * 修订稿：把「已采纳」的区间替换为 `suggestion_text`。
 *
 * `applied` 是权威结果——UI 必须据此展示「已采纳」状态，
 * 而不是照搬用户点击过的集合（点击过的条目可能因区间无效或重叠而未被替换）。
 */
export function buildRedline(
  text: string,
  findings: ReviewFinding[],
  adopted: ReadonlySet<number>,
): RedlineResult {
  const { ranges, invalid } = collectRanges(text, findings);
  const invalidRanges = invalid.filter((i) => adopted.has(i));

  // 采纳了但拿不出可替换文本的，单独归入 conflicts（未产生任何替换）
  const adoptable: Range[] = [];
  const noText: number[] = [];
  for (const r of ranges) {
    if (!adopted.has(r.index)) continue;
    if (suggestionSpec(findings[r.index]).adoptable) adoptable.push(r);
    else noText.push(r.index);
  }

  const { kept, dropped } = dedupeOverlaps(adoptable);
  const conflicts = [...dropped, ...noText, ...invalidRanges];

  const segments: Segment[] = [];
  const parts: string[] = [];
  let pos = 0;

  for (const r of kept) {
    const replacement = suggestionSpec(findings[r.index]).adoptable as string;
    if (r.start > pos) {
      const gap = text.slice(pos, r.start);
      segments.push({ text: gap, findingIndex: null });
      parts.push(gap);
    }
    segments.push({ text: replacement, findingIndex: r.index });
    parts.push(replacement);
    pos = r.end;
  }

  if (pos < text.length) {
    const tail = text.slice(pos);
    segments.push({ text: tail, findingIndex: null });
    parts.push(tail);
  }

  return {
    segments,
    plain: parts.join(""),
    applied: kept.map((r) => r.index),
    conflicts,
    invalidRanges,
  };
}

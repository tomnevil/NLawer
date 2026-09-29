#!/usr/bin/env python
"""决策探针：`BottomSheet` 的 `scrollOwner="content"` 槽位该写成什么？

## 为什么要测

PRD **NR-11** 点名要求通知中心移动 BottomSheet「列表可滚动、**支持下拉刷新**」。
但 `BottomSheet` 与 `PullToRefresh` **各自带滚动容器**：

| 组件 | 滚动容器 |
|---|---|
| `BottomSheet` | `BottomSheet.tsx` **硬编码** `scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4` |
| `PullToRefresh` | `PullToRefresh.tsx` 自带 `ref={scrollRef}` + `overflow-y-auto overscroll-contain` |

`PullToRefresh` 的手势守卫是 `if (!el || el.scrollTop > 0) return` ——
它**假设自己那个容器就是真正在滚的那个**。若真正滚动的是外层，内层 `scrollTop` 恒为 0
⇒ **守卫失效** ⇒ 列表滚到中段后上滑，仍会被判成「在顶部」而触发刷新。
（第一轮取证：`evidence/probe_pulltorefresh_in_sheet.py`，臂 A 朴素嵌套不可用。）

⇒ 所以 §9 第 10 项裁定 **①**：给 `BottomSheet` 加「把滚动权交给调用方」的开关
（= `scrollOwner="content"`）。**本探针回答的是「那个槽位具体怎么写」。**

## 五臂（合成页，与产品无关）

| 臂 | 槽位 | 子元素 | 期望 |
|---|---|---|---|
| **P** | `grid` + `grid-template-rows: repeat(1, minmax(0,1fr))` | 单子元素（无 `min-h-0`） | ✅ 成立 |
| **Q** | `flex` 列 | 单子元素（**无** `min-h-0`） | ❌ **内层滚不动** |
| **R** | `flex` 列 | 单子元素（有 `min-h-0`） | ✅ 成立 |
| **S** | `flex` 列 | **三个**子元素（表头 / PTR / 页脚） | ✅ 成立 |
| **F** | `grid` + `grid-rows-1` | **三个**子元素 | ❌ **第一个拿不到自然高度**（实测 13px vs 34px） |

「成立」= 内层可滚 **且** 内层能表达「不在顶部」（守卫可用）**且** 槽位不滚
**且** 各子元素未被压扁。

## 🚨 为什么判定要查四项（本轮踩过的坑）

本探针的第一版（临时脚本，未入仓）判定**只查 `innerOverflows`** ⇒ F 臂原始测量是
`调用方高 = 0`（表头**隐形**），判定却印出「契约可放宽」—— **与自己的原始数字矛盾**。
⇒ 若照判定读，会得出**相反的设计结论**（放宽契约 ⇒ 真机上表头不可见）。
已提炼为 `.workbuddy-ai/memory/methodology.md` **#204**（同族 **#207**：只钉一个信号
⇒ 另一个方向没有任何判据）。
⇒ 因此本探针**同时打印原始测量与判定**，且判定覆盖「不成立」的**多种形态**。

### 🚨 同一坑的第二层（同日，入仓时又踩到）

第二版判定把「被压扁」写成 `height == 0` —— **仍然太窄**：F 臂的表头换成
**带内边距**的元素（`.pin` 有 `padding:6px 16px`）后，它缩到 **13px** 而不是 0
（`== 0` 判不出来 ⇒ 又被判成「成立」，探针 exit 2 报「与预期不符」）。
⇒ 正确判法是**跨臂比较自然高度**：同一个表头元素在**臂 S（`flex`，已知正确）**里的高度
就是它的**自然高度**；臂 F（`grid`）若对不上 ⇒ 被压扁。
⇒ **绝对阈值（`== 0`）不如相对比较** —— 阈值要猜「多小才算坏」，相对比较不用猜。

## 结论（2026-09-26 实测）

- **槽位必须用 `flex` 列** —— `grid-rows-1` 只支持**单**子元素，而通知中心的内容天然是
  「表头 / 滚动列表 / 页脚」**三段**（表头页脚要钉住）。
- `flex` 槽位下调用方有**两条义务**：① 滚动层 `flex-1`；
  ② 再包一层 `flex` 时必须带 `min-h-0`（否则**静默失效**，不报错）。
- 契约见 `deliverables/ui-design/mobile-feature-integration-spec.md` §5「✅ 定稿」。

**这是决策探针，不是缺陷审计** —— 它不判产品对错，只回答「槽位怎么写」。
⇒ 退出码只有 `0`（测成且各臂与预期一致）/ `2`（环境问题，或**结果与预期不符 ⇒ 先怀疑探针**）。

用法：

    python evidence/probe_sheet_scroll_contract.py
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cdp import Browser  # noqa: E402

ROWS = 40
ROW_H = 40
SHEET_H = 400

_CSS = """
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 system-ui, sans-serif; }
  .sheet { position:absolute; left:0; right:0; bottom:0; display:flex; flex-direction:column;
           height:%(sheet)dpx; border-top:1px solid #ccc; background:#fff; }
  .sheet-head { flex:0 0 auto; padding:8px 16px; border-bottom:1px solid #eee; }
  /* 候选槽位：grid —— 只支持单子元素 */
  .slot-grid { display:grid; min-height:0; flex:1 1 0%%;
               grid-template-rows: repeat(1, minmax(0,1fr)); }
  /* 候选槽位：flex 列 —— 支持 N 个子元素（**定稿采用**） */
  .slot-flex { display:flex; flex-direction:column; min-height:0; flex:1 1 0%%; }
  .caller      { display:flex; flex-direction:column; }                /* 无 min-height:0 */
  .caller-safe { display:flex; flex-direction:column; min-height:0; }  /* 有 min-height:0 */
  .pin  { flex:0 0 auto; padding:6px 16px; border-bottom:1px solid #eee; }
  .foot { flex:0 0 auto; padding:6px 16px; border-top:1px solid #eee; }
  /* PullToRefresh 根的等价物（根 flex-1 + min-h-0） */
  .ptr-root { position:relative; display:flex; min-height:0; flex-direction:column; flex:1 1 0%%; }
  /* PullToRefresh 内层滚动容器的等价物 */
  .ptr-scroll { min-height:0; flex:1 1 0%%; overflow-y:auto; overscroll-behavior:contain; }
  .row { height:%(row)dpx; border-bottom:1px solid #f0f0f0; padding:8px 4px; }
  .tag { position:fixed; top:0; left:0; z-index:9; background:#eee; padding:2px 6px; }
""" % {"sheet": SHEET_H, "row": ROW_H}


def _rows(n: int = ROWS) -> str:
    return "".join(f'<div class="row">第 {i + 1} 条</div>' for i in range(n))


def _ptr() -> str:
    return f'<div class="ptr-root" data-root><div class="ptr-scroll" data-inner>{_rows()}</div></div>'


def _three() -> str:
    """表头 / PTR / 页脚 —— 与 `NotificationCenter` 的三段装配同形。"""
    return (f'<div class="pin" data-head>通知 / 全部已读</div>{_ptr()}'
            f'<div class="foot" data-foot>查看全部通知</div>')


def _page(arm: str) -> str:
    if arm == "P":
        slot, inner_html = "slot-grid", f'<div class="caller" data-caller>{_ptr()}</div>'
    elif arm == "Q":
        slot, inner_html = "slot-flex", f'<div class="caller" data-caller>{_ptr()}</div>'
    elif arm == "R":
        slot, inner_html = "slot-flex", f'<div class="caller-safe" data-caller>{_ptr()}</div>'
    elif arm == "S":
        slot, inner_html = "slot-flex", _three()
    else:  # F：grid 槽位塞三个子元素
        slot, inner_html = "slot-grid", _three()
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='tag'>arm {arm}</div>"
        f'<div class="sheet" data-sheet><div class="sheet-head">把手</div>'
        f'<div class="{slot}" data-slot>{inner_html}</div></div>'
        "</body></html>"
    )


#: (臂, 说明, 期望「成立」)
ARMS: tuple[tuple[str, str, bool], ...] = (
    ("P", "槽位 grid + 单子元素（无 min-h-0）", True),
    ("Q", "槽位 flex + 单子元素（**无** min-h-0）", False),
    ("R", "槽位 flex + 单子元素（有 min-h-0）", True),
    ("S", "槽位 flex + **三**个子元素（表头 / PTR / 页脚）", True),
    ("F", "槽位 grid + **三**个子元素", False),
)

MEASURE_JS = """(() => {
  const q = (s) => document.querySelector(s);
  const h = (el) => el ? Math.round(el.getBoundingClientRect().height) : null;
  const slot = q('[data-slot]'), inner = q('[data-inner]');
  const head = q('[data-head]'), foot = q('[data-foot]'), caller = q('[data-caller]');
  if (slot) slot.scrollTop = 999999;
  if (inner) inner.scrollTop = 999999;
  const i = inner ? { clientH: inner.clientHeight, scrollH: inner.scrollHeight, top: inner.scrollTop } : null;
  return {
    slotH: h(slot), callerH: h(caller), headH: h(head), footH: h(foot), innerH: h(inner),
    innerClientH: i ? i.clientH : null,
    innerScrollH: i ? i.scrollH : null,
    innerTop: i ? i.top : null,
    innerOverflows: !!(i && i.scrollH > i.clientH + 1),
    innerCanReportNotTop: !!(i && i.top > 0),
    slotScrolls: !!(slot && slot.scrollTop > 0),
  };
})()"""


def _n(v: object, w: int = 5) -> str:
    """格式化测量值：`None` 表示该臂**故意没有这个元素**（如单子元素臂没有表头）⇒ 打印 `—`。"""
    return f"{v:>{w}}" if isinstance(v, int) else f"{'—':>{w}}"


def holds(r: dict) -> bool:
    """「成立」的四项合取 —— ⚠️ **不能只查 `innerOverflows`**（见模块 docstring 的坑）。"""
    if not r:
        return False
    collapsed = [k for k in ("callerH", "headH", "footH") if r.get(k) == 0]
    return bool(
        r.get("innerOverflows")
        and r.get("innerCanReportNotTop")
        and not r.get("slotScrolls")
        and not collapsed
    )


async def run() -> int:
    results: dict[str, dict] = {}
    async with Browser(headless=True, width=390, height=844) as b:
        # 桌面仿真：合成页用**固定 px 高度**，不依赖视口；且 `data:` URL 上没有真实文档来源，
        # 移动仿真不认 `width=device-width`（见 `verify_mobile_375.py` 的那次教训）。
        await b.apply_device(safe_area=None, mobile=False)
        for arm, desc, _expect in ARMS:
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(_page(arm))
            await b.goto(url, wait=0.45)
            r = await b.cdp.evaluate(MEASURE_JS) or {}
            results[arm] = r
            print(f"\n── 臂 {arm}：{desc}")
            if not r:
                print("   [env] 未取到测量结果")
                return 2
            print(f"   槽位高={_n(r['slotH'], 4)}  调用方高={_n(r['callerH'], 4)}  "
                  f"表头高={_n(r['headH'], 4)}  页脚高={_n(r['footH'], 4)}  内层盒高={_n(r['innerH'], 4)}")
            print(f"   内层 clientH={_n(r['innerClientH'])}  scrollH={_n(r['innerScrollH'])}  "
                  f"滚到底 scrollTop={_n(r['innerTop'], 4)}")
            print(f"   → 内层可滚={r['innerOverflows']}  能表达「不在顶部」={r['innerCanReportNotTop']}  "
                  f"槽位在滚={r['slotScrolls']}")

    print("\n" + "=" * 74)
    bad: list[str] = []
    # **自然高度**：同一个表头元素在**臂 S（`flex`，已知正确）**里的高度。
    # ⚠️ F 臂不能只判 `headH == 0` —— 带内边距的元素会缩到 13px 而不是 0（见 docstring 第二层）。
    natural_head = results.get("S", {}).get("headH")
    if natural_head is None:
        print("[env] 臂 S 未取到表头高 ⇒ 无自然高度基准，F 臂无法判定（不猜）", file=sys.stderr)
        return 2
    print(f"自然高度基准（臂 S 的表头）= {natural_head}px\n")
    print("判定（『布局正确』= 内层可滚 且 守卫可用 且 槽位不滚 且 各子元素未被压扁）：")
    for arm, _desc, expect in ARMS:
        r = results.get(arm, {})
        if arm == "F":
            # grid 槽位只支持单子元素：3 个子元素时**第一个拿不到自然高度**。
            got = r.get("headH") == natural_head
            note = f"  （表头高 {r.get('headH')} vs 自然高度 {natural_head}）"
        else:
            got = holds(r)
            collapsed = [k for k in ("callerH", "headH", "footH") if r.get(k) == 0]
            note = f"  ⚠️ 被压成 0 高：{collapsed}" if collapsed else ""
        ok = got == expect
        print(f"  [{arm}] 期望{'布局正确' if expect else '布局不正确'} "
              f"实测{'布局正确' if got else '布局不正确'} {'✓' if ok else '✗'}{note}")
        if not ok:
            bad.append(f"[{arm}] 期望{'布局正确' if expect else '布局不正确'}，"
                       f"实测{'布局正确' if got else '布局不正确'}")

    if bad:
        print("\n⚠️ 有臂的结果与预期不符 —— **先怀疑探针本身**，别急着下结论：")
        for x in bad:
            print(f"   - {x}")
        return 2

    print("\n结论（与 §9 第 10 项定稿 ① 一致）：")
    print("  · 槽位**必须用 `flex` 列** —— `grid-rows-1` 只支持**单**子元素")
    print("    （臂 F 的表头被压到 13px，其自然高度是 34px）；")
    print("    而通知中心的内容天然是「表头 / 滚动列表 / 页脚」**三段**（臂 S 成立）。")
    print("  · `flex` 槽位下调用方**两条义务**：① 滚动层 `flex-1`；")
    print("    ② 再包一层 `flex` 时必须带 `min-h-0`（否则臂 Q 的形态：内层 `scrollTop` 恒 0、**静默失效**）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="决策探针：BottomSheet 的 scrollOwner=content 槽位该用 grid 还是 flex"
    )
    ap.parse_args()
    try:
        return asyncio.run(run())
    except Exception as e:  # 环境问题（起不来浏览器等）
        print(f"[env] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

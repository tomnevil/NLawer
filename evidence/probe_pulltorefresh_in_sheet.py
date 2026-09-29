#!/usr/bin/env python
"""决策探针：`PullToRefresh` 能不能装进 `BottomSheet` 而不套两层滚动？

## 为什么要测

PRD **NR-11** 点名要求通知中心移动 BottomSheet「列表可滚动、**支持下拉刷新**」。
但两个组件**各自带滚动容器**：

| 组件 | 滚动容器 |
|---|---|
| `BottomSheet` | `BottomSheet.tsx:170` **硬编码** `<div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">` |
| `PullToRefresh` | `PullToRefresh.tsx:126-132` 自带 `ref={scrollRef}` + `overflow-y-auto overscroll-contain` |

⇒ 直接套会是**两层嵌套滚动**。

## 为什么两层就坏了（不是「多一层」这么轻）

`PullToRefresh` 的手势守卫是 `if (el.scrollTop > 0) return`（`:55` / `:62`）——
它假设**自己那个容器就是真正在滚的那个**。

若真正滚动的是**外层**，内层的 `scrollTop` 会**恒为 0**，于是：

> 列表已经滚到第 50 条，用户**往上**滑想继续看 ⇒ 内层仍判 `scrollTop === 0` ⇒
> 触发下拉刷新，列表被拉走。

这正是那条守卫要防的事，**守卫本身失效了**。

## 本探针做什么

**合成页面**（与产品无关），按两个组件的**真实 class 结构**搭出三臂，量：

- **P1** 内层滚动容器有没有**确定高度**（`clientHeight < scrollHeight` ⇒ 内层真能滚）
- **P2** 实际滚动发生在**哪一层**（设 `scrollTop = 99999`，看谁的 `scrollTop` 变了）
- **P3** 因此 `PullToRefresh` 的 `scrollTop > 0` 守卫**还成不成立**
  （A 臂内层 `scrollTop` 恒 0 ⇒ 守卫失效）

**这是决策探针，不是缺陷审计**——它不判产品对错，只回答「哪种接法可行」。
⇒ 退出码只有 `0`（测成）/ `2`（环境问题），**没有 `1`**。

用法：

    python evidence/probe_pulltorefresh_in_sheet.py
    python evidence/probe_pulltorefresh_in_sheet.py --shot
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

# ---------------------------------------------------------------------------
# 三臂。class 名沿用真实组件里的写法，但本页**自带等价 CSS**
#（合成页没有 Tailwind 构建产物）。
#
#   A  朴素嵌套：PullToRefresh 根**不加高度** ⇒ 内层撑到内容高、外层滚
#   B  根加 height:100% ⇒ 内层拿到确定高度、内层滚
#   C  对照：只有内层（= BottomSheet 把滚动权交给调用方）
# ---------------------------------------------------------------------------
_CSS = """
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 system-ui, sans-serif; }
  .sheet {
    position: absolute; left: 0; right: 0; bottom: 0;
    display: flex; flex-direction: column; height: %(sheet)dpx;
    border-top: 1px solid #ccc; background: #fff;
  }
  .sheet-head { flex: 0 0 auto; padding: 8px 16px; border-bottom: 1px solid #eee; }
  /* BottomSheet.tsx:170 的等价物 */
  .bs-content { min-height: 0; flex: 1 1 0%%; overflow-y: auto; padding: 16px; }
  /* PullToRefresh.tsx:97 的等价物 */
  .ptr-root { position: relative; display: flex; min-height: 0; flex-direction: column; }
  .ptr-root.hfull { height: 100%%; }
  /* PullToRefresh.tsx:126-132 的等价物 */
  .ptr-scroll { min-height: 0; flex: 1 1 0%%; overflow-y: auto; overscroll-behavior: contain; }
  .row { height: %(row)dpx; border-bottom: 1px solid #f0f0f0; padding: 8px 4px; }
  .tag { position: fixed; top: 0; left: 0; z-index: 9; background: #eee; padding: 2px 6px; }
""" % {"sheet": SHEET_H, "row": ROW_H}


def _rows() -> str:
    return "".join(f'<div class="row">第 {i + 1} 条</div>' for i in range(ROWS))


def _page(arm: str) -> str:
    if arm == "A":  # 朴素嵌套
        body = f"""
          <div class="sheet" data-sheet>
            <div class="sheet-head">通知中心</div>
            <div class="bs-content" data-outer>
              <div class="ptr-root" data-root>
                <div class="ptr-scroll" data-inner>{_rows()}</div>
              </div>
            </div>
          </div>"""
    elif arm == "B":  # 根加 height:100%
        body = f"""
          <div class="sheet" data-sheet>
            <div class="sheet-head">通知中心</div>
            <div class="bs-content" data-outer>
              <div class="ptr-root hfull" data-root>
                <div class="ptr-scroll" data-inner>{_rows()}</div>
              </div>
            </div>
          </div>"""
    else:  # C 对照：只有内层
        body = f"""
          <div class="sheet" data-sheet>
            <div class="sheet-head">通知中心</div>
            <div class="ptr-root hfull" data-root style="min-height:0;flex:1 1 0%">
              <div class="ptr-scroll" data-inner>{_rows()}</div>
            </div>
          </div>"""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='tag'>arm {arm}</div>{body}</body></html>"
    )


ARMS = (
    ("A", "朴素嵌套（PullToRefresh 根**不加高度**）"),
    ("B", "根加 `height:100%`"),
    ("C", "对照：只有内层滚动（= BottomSheet 把滚动权交给调用方）"),
)

MEASURE_JS = """(() => {
  const q = (s) => document.querySelector(s);
  const outer = q('[data-outer]'), inner = q('[data-inner]'), root = q('[data-root]');
  const m = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { clientH: el.clientHeight, scrollH: el.scrollHeight, scrollTop: el.scrollTop, overflowY: cs.overflowY };
  };
  const before = { root: m(root), outer: m(outer), inner: m(inner) };
  // 两层都试滚到底，看谁**真的**能滚
  if (outer) outer.scrollTop = 999999;
  if (inner) inner.scrollTop = 999999;
  const after = { outer: m(outer), inner: m(inner) };
  // P3：守卫是否还能成立 —— 内层能不能表达「不在顶部」
  const innerCanReportNotTop = !!(after.inner && after.inner.scrollTop > 0);
  const innerOverflows = !!(after.inner && after.inner.scrollH > after.inner.clientH + 1);
  const outerScrolls = !!(after.outer && after.outer.scrollTop > 0);
  return {
    vw: innerWidth, vh: innerHeight,
    rootH: root ? Math.round(root.getBoundingClientRect().height) : null,
    innerClientH: after.inner ? after.inner.clientH : null,
    innerScrollH: after.inner ? after.inner.scrollH : null,
    innerScrollTopAfter: after.inner ? after.inner.scrollTop : null,
    outerClientH: after.outer ? after.outer.clientH : null,
    outerScrollH: after.outer ? after.outer.scrollH : null,
    outerScrollTopAfter: after.outer ? after.outer.scrollTop : null,
    innerOverflows, outerScrolls, innerCanReportNotTop,
  };
})()"""


def _n(v: object, w: int = 5) -> str:
    """格式化测量值：`None` 表示该臂**故意没有这个元素**（如对照臂没有外层），打印成 `—`。"""
    return f"{v:>{w}}" if isinstance(v, int) else f"{'—':>{w}}"


async def run(shot: bool) -> int:
    results: dict[str, dict] = {}
    async with Browser(headless=True, width=390, height=844) as b:
        # 桌面仿真：合成页用**固定 px 高度**，不依赖视口；且 `data:` URL 上没有真实文档来源，
        # 移动仿真不认 `width=device-width`（见 `verify_mobile_375.py` 的那次教训）。
        await b.apply_device(safe_area=None, mobile=False)
        for arm, desc in ARMS:
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(_page(arm))
            await b.goto(url, wait=0.45)
            r = await b.cdp.evaluate(MEASURE_JS)
            results[arm] = r or {}
            print(f"\n── 臂 {arm}：{desc}")
            r = results[arm]
            if not r:
                print("   [env] 未取到测量结果")
                return 2
            print(f"   根高 {r['rootH']}px")
            print(f"   外层 .bs-content   clientH={_n(r['outerClientH'])}  scrollH={_n(r['outerScrollH'])}  "
                  f"滚到底后 scrollTop={_n(r['outerScrollTopAfter'], 4)}")
            print(f"   内层 .ptr-scroll   clientH={_n(r['innerClientH'])}  scrollH={_n(r['innerScrollH'])}  "
                  f"滚到底后 scrollTop={_n(r['innerScrollTopAfter'], 4)}")
            print(f"   → 内层可滚={r['innerOverflows']}  外层在滚={r['outerScrolls']}  "
                  f"内层能表达「不在顶部」={r['innerCanReportNotTop']}")
            if shot:
                p = HERE / f"shot_ptr_in_sheet_{arm}.png"
                await b.screenshot(p, full=False)
                print(f"   截图 {p.name}")

    print("\n" + "=" * 74)
    print("判定：")
    a, b_, c = results.get("A", {}), results.get("B", {}), results.get("C", {})
    problems: list[str] = []

    # —— P1/P2：谁在滚 ——
    if a.get("outerScrolls") and not a.get("innerOverflows"):
        print("  [A] 朴素嵌套 ⇒ **外层在滚、内层滚不动**（内层 clientH == scrollH）")
    else:
        problems.append(f"[A] 预期「外层在滚、内层滚不动」，实测 outerScrolls={a.get('outerScrolls')} "
                        f"innerOverflows={a.get('innerOverflows')}")
    if not a.get("innerCanReportNotTop"):
        print("      ⇒ 内层 `scrollTop` 恒为 0 ⇒ `PullToRefresh` 的 `scrollTop > 0` 守卫**失效**：")
        print("         列表滚到中段后上滑，仍会被判成「在顶部」而触发刷新。")
    else:
        problems.append("[A] 预期内层无法表达「不在顶部」，实测可以")

    if b_.get("innerOverflows") and not b_.get("outerScrolls"):
        print("  [B] 根加 `height:100%` ⇒ **内层独占滚动、外层不再滚** ⇒ 守卫可用 ✓")
    else:
        problems.append(f"[B] 预期「内层独占滚动」，实测 innerOverflows={b_.get('innerOverflows')} "
                        f"outerScrolls={b_.get('outerScrolls')}")

    if c.get("innerOverflows") and not c.get("outerScrolls"):
        print("  [C] 对照（单层）⇒ 内层独占滚动 ✓（与 B 同结果，说明 B 的写法把结构收敛回了单层）")
    else:
        problems.append(f"[C] 对照臂不干净：innerOverflows={c.get('innerOverflows')} outerScrolls={c.get('outerScrolls')}")

    if problems:
        print("\n⚠️ 有臂的结果与预期不符 —— **先怀疑探针本身**，别急着下结论：")
        for p in problems:
            print(f"   - {p}")
        return 2

    print("\n结论：**`BottomSheet` 的内容区目前会和外层滚动打架**。")
    print("  · 朴素嵌套**不可用**（守卫失效，刷新手势会无视滚动位置触发）；")
    print("  · 加 `height:100%` 能收敛成单层，但它**依赖外层的 padding / flex 恰好如此**——")
    print("    属于「碰巧对」，不是「由构造对」。")
    print("  ⇒ 建议给 `BottomSheet` 增加一个「把滚动权交给调用方」的开关")
    print("    （或让 `PullToRefresh` 接受外部滚动元素），再接 NR-11。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="决策探针：PullToRefresh 装进 BottomSheet 会不会套两层滚动")
    ap.add_argument("--shot", action="store_true", help="每臂出一张视口截图")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args.shot))
    except Exception as e:  # 环境问题（起不来浏览器等）
        print(f"[env] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

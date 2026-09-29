#!/usr/bin/env python
"""判据探针：`PullToRefresh` 的 `scrollTarget`（外部滚动元素）契约。

## 为什么要测

`PullToRefresh` 的手势守卫是「**内容已在顶部才接管**」：

    if (!el || el.scrollTop > 0) return;      // el = 它**自己那个内层容器**

⇒ 它**假设自己那个容器就是真正在滚的那个**。而走 `AppShell` 的页面（律师端 / 运营端）
由**文档**滚动：根是 `min-h-screen`、`<main>` **没有 `overflow`** ⇒ 内层容器
**永远不会滚**、`scrollTop` 恒 0 ⇒ 守卫失效（列表滚到中段上滑**仍会触发刷新**）。

实测结论见 `deliverables/ui-design/mobile-feature-integration-spec.md` §5.1
「两个被实测挡住的落点」一（原始取证：`evidence/probe_pulltorefresh_in_sheet.py` 臂 A）。

⇒ §9 第 10 项之后的拍板：给 `PullToRefresh` 加 **`scrollTarget`**（外部滚动目标），
而不是去改 `AppShell` 的 `<main>`（后者影响四端**全部**页面，爆炸半径大）。

## 三臂（合成页，与产品无关）

| 臂 | 形态 | 期望 |
|---|---|---|
| **D** | 调用方给根**确定高度** + **不传** `scrollTarget`（= im 的 `h-dvh` 形态） | ✅ 内层可滚、能表达「不在顶部」⇒ **默认行为不变** |
| **A** | 页面由**文档**滚动 + **不传** `scrollTarget`（= 修之前的现状） | ❌ 内层滚不动（`scrollTop` 恒 0）⇒ **守卫瞎** |
| **W** | 页面由**文档**滚动 + `scrollTarget="window"`（= 修好之后） | ✅ 文档可滚、内层**不再是滚动容器**、内容未被压扁 |

「成立」对 **W** 是四项合取：文档可滚 **且** 内层 `overflow-y` 计算值为 `visible`
（⇒ 触摸滚动会**链到文档**，不是被内层吞掉）**且** 根高 > 0 **且** 内层高 > 0。

### 🚨 为什么要查 `overflow-y` 的**计算值**，而不是只看「内层滚不动」

臂 A 与臂 W 的「内层滚不动」是**同一个测量结果**（`scrollHeight == clientHeight`），
但成因相反：A 是**内层拿到了滚动权却无处可滚**，W 是**内层根本不持有滚动权**。
只查「滚不动」⇒ **A 与 W 无法区分** ⇒ 会把「没修」读成「修好了」。
`getComputedStyle(inner).overflowY` 才把两者分开（`auto` vs `visible`）。
（同族坑：`.workbuddy-ai/memory/methodology.md` #204 / #207 —— 只钉一个信号 ⇒
另一个方向没有判据。）

## 🚨 本探针证不了什么

它测的是**合成页的 CSS 形态**，不是 `PullToRefresh.tsx` 本身。所以它**证明不了**
「守卫真的读了外部目标」。后半段的**源码级地板**（断言那段分支还在）只能**挡回退**，
**不能证真** —— 与 `verify_component_wiring.py` 自己的表态一致。

⇒ **真机渲染验证仍走 `browser-all` job**（四端生产构建 + 隔离库 + 真后端）。

## 退出码

- `0` 通过（三臂与预期一致，且源码级地板全在）
- `2` 环境问题，或**结果与预期不符 ⇒ 先怀疑探针**（决策探针不判产品缺陷）

用法：

    python evidence/probe_ptr_scroll_target.py
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

ROOT = HERE.parent
PTR_TSX = ROOT / "frontend" / "packages" / "ui" / "src" / "components" / "mobile" / "PullToRefresh.tsx"

ROWS = 40
ROW_H = 40
ROOT_FIXED_H = 600

_CSS = """
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 system-ui, sans-serif; }
  /* AppShell 主区等价物：根 min-h-screen + <main> flex-1，**没有 overflow** ⇒ 文档在滚 */
  .shell { min-height: 100vh; display: flex; flex-direction: column; }
  .main  { flex: 1 1 auto; padding: 16px; }
  /* PullToRefresh 根等价物：`relative flex min-h-0 flex-col` */
  .ptr-root { position: relative; display: flex; min-height: 0; flex-direction: column; }
  /* 臂 D：调用方给根一个确定高度（im 的 `h-dvh` / `className="h-full"` 形态） */
  .ptr-root-fixed { height: %(fixed)dpx; }
  /* 内层·默认模式 —— 与组件里的字面量逐字同形：
     `scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain` */
  .ptr-inner-default { min-height: 0; flex: 1 1 0%%; overflow-y: auto; overscroll-behavior: contain; }
  /* 内层·外部模式 —— `overflow-visible`，**不是**滚动容器 */
  .ptr-inner-external { overflow: visible; }
  .row { height: %(row)dpx; border-bottom: 1px solid #f0f0f0; padding: 8px 4px; }
""" % {"fixed": ROOT_FIXED_H, "row": ROW_H}


def _rows(n: int = ROWS) -> str:
    return "".join(f'<div class="row">第 {i + 1} 条</div>' for i in range(n))


def _page(arm: str) -> str:
    if arm == "D":  # 确定高度容器 + 不传 scrollTarget
        root_cls, inner_cls = "ptr-root ptr-root-fixed", "ptr-inner-default"
    elif arm == "A":  # 文档滚动 + 不传 scrollTarget（现状）
        root_cls, inner_cls = "ptr-root", "ptr-inner-default"
    else:  # W：文档滚动 + scrollTarget="window"（修好之后）
        root_cls, inner_cls = "ptr-root", "ptr-inner-external"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="shell"><div class="main">'
        f'<div class="{root_cls}" data-root>'
        f'<div class="{inner_cls}" data-inner>{_rows()}</div>'
        "</div></div></div>"
        "</body></html>"
    )


#: (臂, 说明, 期望「这个形态的特征成立」)
#: ⚠️ 三臂都期望 `True` —— 每臂断言的是「**它这个形态的特征如描述**」，
#: 包括臂 A（它**就是要**复现「守卫瞎」，所以「内层滚不动」是**预期**结果，不是失败）。
ARMS: tuple[tuple[str, str, bool], ...] = (
    ("D", "确定高度容器 + 不传 scrollTarget —— 应：内层仍是滚动容器且真的能滚", True),
    ("A", "文档滚动 + 不传 scrollTarget —— 应：文档在滚、内层滚不动（守卫瞎的成因）", True),
    ("W", "文档滚动 + scrollTarget=window —— 应：文档在滚、内层 overflow-y=visible", True),
)

MEASURE_JS = """(() => {
  const q = (s) => document.querySelector(s);
  const h = (el) => el ? Math.round(el.getBoundingClientRect().height) : null;
  const se = document.scrollingElement || document.documentElement;
  const root = q('[data-root]'), inner = q('[data-inner]');
  // 文档能不能滚：先滚到底看 scrollTop 能不能 > 0
  const keep = se.scrollTop;
  se.scrollTop = 999999;
  const docTopMax = se.scrollTop;
  se.scrollTop = keep;
  if (inner) inner.scrollTop = 999999;
  const i = inner ? { clientH: inner.clientHeight, scrollH: inner.scrollHeight, top: inner.scrollTop } : null;
  return {
    docScrollH: se.scrollHeight,
    docClientH: se.clientHeight,
    docTopMax: docTopMax,
    docScrolls: docTopMax > 0,
    rootH: h(root),
    innerH: h(inner),
    innerOverflowY: inner ? getComputedStyle(inner).overflowY : null,
    innerClientH: i ? i.clientH : null,
    innerScrollH: i ? i.scrollH : null,
    innerTop: i ? i.top : null,
    innerOverflows: !!(i && i.scrollH > i.clientH + 1),
    innerCanReportNotTop: !!(i && i.top > 0),
  };
})()"""


def _n(v: object, w: int = 5) -> str:
    return f"{v:>{w}}" if isinstance(v, int) else f"{'—':>{w}}"


def holds(arm: str, r: dict) -> bool:
    """各臂的「符合预期」判据 —— ⚠️ **不能只看「内层滚不动」**（见模块 docstring）。"""
    if not r:
        return False
    if arm == "D":
        # 默认模式不变：内层仍是滚动容器（overflow-y:auto）**且**真的能滚、能表达「不在顶部」
        return bool(
            r.get("innerOverflowY") == "auto"
            and r.get("innerOverflows")
            and r.get("innerCanReportNotTop")
        )
    if arm == "A":
        # 现状：文档在滚，但内层仍是滚动容器且**滚不动** ⇒ 守卫读内层时恒「在顶部」
        return bool(
            r.get("docScrolls")
            and r.get("innerOverflowY") == "auto"
            and not r.get("innerOverflows")
        )
    # W：文档在滚 + 内层**不是**滚动容器（触摸滚动会链到文档）+ 内容没被压扁
    return bool(
        r.get("docScrolls")
        and r.get("innerOverflowY") == "visible"
        and (r.get("rootH") or 0) > 0
        and (r.get("innerH") or 0) > 0
    )


# ---------------------------------------------------------------------------
# 源码级地板：**只挡回退，不能证真**（同 `verify_component_wiring.py` 的表态）。
# ---------------------------------------------------------------------------
DEFAULT_CLASS = "scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain"

SOURCE_FLOOR: tuple[tuple[str, str], ...] = (
    ("scrollTarget 的公开类型存在", "export type PullToRefreshScrollTarget"),
    ("类型支持 window（文档滚动）", '"window"'),
    ("外部目标读取函数存在", "function readExternalScrollTop"),
    ("守卫按「传没传 scrollTarget」分流", "if (scrollTarget === undefined)"),
    ("默认模式的类名逐字未变", DEFAULT_CLASS),
    ("外部模式的类名不是滚动容器", '"overflow-visible"'),
    ("取不到目标时返回 null（不降级成 0）", "return el ? el.scrollTop : null"),
)


def check_source_floor() -> list[tuple[str, bool]]:
    """返回 [(标签, 是否命中)]；**文件不存在 ⇒ 全 False**（并已由调用方判成环境问题）。"""
    if not PTR_TSX.exists():
        return [(label, False) for label, _ in SOURCE_FLOOR]
    src = PTR_TSX.read_text(encoding="utf-8", errors="replace")
    return [(label, needle in src) for label, needle in SOURCE_FLOOR]


async def run() -> int:
    results: dict[str, dict] = {}
    async with Browser(headless=True, width=390, height=844) as b:
        # 桌面仿真：合成页用**固定 px 高度**，不依赖视口；且 `data:` URL 上没有真实文档来源，
        # 移动仿真不认 `width=device-width`（同 `probe_sheet_scroll_contract.py` 的教训）。
        await b.apply_device(safe_area=None, mobile=False)
        for arm, desc, _expect in ARMS:
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(_page(arm))
            await b.goto(url, wait=0.45)
            r = await b.cdp.evaluate(MEASURE_JS) or {}
            results[arm] = r
            print(f"\n-- 臂 {arm}：{desc}")
            if not r:
                print("   [env] 未取到测量结果")
                return 2
            print(f"   文档 scrollH={_n(r['docScrollH'])}  clientH={_n(r['docClientH'])}  "
                  f"滚到底 scrollTop={_n(r['docTopMax'], 4)}  -> 文档可滚={r['docScrolls']}")
            print(f"   根高={_n(r['rootH'], 4)}  内层盒高={_n(r['innerH'], 4)}  "
                  f"内层 overflow-y={r['innerOverflowY']}")
            print(f"   内层 clientH={_n(r['innerClientH'])}  scrollH={_n(r['innerScrollH'])}  "
                  f"滚到底 scrollTop={_n(r['innerTop'], 4)}")
            print(f"   -> 内层可滚={r['innerOverflows']}  "
                  f"能表达「不在顶部」={r['innerCanReportNotTop']}")

    print("\n" + "=" * 74)
    bad: list[str] = []
    print("判定（每臂断言的是「**它这个形态的特征如描述**」，含臂 A 要复现的那个坏形态）：")
    for arm, desc, expect in ARMS:
        r = results.get(arm, {})
        got = holds(arm, r)
        ok = got == expect
        note = ""
        if arm == "W":
            note = f"  （内层 overflow-y={r.get('innerOverflowY')}，应为 visible）"
        elif arm == "A":
            note = f"  （内层 overflow-y={r.get('innerOverflowY')}，应为 auto 且滚不动）"
        print(f"  [{arm}] 特征成立={'是' if got else '否'} 期望={'是' if expect else '否'} "
              f"{'[ok]' if ok else '[!!]'}  {desc}{note}")
        if not ok:
            bad.append(f"[{arm}] 特征成立={got}，期望={expect}（{desc}）")

    print("\n源码级地板（**只挡回退，不能证真**）：")
    floor = check_source_floor()
    if not PTR_TSX.exists():
        print(f"  [env] 找不到 {PTR_TSX}", file=sys.stderr)
        return 2
    for label, ok in floor:
        print(f"  {'[ok]' if ok else '[!!]'} {label}")
        if not ok:
            bad.append(f"源码级地板未命中：{label}")

    if bad:
        print("\n[!!] 有臂或地板不符 —— **先怀疑探针本身**，别急着下结论：")
        for x in bad:
            print(f"   - {x}")
        return 2

    print("\n结论（与 §5「两个被实测挡住的落点」一的拍板一致）：")
    print("  · 臂 D：不传 `scrollTarget` 时内层仍是滚动容器且真的能滚 ⇒ **默认行为逐字不变**。")
    print("  · 臂 A：页面由文档滚动时，内层 `scrollTop` 恒 0 ⇒ 守卫读内层就**瞎**（现状为何接不上）。")
    print("  · 臂 W：`scrollTarget=\"window\"` + 内层 `overflow-visible` ⇒ 文档接管滚动、")
    print("    内层不再是滚动容器、内容未被压扁 ⇒ 守卫改读文档即可成立。")
    print("  ⚠️ 本探针测的是合成页 CSS 形态；**真机渲染验证仍走 `browser-all` job**。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="判据探针：PullToRefresh 的 scrollTarget 契约")
    ap.parse_args()
    try:
        return asyncio.run(run())
    except Exception as e:  # 环境问题（起不来浏览器等）
        print(f"[env] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§8.3「相邻可点元素间距 ≥ 8px」门禁（渲染级）。

## 出处

`§8.3「触控目标与拇指热区」` §8.3：

> **触控目标**
> - 最小 **44 × 44px**（Apple HIG / WCAG 2.5.5）
> - 本项目标准 **48 × 48px**
> - **列表行高 ≥ 48px**；主按钮高度 48px
> - **相邻可点元素间距 ≥ 8px，避免误触**
> - 图标按钮必须有 48px 的**不可见热区**

⚠️ **本节有 5 条 bullet，本门禁只做第 4 条**：
「48px 不可见热区」由 `verify_tap_targets.py` 管（用 `elementFromPoint` 真的去戳）；
「按钮高度 48px」由 `verify_buttons.py` 管；「列表行高 ≥ 48px」由 §8.3 同节的**另一条**管。
⇒ **不重叠**。这一点很要紧：因为「列表行」那条的存在，**上下贴紧的行不算本条的缺陷**。

## 为什么这条判据极容易做成假红（实测踩到三类）

第一版朴素实现（「任意两个可点元素距离 < 8px 就报」）在 25 页上量到 **9 对**，
逐对打开看**只有 4 对是真的**：

| 类别 | 实测对数 | 真相 |
|---|---|---|
| **并排的小按钮** | **4 对** | ✅ **真的** —— `Pagination` 的页码按钮 `‹ 1 ›` 只隔 **4px**（根因 `Pagination.tsx:96` 的 `gap-1`） |
| **列表行之间** | 2 对 | ❌ **量的是 `border-b` 的 1px**，不是间距；且列表行由 §8.3「列表行高 ≥ 48px」管 |
| **固定底栏 vs 滚动内容** | 2 对 | ❌ 卡片在 `div.scroll-thin.overflow-y-auto` 里、按钮在 `nav.fixed.inset-x-0.bottom-0` 里 ⇒ **两层**，「水平距离」没有意义（卡片只是从底栏**下面**滚过去） |

⇒ 三条**已声明的排除规则**（E1–E3，见下）+ A1/A2/A3 分档。

⚠️ **E1 第一版写错了，而且全量跑才暴露**（见 `evidence/adjacent_full_run.txt`）：
第一版只查**元素自身**的 `computed position`，而 `im /` 上那个 `<BUTTON>` 自身是
`relative`、**它的祖先** `<nav class="fixed inset-x-0 bottom-0">` 才是固定层
⇒ E1 一对都没排除（`E1 排除固定/粘性层 0 个`），全量输出 **6 对**而不是 4 对。
**自检没抓到它**，因为注入的探针把 `position:fixed` 直接写在按钮自己身上（那才是第一版能识别的形态）
—— 这正是「自检的形态要覆盖真实缺陷的形态」。
⇒ 改成**成对**判据：**恰好一方处于固定/粘性层（含祖先）⇒ 跨层 ⇒ 不比较**（间距随滚动而变，无意义）；
**两方同层**（都固定、或都不固定）⇒ 照常比较。

## 🚨 判据设计的坑

1. **跨层不比较**（E1）：固定/粘性层与滚动层之间算「相邻」无意义。判据必须**沿祖先链**找固定层，
   只看元素自身的 `position` 会漏（实测：`im /` 稳报 2 条假红，卡片 vs TabBar）。
   但**不能**因此把「固定元素」整体丢掉 —— 那样**两个并排的固定按钮**（真实的误触风险）
   就永远查不出来。⇒ 排除要**成对**做，不要**逐个**做。
2. **上下贴紧 ≠ 并排贴紧**（A1 vs A2）：规范担心的是**误触**，主要发生在**并排**的小目标之间；
   上下堆叠是全宽列表/表单的常态。⇒ **A1 硬判、A2 只报**。
3. **`gap == 0` 多是「一个复合控件」**（E3）：实测 **67 对**（分段控件 / 标签栏 / 页码）。
   ⇒ 只报不判。**只量「< 8px」会把 67 对噪音淹没 4 对真缺陷** —— 这正是「假红掩盖真红」。
   ⚠️ 但「67 对」**不等于**「67 对同父」：第一版按 `parentElement.tagName` 判同父，误得 **67 对同父**；
   按**节点身份**重判只有 **3 对**（见坑 4）。⇒ 这类「统计数字」也要能被证伪。
4. **「同父」要按 DOM 节点身份判，不能按 tagName**：第一版比较 `parentElement.tagName`
   ⇒ `im` 列表里两个不同 `<li>` 下的 `<a>` 被误判成「同父」（都是 `LI`）。
5. **量到的「间隙」可能是边框**：`border-b` 1px 会让两行看起来「相隔 1px」。⇒ E2 用
   「全宽 + 上下 + ≤2px」把这类**列表行**归到 §8.3 的另一条 bullet。
6. 🚨 **「0 对」也可能是「页面没渲染出来」**（比「判据是空的」更隐蔽）：同一套代码连跑三次，
   其中一次只采到 **195 个**元素（正常 **260 个**）⇒ **A1 报 0 对、EXIT=0** —— 一次**假绿**。
   逐页覆盖把它钉死了：`lawyer` 六页**全是 5 个**（只剩 AppShell）、`web /billing` 8→5，
   而 `admin` / `im` **完全不变** ⇒ 是 `lawyer` 的**内容区**没渲染出来（API 失败 ⇒ 错误态）。
   ⚠️ **第一版守卫等「骨架屏消失」，实测一次都没触发** —— API 失败渲染的是**错误态而不是骨架屏**
   ⇒ **信号选错了**。   ⇒ 改成数「**内容区**可点元素数」（`content_coverage`）：
   固定/粘性层就是 AppShell，扣掉它；**内容区为 0 ⇒ 该页结论不可信 ⇒ 退出码 2**（不是 0）。
   同时输出**每页覆盖数**（`总数/内容区数`）让这类漂移**看得见**。
   ⚠️ **第二版守卫也错了，错在把「视口内」当成了「渲染出来」**：`web /billing` 的内容
   （4 个输入框 `y=965`/`1043`、按钮 `y=1100`，而视口只有 **844**）**全在首屏之下**
   ⇒ 采集时按视口筛，就把**好页面**判成「没渲染」，**连跑三次全部 exit 2**。
   ⇒ **判据问「此刻屏幕上有什么」，守卫问「内容区到底渲染出东西没有」——两个问题，分开问**：
   采集**不再**丢视口外元素，改成打 `vis` 标志；配对时只比较双方都 `vis` 的，
   覆盖率守卫则**不看视口**。

用法：
    python evidence/verify_adjacent_targets.py --self-test          # 纯函数自测，不起浏览器
    python evidence/verify_adjacent_targets.py                      # 全量（25 页，约 2.5 min）
    python evidence/verify_adjacent_targets.py --render-self-test   # 渲染层故障注入
    python evidence/verify_adjacent_targets.py --dump               # 打印原始量测

退出码：0 = 通过；1 = 产品缺陷；2 = 环境问题
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

#: §8.3 的线
MIN_GAP = 8.0
#: 只看近邻（超过这个距离就不算「相邻」）
MAX_GAP = 12.0
#: E2：全宽上下贴紧 ⇒ 列表行（由「列表行高 ≥ 48px」那条管）
LISTROW_MAX_GAP = 2.0
#: E2：宽度 ≥ 容器宽度的这个比例 ⇒ 算「全宽」
FULL_WIDTH_RATIO = 0.9

TAP_SELECTOR = ('a[href], button, input:not([type=hidden]), select, textarea, summary,'
                ' [role="button"], [role="tab"], [role="link"],'
                ' [tabindex]:not([tabindex="-1"])')

#: 就绪 / 覆盖率守卫。
#: ⚠️ **实测踩过（本门禁最危险的一次）**：同一套代码连跑三次，其中一次只采到 **195 个**
#: 元素（正常 **260 个**）⇒ **A1 报 0 对、EXIT=0** —— 一次**假绿**。
#: 逐页覆盖显示：`lawyer` 六页**全是 5 个**（只剩 AppShell）、`web /billing` 8→5，
#: 而 `admin` / `im` 完全不变 ⇒ **是 `lawyer` 的内容区没渲染出来**（API 失败 ⇒ 错误态）。
#: ⚠️ 第一版守卫等的是「骨架屏消失」，**实测一次都没触发** —— 因为 API 失败渲染的是
#: **错误态而不是骨架屏** ⇒ **信号选错了**。⇒ 改成数「**内容区**可点元素」：
#: 固定/粘性层（本项目实测只有 `header.sticky.top-0.z-topbar` 与
#: `nav.fixed.inset-x-0.bottom-0` 两个来源）就是 AppShell ⇒ 扣掉它们，
#: **内容区一个可点元素都没有 ⇒ 该页没渲染出来 ⇒ 结论不可信**（守卫见 `content_coverage`）。


COLLECT_JS = r"""(async () => {
  const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  await frame();
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, summary,'
            + ' [role="button"], [role="tab"], [role="link"], [tabindex]:not([tabindex="-1"])';
  const pid = new Map();
  let seq = 0;
  //: 是否处于固定/粘性层 —— **必须沿祖先链找**：`im /` 的 `<button>` 自身是 `relative`，
  //: 固定的是它的祖先 `<nav class="fixed …">`。只看自身的 `position` 会漏（实测踩过）。
  //: 返回**把它变成固定层的那个元素**（自身或祖先），供审计「为什么这 150 个算固定层」。
  const pinnedBy = (el) => {
    let n = el;
    while (n && n !== document.documentElement) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'sticky') return n;
      n = n.parentElement;
    }
    return null;
  };
  const items = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (parseFloat(cs.opacity) < 0.05) continue;
    // ⚠️ **不再**在采集阶段丢掉视口外的元素。两个问题必须分开：
    //   「哪些元素此刻在屏幕上」→ 判据（`vis` 标志，配对时用）
    //   「内容区到底有没有渲染出东西」→ 覆盖率守卫（**不**看视口）
    // 混在一起的代价（实测踩过）：`web /billing` 的内容全是首屏之下的
    // （输入框 y=965/1043、按钮 y=1100，视口只有 844）⇒ 守卫误判「内容没渲染」，
    // 把每一轮都判成 exit 2，而页面其实是好的。
    const vis = !(r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth);
    const p = el.parentElement;
    if (p && !pid.has(p)) pid.set(p, ++seq);
    const pr = p ? p.getBoundingClientRect() : null;
    const pb = pinnedBy(el);
    items.push({
      l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height,
      vis: vis,
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 90),
      text: (el.textContent || '').trim().slice(0, 14),
      pos: cs.position,
      pinned: !!pb,
      pinnedBy: pb ? (pb.tagName.toLowerCase() + '.' + (pb.className || '').toString().trim()
                     .split(/\s+/).slice(0, 3).join('.')).slice(0, 60) : '',
      pid: p ? pid.get(p) : 0,
      parentW: pr ? pr.width : 0,
    });
  }
  return { vw: innerWidth, items: items };
})()"""

APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin",
            "pages": ("/", "/qa", "/documents", "/contract-review",
                      "/compliance", "/knowledge", "/billing")},
    "lawyer": {"port": 3001, "user": "lawyer_wang",
               "pages": ("/", "/dispatches", "/cases", "/reviews",
                         "/archives", "/notifications")},
    "admin": {"port": 3002, "user": "admin",
              "pages": ("/", "/reviews", "/cases", "/dispatches",
                        "/compliance", "/billing", "/complaints", "/audit")},
    "im": {"port": 3003, "user": "client",
           "pages": ("/", "/chat", "/cases", "/me")},
}


# ─────────────────────────── 纯函数判据 ───────────────────────────

def is_pinned(item: dict) -> bool:
    """E1：元素是否处于**固定/粘性层**（自身或其祖先 `position: fixed|sticky`）。

    这个值由渲染层沿祖先链算好（见 `COLLECT_JS` 的 `isPinned`）。
    ⚠️ E1 是**成对**判据（「恰好一方 pinned ⇒ 跨层 ⇒ 不比较」），**不是**「pinned 的就不参与」——
    后者会把「两个并排的固定按钮」这个真实缺陷漏掉。
    """
    return bool(item.get("pinned"))


def pair_geometry(a: dict, b: dict) -> tuple[float, str] | None:
    """两个元素的几何关系。返回 `(间隙, 轴)` 或 `None`（不构成相邻）。

    「相邻」= 一轴不重叠、另一轴交叠 ≥ 较小边长的 50%（同一行/列上的邻居）。
    轴 `"h"` = 并排（水平分离）；轴 `"v"` = 上下（垂直分离）。
    """
    h_ov = min(a["r"], b["r"]) - max(a["l"], b["l"])
    v_ov = min(a["b"], b["b"]) - max(a["t"], b["t"])
    if v_ov <= 0 < h_ov:
        if h_ov < 0.5 * min(a["w"], b["w"]):
            return None
        return (-v_ov, "v")
    if h_ov <= 0 < v_ov:
        if v_ov < 0.5 * min(a["h"], b["h"]):
            return None
        return (-h_ov, "h")
    return None


def is_full_width(item: dict) -> bool:
    pw = item.get("parentW") or 0
    return pw > 0 and item["w"] >= FULL_WIDTH_RATIO * pw


def content_coverage(items: list[dict]) -> tuple[int, int]:
    """`(可点元素总数, 内容区可点元素数)` —— **覆盖整个 DOM，不看视口**。

    「内容区」= **不处于固定/粘性层**的元素。本项目里固定层就是 AppShell 的
    `header.sticky.top-0.z-topbar` 与 `nav.fixed.inset-x-0.bottom-0`（实测只有这两个来源）。
    ⇒ 内容区为 0 说明**页面内容没渲染出来**（只剩外壳），该页结论不可信。

    ⚠️ **必须不按视口筛**：`web /billing` 的内容全在首屏之下（输入框 `y=965`/`1043`、
    按钮 `y=1100`，视口只有 844）⇒ 若按视口筛，守卫会把**好页面**判成「没渲染」，
    于是每一轮都 exit 2（实测踩过：连跑三次全部误报）。
    ⇒ 判据（「哪些元素此刻在屏幕上」）与守卫（「内容区到底有没有渲染出东西」）
    **是两个问题，必须分开问**。
    """
    return len(items), sum(1 for x in items if not is_pinned(x))


def is_list_row_pair(a: dict, b: dict, gap: float, axis: str) -> bool:
    """E2：上下贴紧的**全宽**元素 ⇒ 列表行（由 §8.3「列表行高 ≥ 48px」管）。

    ⚠️ 量到的 1px 间隙通常是 `border-b` 分隔线，不是设计上的间距。
    """
    return axis == "v" and gap <= LISTROW_MAX_GAP and is_full_width(a) and is_full_width(b)


def pair_up(items: list[dict], min_gap: float = MIN_GAP,
            max_gap: float = MAX_GAP) -> tuple[list[dict], list[dict], list[dict], dict]:
    """把元素两两配对并分档。

    返回 `(defects, notes, zero_pairs, stats)`：
      - `defects`：**A1** 并排且 `0 < gap < 8` ⇒ 缺陷
      - `notes`：**A2** 上下且 `0 < gap < 8`；**A3** `gap == 0`
      - `zero_pairs`：`gap == 0` 的对（供聚合报告）
      - `stats`：计数

    ⚠️ E1 是**成对**排除：`a.pinned != b.pinned` ⇒ 跨层 ⇒ 跳过。
    **不能**先把 pinned 的元素整体滤掉（那样两个并排的固定按钮就永远查不出来）。
    """
    defects: list[dict] = []
    notes: list[dict] = []
    zero_pairs: list[dict] = []
    skipped_listrow = 0
    excluded_layer = 0

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if not (a.get("vis", True) and b.get("vis", True)):
                continue                            # 视口外的元素不构成「误触」场景
            geo = pair_geometry(a, b)
            if geo is None:
                continue
            gap, axis = geo
            if gap > max_gap:
                continue
            if is_pinned(a) != is_pinned(b):        # E1：跨层不比较
                excluded_layer += 1
                continue
            rec = {"gap": round(gap, 1), "axis": axis,
                   "sameParent": a.get("pid") == b.get("pid"),
                   "a": a.get("text", ""), "b": b.get("text", ""),
                   "at": a.get("tag", ""), "bt": b.get("tag", ""),
                   "acls": a.get("cls", ""), "bcls": b.get("cls", "")}
            if gap == 0:
                zero_pairs.append(rec)
                continue
            if gap >= min_gap:
                continue
            if is_list_row_pair(a, b, gap, axis):
                skipped_listrow += 1
                continue
            (defects if axis == "h" else notes).append(rec)

    stats = {
        "items": len(items),
        "excluded_layer": excluded_layer,
        "defects": len(defects),
        "notes_v": len(notes),
        "zero": len(zero_pairs),
        "skipped_listrow": skipped_listrow,
        "zero_same_parent": sum(1 for z in zero_pairs if z["sameParent"]),
        "pinned_items": sum(1 for x in items if is_pinned(x)),
        "pinned_by": {k: sum(1 for x in items if x.get("pinnedBy") == k)
                      for k in {x.get("pinnedBy") for x in items if is_pinned(x)}},
    }
    return defects, notes, zero_pairs, stats


# ─────────────────────────── 渲染层 ───────────────────────────

async def login(b, base: str, username: str) -> bool:
    from app.seed.data import DEMO_USERS
    u = next(x for x in DEMO_USERS if x["username"] == username)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def run_render(dump: bool = False) -> tuple[list[str], int, int, int]:
    from cdp import Browser
    out: list[str] = []
    env = 0
    scanned = 0
    all_def: list[dict] = []
    all_zero: list[dict] = []
    cov: dict[str, list[str]] = {}
    unreliable: list[str] = []
    tot = {"items": 0, "excluded_layer": 0, "notes_v": 0, "skipped_listrow": 0,
           "pinned_items": 0, "pinned_by": {}}
    async with Browser(headless=True, width=390, height=844, device_scale_factor=2) as b:
        await b.apply_device(mobile=True)
        for app, spec in APPS.items():
            base = f"http://localhost:{spec['port']}"
            try:
                if not await login(b, base, spec["user"]):
                    out.append(f"[ENV] [{app}] 登录失败")
                    env += 1
                    continue
            except Exception as e:                                       # noqa: BLE001
                out.append(f"[ENV] [{app}] 登录异常：{type(e).__name__}")
                env += 1
                continue
            for path in spec["pages"]:
                await b.goto(f"{base}{path}", wait=1.5)
                await b.settle(extra=2.0)
                r = await b.cdp.evaluate(COLLECT_JS)
                if not isinstance(r, dict):
                    continue
                items = r.get("items") or []
                scanned += 1
                n_all, n_content = content_coverage(items)
                cov.setdefault(app, []).append(f"{path} {n_all}/{n_content}")
                if n_content == 0:
                    unreliable.append(f"{app}{path}")
                d, n, z, st = pair_up(items)
                for x in d:
                    all_def.append({"app": app, "path": path, **x})
                for x in z:
                    all_zero.append({"app": app, "path": path, **x})
                tot["items"] += st["items"]
                tot["excluded_layer"] += st["excluded_layer"]
                tot["notes_v"] += st["notes_v"]
                tot["skipped_listrow"] += st["skipped_listrow"]
                tot["pinned_items"] += st["pinned_items"]
                for k, v in (st.get("pinned_by") or {}).items():
                    if k:
                        tot["pinned_by"][k] = tot["pinned_by"].get(k, 0) + v
    if dump:
        print("\n" + json.dumps(all_def[:60], ensure_ascii=False, indent=2))
    out.append(f"扫描可点元素 {tot['items']} 个（其中处于固定/粘性层 {tot['pinned_items']} 个）")
    out.append("── 每页覆盖（可点元素数 / **内容区**可点元素数；内容区 0 = 该页没渲染出来）──")
    for app, lst in cov.items():
        out.append(f"  {app}: " + " · ".join(lst))
    if unreliable:
        out.append(f"  [ENV] {len(unreliable)} 页内容区为 0（只剩 AppShell）⇒ **本轮结论不可信**："
                   f"{', '.join(unreliable[:6])}{' …' if len(unreliable) > 6 else ''}")
    if tot["pinned_by"]:
        top = sorted(tot["pinned_by"].items(), key=lambda x: -x[1])[:4]
        out.append("  [E1·审计] 固定层来自："
                   + " · ".join(f"{k!r} × {v}" for k, v in top))
    if tot["excluded_layer"]:
        out.append(f"[E1] 按「跨固定/滚动层」排除 {tot['excluded_layer']} 对"
                   f"（恰好一方 pinned ⇒ 间距随滚动而变，无意义；**同层**照常比较）")
    if all_def:
        uniq: dict[tuple, list[str]] = {}
        for x in all_def:
            uniq.setdefault((x["gap"], x["axis"], x["acls"][:60]), []).append(f"{x['app']}{x['path']}")
        out.append(f"[A1] 并排且 0 < gap < {MIN_GAP:g}px：**{len(all_def)} 对**"
                   f"（{len(uniq)} 条根因）")
        for (gap, axis, acls), locs in sorted(uniq.items(), key=lambda x: -len(x[1])):
            out.append(f"        gap={gap}px  @ {len(set(locs))} 页：{', '.join(sorted(set(locs))[:4])}")
            out.append(f"            被点元素类名：{acls!r}")
    else:
        out.append(f"[A1] 并排且 0 < gap < {MIN_GAP:g}px：0 对 ✓")
    if tot["notes_v"]:
        out.append(f"[A2] 上下且 0 < gap < {MIN_GAP:g}px：{tot['notes_v']} 对"
                   f"（**只报不判**：上下堆叠是全宽列表/表单的常态）")
    if all_zero:
        same = sum(1 for z in all_zero if z["sameParent"])
        out.append(f"[A3] gap == 0：{len(all_zero)} 对（其中同父 {same} 对）"
                   f"（**只报不判**：多是一个复合控件的内部）")
    if tot["skipped_listrow"]:
        out.append(f"[E2] 按「列表行」排除 {tot['skipped_listrow']} 对"
                   f"（全宽 + 上下 + ≤{LISTROW_MAX_GAP:g}px ⇒ 归 §8.3「列表行高 ≥ 48px」）")
    return out, env, len(all_def), scanned, len(unreliable)


# ─────────────────────── 渲染层故障注入自检 ───────────────────────

INJECT_JS = r"""(() => {
  const box = document.createElement('div');
  box.id = '__adj_probe';
  box.setAttribute('style', 'position:absolute;left:8px;top:8px;display:flex;'
    + 'flex-direction:row;gap:3px;');
  const mk = (id, w) => {
    const b = document.createElement('button');
    b.className = '__adj_probe ' + id;
    b.setAttribute('style', 'width:' + w + 'px;height:40px;');
    b.textContent = id;
    box.appendChild(b);
  };
  mk('hit', 40);        // 与下一个间隔 3px ⇒ 应报
  mk('clean', 40);      // 与下一个间隔 3px？不 —— 用下面单独一行控制
  document.body.appendChild(box);

  const box2 = document.createElement('div');
  box2.setAttribute('style', 'position:absolute;left:8px;top:80px;display:flex;'
    + 'flex-direction:row;gap:8px;');
  const mk2 = (id, w) => {
    const b = document.createElement('button');
    b.className = '__adj_probe ' + id;
    b.setAttribute('style', 'width:' + w + 'px;height:40px;');
    b.textContent = id;
    box2.appendChild(b);
  };
  mk2('gap8a', 40);     // 间隔 8px ⇒ 不该报（正好等于线）
  mk2('gap8b', 40);
  document.body.appendChild(box2);

  const box3 = document.createElement('div');
  box3.setAttribute('style', 'position:absolute;left:8px;top:160px;display:flex;'
    + 'flex-direction:column;gap:3px;');
  const mk3 = (id) => {
    const b = document.createElement('button');
    b.className = '__adj_probe ' + id;
    b.setAttribute('style', 'width:60px;height:40px;');
    b.textContent = id;
    box3.appendChild(b);
  };
  mk3('vhit_a'); mk3('vhit_b');   // 上下间隔 3px ⇒ 归 A2（只报）
  document.body.appendChild(box3);

  const box4 = document.createElement('div');
  box4.setAttribute('style', 'position:absolute;left:200px;top:160px;display:flex;'
    + 'flex-direction:row;gap:0px;');
  const mk4 = (id) => {
    const b = document.createElement('button');
    b.className = '__adj_probe ' + id;
    b.setAttribute('style', 'width:40px;height:40px;');
    b.textContent = id;
    box4.appendChild(b);
  };
  mk4('zero_a'); mk4('zero_b');   // 贴紧 ⇒ 归 A3（只报）
  document.body.appendChild(box4);

  const fx = document.createElement('button');
  fx.className = '__adj_probe fixed_a';
  fx.setAttribute('style', 'position:fixed;left:8px;bottom:0;width:40px;height:40px;');
  fx.textContent = 'fixed_a';       // ⚠️ 必须给文本：断言按文本配对，不给 ⇒ 断言空转（旧版就这样空转过）
  document.body.appendChild(fx);
  const fx2 = document.createElement('button');
  fx2.className = '__adj_probe fixed_b';
  fx2.setAttribute('style', 'position:fixed;left:51px;bottom:0;width:40px;height:40px;');
  fx2.textContent = 'fixed_b';
  document.body.appendChild(fx2);   // 两个都 fixed ⇒ **同层** ⇒ 间隔 3px **应报**（旧版逐个丢弃 ⇒ 漏）

  // 跨层对照：一个在固定层（按钮自身 fixed）、一个在文档流（祖先都不是 fixed）
  const wstat = document.createElement('div');
  wstat.setAttribute('style', 'position:absolute;left:8px;top:300px;display:flex;');
  const cs2 = document.createElement('button');
  cs2.className = '__adj_probe cross_static';
  cs2.setAttribute('style', 'width:40px;height:40px;');
  cs2.textContent = 'cross_static';
  wstat.appendChild(cs2);
  document.body.appendChild(wstat);
  const cf = document.createElement('button');
  cf.className = '__adj_probe cross_fixed';
  cf.setAttribute('style', 'position:fixed;left:51px;top:300px;width:40px;height:40px;');
  cf.textContent = 'cross_fixed';
  document.body.appendChild(cf);    // 间隔 3px 但**跨层** ⇒ E1 应排除

  return document.querySelectorAll('.__adj_probe').length;
})()"""


REMOVE_CONTENT_JS = r"""(() => {
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, summary,'
            + ' [role="button"], [role="tab"], [role="link"], [tabindex]:not([tabindex="-1"])';
  const pinned = (el) => {
    let n = el;
    while (n && n !== document.documentElement) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'sticky') return true;
      n = n.parentElement;
    }
    return false;
  };
  let k = 0;
  for (const el of [...document.querySelectorAll(SEL)]) {
    if (!pinned(el)) { el.remove(); k++; }
  }
  return k;
})()"""


async def render_self_test() -> int:
    from cdp import Browser
    base = "http://localhost:3003"
    n_all2 = n_content2 = -1
    try:
        async with Browser(headless=True, width=390, height=844,
                           device_scale_factor=2) as b:
            await b.apply_device(mobile=True)
            if not await login(b, base, "client"):
                print("[ENV] 渲染自检：登录失败")
                return 2
            await b.goto(f"{base}/", wait=1.5)
            await b.settle(extra=1.5)
            n = await b.cdp.evaluate(INJECT_JS)
            if not n:
                print("[ENV] 渲染自检：注入失败")
                return 2
            await b.settle(extra=0.8)
            r = await b.cdp.evaluate(COLLECT_JS) or {}
            items = [x for x in (r.get("items") or [])
                     if "__adj_probe" in str(x.get("cls", ""))]

            # ── 覆盖率守卫的故障注入：把**内容区**可点元素全部删掉 ⇒ 守卫必须判「不可信」 ──
            await b.cdp.evaluate(REMOVE_CONTENT_JS)
            await b.settle(extra=0.6)
            r2 = await b.cdp.evaluate(COLLECT_JS) or {}
            n_all2, n_content2 = content_coverage(r2.get("items") or [])
    except Exception as e:                                              # noqa: BLE001
        print(f"[ENV] 渲染自检异常：{type(e).__name__}: {e}")
        return 2

    d, notes, zero, st = pair_up(items)
    def pairs(rs):
        return {frozenset((x["a"], x["b"])) for x in rs}
    dset, nset, zset = pairs(d), pairs(notes), pairs(zero)

    print(f"渲染层自检（故障注入）：注入 {n} 个探针，扫回 {len(items)} 个"
          f"（E1 跨层排除 {st['excluded_layer']} 对，其中处于固定层 {st['pinned_items']} 个）")
    checks = [
        ("⓪ 探针自身完整性：所有探针文本非空（否则按文本配对会**空转** ⇒ 断言变成只会通过）",
         sum(1 for x in items if not str(x.get("text", "")).strip()), 0),
        ("① 检出能力：hit|clean（并排 3px）**必须**进 A1",
         frozenset(("hit", "clean")) in dset, True),
        ("② 特异性：gap8a|gap8b（并排 8px，正好等于线）**不得**进 A1",
         frozenset(("gap8a", "gap8b")) in dset, False),
        ("③ 分档：vhit_a|vhit_b（上下 3px）**不得**进 A1，应进 A2",
         (frozenset(("vhit_a", "vhit_b")) in dset,
          frozenset(("vhit_a", "vhit_b")) in nset), (False, True)),
        ("④ 分档：zero_a|zero_b（贴紧）**不得**进 A1，应进 A3",
         (frozenset(("zero_a", "zero_b")) in dset,
          frozenset(("zero_a", "zero_b")) in zset), (False, True)),
        ("⑤ E1：**同层**两个 fixed 间隔 3px ⇒ 间距稳定 ⇒ **必须**报（旧版逐个丢弃 ⇒ 漏）",
         frozenset(("fixed_a", "fixed_b")) in dset, True),
        ("⑥ E1：**跨层** cross_static|cross_fixed 间隔 3px ⇒ **不得**报",
         frozenset(("cross_static", "cross_fixed")) in dset, False),
        ("⑦ 覆盖率守卫：把**内容区**可点元素全删掉后 ⇒ 内容区数 == 0 且总数 > 0"
         "（否则「0 对」会被读成干净）",
         (n_content2 == 0, n_all2 > 0), (True, True)),
    ]
    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"渲染自检 {'通过' if not bad else f'失败 {bad} 项'}"
          f"（8 项：1 探针完整性 + 1 检出 + 2 特异性 + 2 分档 + 1 E1 跨层 + 1 覆盖率守卫）")
    return 0 if not bad else 1


# ─────────────────────────── 自测 ───────────────────────────

def _mk(tag: str, left: float, t: float, w: float, h: float, text: str = "",
        cls: str = "", pinned: bool = False, pid: int = 1,
        parent_w: float = 390.0, pinned_by: str = "", vis: bool = True) -> dict:
    return {"tag": tag, "l": left, "t": t, "r": left + w, "b": t + h, "w": w, "h": h,
            "text": text, "cls": cls, "pinned": pinned, "pid": pid, "parentW": parent_w,
            "pinnedBy": pinned_by or ("button.probe" if pinned else ""), "vis": vis}


def self_test() -> int:
    n = 0
    fails: list[str] = []

    def arm(name: str, got, want) -> None:
        nonlocal n
        n += 1
        if got != want:
            fails.append(f"{name}: got={got!r} want={want!r}")

    # ── 几何：并排 / 上下 / 重叠 / 对角 ──
    a = _mk("BUTTON", 0, 0, 40, 40)
    b = _mk("BUTTON", 43, 0, 40, 40)          # 并排 3px
    arm("几何：并排 3px ⇒ (3,'h')", pair_geometry(a, b), (3.0, "h"))
    c = _mk("BUTTON", 0, 43, 40, 40)          # 上下 3px
    arm("几何：上下 3px ⇒ (3,'v')", pair_geometry(a, c), (3.0, "v"))
    ov = _mk("BUTTON", 20, 20, 40, 40)        # 重叠
    arm("几何：重叠 ⇒ None", pair_geometry(a, ov), None)
    dg = _mk("BUTTON", 60, 60, 40, 40)        # 对角
    arm("几何：对角 ⇒ None", pair_geometry(a, dg), None)
    far = _mk("BUTTON", 5, 0, 40, 40)
    arm("几何：交叠不足 50% ⇒ None", pair_geometry(a, far), None)
    arm("几何：贴紧 ⇒ 0", pair_geometry(a, _mk("BUTTON", 40, 0, 40, 40)), (0.0, "h"))

    # ── E1：固定/粘性层标记（成对判据的输入）──
    arm("E1：pinned 元素被识别", is_pinned(_mk("BUTTON", 0, 0, 40, 40, pinned=True)), True)
    arm("E1：非 pinned 不排除", is_pinned(a), False)

    # ── E2：全宽上下贴紧 ⇒ 列表行 ──
    r1 = _mk("A", 0, 0, 380, 68, parent_w=380.0)
    r2 = _mk("A", 0, 69, 380, 68, parent_w=380.0)
    arm("E2：全宽 + 上下 1px ⇒ 列表行", is_list_row_pair(r1, r2, 1.0, "v"), True)
    arm("E2：并排不算列表行", is_list_row_pair(r1, r2, 1.0, "h"), False)
    narrow = _mk("A", 0, 69, 120, 68, parent_w=380.0)
    arm("E2：不全宽 ⇒ 不是列表行", is_list_row_pair(r1, narrow, 1.0, "v"), False)
    arm("E2：上下 5px ⇒ 不是列表行", is_list_row_pair(r1, _mk("A", 0, 73, 380, 68, parent_w=380.0),
                                                     5.0, "v"), False)

    # ── A1/A2/A3 分档（纯函数，一次跑全部分支）──
    def one(items):
        return pair_up(items)

    # A1：并排 3px ⇒ 1 条缺陷
    d, nt, z, st = one([_mk("BUTTON", 0, 0, 40, 40, "x", pid=1),
                        _mk("BUTTON", 43, 0, 40, 40, "y", pid=1)])
    arm("A1：并排 3px ⇒ 1 条缺陷", len(d), 1)
    arm("A1：缺陷 gap=3", d[0]["gap"] if d else None, 3.0)
    arm("A1：缺陷同父=True", d[0]["sameParent"] if d else None, True)
    # A1：并排 8px ⇒ 0 条（正好等于线，闭区间合规）
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40), _mk("BUTTON", 48, 0, 40, 40)])
    arm("A1：并排 8px ⇒ 0 条（≥ 线）", len(d), 0)
    # A1：并排 4px 但**不同父** ⇒ 仍报（规范只管距离）
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40, pid=1), _mk("BUTTON", 44, 0, 40, 40, pid=2)])
    arm("A1：不同父也报", len(d), 1)
    # A2：上下 3px ⇒ 进 notes 不进 defects
    d, nt, _, _ = one([_mk("BUTTON", 0, 0, 40, 40), _mk("BUTTON", 0, 43, 40, 40)])
    arm("A2：上下 3px ⇒ 0 缺陷", len(d), 0)
    arm("A2：上下 3px ⇒ 1 条只报", len(nt), 1)
    # A3：贴紧 ⇒ 进 zero
    d, nt, z, _ = one([_mk("BUTTON", 0, 0, 40, 40), _mk("BUTTON", 40, 0, 40, 40)])
    arm("A3：贴紧 ⇒ 0 缺陷", len(d), 0)
    arm("A3：贴紧 ⇒ 1 条 zero", len(z), 1)
    # E1：**跨层**（恰好一方 pinned）并排 3px ⇒ 排除（间距随滚动而变）
    d, nt, z, st = one([_mk("BUTTON", 0, 0, 40, 40, pinned=True),
                        _mk("BUTTON", 43, 0, 40, 40)])
    arm("E1：跨层并排 3px ⇒ 0 缺陷 0 提示 0 zero", (len(d), len(nt), len(z)), (0, 0, 0))
    arm("E1：统计里 excluded_layer=1", st["excluded_layer"], 1)
    # E1：**同层**（都 pinned）并排 3px ⇒ 仍报（旧版逐个丢弃 ⇒ 漏掉这类真实缺陷）
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40, pinned=True),
                      _mk("BUTTON", 43, 0, 40, 40, pinned=True)])
    arm("E1：同层两个 pinned 并排 3px ⇒ **仍报**", len(d), 1)
    arm("E1：统计里 pinned_items=2",
        one([_mk("BUTTON", 0, 0, 40, 40, pinned=True),
             _mk("BUTTON", 43, 0, 40, 40, pinned=True)])[3]["pinned_items"], 2)
    arm("E1：pinned_by 按来源聚合（审计用）",
        one([_mk("BUTTON", 0, 0, 40, 40, pinned=True, pinned_by="nav.fixed"),
             _mk("BUTTON", 43, 0, 40, 40, pinned=True, pinned_by="nav.fixed")])[3]
        ["pinned_by"].get("nav.fixed"), 2)
    # E1：**同层**（都不 pinned）不受影响
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40), _mk("BUTTON", 43, 0, 40, 40)])
    arm("E1：同层都不 pinned ⇒ 仍报", len(d), 1)
    # E2：全宽上下 1px ⇒ 被跳过
    d, nt, z, st = one([_mk("A", 0, 0, 380, 68, parent_w=380.0),
                        _mk("A", 0, 69, 380, 68, parent_w=380.0)])
    arm("E2：全宽上下 1px ⇒ 0 缺陷 0 提示", (len(d), len(nt)), (0, 0))
    arm("E2：统计里 skipped_listrow=1", st["skipped_listrow"], 1)
    # 超过 MAX_GAP ⇒ 不算相邻
    d, nt, z, _ = one([_mk("BUTTON", 0, 0, 40, 40), _mk("BUTTON", 60, 0, 40, 40)])
    arm("超过 12px ⇒ 不算相邻", (len(d), len(nt), len(z)), (0, 0, 0))
    # 空输入
    arm("空输入不炸", pair_up([])[3]["items"], 0)

    # ── 视口外元素：不参与配对（但计入覆盖率）──
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40, vis=False),
                      _mk("BUTTON", 43, 0, 40, 40, vis=False)])
    arm("视口外：并排 3px ⇒ 不报（够不着就谈不上误触）", len(d), 0)
    d, _, _, _ = one([_mk("BUTTON", 0, 0, 40, 40, vis=True),
                      _mk("BUTTON", 43, 0, 40, 40, vis=False)])
    arm("视口外：一内一外 ⇒ 不报", len(d), 0)
    arm("视口外：但仍计入覆盖率（内容区 2）",
        content_coverage([_mk("BUTTON", 0, 0, 40, 40, vis=False),
                          _mk("BUTTON", 43, 0, 40, 40, vis=False)]), (2, 2))

    # ── 覆盖率守卫：内容区为 0 ⇒ 该页不可信 ──
    arm("覆盖：全在固定层里 ⇒ 内容区 0",
        content_coverage([_mk("BUTTON", 0, 0, 40, 40, pinned=True),
                          _mk("BUTTON", 43, 0, 40, 40, pinned=True)]), (2, 0))
    arm("覆盖：都不在固定层 ⇒ 内容区 2",
        content_coverage([_mk("BUTTON", 0, 0, 40, 40),
                          _mk("BUTTON", 43, 0, 40, 40)]), (2, 2))
    arm("覆盖：混合 ⇒ (3, 2)",
        content_coverage([_mk("BUTTON", 0, 0, 40, 40, pinned=True),
                          _mk("BUTTON", 43, 0, 40, 40),
                          _mk("BUTTON", 86, 0, 40, 40)]), (3, 2))
    arm("覆盖：空 ⇒ (0, 0)", content_coverage([]), (0, 0))

    # ── 控制臂：线的出处 ──
    arm("控制臂：MIN_GAP == §8.3 的 8", MIN_GAP, 8.0)

    print(f"自测 {n - len(fails)}/{n} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    return 0 if not fails else 1


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="§8.3 相邻可点元素间距门禁")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--render-self-test", action="store_true")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if a.render_self_test:
        return asyncio.run(render_self_test())

    print("§8.3「相邻可点元素间距 ≥ 8px」门禁（渲染级，390×844）")
    print(f"  出处：§8.3「相邻可点元素间距 ≥ 8px，避免误触」（§8.3 第 4 条 bullet）· 线 = {MIN_GAP:g}px")
    print("  只做第 4 条：48px 热区归 verify_tap_targets.py；按钮高度归 verify_buttons.py；"
          "列表行高归 §8.3 另一条")
    print("\n── 渲染层 A1/A2/A3（E1–E3 已声明的排除；E1 是**成对**判据）──")
    notes, env, n_def, scanned, n_unrel = asyncio.run(run_render(dump=a.dump))
    for t in notes:
        print(f"  {t}")
    if scanned == 0:
        print("  [ENV] 一页都没扫到（登录全失败？）⇒ 退出码 2")
        return 2
    if n_unrel:
        print(f"  [ENV] {n_unrel} 页内容区没渲染出来 ⇒ **本轮结论不可信**（尤其「0 对」不可读作干净）"
              f" ⇒ 退出码 2")
        return 2
    return 1 if n_def else 0


if __name__ == "__main__":
    sys.exit(main())

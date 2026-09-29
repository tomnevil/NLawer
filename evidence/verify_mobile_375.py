"""移动端**宽度基准**审计：§8.1 说 im「设计基准直接按 375px 起草」——那 375 就量一次。

## 为什么要单独做这一条

四端所有的移动端探针（`verify_mobile_safe_area.py` / `verify_mobile_actionbar.py` /
`verify_tap_targets.py` / `verify_im_safe_area.py` / `verify_im_tabs.py`）**全部跑 390×844**
（iPhone 12–14）。而规范 §8.1 明说：

> | **im（客户端）** | 移动优先 | 全部。C 端客户几乎只用手机，
>   **设计基准直接按 375px 起草**，桌面视为放大适配 |

⇒ **规范写明的设计基准，此前一次都没被测过。** 375 比 390 窄 15px，
横向溢出 / 截断 / 挤压这类缺陷恰好只在**更窄**的视口暴露。

## 判据与出处

| 编号 | 判据 | 出处 |
|---|---|---|
| **H1** | 页面**不产生横向溢出**（`documentElement.scrollWidth ≤ clientWidth`） | §8.2「单列」；§8.5 把「横向滚动」限定为**强对比场景**的**显式**手段 |
| **H2** | 若溢出，**指出越界元素**（可行动） | 报告要能指到元素，不能只说「溢出了」 |

> 🚨 **前提锁（否则这条判据会制造大量假红）**：
> §8.5 明确规定「需横向对比数值 ⇒ **横向滚动 + 首列冻结**」是**设计手段**，
> 且要求「必须显示滚动提示」。所以**在 `overflow-x: auto|scroll` 的容器内部的元素，
> 越界是设计如此，不是缺陷** —— 本探针把它们**排除**，并单列「观察」。
> 不排除的话，驾驶舱那种宽表会被稳定报成几十条「缺陷」。
>
> 同理，`position: fixed` 的元素相对视口定位，本来就不构成文档级溢出。

## 对照组（本探针最重要的一点）

同一页分别在 **390（既有基线）/ 375（规范基准）/ 320（最窄常见）** 下量。
- 只在 375/320 溢出、390 不溢出 ⇒ **窄视口特有**（本探针的价值所在）
- 三个宽度都溢出 ⇒ **与宽度无关的既有缺陷**（另一个问题，别混为一谈）

**没有 390 这一臂，就无法区分「375 太窄」与「这一页本来就有问题」。**

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（登录失败 / 页面没渲染出来 / 前提不成立）

用法：
    python evidence/verify_mobile_375.py
    python evidence/verify_mobile_375.py --apps im --widths 375,390
    python evidence/verify_mobile_375.py --only /chat
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

# 与 `verify_runtime_health.py` 保持**同一套页表**。
# 覆盖率不足（「只扫 1 页 → 0 个问题」）在本项目被明确认定为**不是健康**，
# 所以这里刻意复用那张已经补全过的表，而不是另起一张更小的。
APPS: dict[str, dict] = {
    "web": {
        "port": 3000,
        "user": "ent_admin",
        "pages": ("/", "/qa", "/documents", "/contract-review", "/compliance", "/knowledge", "/billing"),
    },
    "lawyer": {
        "port": 3001,
        "user": "lawyer_wang",
        "pages": ("/", "/dispatches", "/cases", "/reviews", "/archives", "/notifications"),
    },
    "admin": {
        "port": 3002,
        "user": "admin",
        "pages": ("/", "/reviews", "/cases", "/dispatches", "/compliance", "/billing", "/complaints", "/audit"),
    },
    "im": {
        "port": 3003,
        "user": "client",
        "pages": ("/", "/chat", "/cases", "/cases/@first-case", "/me"),
    },
}

# (宽, 高, 说明)。390 是**对照组**，不是判据对象。
WIDTHS: tuple[tuple[int, int, str], ...] = (
    (390, 844, "对照（既有基线）"),
    (375, 667, "§8.1 设计基准"),
    (320, 568, "最窄常见"),
)

MEASURE_JS = """(() => {
  const de = document.documentElement;
  const vw = innerWidth;
  const cw = de.clientWidth;

  // ---- 「设计上就要横向滚动」的容器：其内部元素越界不算缺陷（§8.5） ----
  const scrollableAncestor = (el) => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const ox = cs.overflowX;
      if ((ox === 'auto' || ox === 'scroll') && n.scrollWidth > n.clientWidth + 1) {
        return n.tagName.toLowerCase() + '.' +
               (n.className || '').toString().split(' ').slice(0, 3).join('.');
      }
    }
    return null;
  };

  const over = [], inScroller = [];
  for (const el of document.body.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) continue;
    if (r.right <= cw + 0.5) continue;

    const who = el.tagName.toLowerCase() + '.' +
                (el.className || '').toString().split(' ').slice(0, 4).join('.');
    const item = {
      who,
      right: Math.round(r.right * 10) / 10,
      w: Math.round(r.width),
      pos: cs.position,
      txt: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 22),
    };
    const sc = scrollableAncestor(el);
    if (sc) { item.scroller = sc; inScroller.push(item); }
    else { over.push(item); }
  }

  // 越界元素往往成串（祖先 + 后代）。按「右边界最远」排序并去重，
  // 让报告指向**最能说明问题的那几个**，而不是 30 条同源噪声。
  const dedupe = (arr) => {
    const seen = new Set(), out = [];
    for (const x of arr.sort((a, b) => b.right - a.right)) {
      if (seen.has(x.who)) continue;
      seen.add(x.who);
      out.push(x);
      if (out.length >= 8) break;
    }
    return out;
  };

  return {
    vw, cw,
    sw: de.scrollWidth,
    bodySw: document.body ? document.body.scrollWidth : 0,
    overflowPx: Math.max(0, de.scrollWidth - cw),
    over: dedupe(over),
    overCount: over.length,
    inScroller: dedupe(inScroller),
    inScrollerCount: inScroller.length,
    bodyText: (document.body.innerText || '').trim().length,
  };
})()"""


# ── 自检：**用合成页面**验证测量 JS 本身 ──────────────────────────────
#
# 为什么要有这一步：四端全绿时，无法区分「真的没溢出」与「这条判据根本不会红」。
# 合成页面把「被测对象」从产品换成**已知答案的样本**，于是两种情形可以分开。
#
# ⚠️ 三臂**分开报**（本项目的纪律：检出能力与「对照组干净」不能混成一个布尔）：
#   Q1 脏样本 ⇒ **必须报红**（检出能力）
#   Q2 干净样本 ⇒ **必须不报**（对照组干净）
#   Q3 横滚容器内的宽元素 ⇒ **不得**算进文档级溢出，且必须被归到 `inScroller`
#      ——这一臂专门挡「把 §8.5 的设计手段报成缺陷」这个形状的假红。
_VP = "<meta name='viewport' content='width=device-width,initial-scale=1'>"
SELFTEST: tuple[tuple[str, str, str], ...] = (
    ("Q1", "dirty-600px",
     _VP + "<body style='margin:0'><div style='width:600px;height:20px'>W</div></body>"),
    ("Q2", "clean",
     _VP + "<body style='margin:0'><div style='width:100%;height:20px'>OK</div></body>"),
    ("Q3", "scroller",
     _VP + "<body style='margin:0'><div style='overflow-x:auto;width:100%'>"
           "<div style='width:600px;height:20px'>T</div></div></body>"),
)


async def run_self_test() -> int:
    import urllib.parse

    print("── 自检（合成页面，宽度固定 375）──")
    print("   Q1 检出能力 / Q2 干净对照 / Q3 §8.5 横滚容器对照 —— **分开报**\n")

    fails: list[str] = []
    async with Browser(headless=True, width=375, height=667, device_scale_factor=1) as b:
        # ⚠️ **自检用桌面仿真（`mobile=False`），不是移动仿真。**
        #
        # 第一版用了 `mobile=True`，前提锁当场报「视口 600 ≠ 375」——
        # `data:` URL 上没有真实文档来源，Chrome 的移动视口仿真不认 `width=device-width`，
        # 布局视口直接撑到内容宽度（600）。
        #
        # 但自检要验证的是**测量 JS**（溢出判定 + 归因 + 横滚容器排除），
        # 与「移动仿真」无关；桌面仿真下 `innerWidth` **精确等于**给定宽度，正合用。
        # ⇒ 换仿真模式而不是放宽前提锁：**前提锁拦下的是真问题，不该为了让它变绿而改判据。**
        await b.cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=375, height=667, deviceScaleFactor=1, mobile=False,
        )
        for tag, name, html in SELFTEST:
            await b.goto("data:text/html," + urllib.parse.quote(html), wait=0.6)
            await asyncio.sleep(0.3)
            m = await b.cdp.evaluate(MEASURE_JS)
            if not m:
                print(f"   [{tag}] {name:<12} ✗ 测量返回空（环境问题）")
                return 2
            if m["vw"] != 375:
                print(f"   [{tag}] {name:<12} ✗ 前提不成立：视口 {m['vw']} ≠ 375")
                return 2

            if tag == "Q1":
                ok = m["overflowPx"] > 0 and any("600px" in x["who"] or x["w"] >= 600 for x in m["over"])
                print(f"   [{tag}] {name:<12} 溢出={m['overflowPx']}px  越界元素={len(m['over'])} 个"
                      f"  ⇒ {'✓ 能检出且能归因' if ok else '✗ **没检出**'}")
                if not ok:
                    fails.append("Q1：脏样本没被检出（判据不会红）")
            elif tag == "Q2":
                ok = m["overflowPx"] == 0 and m["overCount"] == 0
                print(f"   [{tag}] {name:<12} 溢出={m['overflowPx']}px  越界元素={m['overCount']} 个"
                      f"  ⇒ {'✓ 对照组干净' if ok else '✗ **干净样本被误报**'}")
                if not ok:
                    fails.append("Q2：干净样本被误报")
            else:
                ok = m["overflowPx"] == 0 and m["overCount"] == 0 and m["inScrollerCount"] > 0
                print(f"   [{tag}] {name:<12} 文档级溢出={m['overflowPx']}px  "
                      f"计为缺陷={m['overCount']} 个  归入 inScroller={m['inScrollerCount']} 个"
                      f"  ⇒ {'✓ 设计手段未被误判' if ok else '✗ **横滚容器被误报/漏判**'}")
                if not ok:
                    fails.append("Q3：§8.5 横滚容器未被正确排除")

    print()
    if fails:
        print("  ✗ 自检失败（**这是工具的问题，不是产品的问题**）：")
        for f in fails:
            print(f"      · {f}")
        print("\nSELFTEST_EXIT=1")
        return 1
    print("  ✓ 检出能力（Q1）与两组对照（Q2/Q3）**分别**成立")
    print("\nSELFTEST_EXIT=0")
    return 0


async def login(b: Browser, base: str, username: str) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == username)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def resolve_path(b: Browser, base: str, path: str) -> str:
    """把 `@first-case` 换成真实 id。

    与 `verify_runtime_health.py` 同法：**从 DOM 读链接**，不调 API
    ——SDK 把 access token 存在模块内存变量里，CDP 里发 `fetch` 带不上鉴权。
    解析失败**抛异常**（归类为环境问题），绝不静默跳过。
    """
    if "@first-case" not in path:
        return path
    await b.goto(f"{base}/cases", wait=1.5)
    await b.settle(extra=2.0)
    href = await b.cdp.evaluate(
        "(() => { const a = document.querySelector('a[href^=\"/cases/\"]');"
        " return a ? a.getAttribute('href') : null; })()"
    )
    if not href:
        raise RuntimeError(f"{base}/cases 上没有指向案件详情的链接（动态路由测不到）")
    return path.replace("@first-case", href.rsplit("/", 1)[-1])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="", help="只测某几端，逗号分隔（默认全部）")
    ap.add_argument("--widths", default="", help="只测某几个宽度，逗号分隔（默认 390,375,320）")
    ap.add_argument("--only", default="", help="只测某个路径（调试用）")
    ap.add_argument("--self-test", action="store_true",
                    help="用合成页面验证测量 JS 本身（检出能力 + 两组对照）")
    args = ap.parse_args()

    if args.self_test:
        return await run_self_test()

    apps = [a for a in args.apps.split(",") if a.strip()] or list(APPS)
    for a in apps:
        if a not in APPS:
            print(f"未知端：{a}（可选 {list(APPS)}）")
            return 2

    widths = WIDTHS
    if args.widths.strip():
        want = [int(w) for w in args.widths.split(",") if w.strip()]
        widths = tuple(w for w in WIDTHS if w[0] in want)

    print("── 移动端宽度基准审计（§8.1：im 设计基准 375px）──")
    print(f"   宽度：{' / '.join(f'{w}×{h}（{note}）' for w, h, note in widths)}")
    print("   ⚠️ 已排除 `overflow-x:auto|scroll` 容器内的元素（§8.5 规定横滚是设计手段）\n")

    # (app, path, width) -> measurement
    results: dict[tuple[str, str, int], dict] = {}
    # (app, 页表里的 path) -> 解析后的真实路径。
    # ⚠️ 必须**测量时记下来**，不要在汇总段反推 ——
    #    `@first-case` 会被换成真实 id，反推时「path 不等于 real」，
    #    第一版就是在那里用 `startswith` 猜，猜错了会把结果归到错误的行上。
    real_by_path: dict[tuple[str, str], str] = {}
    env_notes: list[str] = []

    for app in apps:
        spec = APPS[app]
        base = f"http://localhost:{spec['port']}"
        pages = [args.only] if args.only else list(spec["pages"])
        print(f"[{app}] :{spec['port']}  账号 {spec['user']}")

        try:
            async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
                if not await login(b, base, spec["user"]):
                    print("   ✗ 登录失败（环境问题）")
                    return 2

                for path in pages:
                    real = await resolve_path(b, base, path)
                    real_by_path[(app, path)] = real
                    await b.goto(f"{base}{real}", wait=1.5)
                    await b.settle(extra=1.5)

                    line = f"   {real:<26}"
                    cells = []
                    for w, h, note in widths:
                        # ⚠️ 用 `setDeviceMetricsOverride` **原地改视口**而不是重新导航：
                        #    `apply_device(mobile=False)` 不会改宽度（本项目的经典陷阱），
                        #    而重新导航 26 页 × 3 档要 78 次加载。原地 reflow 等价且快得多。
                        await b.cdp.send(
                            "Emulation.setDeviceMetricsOverride",
                            width=w, height=h, deviceScaleFactor=1, mobile=True,
                        )
                        await asyncio.sleep(0.35)
                        m = await b.cdp.evaluate(MEASURE_JS)
                        if not m:
                            env_notes.append(f"{app}{real}@{w}: 测量返回空")
                            continue
                        if m["vw"] != w:
                            # 前提不成立 ⇒ **不判**（既不判绿也不判红）
                            cells.append(f"{w}:前提✗(实际{m['vw']})")
                            env_notes.append(f"{app}{real}@{w}: 视口实际 {m['vw']}")
                            continue
                        results[(app, real, w)] = m
                        if m["bodyText"] < 10:
                            env_notes.append(f"{app}{real}@{w}: 正文 <10 字，可能没渲染出来")
                        cells.append(f"{w}:{'✓' if m['overflowPx'] == 0 else '✗溢出%dpx' % m['overflowPx']}")
                    print(f"{line}{'  '.join(cells)}")
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ {type(e).__name__}: {e}（环境问题）")
            return 2
        print()

    if not results:
        print("✗ 一条测量都没拿到（环境问题）")
        return 2

    # ---- 汇总：按「是否只在窄视口出现」分类 ----
    base_w = 390
    only_narrow: list[str] = []
    all_widths: list[str] = []
    for app in apps:
        spec = APPS[app]
        pages = [args.only] if args.only else list(spec["pages"])
        for path in pages:
            real = real_by_path.get((app, path), path)
            keys = [(w, results.get((app, real, w))) for w, _h, _n in widths]
            bad = [(w, m) for w, m in keys if m and m["overflowPx"] > 0]
            if not bad:
                continue
            ctrl = results.get((app, real, base_w))
            where = ", ".join(f"{w}px(+{m['overflowPx']}px)" for w, m in bad)
            entry = f"{app}{real}  →  {where}"
            if ctrl and ctrl["overflowPx"] > 0:
                all_widths.append(entry)
            else:
                only_narrow.append(entry)

    print("── 汇总 ──")
    if not only_narrow and not all_widths:
        print("  ✓ 所有页面在全部宽度下都无横向溢出")
        print(f"\nEXIT=0  通过（{len(results)} 组测量）")
        return 0

    if only_narrow:
        print(f"  ✗ **窄视口特有**的横向溢出（390 对照组正常）—— {len(only_narrow)} 页：")
        for e in only_narrow:
            print(f"      · {e}")
    if all_widths:
        print(f"  ⚠ **与宽度无关**的既有溢出（390 也溢出）—— {len(all_widths)} 页：")
        for e in all_widths:
            print(f"      · {e}")

    # ---- 越界元素归因 ----
    print("\n── 越界元素（已排除设计上就要横滚的容器内元素）──")
    shown = 0
    for (app, path, w), m in sorted(results.items()):
        if m["overflowPx"] <= 0:
            continue
        if not m["over"]:
            print(f"  {app}{path} @{w}px 溢出 {m['overflowPx']}px，"
                  f"但**找不到越界元素**（可能在 ::before/伪元素或文本节点上）")
            shown += 1
            continue
        print(f"  {app}{path} @{w}px  clientWidth={m['cw']} scrollWidth={m['sw']}")
        for x in m["over"]:
            print(f"     · right={x['right']} w={x['w']} {x['pos']:<8} {x['who']}"
                  + (f"  「{x['txt']}」" if x["txt"] else ""))
        shown += 1
        if shown >= 10:
            print("  …（更多从略）")
            break

    # ---- 观察：设计上就要横滚的容器（§8.5） ----
    sc_obs = [(k, m) for k, m in results.items() if m["inScrollerCount"] > 0]
    if sc_obs:
        print("\n── 观察（**不判缺陷**）：§8.5 允许的横向滚动容器 ──")
        seen = set()
        for (app, path, w), m in sorted(sc_obs):
            key = (app, path)
            if key in seen:
                continue
            seen.add(key)
            names = ", ".join(x.get("scroller", "?") for x in m["inScroller"])
            print(f"  {app}{path}：容器 {names} 内有 {m['inScrollerCount']} 个元素超出视口"
                  f" ⇒ 按 §8.5「需横向对比数值」属设计手段（需有滚动提示）")

    if env_notes:
        print("\n── 环境提示（不算缺陷）──")
        for n in env_notes[:8]:
            print(f"  · {n}")

    n_bad = len(only_narrow) + len(all_widths)
    print(f"\nEXIT=1  产品缺陷 {n_bad} 页（其中窄视口特有 {len(only_narrow)} 页）")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

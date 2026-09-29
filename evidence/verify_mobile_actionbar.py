"""`MobileActionBar` 与 `TabBar` 的**叠放**审计（渲染级）。

## 为什么单独做这一条

`verify_mobile_safe_area.py` 只审了**底栏（TabBar）**。
但规范第 08 节要求律师端是「5 项 Tab **+ 常驻底部操作条**」——
于是**两个固定元素叠在一起**，这是安全区/遮挡类缺陷最容易复发的地方：
底栏那条防线过了，不代表它上面那条也对。

## 组件契约（期望值的出处，不是我拍脑袋）

`packages/ui/src/components/mobile/MobileActionBar.tsx`：

| 出处 | 契约 | 本探针的判据 |
|---|---|---|
| l.64 `style={{ bottom: "var(--actionbar-bottom)" }}` | 底边取 `--actionbar-bottom` = `calc(--tabbar-h + --safe-bottom)` ⇒ **正好叠在 TabBar 之上、不留缝不重叠** | **B** `gap == 0` |
| l.54 `style={{ height: "var(--actionbar-h)" }}` + 组件注释「在流内保留**等高**占位」 | 占位高度 == 固定条的**实际高度** | **D1** 占位 == 条 border-box 高 |
| 顶部发丝分隔线（`styles.css` 的 `html .hairline-top`，**背景渐变**，不占盒模型高度） | 条的 border-box 高**恒等于** `--actionbar-h`；且**那条线仍要看得见** | **D1** 差应为 0；**D3** `backgroundImage` 必须已解析、非 `none` |
| l.73-74 `paddingLeft/Right: calc(0.75rem + var(--safe-left/right))` | 只处理**左右**安全区；**底部安全区由 TabBar 负责** ⇒ 条自己**不应**再带 `--safe-bottom` | **C** 条内不得有 `pb ≈ --safe-bottom` |
| l.59 `lg:hidden` | 桌面端整体隐藏 | **F** 1280 宽下 `display:none` |

> ⚠️ **判据 D1 为什么不能「拿 1px 容差糊过去」**：
> 组件注释写的是「**等高**」。1px 也是不等高。
> 但这个项目吃过「用容差把 off-by-one 糊掉」的亏，也吃过「不区分严重性就报红」的亏，
> 所以本探针**同时量两件事**：
> · **D1** 占位 vs 条高（契约是否达成）
> · **D2** 正文最后一个可见元素**是否真的被盖住**（有无可见后果）
> 两者分开报 —— 契约未达成但无可见后果，与「真的裁掉了正文」，不是同一件事。
>
> 🚨 **判据 D3 是 2026-09-20 补的，起因是一次自己的疏漏**：
> D1 的修法是把占 1px 的 `border-t` 换成**不占 1px 的背景渐变**。
> 但 D1 只证明「**边框没了**」，**证明不了「那条线还在」**——
> 若渐变因令牌缺失而失效，两条固定条会**视觉合并**，而 D1 照样全绿。
> ⇒ **替换掉一个可见元素后，必须单独断言「替换物存在」**，
> 否则这道防线从「能挡住 1px 失配」变成「挡住了失配、却放走了没线」。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（登录失败 / 找不到案件 / 安全区模拟不生效）

用法：
    python evidence/verify_mobile_actionbar.py
    python evidence/verify_mobile_actionbar.py --port 3001
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

from cdp import Browser  # noqa: E402

SAFE_ON = {"top": 47, "bottom": 34, "left": 0, "right": 0}
MIN_TAP = 48  # 规范 §8.3

# 找到「固定条」的方式：**认内联样式**，不认类名。
# 类名（`bg-surface/95 backdrop-blur`）会被 Tailwind 重排/合并，脆弱；
# 而 `style.bottom === 'var(--actionbar-bottom)'` 正是组件源码 l.64 写死的那一行，
# 改掉它就必须改探针 —— 这个耦合是**有意**的。
FIND_JS = """(() => {
  const all = [...document.querySelectorAll('div')];
  const bar = all.find(el => el.style && el.style.bottom === 'var(--actionbar-bottom)');
  const spacer = bar ? bar.previousElementSibling : null;
  const spacerOk = !!(spacer && spacer.getAttribute('aria-hidden') !== null
                      && spacer.style && spacer.style.height === 'var(--actionbar-h)');

  // 找不到时给出「候选」——否则分不清「选择方式失效」与「这一页真的没渲染」
  const cands = [];
  if (!bar) {
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const isFixed = cs.position === 'fixed';
      const hasInlineBottom = el.style && el.style.bottom;
      if (isFixed || hasInlineBottom) {
        cands.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().split(' ').slice(0, 4).join(' '),
          position: cs.position,
          inlineBottom: hasInlineBottom ? el.style.bottom : null,
          computedBottom: cs.bottom,
          top: Math.round(r.top), bottom: Math.round(r.bottom),
          h: Math.round(r.height),
        });
      }
    }
  }
  return {found: !!bar, spacerOk, cands: cands.slice(0, 14)};
})()"""

MEASURE_JS = """(() => {
  const docCs = getComputedStyle(document.documentElement);
  const safeRaw = docCs.getPropertyValue('--safe-bottom').trim();
  const safeBottom = parseFloat(safeRaw) || 0;
  const actionbarH = parseFloat(docCs.getPropertyValue('--actionbar-h')) || 0;

  const nav = document.querySelector('nav[aria-label="主导航"]');

  const all = [...document.querySelectorAll('div')];
  const barEl = all.find(el => el.style && el.style.bottom === 'var(--actionbar-bottom)');
  const spacerEl = barEl ? barEl.previousElementSibling : null;

  // ---- 底栏（TabBar）几何：参照面取「按钮区顶边」= top + borderTopWidth ----
  let navBox = null;
  if (nav) {
    const r = nav.getBoundingClientRect();
    const cs = getComputedStyle(nav);
    const bt = parseFloat(cs.borderTopWidth) || 0;
    navBox = {
      top: Math.round(r.top),
      contentTop: Math.round(r.top + bt),
      bottom: Math.round(r.bottom),
      height: Math.round(r.height),
    };
  }

  // ---- 操作条几何 ----
  let bar = null, spacer = null, taps = [];
  if (barEl) {
    const r = barEl.getBoundingClientRect();
    const cs = getComputedStyle(barEl);
    bar = {
      top: Math.round(r.top),
      bottom: Math.round(r.bottom),
      height: Math.round(r.height),          // border-box 高
      borderTop: parseFloat(cs.borderTopWidth) || 0,
      position: cs.position,
      display: cs.display,
      inlineBottom: barEl.style.bottom,
      computedBottom: cs.bottom,
      pb: cs.paddingBottom,
      // 顶部发丝分隔线的**绘制**证据（见判据 D3）。
      // `getComputedStyle` 返回的是**已解析 var() 的 used value**：
      // 若令牌 `--border-default` 不存在，整条 gradient 会失效 ⇒ 这里读到 'none'。
      // 所以「不是 none 且不含 var(」足以证明「线真的会被画出来」。
      bgImage: cs.backgroundImage,
    };
    for (const el of barEl.querySelectorAll('a,button,[role=button],input,select,textarea')) {
      const rr = el.getBoundingClientRect();
      if (rr.width < 1 && rr.height < 1) continue;
      taps.push({
        tag: el.tagName.toLowerCase(),
        label: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 14),
        h: Math.round(rr.height),
      });
    }
    if (spacerEl) {
      const sr = spacerEl.getBoundingClientRect();
      spacer = {
        height: Math.round(sr.height),
        display: getComputedStyle(spacerEl).display,
        inlineHeight: spacerEl.style.height,
      };
    }
  }

  // ---- 判据 C 的扫描：条内有没有元素把 safe-bottom 加在 padding-bottom 上 ----
  const pbSafers = [];
  if (safeBottom > 0 && barEl) {
    for (const el of barEl.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const pb = parseFloat(cs.paddingBottom) || 0;
      if (Math.abs(pb - safeBottom) < 0.6) {
        pbSafers.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().split(' ').slice(0, 3).join(' '),
        });
      }
    }
  }

  // ---- 判据 D2：滚到底后，正文最后一个可见「文本叶子」是否被固定条盖住 ----
  const docScrolls = document.documentElement.scrollHeight - innerHeight > 4;
  if (docScrolls) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    void document.documentElement.offsetHeight;   // 强制布局
  }

  // ⚠️ **必须排除「固定定位子树」，否则这条判据永远报红（假红）。**
  //
  // `MobileActionBar` / `TabBar` 是 `position: fixed`，但它们**仍在 DOM 树里**
  // （通常就在 `main` 之下）。第一版没排除，扫描于是每次都命中**操作条自己的按钮**——
  // 那个按钮当然在条顶之下，于是稳定报出「正文被盖住 55px」。
  // 那不是「正文被盖住」，那是**拿条自己跟条比**。
  // 与 README 坑 11（参照面取错）同源：**参照物选错，正确实现会被测成缺陷。**
  const inFixed = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (getComputedStyle(n).position === 'fixed') return true;
    }
    return false;
  };

  let lastText = null;
  const root = document.querySelector('main') || document.body;
  for (const el of root.querySelectorAll('*')) {
    const t = (el.innerText || '').trim();
    if (!t) continue;
    if ([...el.children].some(c => (c.innerText || '').trim())) continue;  // 只看叶子
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (inFixed(el)) continue;                     // ← 排除固定条自己
    const r = el.getBoundingClientRect();
    if (r.height < 1) continue;
    if (!lastText || r.bottom > lastText.bottom) {
      lastText = {
        bottom: Math.round(r.bottom),
        who: el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(' ')[0],
      };
    }
  }

  return {
    vw: innerWidth, vh: innerHeight,
    safeRaw, safeBottom, actionbarH,
    navBox, bar, spacer, taps, pbSafers, lastText, docScrolls,
    scrollY: Math.round(window.scrollY),
  };
})()"""


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


async def resolve_case_url(b: Browser, base: str) -> str | None:
    """进到某个案件的详情页，返回**实际** URL（不猜 id、不写死）。

    ## 为什么要「点」而不是「读 href」

    `DataTable`（`packages/ui/src/components/DataTable.tsx`）**两种形态**：
    · 桌面 `hidden sm:block` ⇒ `<table><tbody><tr onClick={onRowClick}>`
    · 窄屏 `sm:hidden`        ⇒ `<ul><li onClick={onRowClick}>`

    两者都是 **`onClick` + `router.push`**，**没有 `<a href>`**。
    实测 390×844 下 `/cases` 页 `a` 锚点总数 = **0** ——
    所以「读 href」这条路在这里根本不存在，必须**点一下再读 `location`**。
    （im 的 `/cases` 用的是真实链接，所以 `verify_runtime_health.py` 的读法在那里成立，
    换到 lawyer 就不成立 —— 同一个路径、两种实现，**判据不能跨端复用**。）

    读 DOM 而不是调 API：SDK 把 access token 存在**模块内存变量**里
    （刻意不落 localStorage），CDP 里发 `fetch` 带不上鉴权。

    解析失败时**打印诊断**，否则分不清「页面结构变了」与「我没测成」。
    """
    await b.goto(f"{base}/cases", wait=1.5)
    await b.settle(extra=2.5)

    # 候选点击目标，按「可靠度」排序；逐个试，点完看 URL 变没变。
    candidates = (
        ("a[href^=\"/cases/\"]（真实锚点）",
         "(() => { const a = [...document.querySelectorAll('a[href^=\"/cases/\"]')]"
         ".find(x => x.getAttribute('href').split('/').filter(Boolean).length >= 2);"
         " if (a) { a.click(); return true; } return false; })()"),
        ("tbody tr（桌面表格行）",
         "(() => { const t = [...document.querySelectorAll('tbody tr')]"
         ".find(x => (x.innerText||'').trim() && x.getBoundingClientRect().height > 0);"
         " if (t) { t.click(); return true; } return false; })()"),
        ("main ul li（窄屏卡片）",
         "(() => { const l = [...document.querySelectorAll('main ul li')]"
         ".find(x => (x.innerText||'').trim() && x.getBoundingClientRect().height > 0);"
         " if (l) { l.click(); return true; } return false; })()"),
    )

    for label, js in candidates:
        clicked = await b.cdp.evaluate(js)
        if not clicked:
            continue
        await asyncio.sleep(2.5)
        path = await b.cdp.evaluate("location.pathname") or ""
        if path.startswith("/cases/") and len(path.split("/")) > 2 and path.split("/")[2]:
            print(f"   进入详情：点击 {label} ⇒ {path}")
            return f"{base}{path}"

    # ---- 三种都失败：打诊断，明确这是「没测成」而不是「产品坏了」----
    diag = await b.cdp.evaluate(
        "(() => {"
        " const anchors = [...document.querySelectorAll('a')];"
        " const rows = document.querySelectorAll('[role=row],tbody tr,[data-case-id],li');"
        " return {"
        "   anchorCount: anchors.length,"
        "   hrefs: [...new Set(anchors.map(a => a.getAttribute('href')))].slice(0, 15),"
        "   rowCount: rows.length,"
        "   bodyText: (document.body.innerText || '').replace(/\\n{2,}/g,'\\n').trim().slice(0, 300),"
        " };"
        "})()"
    )
    print("    —— 诊断（`/cases` 页 DOM）——")
    print(f"   锚点总数={diag['anchorCount']}  行元素={diag['rowCount']}")
    print(f"   页面上出现过的 href：{diag['hrefs']}")
    print("   可见正文：")
    for line in (diag["bodyText"] or "").splitlines()[:14]:
        print(f"     | {line}")
    return None


# 按「一定渲染出操作条」的可靠度排序。
# `archive` 永远有（已归档 ⇒ 导出开庭材料包；未归档 ⇒ 归档案件）；
# `analysis` / `review` 只在特定状态才有（见 main() 里的长注释）。
BAR_TABS = ("归档卷宗", "六段式分析", "复核记录")


async def select_tab_with_bar(b: Browser) -> str | None:
    """点页签，直到页面上出现操作条。返回命中的页签名；都不行则返回 None。

    **每次整页导航后都要重新调用一次** —— 导航会把页签重置回默认的 `analysis`。
    """
    for label in BAR_TABS:
        clicked = await b.cdp.evaluate(
            "(() => { const lb = %s;"
            " const el = [...document.querySelectorAll('button,[role=tab],a')]"
            ".find(x => (x.innerText || '').trim() === lb);"
            " if (!el) return false; el.click(); return true; })()" % json.dumps(label)
        )
        if not clicked:
            continue
        await asyncio.sleep(2.5)
        probe = await b.cdp.evaluate(FIND_JS)
        if probe and probe["found"]:
            return label
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3001, help="律师端端口（默认 3001）")
    ap.add_argument("--user", default="lawyer_wang")
    ap.add_argument("--shot", action="store_true", help="存一张取证截图（叠放关系肉眼可查）")
    args = ap.parse_args()

    base = f"http://localhost:{args.port}"
    print(f"── MobileActionBar × TabBar 叠放审计  {base}  390×844 ──\n")

    bad: list[str] = []

    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        if not await login(b, base, args.user):
            print("  ✗ 登录失败（环境问题）")
            return 2

        url = await resolve_case_url(b, base)
        if not url:
            print("  ✗ `/cases` 里找不到案件链接（环境问题：种子数据或路由变更）")
            return 2
        print(f"  案件页：{url.replace(base, '')}")

        # 整页导航进详情（不走客户端路由），保证 dev server 已把该路由编译完
        await b.goto(url, wait=2.0)
        await b.settle(extra=3.0)

        # ⚠️ **操作条是「有条件渲染」的，不能一上来就断言它应该在。**
        #
        # `app/(app)/cases/[id]/page.tsx:1225` 是 `{mobileBar && (…)}`，
        # 而 `mobileBar`（l.831 的 IIFE）按页签返回 null：
        #   · `analysis`：仅在「还没生成 AI 分析」或「已生成且复核可提交」时才有
        #   · `evidence` / `events`：**永远没有**（l.927 注释：「没有『一个决定性动作』，
        #     就不占这块位置」）
        #   · `review`：仅在可裁决 / 可归档时才有
        #   · `archive`：**永远有**（已归档 ⇒ 导出开庭材料包；未归档 ⇒ 归档案件）
        #
        # ⇒ 实测在默认页签（`analysis`）下、案件 8 上**确实没有**操作条 ——
        #   这是**设计如此**，不是缺陷。若不加区分就报「操作条缺失」，
        #   那就是本项目反复踩的**假红**（README 坑 12：断言之前先确认它本来就应该有）。
        #
        # 做法：按「一定会有」的可靠度排序去点页签，取第一个真的渲染出操作条的。
        label = await select_tab_with_bar(b)
        if label is None:
            print("  ✗ 三个页签都没渲染出操作条 ⇒ **无法测量**")
            print("    这不是产品缺陷，是「没有可测的状态」（数据/设计导致）")
            return 2
        print(f"  操作条出现在「{label}」页签 ✓")

        # ---- off 臂：无安全区 ----
        off = await b.cdp.evaluate(MEASURE_JS)

        if args.shot:
            # ⚠️ `screenshot(full=True)` 是**整页**截图，而 `MobileActionBar` / `TabBar`
            #    是 `position: fixed` —— 整页截图里固定元素只会被画在**初始视口**的位置，
            #    翻到页面中部根本看不到它们，容易被误读成「条没渲染」。
            #    ⇒ 两种都存：整页看上下文，**视口截图**看叠放本身。
            shot = HERE / "shot_actionbar_stack_off.png"
            await b.screenshot(shot)
            shot_vp = HERE / "shot_actionbar_stack_off_viewport.png"
            await b.screenshot(shot_vp, full=False)
            print(f"  截图：{shot.name} / {shot_vp.name}")

        # ---- on 臂：模拟刘海屏（底部 34px）----
        dev = await b.apply_device(safe_area=SAFE_ON, mobile=True)
        if not dev.get("supported"):
            print(f"  ✗ 安全区模拟不受支持：{dev.get('note')}（环境问题）")
            return 2
        await b.goto(url, wait=1.5)
        await b.settle(extra=2.5)
        # ⚠️ **整页导航会把页签重置回默认值（`analysis`）**，而默认页签在这个案件上
        #    是**没有**操作条的 ⇒ 必须**重新选一次页签**，否则 `bar` 为 None、
        #    后面直接 `TypeError` 崩掉（第一版就是这么崩的）。
        await select_tab_with_bar(b)
        on = await b.cdp.evaluate(MEASURE_JS)

        if args.shot:
            shot = HERE / "shot_actionbar_stack_on.png"
            await b.screenshot(shot)
            shot_vp = HERE / "shot_actionbar_stack_on_viewport.png"
            await b.screenshot(shot_vp, full=False)
            print(f"  截图：{shot.name} / {shot_vp.name}")

        # ---- 判据 F：桌面 1280 宽应整体隐藏 ----
        #
        # ⚠️ **`apply_device(mobile=False)` 不会改视口宽度** ——
        #    它用的是构造时的 `self.width`（本脚本是 390）。所以在它之后量 `lg:hidden`，
        #    实际是在 **390px** 下量的，而 `lg:`（min-width:1024px）在 390px 下
        #    **本来就不该生效** ⇒ `display:block` 是**正确**的。
        #    第一版据此报了「桌面未隐藏」，是**假红**（第三次栽在「判据的适用范围」上）。
        #    ⇒ 必须**显式**把视口改成 1280，并在下面**断言前提**（innerWidth ≥ 1024）。
        await b.cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=1280, height=900, deviceScaleFactor=1, mobile=False,
        )
        await b.goto(url, wait=1.5)
        await b.settle(extra=2.0)
        await select_tab_with_bar(b)
        desk = await b.cdp.evaluate(MEASURE_JS)

    if not (off and on and desk):
        print("  ✗ 测量返回空（环境问题）")
        return 2

    # ---- 前提锁 ----
    for arm, m, want in (("off", off, 0.0), ("on", on, float(SAFE_ON["bottom"]))):
        if abs(m["safeBottom"] - want) > 0.5:
            print(f"  ✗ 前提不成立：{arm} 臂 --safe-bottom={m['safeRaw']!r}，期望 {want}px（环境问题）")
            return 2

    print(f"  安全区 off={off['safeRaw']!r}  on={on['safeRaw']!r}   "
          f"--actionbar-h={on['actionbarH']}px  视口 {on['vw']}x{on['vh']}\n")

    for arm, m in (("off", off), ("on", on)):
        bar, navBox = m["bar"], m["navBox"]
        if not bar or not navBox:
            print(f"── [{arm}] 臂 ──  ✗ 操作条或底栏不存在，跳过该臂判定")
            bad.append(f"[{arm}] 该臂测不到操作条或底栏（页签选择失败 / 结构变更）")
            continue
        print(f"── [{arm}] 臂 ──")
        print(f"   底栏 TabBar : top={navBox['top']} 按钮区顶={navBox['contentTop']} "
              f"bottom={navBox['bottom']} 高={navBox['height']}")
        print(f"   操作条      : top={bar['top']} bottom={bar['bottom']} 高={bar['height']} "
              f"border-top={bar['borderTop']} {bar['position']} computed bottom={bar['computedBottom']}")

        # ---- 判据 B：叠在 TabBar 正上方，gap == 0 ----
        gap = navBox["contentTop"] - bar["bottom"]
        print(f"   [B] 操作条底边 ↔ 底栏按钮区顶边 gap={gap}px（期望 0）")
        if gap != 0:
            hint = ""
            if gap > 0:
                hint = f"；两固定条之间有 {gap}px 缝，正文会从缝里露出来"
            else:
                hint = f"；操作条压住了底栏 {-gap}px"
            bad.append(f"[{arm}] 判据 B：操作条与底栏 gap={gap}px ≠ 0{hint}")

        # ---- 判据 C：安全区只能由底栏应用一次 ----
        if m["safeBottom"] <= 0:
            print("   [C] （本臂 --safe-bottom=0，扫描为空真 ⇒ 不作为判据）")
        elif m["pbSafers"]:
            names = ", ".join(f"{e['tag']}.{e['cls']}" for e in m["pbSafers"])
            print(f"   [C] 条内带 pb≈safe-bottom 的元素：**{len(m['pbSafers'])} 个 ✗** {names}")
            bad.append(
                f"[{arm}] 判据 C：操作条自己也应用了 --safe-bottom（{names}）"
                f" ⇒ 安全区被应用 2 次，操作条上方会多出 {m['safeBottom']}px 空白"
            )
        else:
            print("   [C] 条内带 pb≈safe-bottom 的元素：0 个 ✓（安全区只由底栏负责）")

        # ---- 判据 D1：等高占位 ----
        sp = m["spacer"]
        if not sp:
            print("   [D1] **找不到等高占位 ✗**")
            bad.append(f"[{arm}] 判据 D1：操作条前没有等高占位（正文末尾会被固定条永久盖住）")
        else:
            diff = bar["height"] - sp["height"]
            print(f"   [D1] 占位={sp['height']}px  条 border-box 高={bar['height']}px"
                  f"  ⇒ 差 {diff}px（期望 0）")
            if diff != 0:
                bad.append(
                    f"[{arm}] 判据 D1：占位 {sp['height']}px ≠ 条高 {bar['height']}px（差 {diff}px）"
                    f" ⇒ 违反组件 l.13「**等高**占位」契约"
                )

        # ---- 判据 D2：有无可见后果（与 D1 分开报，避免把「契约未达成」说成「正文被裁」）----
        lt = m["lastText"]
        if lt:
            covered = lt["bottom"] - bar["top"]
            print(f"   [D2] 正文最后可见元素 bottom={lt['bottom']} ({lt['who']})  "
                  f"条顶={bar['top']}  ⇒ {'被盖住 %dpx ✗' % covered if covered > 0 else '未被盖住 ✓'}")
            if covered > 0:
                bad.append(
                    f"[{arm}] 判据 D2：正文最后一个可见元素被固定条盖住 {covered}px（{lt['who']}）"
                )
        else:
            print("   [D2] 正文里找不到可见文本叶子（空真 ⇒ 不作为判据）")

        # ---- 判据 D3：顶部发丝分隔线**真的会被画出来** ----
        #
        # 🚨 **为什么必须有这一条**：D1 的修法是把 `border-t`（占 1px）换成
        #    `hairline-top` 的背景渐变（不占 1px）。但 D1 只证明「**边框没了**」
        #    （`border-top=0`、高=60），**证明不了「那条线还在」**——
        #    若渐变因令牌缺失而失效，两条固定条会**视觉合并**，而 D1 照样全绿。
        #    ⇒ 替换掉一个可见元素后，必须**单独断言替换物存在**（「存在性优于计数」）。
        #
        # 判据取 `getComputedStyle` 的 `backgroundImage`：它是**已解析 var() 的 used value**，
        # 令牌不存在时整条 gradient 失效、这里会读到 `'none'` ⇒ 足以判死。
        bg = bar["bgImage"] or ""
        if bg == "none" or "var(" in bg:
            print(f"   [D3] 顶部分隔线 **未渲染 ✗** backgroundImage={bg!r}")
            bad.append(
                f"[{arm}] 判据 D3：顶部发丝分隔线未渲染（backgroundImage={bg!r}）"
                f" ⇒ 操作条与上方正文之间失去分隔"
            )
        else:
            short = bg if len(bg) <= 96 else bg[:93] + "..."
            print(f"   [D3] 顶部分隔线已渲染 ✓  backgroundImage={short}")

        # ---- 判据 G：条内触控目标 ----
        small = [t for t in m["taps"] if t["h"] < MIN_TAP]
        print(f"   [G] 条内触控目标 {len(m['taps'])} 个，"
              + ("全部 ≥48px ✓" if not small else f"**{len(small)} 个 <48px ✗** {small}"))
        if small:
            bad.append(
                f"[{arm}] 判据 G：条内 {len(small)} 个触控目标 <{MIN_TAP}px（规范 §8.3）："
                + ", ".join(f"{t['label'] or t['tag']}={t['h']}px" for t in small)
            )
        print()

    # ---- 判据 F：桌面端隐藏 ----
    #
    # 🚨 **前提锁**：`lg:` 是 `min-width:1024px`。若视口没真的到 1024 以上，
    #    这条判据**根本不适用** —— 在 390px 下量 `lg:hidden` 必然量到 `block`，
    #    而那是**正确**的。所以先断言视口宽度，不成立就**不判**（既不判绿也不判红）。
    dbar = desk["bar"]
    print("── [desktop 1280] 臂 ──")
    print(f"   视口 innerWidth={desk['vw']}（`lg:` 断点 1024 ⇒ 前提 innerWidth ≥ 1024）")
    if desk["vw"] < 1024:
        print("   ⚠ 前提不成立：视口未达 lg 断点 ⇒ 判据 F **不适用，不作判定**")
    elif not dbar:
        print("  ✗ 桌面上找不到操作条（`lg:hidden` 下 DOM 仍在，只是 display:none）")
        bad.append("判据 F：桌面臂找不到操作条，无法判定 `lg:hidden`")
    else:
        print(f"   操作条 display={dbar['display']!r}（`lg:hidden` ⇒ 期望 'none'）")
        if dbar["display"] != "none":
            bad.append(f"判据 F：桌面 1280 宽下操作条 display={dbar['display']!r}，应被 `lg:hidden` 隐藏")
    print()

    print("── 汇总 ──")
    if bad:
        print("  ✗ 发现缺陷：")
        for d in bad:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(bad)} 条")
        return 1
    print("  ✓ 操作条正叠在底栏之上（gap=0）；安全区只应用一次；占位等高；桌面端已隐藏")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

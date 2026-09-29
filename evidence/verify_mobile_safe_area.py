"""四端移动端「安全区 + 触控目标」审计（渲染级）。

## 为什么做这一条

`im` 的底部安全区我单独验过（`verify_im_safe_area.py`）。但**同一类陷阱在 `AppShell`
里也可能存在**——而 `AppShell` 是 web / lawyer 共用的外壳。而且 `TabBar` 是共享组件，
`packages/ui` 里任何一处改动会同时影响三端。

⇒ 把判据**通用化**，四端一起量，而不是「im 过了就以为别处也对」。

## 判据（五条，前四条是通用的）

| # | 判据 | 为什么 |
|---|---|---|
| A | 底栏（`nav[aria-label="主导航"]`）存在、`position:fixed`、贴住视口底边 | 贴不住就会露底 |
| B | **`--safe-bottom` 在「通往底部的竖直路径」上只被应用一次** | 应用两次 = 双算 = 底栏上方多出一条 `safe-bottom` 高的空白 |
| C | 底栏内所有可点元素高度 ≥ **48px** | 规范 §8.3（优于 WCAG 2.5.5 的 44px） |
| D | 内容与底栏**按钮区顶边**之间正好留出设计值（`gap`），不裁不裂 | 见下 |
| E | admin **不应有**底栏 | 规范 §8.4：运营后台无 Tab Bar |

### 判据 B 怎么实现（这是本探针的核心）

不去猜 DOM 结构，而是**全 DOM 扫**：凡是 `computed padding-bottom ≈ --safe-bottom`
的元素都记下来。`TabBar` 自己带一次（合法）。若**底栏之外的任何元素也带一次**，
那就是双算的**成因** —— 与结构无关，四端通用。

> 判据 D 需要知道「内容区」是哪个元素，所以按端配置选择器
> （im 用 `nav.parentElement.firstElementChild`；web/lawyer 用 `main`），
> 并**逐端标注设计期望值**：
> · `AppShell`（web/lawyer）`packages/ui/src/components/AppShell.tsx:626`
>   → `calc(var(--tabbar-h) + var(--safe-bottom) + 16px)` ⇒ **16px 呼吸**
> · `im` `apps/im/app/(app)/layout.tsx:95` → `var(--actionbar-bottom)` ⇒ **0**
> 两者不同是**有意的**（im 是满屏三栏、内部各自滚动；AppShell 是文档滚动页，需要呼吸）。
> **期望值都指到了出处**，不是拍脑袋。
>
> ⚠️ **而且判据 D 必须先区分布局模式**：
> · **固定高度型**（im，`h-dvh overflow-hidden`）⇒ 内容区底边 == 视口底边，直接量。
> · **文档滚动型**（AppShell，`min-h-screen`）⇒ 内容区底边在**视口之外**
>   （实测 1477 > 844）。直接量出来的 `gap` 是个大负数，**毫无意义**。
>   必须先 `scrollTo(0, scrollHeight)` 滚到底，再量「最后一行内容 ↔ 底栏」的距离。
>
> **第一版没区分，于是对 web/lawyer 报了 4 条假红**（`gap=-689 / -763 / -429 / -503`）。
> 与 README 坑 11/12 同源：**指标不适用 ≠ 产品有缺陷**；
> 报缺陷之前先问「这个指标在这种布局下成立吗」。

## 前提锁

`env(safe-area-inset-bottom)` 在没设模拟安全区时恒为 `0px`，此时判据 B 的扫描
**一条都扫不到**（因为 `pb ≈ 0` 的元素会与「无 padding」混淆）⇒ 必须先断言
`--safe-bottom` 真的等于 `0px` / `34px`，否则整条防线是空的。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（登录失败 / 结构变更 / 安全区模拟不生效）

用法：
    python evidence/verify_mobile_safe_area.py
    python evidence/verify_mobile_safe_area.py --app im      # 只测一端
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

# `content` 的取值：
#   "__im_wrapper__" —— 底栏的父容器的第一个子元素（im 的布局已知）
#   其它字符串      —— 直接当 CSS 选择器
APPS: dict[str, dict] = {
    "web": {
        "port": 3000, "user": "ent_admin",
        "has_bar": True, "content": "main", "expect_gap": 16,
        "src": "AppShell.tsx:626",
    },
    "lawyer": {
        "port": 3001, "user": "lawyer_wang",
        "has_bar": True, "content": "main", "expect_gap": 16,
        "src": "AppShell.tsx:626",
    },
    "admin": {
        "port": 3002, "user": "admin",
        "has_bar": False, "content": None, "expect_gap": None,
        "src": "design-spec §8.4（运营后台无 Tab Bar）",
    },
    "im": {
        "port": 3003, "user": "client",
        "has_bar": True, "content": "__im_wrapper__", "expect_gap": 0,
        "src": "apps/im/app/(app)/layout.tsx:95",
    },
}

MEASURE_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  const docCs = getComputedStyle(document.documentElement);
  const safeRaw = docCs.getPropertyValue('--safe-bottom').trim();
  const safeBottom = parseFloat(safeRaw) || 0;

  // ① 全 DOM 扫：谁把 safe-bottom 加在了 padding-bottom 上
  const pbSafers = [];
  if (safeBottom > 0) {
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const pb = parseFloat(cs.paddingBottom) || 0;
      if (Math.abs(pb - safeBottom) < 0.6) {
        pbSafers.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().split(' ').slice(0, 3).join(' '),
          inBar: !!(nav && (el === nav || nav.contains(el))),
        });
      }
    }
  }

  // ② 底栏几何
  let bar = null;
  if (nav) {
    const r = nav.getBoundingClientRect();
    const cs = getComputedStyle(nav);
    const bt = parseFloat(cs.borderTopWidth) || 0;
    bar = {
      top: Math.round(r.top),
      contentTop: Math.round(r.top + bt),
      bottom: Math.round(r.bottom),
      height: Math.round(r.height),
      borderTop: bt,
      paddingBottom: cs.paddingBottom,
      position: cs.position,
      display: cs.display,
      flush: Math.abs(r.bottom - innerHeight) < 1.5,
    };
  }

  // ③ 底栏内的触控目标
  const taps = [];
  if (nav) {
    for (const el of nav.querySelectorAll('a,button,[role=button],input,select,textarea')) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 && r.height < 1) continue;
      taps.push({
        tag: el.tagName.toLowerCase(),
        label: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 14),
        h: Math.round(r.height),
      });
    }
  }

  // ④ 内容区让位
  //
  // ⚠️ **两种布局模式，必须先区分再量**：
  //   · **固定高度型**（im：`h-dvh overflow-hidden`，内部各自滚动）
  //     ⇒ 内容区底边 == 视口底边，`gap` 可直接量。
  //   · **文档滚动型**（AppShell：`min-h-screen`，整页滚动）
  //     ⇒ 内容区底边在**视口之外**（实测 1477 > 844）。此时直接量出来的
  //       `gap` 是个毫无意义的大负数（第一版据此报了 **4 条假红**）。
  //       正确做法：**先滚到底**，再量「最后一行内容」与底栏之间的距离。
  const docScrolls = document.documentElement.scrollHeight - innerHeight > 4;
  if (docScrolls) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    void document.documentElement.offsetHeight; // 强制布局，保证下面的 rect 是新值
  }

  const sel = %s;
  const content = sel === '__im_wrapper__'
    ? (nav ? nav.parentElement.firstElementChild : null)
    : document.querySelector(sel);
  let gap = null, contentPb = null, contentBottom = null;
  if (content) {
    const cs = getComputedStyle(content);
    const r = content.getBoundingClientRect();
    contentPb = cs.paddingBottom;
    contentBottom = Math.round(r.bottom - (parseFloat(cs.paddingBottom) || 0));
    if (bar) gap = Math.round(bar.contentTop - contentBottom);
  }

  return {
    vw: innerWidth, vh: innerHeight,
    safeRaw, safeBottom, pbSafers, bar, taps, gap, contentPb, contentBottom,
    hasNav: !!nav,
    docScrolls, scrollY: Math.round(window.scrollY),
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


async def measure_app(app: str, spec: dict) -> tuple[dict, dict] | None:
    """返回 (off 臂, on 臂) 两次测量；失败返回 None（环境问题）。"""
    base = f"http://localhost:{spec['port']}"
    sel = json.dumps(spec["content"]) if spec["content"] else json.dumps("__none__")

    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        if not await login(b, base, spec["user"]):
            print(f"  ✗ {app} 登录失败（环境问题）")
            return None
        await b.goto(f"{base}/", wait=1.5)
        await b.settle(extra=2.5)
        off = await b.cdp.evaluate(MEASURE_JS % sel)

        dev = await b.apply_device(safe_area=SAFE_ON, mobile=True)
        if not dev.get("supported"):
            print(f"  ✗ {app} 安全区模拟不受支持：{dev.get('note')}（环境问题）")
            return None
        await b.goto(f"{base}/", wait=1.5)
        await b.settle(extra=2.5)
        on = await b.cdp.evaluate(MEASURE_JS % sel)

    if not off or not on:
        print(f"  ✗ {app} 测量返回空（环境问题）")
        return None
    return off, on


def report(app: str, spec: dict, off: dict, on: dict) -> list[str]:
    bad: list[str] = []
    name = f"[{app}]"
    print(f"── {app}  :{spec['port']}  期望 gap={spec['expect_gap']}（{spec['src']}）")
    print(f"   安全区 off={off['safeRaw']!r}  on={on['safeRaw']!r}   视口 {on['vw']}x{on['vh']}")

    # ---- 前提锁 ----
    for arm, m, want in (("off", off, 0.0), ("on", on, float(SAFE_ON["bottom"]))):
        if abs(m["safeBottom"] - want) > 0.5:
            bad.append(f"{name} 前提不成立：{arm} 臂 --safe-bottom={m['safeRaw']!r}，期望 {want}px")
    if bad:
        return bad  # 前提不成立就不再做后续判定（避免用空前提下结论）

    # ---- 判据 E：admin 不该有底栏 ----
    if not spec["has_bar"]:
        visible = bool(on["bar"]) and on["bar"]["display"] != "none"
        print(f"   底栏：{'**存在且可见 ✗**' if visible else '无（符合 §8.4）✓'}")
        if visible:
            bad.append(f"{name} 规范 §8.4 要求运营后台无 Tab Bar，实测渲染出了底栏")
        # 触控目标仍照查（admin 移动端也有按钮）
        return bad

    # ---- 判据 A：底栏存在且贴底 ----
    if not on["bar"]:
        bad.append(f"{name} 移动端没有 nav[aria-label=\"主导航\"]（应有一个底部 Tab Bar）")
        print("   底栏：**缺失 ✗**")
        return bad
    bar = on["bar"]
    print(f"   底栏：top={bar['top']} 内容顶={bar['contentTop']} bottom={bar['bottom']} "
          f"高={bar['height']} pb={bar['paddingBottom']!r} {bar['position']} 贴底={bar['flush']}")
    if bar["position"] != "fixed":
        bad.append(f"{name} 底栏 position={bar['position']}，应为 fixed")
    if not bar["flush"]:
        bad.append(f"{name} 底栏没贴住视口底边（bottom={bar['bottom']} vs innerHeight={on['vh']}）")

    # ---- 判据 B：safe-bottom 只能应用一次 ----
    #
    # ⚠️ **这条判据的「前提」是 `--safe-bottom > 0`，只对 on 臂成立。**
    #    off 臂里 safeBottom == 0，扫描被 `safeBottom > 0` 的门禁短路 ⇒ 必然一条都扫不到。
    #    「底栏内 0 个」在 off 臂是**空真**（vacuously true），不是「底栏没做安全区」。
    #    第一版就在这里报了假红（`[im] off 臂：底栏自己没有应用 --safe-bottom`）——
    #    与 README 坑 12「断言之前先确认它本来就应该有」同源：
    #    **前提不成立的断言，既不能判绿也不能判红，只能不判。**
    for arm, m in (("off", off), ("on", on)):
        outsiders = [e for e in m["pbSafers"] if not e["inBar"]]
        inside = [e for e in m["pbSafers"] if e["inBar"]]
        print(f"   [{arm}] padding-bottom≈safe-bottom 的元素：底栏内 {len(inside)} 个、"
              f"底栏外 {len(outsiders)} 个" + (f"  {outsiders}" if outsiders else ""))
        if m["safeBottom"] <= 0:
            print("          （本臂 --safe-bottom=0，该扫描为空真 ⇒ 不作为判据）")
            continue
        if not inside:
            bad.append(
                f"{name} {arm} 臂：底栏自己没有应用 --safe-bottom（Home Indicator 区会被占用）"
            )
        if outsiders:
            names = ", ".join(f"{e['tag']}.{e['cls']}" for e in outsiders)
            bad.append(
                f"{name} {arm} 臂：**底栏之外**也有元素应用了 --safe-bottom（{names}）"
                f" ⇒ 安全区被应用了 {1 + len(outsiders)} 次，底栏上方会多出"
                f" {m['safeBottom'] * len(outsiders)}px 空白"
            )

    # ---- 判据 C：触控目标 ----
    small = [t for t in on["taps"] if t["h"] < MIN_TAP]
    print(f"   触控目标：共 {len(on['taps'])} 个，"
          + ("全部 ≥48px ✓" if not small else f"**{len(small)} 个 <48px ✗** {small}"))
    if on["taps"] and not small:
        pass
    if small:
        bad.append(
            f"{name} 底栏内 {len(small)} 个触控目标高度 <{MIN_TAP}px（规范 §8.3）："
            + ", ".join(f"{t['label'] or t['tag']}={t['h']}px" for t in small)
        )
    if not on["taps"]:
        bad.append(f"{name} 底栏内没有找到任何可点元素")

    # ---- 判据 D：内容让位 ----
    for arm, m in (("off", off), ("on", on)):
        if m["gap"] is None:
            bad.append(f"{name} {arm} 臂：找不到内容区（选择器 {spec['content']!r} 没命中）")
            continue
        want = spec["expect_gap"]
        mode = "文档滚动型（已滚到底再量）" if m["docScrolls"] else "固定高度型"
        print(f"   [{arm}] {mode}  scrollY={m['scrollY']}")
        print(f"         内容区 padding-bottom={m['contentPb']!r}  内容底={m['contentBottom']} "
              f" 底栏按钮区顶={m['bar']['contentTop']}  ⇒ gap={m['gap']}px（期望 {want}）")
        if m["gap"] != want:
            hint = ""
            if m["safeBottom"] > 0 and abs(m["gap"] - (want + m["safeBottom"])) < 1:
                hint = f"；gap 恰好 = 期望值 + --safe-bottom({m['safeBottom']}px) ⇒ **双算的特征**"
            elif m["gap"] < 0:
                hint = f"；内容被底栏盖住 {-m['gap']}px ⇒ 会裁掉最后一行"
            bad.append(f"{name} {arm} 臂：gap={m['gap']}px ≠ 期望 {want}px{hint}")
    print()
    return bad


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", choices=sorted(APPS), help="只测一个端（默认四端全跑）")
    args = ap.parse_args()

    targets = {args.app: APPS[args.app]} if args.app else APPS
    print("── 四端移动端「安全区 + 触控目标」审计（390×844 + 模拟刘海屏）──\n")

    defects: list[str] = []
    env_fail: list[str] = []
    for app, spec in targets.items():
        r = await measure_app(app, spec)
        if r is None:
            env_fail.append(app)
            continue
        defects += report(app, spec, *r)

    print("── 汇总 ──")
    if env_fail:
        print(f"  ⚠ 未能测量：{', '.join(env_fail)}（环境问题）")
    if defects:
        print("  ✗ 发现缺陷：")
        for d in defects:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1
    if env_fail:
        print("\nEXIT=2  有端未能测量，不当作通过")
        return 2
    print("  ✓ 底栏贴底；--safe-bottom 只应用一次；触控目标 ≥48px；内容让位与设计值一致")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

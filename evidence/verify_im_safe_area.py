"""渲染级验证：im（客户端）底部安全区**没有被双算**。

## 为什么需要这个探针

`layout.tsx` 里我做了这么一件事：底部安全区**只让 `TabBar` 加**，
根容器不加。原因是 `TabBar` 组件内部已经写了
`style={{ paddingBottom: "var(--safe-bottom)" }}`。

如果根容器**也**加一次 `padding-bottom: var(--safe-bottom)`，在刘海屏上
（iPhone 实测 34px）底部会多出一整条**空白带**：内容被顶上去 34px，
而 TabBar 还在原位 ⇒ 内容与 TabBar 之间裂开一条 34px 的缝。

**这类缺陷静态门禁全覆盖不到**：`grep` 看得见两个 `var(--safe-bottom)`，
看不见它们是不是叠在了同一条边上；而 `env()` 又只在真浏览器里有值
（桌面 Chrome 上恒为 `0px`，缺陷表现为 0px 缝隙，**根本看不出来**）。

## 判据（怎么算「双算」）

三个元素：
  - 根容器（`nav.parentElement`）：`flex h-dvh flex-col`，只加 top/left/right 安全区
  - 内容区（`root.firstElementChild`）：`min-h-0 flex-1 pb-[var(--actionbar-bottom)]`
  - TabBar（`nav[aria-label="主导航"]`）：`fixed bottom-0`，内部 `paddingBottom: --safe-bottom`

令 `contentBottom = wrap.getBoundingClientRect().bottom - wrap的paddingBottom`。

⚠️ **参照面取 TabBar 的「内容顶」而不是「边框顶」**：`TabBar` 自带
`border-t`（1px，`border-sidebar-border`），所以它的 border-box 比 `--tabbar-h`
高 1px。内容**应该**正好铺到按钮区的顶边（= `nav.top + borderTopWidth`），
由 TabBar 自己那 1px 边框压住这条缝——这是有边框的固定条的正常画法。

  | 状态 | 根容器 pb | wrap pb | nav 边框顶 | nav 内容顶 | contentBottom | gap |
  |---|---|---|---|---|---|---|
  | 正确 | `0px` | `90px` | 753 | **754** | **754** | **0** |
  | 双算 | `34px` | `90px` | 753 | 754 | 720 | **34** |
  | 少留 | `0px` | `89px` | 753 | 754 | 755 | **-1**（内容被压住） |

⇒ `gap = nav内容顶 - contentBottom` 就是「内容与按钮区之间多出来/被吃掉多少」，
而根容器的 `padding-bottom` 是双算的**成因**。两个都测、都报。
**不做任何容差**：这三个量都是整数 CSS px，容差会让上面三行互相混淆。

## 前提锁（否则整条防线是空的）

`env(safe-area-inset-bottom)` 在没设模拟安全区时恒为 `0px`，此时
「`gap == 0`」**必然成立**——不是因为我做对了，而是因为根本没有安全区。
所以每个臂都必须先断言 `--safe-bottom` 的实际值：
  - `off` 臂：`--safe-bottom` 必须 == `0px`
  - `on`  臂：`--safe-bottom` 必须 == `34px`
任一不成立 ⇒ **退出 2（环境问题）**，绝不当成「通过」。
这正是「一条只会绿的防线不算防线」要挡的那种假绿。

## 退出码

  `0` 通过 · `1` 产品缺陷（检测到双算） · `2` 环境问题（登录失败 / 安全区模拟不生效）

用法：
    python evidence/verify_im_safe_area.py            # 只测 im
    python evidence/verify_im_safe_area.py --keep     # 保留截图文件名带 -keep 后缀
    python evidence/verify_im_safe_area.py --port 3003  # 指向别的实例（如生产构建 `next start`）
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

DEFAULT_PORT = 3003
USER = "client"

# 模拟一台 iPhone 14 Pro（刘海 + Home Indicator）
SAFE_ON = {"top": 47, "bottom": 34, "left": 0, "right": 0}

# 一次算完所有量，避免多轮往返（每轮都是一次 WS 往返）
MEASURE_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  if (!nav) return { ok: false, why: '页面上没有 nav[aria-label="主导航"]' };
  const root = nav.parentElement;
  const wrap = root.firstElementChild;
  if (!wrap) return { ok: false, why: '根容器没有子元素，结构不符预期' };

  const docCs = getComputedStyle(document.documentElement);
  const rootCs = getComputedStyle(root);
  const wrapCs = getComputedStyle(wrap);
  const navCs = getComputedStyle(nav);

  const nr = nav.getBoundingClientRect();
  const wr = wrap.getBoundingClientRect();

  const rootPb = parseFloat(rootCs.paddingBottom) || 0;
  const wrapPb = parseFloat(wrapCs.paddingBottom) || 0;
  // 内容区里**真正画到哪儿为止**：box 底边再扣掉它自己留的 padding
  const contentBottom = wr.bottom - wrapPb;

  // TabBar 自带 border-t，参照面要取「内容顶」而不是「边框顶」
  const navBorderTop = parseFloat(navCs.borderTopWidth) || 0;
  const navContentTop = nr.top + navBorderTop;
  const navBottomBorder = parseFloat(navCs.borderBottomWidth) || 0;

  const btn = nav.querySelector('button');
  const br = btn ? btn.getBoundingClientRect() : null;

  return {
    ok: true,
    innerHeight: innerHeight,
    innerWidth: innerWidth,
    token: {
      safeBottom: docCs.getPropertyValue('--safe-bottom').trim(),
      safeTop: docCs.getPropertyValue('--safe-top').trim(),
      tabbarH: docCs.getPropertyValue('--tabbar-h').trim(),
      actionbarBottom: docCs.getPropertyValue('--actionbar-bottom').trim(),
    },
    root: {
      paddingBottom: rootCs.paddingBottom,
      paddingTop: rootCs.paddingTop,
      pbPx: rootPb,
    },
    wrap: {
      paddingBottom: wrapCs.paddingBottom,
      pbPx: wrapPb,
      bottom: Math.round(wr.bottom),
    },
    nav: {
      top: Math.round(nr.top),
      contentTop: Math.round(navContentTop),
      bottom: Math.round(nr.bottom),
      height: Math.round(nr.height),
      borderTop: navBorderTop,
      borderBottom: navBottomBorder,
      paddingBottom: navCs.paddingBottom,
      position: navCs.position,
    },
    btnHeight: br ? Math.round(br.height) : null,
    contentBottom: Math.round(contentBottom),
    // ★ 判据：内容铺到的下边界 与 TabBar「按钮区顶边」之间差多少
    gap: Math.round(navContentTop - contentBottom),
    // TabBar 自己有没有贴住视口真正的底边
    navBottomFlush: Math.abs(nr.bottom - innerHeight) < 1.5,
  };
})()"""


async def login(b: Browser, base: str) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == USER)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


def show(arm: str, m: dict) -> None:
    t = m["token"]
    n = m["nav"]
    print(f"  [{arm}] 视口 {m['innerWidth']}x{m['innerHeight']}")
    print(
        f"        令牌  --safe-bottom={t['safeBottom']!r}  --safe-top={t['safeTop']!r}  "
        f"--tabbar-h={t['tabbarH']!r}  --actionbar-bottom={t['actionbarBottom']!r}"
    )
    print(f"        根容器 padding-bottom = {m['root']['paddingBottom']!r}   (期望 0px)")
    print(f"        内容区 padding-bottom = {m['wrap']['paddingBottom']!r}   bottom={m['wrap']['bottom']}")
    print(
        f"        TabBar 边框顶={n['top']}  内容顶={n['contentTop']}  bottom={n['bottom']}  "
        f"高={n['height']}  border-top={n['borderTop']}px  pb={n['paddingBottom']!r}  {n['position']}"
    )
    print(f"        内容铺到下边界 = {m['contentBottom']}   TabBar 按钮区顶 = {n['contentTop']}")
    print(f"        ★ 缝隙 gap = {m['gap']}px   (期望 0；双算时为 safe-bottom)")
    print(f"        TabBar 贴住视口底边: {m['navBottomFlush']}   触控目标高: {m['btnHeight']}px")
    print()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="截图文件名加 -keep 后缀")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="im 实例端口（默认 3003）")
    args = ap.parse_args()

    # 截图名带上端口后缀：跑生产实例时不覆盖 dev 那两张取证图
    suffix = ("-keep" if args.keep else "") + (
        "" if args.port == DEFAULT_PORT else f"-p{args.port}"
    )
    base = f"http://localhost:{args.port}"
    print("── im 底部安全区「是否双算」渲染级实测 ──")
    print(f"   目标 {base}   用户 {USER}   模拟安全区 {SAFE_ON}\n")

    results: dict[str, dict] = {}

    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        # ---------- 臂 1：不设安全区（基线） ----------
        if not await login(b, base):
            print("  ✗ 登录失败 —— 环境问题，不是产品缺陷")
            return 2
        await b.goto(f"{base}/", wait=1.5)
        await b.settle(extra=2.5)
        m_off = await b.cdp.evaluate(MEASURE_JS)
        if not m_off or not m_off.get("ok"):
            print(f"  ✗ 测量失败：{(m_off or {}).get('why')} —— 环境问题")
            return 2
        results["off"] = m_off
        show("off", m_off)
        await b.screenshot(HERE / f"shot_im_safe_area_off{suffix}.png")

        # ---------- 臂 2：设安全区（处理组） ----------
        # 先设 override 再重新导航：保证文档是带着新 insets **新建**的，
        # 不依赖「运行中改 emulation 会不会触发 env() 重算」这种不确定行为。
        dev = await b.apply_device(safe_area=SAFE_ON, mobile=True)
        if not dev.get("supported"):
            print(f"  ✗ 本机 Chrome 不支持安全区模拟：{dev.get('note')} —— 环境问题")
            return 2
        await b.goto(f"{base}/", wait=1.5)
        await b.settle(extra=2.5)
        m_on = await b.cdp.evaluate(MEASURE_JS)
        if not m_on or not m_on.get("ok"):
            print(f"  ✗ 测量失败：{(m_on or {}).get('why')} —— 环境问题")
            return 2
        results["on"] = m_on
        show("on", m_on)
        await b.screenshot(HERE / f"shot_im_safe_area_on{suffix}.png")

    # ================= 前提锁 =================
    print("── 前提锁（没有它，下面所有断言都是空的）──")
    ok_premise = True
    for arm, want in (("off", 0.0), ("on", float(SAFE_ON["bottom"]))):
        got_raw = results[arm]["token"]["safeBottom"]
        try:
            got = float(got_raw.replace("px", "").strip())
        except ValueError:
            got = -1.0
        good = abs(got - want) < 0.5
        ok_premise &= good
        print(f"  {arm:<4} --safe-bottom 实际 = {got_raw!r}   期望 {want}px   {'✓' if good else '✗'}")
    if not ok_premise:
        print("\n✗ 前提不成立：安全区没有真的生效 ⇒ 这次测量证明不了任何事（退出 2）")
        return 2
    print("  ⇒ 前提成立：off 臂真的没有安全区、on 臂真的有 34px ⇒ 断言有意义\n")

    # ================= 判据 =================
    print("── 判据 ──")
    defects: list[str] = []
    for arm in ("off", "on"):
        m = results[arm]
        safe_px = float(SAFE_ON["bottom"]) if arm == "on" else 0.0
        n = m["nav"]

        # ① 成因：底部安全区绝不能被加在根容器上
        if m["root"]["pbPx"] != 0:
            defects.append(
                f"[{arm}] 根容器 padding-bottom = {m['root']['paddingBottom']}（期望 0px）"
                f" —— 底部安全区被加在了根容器上，与 TabBar 内部那次叠加 ⇒ **双算的成因**"
            )

        # ② 后果：内容必须正好铺到 TabBar 按钮区顶边
        gap = m["gap"]
        if gap > 0:
            hint = ""
            if safe_px > 0 and abs(gap - safe_px) < 1:
                hint = f"；gap 恰好等于 --safe-bottom({safe_px}px) ⇒ **这就是双算的特征**"
            defects.append(
                f"[{arm}] 内容与按钮区之间有 {gap}px 空白带（期望 0）"
                f" —— 内容被顶高了 {gap}px{hint}"
            )
        elif gap < 0:
            defects.append(
                f"[{arm}] 内容越过按钮区顶边 {-gap}px（期望 0）"
                f" —— 内容被 TabBar 盖住 {-gap}px，列表最后一行会被裁"
            )

        # ③ TabBar 自身的高度构成必须自洽：tabbar-h + safe-bottom + border-top
        want_h = 56.0 + safe_px + n["borderTop"]
        if abs(n["height"] - want_h) > 0.5:
            defects.append(
                f"[{arm}] TabBar 高 {n['height']}px ≠ tabbar-h({56})+safe-bottom({safe_px})"
                f"+border-top({n['borderTop']}) = {want_h}px"
            )

        # ④ 内容区的让位量必须等于 --actionbar-bottom
        want_wrap_pb = 56.0 + safe_px
        if abs(m["wrap"]["pbPx"] - want_wrap_pb) > 0.5:
            defects.append(
                f"[{arm}] 内容区 padding-bottom = {m['wrap']['paddingBottom']}"
                f"（期望 --actionbar-bottom = {want_wrap_pb}px）"
            )

        # ⑤ 固定条必须贴住视口底边，否则下面会露底
        if not m["navBottomFlush"]:
            defects.append(
                f"[{arm}] TabBar 没贴住视口底边（bottom={n['bottom']} vs innerHeight={m['innerHeight']}）"
            )

        # ⑥ 触控目标 ≥ 48（规范 §8.3）
        if m["btnHeight"] is not None and m["btnHeight"] < 48:
            defects.append(f"[{arm}] Tab 触控目标高 {m['btnHeight']}px < 48px（规范 §8.3）")

    if defects:
        print("  ✗ 发现缺陷：")
        for d in defects:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1

    off, on = results["off"], results["on"]
    print("  ✓ ① 成因：根容器 padding-bottom 两臂均为 0px —— 底部安全区只由 TabBar 自己加一次")
    print(f"  ✓ ② 后果：缝隙 gap  off={off['gap']}px  on={on['gap']}px —— 内容正好铺到按钮区顶边")
    print(
        f"  ✓ ③ TabBar 高  off={off['nav']['height']}px  on={on['nav']['height']}px"
        f"  （= tabbar-h 56 + safe-bottom + border-top {on['nav']['borderTop']}）"
    )
    print(f"  ✓ ④ 内容区让位随安全区增长：{off['wrap']['paddingBottom']} → {on['wrap']['paddingBottom']}")
    print(f"  ✓ ⑤ TabBar 两臂都贴住视口底边；⑥ 触控目标 {on['btnHeight']}px ≥ 48px")
    print("\nEXIT=0  通过：底部安全区未被双算，且断言在安全区真正生效时依然成立")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

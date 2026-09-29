"""渲染级 + 浏览器端到端验证（真实 Chromium，CDP 驱动）。

补齐文档里长期挂着的两条「仍未验证」：

  ① **渲染级安全区**：`viewport-fit=cover` 过去只验到「进了产物」，
     没验到「iOS 上 `env(safe-area-inset-*)` 真的生效」。
     本脚本用 `Emulation.setSafeAreaInsetsOverride` 模拟刘海屏，
     直接读 **computed style**，并配对照组（insets 归零）排除巧合。

  ② **浏览器端到端**：过去只走 `TestClient`（Python 进程内 HTTP），
     证明了后端契约，但没在真实浏览器里点过「租户视角切换」，
     也没验证「切换 → 整页重载 → 数据变化」这条前端链路。

跑法（backend/ 与 admin dev server 需已启动）：
    python evidence/verify_render_e2e.py

退出码（**三种语义不同，别混**）：
    0  全部通过
    1  产品缺陷（断言失败，且环境已确认可用）
    2  环境问题：服务没起 / 中途挂掉 / 种子数据不足以支撑断言

    ⚠️ 2026-09-18 实测教训：admin dev server 跑到一半退出，本脚本把 12 条断言
       报成「产品缺陷」（如 `I.4 未匹配到《…》`），读者会误判成渲染回归。
       **基础设施故障必须与产品缺陷分开报**，所以本轮加了前置检查 + 掉线守卫。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402
from envprobe import ADMIN, check_services, fingerprint  # noqa: E402

OUT = HERE

PASS = 0
FAIL = 0
FAILED: list[str] = []
ENV_ISSUES: list[str] = []

# 页面出现这些字样 ⇒ 服务已停，**不是页面渲染错了**
OUTAGE_MARKERS = (
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_EMPTY_RESPONSE",
    "无法访问此网站",
    "拒绝了我们的连接请求",
    "This site can’t be reached",
    "This site can't be reached",
)


class EnvDown(RuntimeError):
    """环境不可用（服务未启动 / 中途退出）——**不是产品缺陷**。"""


def env_note(msg: str) -> None:
    ENV_ISSUES.append(msg)
    print(f"  [ENV ] {msg}")


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  -> {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(f"{name}  -> {detail}")
        print(f"  [FAIL] {name}  -> {detail}")


def group(title: str) -> None:
    print(f"\n=== {title} ===")


def env_check(name: str, cond: bool, detail: str = "") -> None:
    """**环境前提**：不满足时记为环境问题（退出码 2），**不计入产品缺陷**。

    与 `check` 的区别就是「这条到底在断言产品，还是在断言环境」。
    """
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  -> {detail}" if detail else ""))
    else:
        env_note(f"{name}  -> {detail}")


# ────────────────────────── 浏览器交互小工具 ──────────────────────────

JS_VARS = """(() => {
  const r = getComputedStyle(document.documentElement);
  return {
    safeTop: r.getPropertyValue('--safe-top').trim(),
    safeBottom: r.getPropertyValue('--safe-bottom').trim(),
    topbarTotal: r.getPropertyValue('--topbar-total').trim(),
    viewport: (document.querySelector('meta[name=viewport]') || {}).content || ''
  };
})()"""

# 顶栏：取 computed padding-top 等于安全区值的元素（AppShell 的 sticky header）
JS_TOPBAR_PAD = """(() => {
  const els = [...document.querySelectorAll('header,div')];
  const hit = els.find(el => {
    const cs = getComputedStyle(el);
    return cs.position === 'sticky' && cs.paddingTop !== '0px' && el.className.includes('topbar');
  }) || els.find(el => getComputedStyle(el).paddingTop === %s);
  return hit ? { pt: getComputedStyle(hit).paddingTop, cls: (hit.className||'').slice(0,80) } : null;
})()"""

JS_MARKER_SET = "window.__nlaw_reload_marker = 'before-switch'; true"
JS_MARKER_GET = "typeof window.__nlaw_reload_marker === 'string' ? window.__nlaw_reload_marker : null"

JS_DASH_NUMS = """(() => {
  const t = (document.body.innerText || '').replace(/\\s+/g, ' ');
  const grab = re => { const m = t.match(re); return m ? m[1] : null; };
  return {
    scope: t.includes('平台本级') ? 'platform' : (t.includes('租户视角') ? '?' : '?'),
    scopeTag: (t.match(/租户视角\\s*(\\S{0,12})/) || [])[1] || null,
    inProgress: grab(/在办案件\\s*([0-9,]+)\\s*件/),
    reviewPending: grab(/复核待确认\\s*([0-9,]+)\\s*项/),
    workOrders: grab(/工单待处理\\s*([0-9,]+)\\s*笔/),
    text: t.slice(0, 220)
  };
})()"""


# ────────────────────────── 浏览器交互小工具 ──────────────────────────
# `click_text` / `type_into` 已收进 `cdp.Browser`（`visual_baseline.py` 也用），
# 这里不再保留第二份拷贝——两份迟早会漂移。


async def login(b: Browser) -> None:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    await b.goto(f"{ADMIN}/login", wait=3)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(5)


# ────────────────────────── 环境前置检查 ──────────────────────────
# 具体实现见 `evidence/envprobe.py`——与 `visual_baseline.py` 共用，
# 免得「绕开沙箱代理」那段逻辑在两份拷贝之间漂移。


def preflight_services() -> None:
    """服务没起就别跑——否则产出的失败**全是假的**，还会被读成产品缺陷。"""
    for p in check_services():
        env_note(p)


def preflight_seed() -> None:
    """**取证**：把后端此刻用的库规模打出来。

    环境漂移真实发生过（2026-09-18：8001 由 demo 种子库被换回默认开发库，
    `firm_hlw` 由 10 例降到 6 例、`platform` 由 4 例降到 0 例），而 **H.6 照样通过**
    ——因为 0→6 也算「变化」。所以库的规模必须可见，不能只信「跑绿了」。
    """
    try:
        fp = fingerprint()
    except Exception as e:  # noqa: BLE001
        env_note(f"后端登录失败，无法核对种子规模：{type(e).__name__}: {e}")
        return
    for tenant, v in sorted(fp.items()):
        print(
            f"  [data] {tenant:9s} 在办={v['in_progress']:3d}  "
            f"工单待处理={v['work_orders_pending']}  工单总额={v['work_orders_total']}"
        )


async def guard(b: Browser, where: str) -> None:
    """页面若显示「连接被拒」⇒ 服务中途退出，抛 `EnvDown`（**不记为产品缺陷**）。"""
    txt = await b.cdp.evaluate("(document.body.innerText||'').slice(0,500)")
    hit = next((m for m in OUTAGE_MARKERS if m in (txt or "")), None)
    if hit:
        raise EnvDown(f"{where}：页面显示「{hit}」⇒ 服务已停止，后续断言无法成立（非产品缺陷）")


# ────────────────────────── 主流程 ──────────────────────────


async def main() -> int:
    # ── 前置检查：服务 / 种子规模 ──────────────────────
    group("前置检查：服务可达性与种子规模")
    preflight_services()
    if ENV_ISSUES:
        print("\n服务未就绪 ⇒ **不启动浏览器**（跑下去产出的失败全是假的）。")
        return 1
    preflight_seed()

    async with Browser(headless=True, width=390, height=844) as b:
        # ══════════ G. 渲染级安全区 ══════════
        group("G. 渲染级安全区（真实浏览器 + 刘海屏模拟）")

        r = await b.apply_device(safe_area={"top": 47, "bottom": 34})
        check("G.0 本机 Chromium 支持安全区模拟", bool(r.get("supported")), str(r))

        await login(b)
        await guard(b, "G.1 登录后")
        check("G.1 登录成功（进入应用）", "/login" not in (await b.cdp.evaluate("location.href")))

        v = await b.cdp.evaluate(JS_VARS)
        check(
            "G.2 🚨 真实 DOM 的 viewport meta 含 `viewport-fit=cover`（过去是死代码的根因）",
            "viewport-fit=cover" in (v.get("viewport") or ""),
            f"content={v.get('viewport')!r}",
        )
        check(
            "G.3 🚨 模拟刘海屏下 `--safe-top` / `--safe-bottom` 解析为真实像素",
            v.get("safeTop") == "47px" and v.get("safeBottom") == "34px",
            f"--safe-top={v.get('safeTop')}  --safe-bottom={v.get('safeBottom')}",
        )
        check(
            "G.4 位置令牌 `--topbar-total` 已含安全区",
            "47px" in (v.get("topbarTotal") or ""),
            f"--topbar-total={v.get('topbarTotal')}",
        )

        tb = await b.cdp.evaluate(JS_TOPBAR_PAD % json.dumps("47px"))
        check(
            "G.5 🚨 顶栏元素 computed padding-top = 47px（安全区真的作用到元素，不只是变量有值）",
            bool(tb) and tb.get("pt") == "47px",
            f"{tb}",
        )
        await b.screenshot(OUT / "shot_safe_area_on.png")

        # 对照组：把安全区归零
        await b.apply_device(safe_area={"top": 0, "bottom": 0})
        await asyncio.sleep(0.6)
        v0 = await b.cdp.evaluate(JS_VARS)
        tb0 = await b.cdp.evaluate(JS_TOPBAR_PAD % json.dumps("0px"))
        check(
            "G.6 对照组：insets 归零 ⇒ `--safe-top`=0px 且顶栏 padding-top=0px（排除「写死 47px」）",
            v0.get("safeTop") == "0px" and bool(tb0) and tb0.get("pt") == "0px",
            f"--safe-top={v0.get('safeTop')}  顶栏 pt={tb0 and tb0.get('pt')}",
        )
        await b.screenshot(OUT / "shot_safe_area_off.png")

        # 恢复刘海屏，后续用例在真实移动端条件下跑
        await b.apply_device(safe_area={"top": 47, "bottom": 34})
        await asyncio.sleep(0.5)

        # ══════════ H. 租户视角切换（整页重载） ══════════
        group("H. 浏览器端到端：租户视角切换")

        await b.goto(f"{ADMIN}/", wait=4)
        await guard(b, "H.1 打开驾驶舱后")
        d0 = await b.cdp.evaluate(JS_DASH_NUMS)
        check("H.1 初始视角为「平台本级」", d0.get("scope") == "platform", f"scopeTag={d0.get('scopeTag')}")

        opened = await b.click_text("切换视角")
        await asyncio.sleep(1.2)
        check("H.2 点「切换视角」弹出浮层", opened)

        # 浮层里找租户 ID 输入框
        typed = await b.type_into("div[role=dialog] input, form input", "firm_hlw")
        if not typed:
            # 退路：浮层内第一个 input
            typed = await b.cdp.evaluate(
                """(() => {
                  const ins = [...document.querySelectorAll('input')];
                  const el = ins[ins.length - 1];
                  if (!el) return false;
                  const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value').set;
                  d.call(el, 'firm_hlw'); el.dispatchEvent(new Event('input', {bubbles: true}));
                  return true;
                })()"""
            )
        check("H.3 能输入目标租户 ID（`firm_hlw`）", bool(typed))

        await b.cdp.evaluate(JS_MARKER_SET)
        await b.click_text("切换")
        await asyncio.sleep(6)

        marker = await b.cdp.evaluate(JS_MARKER_GET)
        check(
            "H.4 🚨 切换触发**整页重载**（JS 上下文被销毁 ⇒ 标记变量消失）",
            marker is None,
            f"重载前标记={marker!r}（None = 已重载）",
        )

        d1 = await b.cdp.evaluate(JS_DASH_NUMS)
        check(
            "H.5 切换后视角标记变为 `firm_hlw`",
            "firm_hlw" in (d1.get("text") or "") or d1.get("scope") != "platform",
            f"scopeTag={d1.get('scopeTag')}  text={ (d1.get('text') or '')[:120] }",
        )
        check(
            "H.6 切换后数据确实变化（不是只改了标签）",
            (d0.get("inProgress"), d0.get("workOrders")) != (d1.get("inProgress"), d1.get("workOrders")),
            f"platform: 在办={d0.get('inProgress')} 工单={d0.get('workOrders')}  ⇒  "
            f"firm_hlw: 在办={d1.get('inProgress')} 工单={d1.get('workOrders')}",
        )

        def _n(x) -> int:
            """`'1,024'` / `None` → int。"""
            return int(str(x).replace(",", "")) if x not in (None, "") else 0

        # ⚠️ H.6 只断言「变了」。若 platform 视角**本来就是空的**，空视图同样能让
        #    `0 ≠ 6` 成立 ⇒ H.6 通过，却证明不了「切换真的换了数据源」。
        #    所以必须锁住「两侧都非空」这个前提（同 F.7 的反向锁思路）。
        env_check(
            "H.7 两个视角都非空（否则 H.6 退化为弱断言：空视图也能让 0≠6 成立）",
            _n(d0.get("inProgress")) > 0 and _n(d1.get("inProgress")) > 0,
            f"platform 在办={d0.get('inProgress')}  firm_hlw 在办={d1.get('inProgress')}",
        )
        await b.screenshot(OUT / "shot_tenant_switched.png")

        # ══════════ I. 复核详情「被复核对象」区块 ══════════
        group("I. 浏览器端到端：复核详情「被复核对象」区块")

        await b.goto(f"{ADMIN}/reviews", wait=5)
        await guard(b, "I.1 打开复核列表后")
        rows = await b.cdp.evaluate("document.querySelectorAll('tbody tr').length")
        check("I.1 复核队列有数据行（firm_hlw 视角）", rows > 0, f"行数={rows}")

        opened_detail = await b.cdp.evaluate(
            """(() => {
              const tr = document.querySelector('tbody tr');
              if (!tr) return false;
              const btn = tr.querySelector('button, a');
              if (btn) { btn.click(); return true; }
              tr.click(); return true;
            })()"""
        )
        await asyncio.sleep(2.5)
        detail = await b.cdp.evaluate("(document.body.innerText||'').replace(/\\s+/g,' ')")
        check("I.2 能打开复核详情", bool(opened_detail) and "被复核对象" in detail)

        check("I.3 详情含「被复核对象」区块", "被复核对象" in detail)
        check(
            "I.4 🚨 「关键法条」渲染出**真实法条**（`law_name`/`article_no` 键名正确的渲染级证明）",
            "关键法条" in detail and bool(re.search(r"《[^》]{4,}》\s*第\d+条", detail)),
            f"匹配={re.search(r'《[^》]{2,40}》[^；]{0,20}', detail).group(0) if re.search(r'《[^》]{2,40}》', detail) else '未匹配到《…》'}",
        )
        check(
            "I.5 详情含「类案」列表",
            "类案" in detail,
            (re.search(r"类案[^；]{0,60}", detail).group(0) if "类案" in detail else "未出现"),
        )
        check(
            "I.6 详情含「n/m 可溯源」计数（本轮新增的监督信号）",
            "可溯源" in detail,
            (re.search(r"（\d+/\d+ 可溯源）", detail).group(0) if "可溯源" in detail else "未出现"),
        )
        check(
            "I.7 详情含「复核留痕」区块",
            "复核留痕" in detail,
            (re.search(r"复核留痕[^；]{0,50}", detail).group(0) if "复核留痕" in detail else "未出现"),
        )

        # 弹窗内部滚动到底，让「类案」进入可视区（截图取证用；不影响上面的断言）
        await b.cdp.evaluate(
            """(() => {
              const dlg = document.querySelector('[role=dialog]');
              if (!dlg) return 0;
              const sc = [...dlg.querySelectorAll('*')].filter(el => {
                const cs = getComputedStyle(el);
                return /auto|scroll/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 10;
              });
              sc.forEach(el => { el.scrollTop = el.scrollHeight; });
              return sc.length;
            })()"""
        )
        await asyncio.sleep(0.8)
        await b.screenshot(OUT / "shot_review_detail.png")

    # 生成「安全区开 / 关」并排对比图（这是最直观的取证）
    try:
        from PIL import Image, ImageDraw

        on = Image.open(OUT / "shot_safe_area_on.png")
        off = Image.open(OUT / "shot_safe_area_off.png")
        h = 700
        on = on.crop((0, 0, on.width, min(h, on.height)))
        off = off.crop((0, 0, off.width, min(h, off.height)))
        gap, top = 24, 34
        canvas = Image.new("RGB", (on.width + off.width + gap * 3, h + top), (255, 255, 255))
        canvas.paste(off, (gap, top))
        canvas.paste(on, (gap * 2 + off.width, top))
        d = ImageDraw.Draw(canvas)
        d.text((gap + 8, 10), "insets = 0   (no notch)", fill=(180, 30, 30))
        d.text((gap * 2 + off.width + 8, 10), "insets top=47 bottom=34  (notch)", fill=(20, 120, 70))
        canvas.save(OUT / "shot_safe_area_compare.png")
        print("\n并排对比图：evidence/shot_safe_area_compare.png（左=无刘海 / 右=有刘海）")
    except Exception as e:  # noqa: BLE001
        print(f"\n（并排对比图生成失败，不影响断言：{e}）")

def report() -> int:
    """汇总并给出**语义明确**的退出码。"""
    print("\n" + "=" * 68)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全部通过" if not FAILED else ""))
    if FAILED:
        print("\n产品缺陷（断言失败）：")
        for f in FAILED:
            print("  - " + f)
    if ENV_ISSUES:
        print("\n环境问题（**不是产品缺陷**，本轮结论不成立）：")
        for e in ENV_ISSUES:
            print("  ! " + e)
    print("=" * 68)

    if ENV_ISSUES:
        print("=> 退出码 2：环境未就绪 / 数据不足，先按 evidence/README.md 备好环境再跑")
        return 2
    if FAILED:
        print("=> 退出码 1：产品缺陷")
        return 1
    print("=> 退出码 0：全部通过")
    return 0


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except EnvDown as e:
        # 服务中途掉线：记为环境问题，**不记为产品缺陷**
        env_note(str(e))
    sys.exit(report())

"""决策探针：`AppLayout.tsx:255` 深色模式按钮补 `.tap-ghost`，**桌面端会不会出事**？

## 要回答的问题（这不是缺陷审计，是「拍板前的取证」）

`admin-gap-analysis.md` #18 留了一个待拍板项：

- **方案 ①**：补 `tap-ghost` ⇒ 移动端 + 桌面都 48px 不可见热区；
- **方案 ②**：只在移动端补，桌面关掉。

登记时给的「支持 ②」的理由是团队在同一个顶栏里对 `SyncQueueBadge` 写过
「桌面顶栏只有 56px，48px 的触控热区在这里显得笨重」并用 `sm:min-h-0` 关掉了。

> ⚠️ **但这条类比可能不成立**：`SyncQueueBadge` 用的是 `min-h-0`——那改的是
> **可见盒子的最小高度**，48px 的可见块在 56px 顶栏里当然显得笨重。
> 而 `.tap-ghost` 是 `::after` 伪元素，**肉眼完全看不见**，不改变任何视觉密度。
> ⇒ 「显得笨重」这个理由**不能从 `SyncQueueBadge` 迁移到 `tap-ghost` 上**。

于是 ② 剩下的唯一硬理由就只有：**桌面顶栏元素密集，多个 48px 热区会互相重叠、
彼此抢点击**——这正是 im `/chat` 那处违规的成因（实测「邻居抢走重叠区」）。

**本探针就是去测这件事**：在真实浏览器里把 `tap-ghost` 加到这个按钮上，
量它自己够不够 48px，并量**邻居的热区有没有因此缩水**（缩水 = 抢了别人的点击）。

## 判据

| 编号 | 判据 | 出处 |
|---|---|---|
| **A** | 补 `tap-ghost` 后，按钮热区在**每个宽度**都达到 48×48 | §8.3「图标按钮必须有 48px 的不可见热区」 |
| **B** | 补了之后，**没有任何邻居的热区因此变小** | 成因 B（邻居抢重叠区）的反面 |

## 为什么必须实测、不能靠推理

`elementFromPoint` 的命中结果取决于**绘制顺序**与**伪元素盒子的实际位置**，
两者都不是读源码能算准的（本项目已经栽过一次：im「新建咨询」盒子 36×36、热区 48×49）。

## 退出码

`0` 判定完成 · `2` 环境问题（登录失败 / 找不到按钮 / 量不到）

> 注意：本探针**不判产品缺陷**，所以**没有退出码 1**。
> 它是一次**对照实验**，输出的是「① 可行 / ① 有代价」的证据。
用法：
    python evidence/probe_tapghost_desktop.py
    python evidence/probe_tapghost_desktop.py --port 3001 --widths 390,1024,1280,1440
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

TAP = 48  # 规范 §8.3

# 顶栏里所有的图标按钮：主题按钮 + 它的左右邻居。
# 用 `aria-label` 认主题按钮（源码 l.253 就是这么写的），不认类名——
# 类名会被 Tailwind 重排，而 `aria-label` 是**语义**，改了就是改了无障碍属性。
SCAN_JS = """(async () => {
  const TAP = 48;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const header = document.querySelector('header');
  if (!header) return { error: 'no-header' };

  const themeBtn = [...header.querySelectorAll('button')].find(b => {
    const lb = b.getAttribute('aria-label') || '';
    return lb === '切换到深色模式' || lb === '切换到浅色模式';
  });
  if (!themeBtn) return { error: 'no-theme-button',
                          labels: [...header.querySelectorAll('button')]
                            .map(b => b.getAttribute('aria-label') || (b.innerText||'').trim().slice(0,10)) };

  // 顶栏里所有「图标按钮」（有 svg、无可见文字、有尺寸）——含主题按钮自己
  const all = [...header.querySelectorAll('button,a[role=button],[role=button]')].filter(el => {
    if (!el.querySelector('svg')) return false;
    if ((el.innerText || '').trim().length > 0) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  });

  const label = (el) => {
    const lb = el.getAttribute('aria-label');
    if (lb) return lb.slice(0, 12);
    return el.tagName.toLowerCase() + '.' + (el.className||'').toString().split(' ')[0];
  };

  // 热区 = 过中心的**连续命中段**长度（与 verify_tap_targets.py 同一套量法）
  const measure = (el) => {
    const r = el.getBoundingClientRect();
    const cx = Math.round(r.left + r.width / 2);
    const cy = Math.round(r.top + r.height / 2);
    const hits = (x, y) => {
      const t = document.elementFromPoint(x, y);
      return !!(t && (t === el || el.contains(t)));
    };
    const extent = (horiz) => {
      const rr = el.getBoundingClientRect();
      const lo = (horiz ? Math.floor(rr.left) : Math.floor(rr.top)) - 60;
      const hi = (horiz ? Math.ceil(rr.right) : Math.ceil(rr.bottom)) + 60;
      const at = (v) => (horiz ? hits(v, cy) : hits(cx, v));
      const who = (v) => {
        const t = document.elementFromPoint(horiz ? v : cx, horiz ? cy : v);
        if (!t) return 'null';
        return t.tagName.toLowerCase() + '.' +
          (t.className || '').toString().split(' ').slice(0, 2).join('.');
      };
      const a = horiz ? cx : cy;
      if (!at(a)) return { size: 0, blockStart: null, blockEnd: null };
      let s = a, e = a;
      while (s - 1 >= lo && at(s - 1)) s--;
      while (e + 1 <= hi && at(e + 1)) e++;
      return { size: e - s + 1,
               blockStart: s - 1 >= lo ? who(s - 1) : null,
               blockEnd: e + 1 <= hi ? who(e + 1) : null };
    };
    const hx = extent(true), vy = extent(false);
    return { w: hx.size, h: vy.size, whoW: [hx.blockStart, hx.blockEnd],
             whoH: [vy.blockStart, vy.blockEnd] };
  };

  const snapshot = () => {
    const map = new Map();
    for (const el of all) map.set(el, measure(el));
    return [...map.entries()].map(([el, m]) => ({
      label: label(el), isTheme: el === themeBtn, ...m,
      cls: (el.className || '').toString().split(' ').slice(0, 3).join(' '),
      box: (() => { const r = el.getBoundingClientRect();
                    return { w: Math.round(r.width), h: Math.round(r.height) }; })(),
    }));
  };

  const before = snapshot();

  // ---- 注入：只加一个类，等价于「方案 ① 的改动」 ----
  themeBtn.classList.add('tap-ghost');
  await sleep(80);
  const after = snapshot();

  // 撤销，别把页面留在改动状态（同一会话后面还要用）
  themeBtn.classList.remove('tap-ghost');
  await sleep(40);

  return {
    vw: innerWidth,
    themeClsBefore: (before.find(x => x.isTheme) || {}).cls,
    before, after,
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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3001, help="律师端端口（默认 3001）")
    ap.add_argument("--user", default="lawyer_wang")
    ap.add_argument("--path", default="/", help="顶栏在哪个页面量（默认首页）")
    ap.add_argument("--widths", default="390,1024,1280,1440")
    args = ap.parse_args()

    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    base = f"http://localhost:{args.port}"

    print("── `.tap-ghost` 补到顶栏主题按钮：桌面端代价取证 ──")
    print(f"   {base}{args.path}   宽度 {widths}\n")

    async with Browser(headless=True, width=widths[0], height=900, device_scale_factor=1) as b:
        if not await login(b, base, args.user):
            print("  ✗ 登录失败（环境问题）")
            return 2

        results = []
        for w in widths:
            await b.cdp.send(
                "Emulation.setDeviceMetricsOverride",
                width=w, height=900, deviceScaleFactor=1, mobile=False,
            )
            await b.goto(f"{base}{args.path}", wait=1.5)
            await b.settle(extra=2.0)
            r = await b.cdp.evaluate(SCAN_JS)
            if not r or r.get("error"):
                print(f"  ✗ [{w}] 扫描失败：{r.get('error') if r else 'null'}"
                      f" {r.get('labels') if r else ''}（环境问题）")
                return 2
            if r["vw"] != w:
                print(f"  ⚠ [{w}] 前提不成立：视口实际 {r['vw']} ⇒ 本档不判定")
                continue
            results.append((w, r))

    if not results:
        print("  ✗ 没有任何一档前提成立（环境问题）")
        return 2

    # ---- 判据 A / B ----
    verdicts: list[str] = []

    for w, r in results:
        before = {x["label"]: x for x in r["before"]}
        after = {x["label"]: x for x in r["after"]}
        theme = next(x for x in r["after"] if x["isTheme"])

        print(f"── 宽度 {w} ──")
        print(f"   主题按钮 盒子 {theme['box']['w']}×{theme['box']['h']}"
              f"   class={theme['cls']!r}")
        b_th = next(x for x in r["before"] if x["isTheme"])
        print(f"   补之前 热区 {b_th['w']}×{b_th['h']}")
        ok = theme["w"] >= TAP and theme["h"] >= TAP
        print(f"   补之后 热区 {theme['w']}×{theme['h']}   "
              f"[A] {'✓ ≥48' if ok else '✗ 未达 48'}")
        if not ok:
            print(f"        横向止于 {theme['whoW']}   纵向止于 {theme['whoH']}")
            verdicts.append(f"[{w}] 判据 A：补了 tap-ghost 仍不足 48px（{theme['w']}×{theme['h']}）")

        # ---- 判据 B：邻居有没有被抢 ----
        stolen = []
        for lb, a in after.items():
            if a["isTheme"]:
                continue
            bf = before.get(lb)
            if not bf:
                continue
            if a["w"] < bf["w"] or a["h"] < bf["h"]:
                stolen.append(f"{lb}({bf['w']}×{bf['h']}→{a['w']}×{a['h']})")
        if stolen:
            print(f"   [B] ✗ 邻居热区被抢：{', '.join(stolen)}")
            verdicts.append(f"[{w}] 判据 B：补热区后邻居被抢 —— {', '.join(stolen)}")
        else:
            n = len([x for x in after.values() if not x["isTheme"]])
            print(f"   [B] ✓ {n} 个邻居热区**均未缩水**（无重叠抢占）")

        # 顺带：每个邻居自己的热区够不够（桌面本来就不要求，仅记录）
        neigh = [f"{x['label']}={x['w']}×{x['h']}" for x in r["after"] if not x["isTheme"]]
        print(f"   邻居热区：{', '.join(neigh) if neigh else '（无）'}")
        print()

    print("── 结论 ──")
    if verdicts:
        print("  ✗ 方案 ①（全宽度补 tap-ghost）**有代价**：")
        for v in verdicts:
            print(f"      · {v}")
        print("\nEXIT=0  取证完成（本探针不判产品缺陷，代价见上）")
    else:
        print("  ✓ 方案 ①（全宽度补 tap-ghost）**可行**：")
        print("      · 每个宽度都达到 48×48；")
        print("      · 没有任何邻居的热区因此缩水 ⇒ 桌面密集顶栏里也没有互相抢占。")
        print("\nEXIT=0  取证完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

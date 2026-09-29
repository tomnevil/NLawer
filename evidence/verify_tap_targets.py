"""规范 §8.3「图标按钮必须有 48px 的不可见热区」审计（渲染级）。

## 判据的出处

`deliverables/ui-design/design-spec.md` §8.3：

> - 本项目标准 **48 × 48px** —— 法律产品用户年龄跨度大，含 45+ 律师与客户
> - 列表行高 ≥ 48px；主按钮高度 48px
> - **图标按钮必须有 48px 的不可见热区，即使图标只有 20px**

## 为什么不能「量按钮的盒子」

「不可见热区」的常见实现有**三种**：

1. **padding / min-h-tap** ⇒ 盒子本身就是 48px ⇒ 量 `getBoundingClientRect()` 能测到；
2. **伪元素扩张**（`::after { position:absolute; inset:-14px; content:'' }`）
   ⇒ **盒子只有 20px，但热区是 48px** ⇒ 量盒子会**误报缺陷**（假红）；
3. **绝对定位的透明覆盖层** ⇒ 同 2。

⇒ 所以本探针**不量盒子**，而是用 `document.elementFromPoint` **真的去戳**：
从元素中心向四个方向逐像素外扩，看**最远能戳中它自己**多少像素。
这直接量的是**「能不能点到」**——需求本身说的是「热区」，不是「盒子尺寸」。
**实现方式无关**（padding / 伪元素 / 覆盖层都能测到）。

> 这与本项目的教训同源：**别拿代理指标（盒子尺寸）去代替真实性质（可点击范围）。**
> 代理指标在一种实现下成立、在另一种下不成立 —— 那正是假红的来源。

## 边界（工具报「干净」时要先看这里）

- 只扫**当前 DOM 里存在**的图标按钮。折叠面板 / 懒加载里没渲染出来的扫不到。
- 只扫**图标按钮**（有 `svg` 且**无可见文字**）。带文字的按钮不在本判据范围
  （§8.3 对它们的约束是「高度 48px」，由 `verify_mobile_safe_area.py` 覆盖底栏那批）。
- 靠近视口边缘时，朝外的方向戳不到 ⇒ 该方向记 0 并在输出里标注，
  **不据此判缺陷**（那是量不到，不是点不到）。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（登录失败 / 页面没渲染出来）
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

# 每端扫哪些页。**故意不止扫首页** —— 「只扫 1 页」是覆盖率不足，
# 不是「这一端很健康」（见 evidence/README.md 关于页表的教训）。
APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin", "pages": ("/qa", "/")},
    "lawyer": {"port": 3001, "user": "lawyer_wang", "pages": ("/cases", "/")},
    "admin": {"port": 3002, "user": "admin", "pages": ("/", "/cases")},
    "im": {"port": 3003, "user": "client", "pages": ("/", "/chat", "/cases")},
}

# 在页面里跑：枚举图标按钮 → 逐个滚动到视口中央 → 用 elementFromPoint 逐像素外扩测热区
SCAN_JS = """(async () => {
  const TAP = 48;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const isIconButton = (el) => {
    if (!el.querySelector('svg')) return false;
    if ((el.innerText || '').trim().length > 0) return false;   // 有可见文字的算普通按钮
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  };

  const cands = [...document.querySelectorAll('button,a[role=button],[role=button],a')]
    .filter(isIconButton);

  const out = [];
  for (let i = 0; i < cands.length; i++) {
    const el = cands[i];
    // 滚到视口中央，保证四个方向都戳得到（否则贴边方向必然戳空）
    el.scrollIntoView({ block: 'center', inline: 'center' });
    await sleep(60);

    const r = el.getBoundingClientRect();
    const cx = Math.round(r.left + r.width / 2);
    const cy = Math.round(r.top + r.height / 2);

    const hits = (x, y) => {
      const t = document.elementFromPoint(x, y);
      // 只认「命中它自己或它的后代」。命中**祖先**不算 ——
      // 那种情况下点击不会落到这个按钮上。
      return !!(t && (t === el || el.contains(t)));
    };

    // ⚠️ **必须量「连续命中段」，不能「从中心向两侧逐格数」。**
    //
    // 第一版是 `reach(-1,0) + reach(1,0)`：从**四舍五入后的整数中心**向两侧数格子。
    // 但 `getBoundingClientRect()` 的边界常带小数（如 `left=10.5`），
    // 中心取整后两侧可数的格数**不对称** ⇒ 一个 **48px 的盒子会稳定量出 47px**。
    // 实测：**17 条「缺陷」里每一条都恰好差 1、且只差在宽**——
    // 这是**测量伪影**，不是 17 个产品缺陷。
    // 与 README 坑 11（参照面取「边框盒」⇒ 正确实现被测成 `-1px`）**同一个签名**。
    //
    // 正确做法：在过中心的整条扫描线上找**包含中心的连续命中段**，取它的长度。
    // 盒子跨 `[10.5, 58.5]` ⇒ 整数点 11..58 全命中 ⇒ 长度 **48**，与盒子一致。
    const extent = (horiz) => {
      const r = el.getBoundingClientRect();
      const lo = (horiz ? Math.floor(r.left) : Math.floor(r.top)) - 40;
      const hi = (horiz ? Math.ceil(r.right) : Math.ceil(r.bottom)) + 40;
      const at = (v) => (horiz ? hits(v, cy) : hits(cx, v));
      // 命中段停在哪，就看看**那里站着谁** —— 把「热区太小」变成「被谁挡住」，才可行动
      const who = (v) => {
        const t = document.elementFromPoint(horiz ? v : cx, horiz ? cy : v);
        if (!t) return "null（视口外）";
        return t.tagName.toLowerCase() + "." +
          (t.className || '').toString().split(' ').slice(0, 2).join('.');
      };
      const a = horiz ? cx : cy;
      if (!at(a)) return { size: 0, blockStart: null, blockEnd: null, truncStart: false, truncEnd: false };
      let s = a, e = a;
      while (s - 1 >= lo && at(s - 1)) s--;
      while (e + 1 <= hi && at(e + 1)) e++;
      return {
        size: e - s + 1,
        blockStart: s - 1 >= lo ? who(s - 1) : null,
        blockEnd: e + 1 <= hi ? who(e + 1) : null,
        truncStart: s <= lo,
        truncEnd: e >= hi,
      };
    };

    const hx = extent(true), vy = extent(false);

    // ---- 归因（只在疑似违规时做）：把兄弟元素全 `visibility:hidden` 再量一次 ----
    //
    // 两种成因的**修法完全不同**：
    //   · `hotWNoSiblings == 48` ⇒ 按钮**自己的热区是够的**，是被**邻居抢走了重叠区**
    //     （相邻 `.tap-ghost` 的 `::after` 会互相重叠，DOM 靠后的绘制在上层 ⇒ 抢走）
    //     ⇒ 修法是间距 / 层叠，不是给按钮加热区；
    //   · `hotWNoSiblings < 48` ⇒ 按钮**自己就没有热区**（多半漏了 `tap-ghost`）
    //     ⇒ 修法是补热区。
    // **不区分就开修，会修错地方。**
    let noSib = null;
    if (hx.size < TAP || vy.size < TAP) {
      const sibs = el.parentElement
        ? [...el.parentElement.children].filter(c => c !== el)
        : [];
      const saved = sibs.map(s => s.style.visibility);
      sibs.forEach(s => { s.style.visibility = 'hidden'; });
      await sleep(40);
      const hx2 = extent(true), vy2 = extent(false);
      sibs.forEach((s, i) => { s.style.visibility = saved[i]; });
      await sleep(20);
      noSib = { w: hx2.size, h: vy2.size, n: sibs.length };
    }

    // 扫描被视口截断 ⇒ 这个方向**量不到**（不是点不到），单独标注
    const truncated = hx.truncStart || hx.truncEnd || vy.truncStart || vy.truncEnd;

    out.push({
      tag: el.tagName.toLowerCase(),
      label: (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 20),
      cls: (el.className || '').toString().split(' ').slice(0, 4).join(' '),
      boxW: Math.round(r.width), boxH: Math.round(r.height),
      hotW: hx.size, hotH: vy.size,
      blockX: [hx.blockStart, hx.blockEnd],
      blockY: [vy.blockStart, vy.blockEnd],
      noSib,
      truncated,
    });
  }
  return { total: cands.length, items: out, vw: innerWidth, vh: innerHeight };
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
    ap.add_argument("--app", choices=sorted(APPS), help="只测一个端（默认四端全跑）")
    args = ap.parse_args()

    targets = {args.app: APPS[args.app]} if args.app else APPS
    print("── §8.3 图标按钮 48px 热区审计（390×844，用 elementFromPoint 实测可点范围）──\n")

    bad: list[str] = []
    env_fail: list[str] = []
    scanned = 0

    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        await b.apply_device(mobile=True)
        for app, spec in targets.items():
            base = f"http://localhost:{spec['port']}"
            if not await login(b, base, spec["user"]):
                print(f"[{app}] ✗ 登录失败（环境问题）")
                env_fail.append(app)
                continue

            for path in spec["pages"]:
                await b.goto(f"{base}{path}", wait=1.5)
                await b.settle(extra=2.5)
                res = await b.cdp.evaluate(SCAN_JS)
                if not res:
                    print(f"[{app}] {path}  ✗ 扫描返回空（环境问题）")
                    env_fail.append(f"{app}{path}")
                    continue
                scanned += 1

                items = res["items"]
                # ⚠️ **扫描被视口截断的元素不作判定** —— 那是「量不到」，不是「点不到」。
                #    前提不成立时**既不判绿也不判红**（与 README 坑 14 同一条纪律）。
                measurable = [it for it in items if not it["truncated"]]
                skipped = [it for it in items if it["truncated"]]
                violations = [
                    it for it in measurable
                    if it["hotW"] < TAP or it["hotH"] < TAP
                ]
                print(f"[{app}] {path:<8} 图标按钮 {len(items):>2} 个"
                      + (f"  ⇒ **{len(violations)} 个热区 <48px**" if violations else "  ⇒ 全部 ≥48px ✓")
                      + (f"（{len(skipped)} 个因贴边截断未判定）" if skipped else ""))

                for it in items:
                    mark = ""
                    if it["truncated"]:
                        mark = "  ⚠ 贴边截断，未判定"
                    elif it["hotW"] < TAP or it["hotH"] < TAP:
                        mark = "  ✗"
                    elif it["hotW"] > it["boxW"] + 2 or it["hotH"] > it["boxH"] + 2:
                        mark = "  （热区大于盒子 ⇒ 用了伪元素/覆盖层，量盒子会误报）"
                    blockers = [b for b in (it["blockX"] + it["blockY"]) if b]
                    blk = f"   挡住者：{blockers}" if (blockers and mark.strip() == "✗") else ""
                    ns = it.get("noSib")
                    cause = ""
                    if ns and mark.strip() == "✗":
                        cause = (
                            f"  归因：隐藏 {ns['n']} 个兄弟后热区 {ns['w']}×{ns['h']} ⇒ "
                            + ("**邻居抢走了重叠区**（自己热区够）"
                               if ns["w"] >= TAP and ns["h"] >= TAP
                               else "**按钮自己就没有热区**")
                        )
                    print(f"      · {it['tag']}.{it['cls'][:34]:<34} "
                          f"盒子 {it['boxW']:>2}×{it['boxH']:<2}  热区 {it['hotW']:>2}×{it['hotH']:<2}"
                          f"  {it['label']!r}{mark}{blk}")
                    if cause:
                        print(f"        {cause}")

                for it in violations:
                    why = []
                    if it["hotW"] < TAP:
                        why.append(f"宽 {it['hotW']}px")
                    if it["hotH"] < TAP:
                        why.append(f"高 {it['hotH']}px")
                    # 把「热区太小」翻译成「被谁挡住」——否则拿到报告的人不知道从哪下手
                    blockers = [b for b in (it["blockX"] + it["blockY"]) if b]
                    extra = f"；命中段两端被 {', '.join(blockers)} 挡住" if blockers else ""
                    ns = it.get("noSib")
                    attrib = ""
                    if ns:
                        attrib = (
                            f"；归因：隐藏兄弟后 {ns['w']}×{ns['h']} ⇒ "
                            + ("邻居抢走重叠区（自身热区够）"
                               if ns["w"] >= TAP and ns["h"] >= TAP
                               else "按钮自身缺热区")
                        )
                    bad.append(
                        f"[{app}] {path} 图标按钮 {it['label'] or it['cls']!r} "
                        f"热区 {'/'.join(why)} < 48px（盒子 {it['boxW']}×{it['boxH']}{extra}{attrib}）"
                    )
            print()

    print("── 汇总 ──")
    print(f"  扫描 {scanned} 个页面")
    if env_fail:
        print(f"  ⚠ 未能测量：{', '.join(env_fail)}（环境问题）")
    if bad:
        print("  ✗ 发现缺陷：")
        for d in bad:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(bad)} 条")
        return 1
    if env_fail:
        print("\nEXIT=2  有页面未能测量，不当作通过")
        return 2
    print("  ✓ 所有图标按钮的实际可点范围都 ≥48×48px（规范 §8.3）")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

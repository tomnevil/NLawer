#!/usr/bin/env python3
"""§2.1 通知下拉面板 —— **渲染级**对比度门禁（P1/P2/P3）。

## 为什么要有这一条

`NotificationCenter` 的桌面面板是 `{open && !isMobile && (…)}` ⇒ **默认关闭**。
`verify_contrast.py` 的 T4 是「打开页面 → 量 DOM」，**不点铃铛** ⇒ 面板里的
未读角标 / 「全部已读」/「查看全部通知」三处**结构上量不到**。

2026-10-29 把这三处 `text-brand-600|700` 改成角色令牌 `text-link`（深色档
`--brand-700` 仍是浅色值 `#274C93`，暗底只有 1.98:1），但**没有任何判据能证明它真的变好了**。
⇒ 本判据补这条渲染级地板。

## 🚨 两个「假绿」陷阱（写这条判据时实测踩到的，判据必须自己防住）

1. **空态**：种子库对任何账号都**不产生通知** ⇒ `unread.total=0` 时角标不渲染、
   `unreadItems=0` 时「全部已读」不渲染。**面板挂载了、判出「0 缺陷」，但那两处根本不在 DOM 里**。
   ⇒ 探针**自己往库里造 3 条 `is_read=0`**（P2 就是拦这个的：三处缺任何一个都判红）。
2. **按钮文字是直接文本节点**：`<button><CheckIcon/>全部已读</button>` 里，
   文字**不是**叶子元素（同级有图标子元素）⇒ 只认 `children.length===0` 的选择器会跳过它。
   ⇒ JS 侧按 `button.textContent` 找。

## 判据表

| 编号 | 检查 | 期望 |
|---|---|---|
| **P1** | 点铃铛后 `div[role="dialog"][aria-label="通知"]` 存在 | 必须挂载 |
| **P2** | 三处强调文本**都渲染** | 缺任何一个 ⇒ 红（空态假绿） |
| **P3** | 三处文字色 == 页面现读的角色令牌 `--link`（当档） | 相等 |
| **P4** | 面板打开态的整页 `judge_render`（WCAG 1.4.3，按字号取门槛） | 0 条 |
| **P5** | 深色档文字色 == `--brand-700` 当档值的元素数（漏改指纹） | **0 个** |

⚠️ **P3 与 P4 互补，别合并**：P3 判「**用的是角色令牌**」（语义：链接/强调文本必须走 `--link`），
P4 判「**比值够不够**」。只留 P4 会漏「值碰巧相同但语义令牌用错」；只留 P3 会漏「令牌本身不够亮」。

## 为什么独立成 job（`browser-ncpanel`）

面板要**未读数据**才渲染那两处，而 `run_browser_job` 的种子是**同 job 内所有探针共享**的
⇒ 探针写库会污染后续探针（`NotificationCenter` 的红点逻辑就是按 `unread==0` 判的）。
⇒ 给它一条**独立 job**（自己的隔离库、只跑它一条），写库就没有副作用。

## 边界（只证伪）

- 只测**桌面**面板（`!isMobile`）；移动端走 BottomSheet，是另一套 DOM。
- 只测**三处强调文本**的令牌一致性 + 整页比值；不测「面板里的每条通知」逐个语义。
- 拦的是**这一条渲染路径**，证不了「没有任何别的地方用 brand 当文本色」。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import re
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(HERE))

import cdp  # noqa: E402
import verify_contrast as vc  # noqa: E402
from approutes import app_base  # noqa: E402

#: 探针要造几条未读。≥2 才能同时点亮角标与「全部已读」。
UNREAD_ROWS: tuple[tuple[str, str, str], ...] = (
    ("DISPATCH_CREATED", "新案件派单", "您有一条新的案件派单待接收"),
    ("REVIEW_REQUIRED", "待复核", "案件需要您复核"),
    ("EVIDENCE_MISSING", "证据缺失", "案件缺少关键证据"),
)

#: 三处强调文本的键 → 人读标签。**顺序即报错顺序**。
TARGETS: tuple[tuple[str, str], ...] = (
    ("badge", "未读角标"),
    ("readAll", "全部已读"),
    ("viewAll", "查看全部通知"),
)

#: 注入反证的开关（自检里临时关掉 ⇒ P2 的「必须渲染」臂必不再命中）。
REQUIRE_ALL = True


# ── 纯函数（自检直接拿它们做臂）────────────────────────────────────────────────

def db_file_from_url(url: str | None) -> str | None:
    """从 `DATABASE_URL` 里取出 SQLite 文件路径。取不到 ⇒ `None`（调用方判 exit 2）。

    ⚠️ 三种写法都要认：`sqlite+aiosqlite:///<abs>`、`sqlite:///<abs>`、
    以及 Windows 盘符（`...:///C:/x.db` 拼出来是三斜杠）。
    """
    if not url:
        return None
    m = re.match(r"^sqlite(?:\+aiosqlite)?:///(.+)$", url.strip())
    return m.group(1) if m else None


def judge_panel(p: dict, expect_link: str | None,
                needle: str | None = None) -> tuple[list[str], list[str]]:
    """P1–P3 + P5。**纯函数**（自检直接喂合成 payload）。"""
    problems: list[str] = []
    notes: list[str] = []
    if not p.get("mounted"):
        return ["P1 面板未挂载：点铃铛后 `div[role=dialog][aria-label=通知]` 不存在"], notes
    if not expect_link:
        return ["P3 读不到角色令牌 `--link` ⇒ 没有期望值可判（环境问题）"], notes

    for key, label in TARGETS:
        v = p.get(key)
        if not v:
            if REQUIRE_ALL:
                problems.append(
                    f"P2 {label} 未渲染 ⇒ **空态假绿**（先造未读数据；"
                    f"`unread.total==0` 时角标/按钮按设计不渲染）")
            continue
        if v.get("color") != expect_link:
            problems.append(f"P3 {label} 文字色 {v.get('color')} ≠ 角色令牌 `--link` "
                            f"{expect_link}（{v.get('fs')}/{v.get('fw')}）")
        else:
            notes.append(f"{label} = `--link` ✓")

    if needle:
        leaks = [x for x in (p.get("all") or []) if (x.get("c") or "").strip() == needle]
        if leaks:
            problems.append(f"P5 仍有 {len(leaks)} 处文字色 == `--brand-700` 当档值 "
                            f"{needle}（漏改指纹）：{[x.get('t') for x in leaks][:3]}")
    return problems, notes


# ── 浏览器侧 ──────────────────────────────────────────────────────────────────

#: 点铃铛（幂等：已展开就不重复点）。
#: 🚨 **必须按 `aria-label` 找，不能按 `[aria-haspopup="dialog"]` 取第一个**：
#:    四端头部里可能还有别的 dialog 触发器（用户菜单等）⇒ 取第一个会点到**别的**按钮，
#:    表现为「点了但面板没挂载」（实测踩到：admin 端就是这么红的）。
#: 铃铛的 label 是 `通知` 或 `通知，N 条未读`（见 `NotificationCenter.tsx` 的 `aria-label`）。
OPEN_JS = """(() => {
  const cands = [...document.querySelectorAll('button[aria-haspopup="dialog"]')]
    .map((b) => ({label: b.getAttribute('aria-label'),
                  expanded: b.getAttribute('aria-expanded')}));
  const b = document.querySelector('button[aria-haspopup="dialog"][aria-label^="通知"]')
         || document.querySelector('button[aria-haspopup="dialog"]');
  if (!b) return {clicked: false, why: 'no-bell', cands};
  if (b.getAttribute('aria-expanded') !== 'true') b.click();
  return {clicked: true, label: b.getAttribute('aria-label'),
          expanded: b.getAttribute('aria-expanded'), cands};
})()"""

#: 取三处强调文本 + 页面现读的 `--link` + 全部文字色（供 P5 算漏改指纹）。
PANEL_JS = r"""(() => {
  const dlg = document.querySelector('div[role="dialog"][aria-label="通知"]');
  if (!dlg) return {mounted: false};
  const pick = (pred) => {
    for (const el of dlg.querySelectorAll('*')) {
      if (el.children.length) continue;
      const t = (el.textContent || '').trim();
      if (pred(t)) {
        const st = getComputedStyle(el);
        return {text: t.slice(0, 24), color: st.color, fs: st.fontSize, fw: st.fontWeight};
      }
    }
    return null;
  };
  // 🚨 按钮的文字是**直接文本节点**（同级还有 <CheckIcon>）⇒ 上面的叶子选择器会跳过它
  const btn = (pred) => {
    for (const el of dlg.querySelectorAll('button')) {
      if (pred((el.textContent || '').trim())) {
        const st = getComputedStyle(el);
        return {text: el.textContent.trim().slice(0, 24), color: st.color,
                fs: st.fontSize, fw: st.fontWeight};
      }
    }
    return null;
  };
  const all = [];
  for (const el of dlg.querySelectorAll('*')) {
    if (el.children.length || !(el.textContent || '').trim()) continue;
    const st = getComputedStyle(el);
    all.push({t: el.textContent.trim().slice(0, 18), c: st.color});
  }
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--link').trim();
  const nums = raw.split(/[\s,]+/).map(Number);
  const link = nums.length === 3 && nums.every((n) => !Number.isNaN(n))
    ? `rgb(${nums[0]}, ${nums[1]}, ${nums[2]})` : null;
  return {
    mounted: true,
    nodes: dlg.querySelectorAll('*').length,
    texts: all.length,
    badge: pick((t) => /条未读$/.test(t)),
    readAll: btn((t) => t === '全部已读'),
    viewAll: btn((t) => t.startsWith('查看全部')),
    all: all,
    link: link,
    linkRaw: raw,
  };
})()"""


def seed_unread(db_file: str, username: str) -> tuple[int, str]:
    """给 `username` 造 `UNREAD_ROWS` 条未读通知。返回 `(造了几条, 说明)`。

    🚨 **幂等**：先删掉本探针造的那几条（按 title 前缀 + `is_read=0`），再插。
    这样重复跑不会累加，也不会把用户真实通知删掉。
    """
    con = sqlite3.connect(db_file)
    try:
        row = con.execute("SELECT id, tenant_id FROM users WHERE username=?",
                          (username,)).fetchone()
        if not row:
            return 0, f"库里没有用户 `{username}`"
        uid, tenant = row
        titles = [r[1] for r in UNREAD_ROWS]
        con.executemany(
            "DELETE FROM notifications WHERE user_id=? AND title=? AND is_read=0",
            [(uid, t) for t in titles])
        con.executemany(
            "INSERT INTO notifications (tenant_id, user_id, type, title, content, is_read)"
            " VALUES (?,?,?,?,?,0)",
            [(tenant, uid, t, title, content) for t, title, content in UNREAD_ROWS])
        con.commit()
        n = con.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
            (uid,)).fetchone()[0]
        return len(UNREAD_ROWS), f"user_id={uid} · tenant={tenant} · 未读合计 {n}"
    finally:
        con.close()


async def run_render(app: str, user: str) -> int:
    base = app_base(app)
    # 与后端**同一张库**（`run_browser_job` 保证后端与探针用同一套 env）
    db_file = db_file_from_url(os.environ.get("DATABASE_URL"))
    if not db_file or not pathlib.Path(db_file).exists():
        print("[env] 取不到库文件（DATABASE_URL）⇒ 造不出未读数据 ⇒ 「我没测成」（exit 2）")
        return 2

    made, detail = seed_unread(db_file, user)
    if not made:
        print(f"[env] 造未读失败：{detail} ⇒ exit 2")
        return 2
    print(f"✓ 造了 {made} 条未读通知（{detail}）")

    total = 0
    bad = 0
    async with cdp.Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        if not await vc.login(b, base, user):
            print(f"[env] 登录 {user} 失败 ⇒ exit 2")
            return 2

        for theme, needle in (("light", "rgb(30, 60, 119)"), ("dark", "rgb(39, 76, 147)")):
            # ↑ 浅色 `--brand-700`=#1E3C77 · 深色 `--brand-700`=#274C93（漏改指纹）
            await b.cdp.evaluate(f"localStorage.setItem('nlaw-theme', {theme!r})")
            await b.goto(f"{base}/", wait=2.0)
            await b.settle(extra=1.0)
            opened = await b.cdp.evaluate(OPEN_JS)
            await asyncio.sleep(0.8)
            await b.settle(extra=0.8)
            p = await b.cdp.evaluate(PANEL_JS)

            label = f"{app}/通知面板 @1280/{theme}"
            probs, notes = judge_panel(p, p.get("link"), needle)
            total += 1
            if not opened.get("clicked"):
                probs.insert(0, f"点不到铃铛（{opened.get('why')}）⇒ 页面没有通知入口？"
                                f"；页面里的 dialog 触发器：{opened.get('cands')}")
            elif not p.get("mounted"):
                # ⚠️ **别拿 `aria-expanded` 当判据**：它是 React 重渲染后才更新的，
                #    `b.click()` 之后立刻读**必然还是 false**（实测踩到，白红两轮）。
                #    「面板在不在 DOM 里」（P1）才是可靠信号；`aria-expanded` 只当诊断信息。
                probs.insert(0, f"点了铃铛但面板仍未挂载（label={opened.get('label')}，"
                                f"点后立刻读到 aria-expanded={opened.get('expanded')}）")
            if probs:
                bad += 1
                print(f"  ✗ {label}  面板节点 {p.get('nodes')} · 文本 {p.get('texts')}")
                for x in probs:
                    print("      ", x)
            else:
                print(f"  ✓ {label}  面板节点 {p.get('nodes')} · 文本 {p.get('texts')}")

            # P4：面板**打开态**的整页比值判定
            m = await b.cdp.evaluate(vc.MEASURE_JS)
            pre = vc._premise(m, 1280)
            if pre:
                print(f"  [env] P4 前提不成立：{pre[0]} ⇒ 本档不判（exit 2）")
                return 2
            rprobs, rnotes = vc.judge_render(m)
            if rprobs:
                bad += 1
                print(f"  ✗ P4 面板打开态：文本 {len(m['texts'])} · 缺陷 {len(rprobs)}")
                for x in rprobs:
                    print("      ", x)
            else:
                print(f"  ✓ P4 面板打开态：文本 {len(m['texts'])} · 缺陷 0 · 区间内 {len(rnotes)}")

    print(f"\n── 通知面板：{total} 组 · 不合格 {bad} 组 ──")
    return 0 if bad == 0 else 1


# ── 自检（纯函数，不启浏览器、不读真实仓库）────────────────────────────────────

_SYN_OK = {
    "mounted": True, "nodes": 47, "texts": 12,
    "badge": {"text": "3 条未读", "color": "rgb(138, 168, 224)", "fs": "11px", "fw": "500"},
    "readAll": {"text": "全部已读", "color": "rgb(138, 168, 224)", "fs": "13px", "fw": "400"},
    "viewAll": {"text": "查看全部通知", "color": "rgb(138, 168, 224)", "fs": "13px", "fw": "400"},
    "all": [{"t": "3 条未读", "c": "rgb(138, 168, 224)"}],
    "link": "rgb(138, 168, 224)",
}
_LINK = "rgb(138, 168, 224)"


def _without(key: str) -> dict:
    d = dict(_SYN_OK)
    d[key] = None
    return d


def run_self_test() -> int:
    bad: list[str] = []
    print("── `verify_notification_panel.py --self-test`（纯函数：合成 payload）──")

    def arm(name: str, got, want) -> None:
        ok = got == want
        print(f"  [{'✓' if ok else '✗'}] {name}  期望 {want}  实测 {got}")
        if not ok:
            bad.append(name)

    # ── db_file_from_url：三种合法写法 + 两种坏输入 ──
    arm("D1 aiosqlite 绝对路径",
        db_file_from_url("sqlite+aiosqlite:///C:/tmp/a.db"), "C:/tmp/a.db")
    arm("D2 同步写法", db_file_from_url("sqlite:///./ci.db"), "./ci.db")
    arm("D3 posix 绝对", db_file_from_url("sqlite+aiosqlite:////tmp/a.db"), "/tmp/a.db")
    arm("D4 空值", db_file_from_url(None), None)
    arm("D5 非 sqlite", db_file_from_url("postgresql://x/y"), None)

    # ── P1/P2/P3/P5 ──
    arm("A1 三处齐全 + 颜色一致 ⇒ 0 条", len(judge_panel(_SYN_OK, _LINK)[0]), 0)
    arm("A2 面板未挂载 ⇒ 1 条", len(judge_panel({"mounted": False}, _LINK)[0]), 1)
    arm("A3 角标缺失（空态假绿）⇒ 1 条",
        len(judge_panel(_without("badge"), _LINK)[0]), 1)
    arm("A4 全部已读缺失 ⇒ 1 条", len(judge_panel(_without("readAll"), _LINK)[0]), 1)
    arm("A5 颜色不对 ⇒ 1 条",
        len(judge_panel({**_SYN_OK, "badge": {**_SYN_OK["badge"],
                                             "color": "rgb(39, 76, 147)"}}, _LINK)[0]), 1)
    arm("A6 读不到 --link ⇒ 1 条", len(judge_panel(_SYN_OK, None)[0]), 1)
    arm("A7 漏改指纹 ⇒ 1 条",
        len(judge_panel({**_SYN_OK, "all": [{"t": "x", "c": "rgb(39, 76, 147)"}]},
                        _LINK, needle="rgb(39, 76, 147)")[0]), 1)

    # 注入反证：摘掉 P2 的「必须渲染」⇒ A3/A4 必不再命中。
    # ⚠️ 没有这一臂，「A3 命中」可能只是别的规则顺带命中的（同 `verify_ink400_body` 的 I1/I2）。
    global REQUIRE_ALL
    saved = REQUIRE_ALL
    REQUIRE_ALL = False
    try:
        n = len(judge_panel(_without("badge"), _LINK)[0])
    finally:
        REQUIRE_ALL = saved
    arm("I1 注入反证（摘掉 P2 ⇒ 角标缺失必不命中）", n, 0)

    if bad:
        print(f"❌ 自检失败 {len(bad)} 臂：{bad}")
        return 1
    print("✅ 自检通过：5 输入臂 + 7 判定臂 + 1 注入反证")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§2.1 通知下拉面板渲染级对比度门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（合成 payload）")
    ap.add_argument("--app", default="admin", choices=["web", "lawyer", "admin", "im"])
    ap.add_argument("--user", default=None, help="登录账号（默认取 verify_contrast.APPS）")
    a = ap.parse_args()
    if a.self_test:
        return run_self_test()
    user = a.user or vc.APPS[a.app]["user"]
    return asyncio.run(run_render(a.app, user))


if __name__ == "__main__":
    sys.exit(main())

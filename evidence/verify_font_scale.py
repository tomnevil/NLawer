#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§3.2「字号阶梯」门禁 —— 任意值字号 `text-[Npx]` 是否突破阶梯。

## 为什么这个维度之前没有门禁

`verify_typography.py` 有 **T1c**（任意值**字重**）、**T3b**（`text-<档>` 的计算字号
必须等于该档 px），但**没有任意值字号**的判据 —— 实测：

    $ grep -n 'text-[' evidence/verify_typography.py    # 0 命中

而 **T3b 的选择器只认 `text-<档>` 形式** ⇒ `text-[11px]` / `text-[10px]` 这类
**进不了它的元素集合**（「以属性 X 筛选的门禁，发现不了『缺少 X』的缺陷」——
与 `verify_focus_ring.py` 的 `FOCUSABLE` 是同一个结构性盲区）。

## 出处（期望值一律从 `tailwind.preset.ts` 解析，不硬编码）

| 判据 | 出处 |
|---|---|
| T1 | `§3.2「字号阶梯」` §3.2 阶梯表的**最小档 = 11px**（`caption`）+ `§8.5「案号、金额、日期在移动端仍保持等宽字体」` §8.5「案号、金额、日期…**字号可降至 11–12px**」⇒ 11px 是本项目字号下限 |
| T2 | §3.2 阶梯是**封闭的 10 档**（`tailwind.preset.ts:159-167` 落地）⇒ 阶梯外的 px 值没有对应的行高/字重 |
| T3 | 阶梯的每一档都是「**字号 + 行高 + 字重**」**三元组** ⇒ 用 `text-[Npx]` 只给字号，**行高与字重必然脱离阶梯**（可实测） |
| T4 | §3.3 规则 2「正文行高 ≥ 1.65」（`§3.3「正文行高 ≥ 1.65」`）—— 仅作**报告**，因为「是不是正文」判据看不出来 |

## 🚨 判据设计的坑

1. **`leading-none` 在角标上是合理的** ⇒ 不能因为「行高比不在阶梯里」就判红。
   行高是**语境依赖**的 ⇒ **T4 只报不判**。
2. **`text-[11px]` == `caption` 档** ⇒ 该报的是「**可改用令牌**」（令牌自带
   `lineHeight: 1.5` + `fontWeight: 500`），**不是**「字号错」。⇒ T3 是提示，不是缺陷。
3. **`text-[var(--safe-bottom)]` 这类变量引用是合法的** ⇒ 只认**纯数字 px**。
4. **预览页要单列**：`components-preview` 不是产品页（与 `verify_component_wiring.py`
   的口径一致），它的任意值字号单独统计，不混进产品缺陷数。
5. 🚨 **T5 在演示数据下天然出 0 条** —— 三处 `text-[10px]` 全是**角标类**条件元素
   （`TabBar` 的 `item.badge`、`CitationChip`、`SegmentedControl` 的 `option.badge`），
   演示数据里 `badge` 多为空 ⇒ **渲染不出来**。实测（`evidence/t5_effective_probe.txt`）：
   `im /` 与 `web /qa` 两页里 `className` 含 `text-[10px]` 的元素 **0 个**，
   而对照组 `text-caption` 的元素计算字号**确为 11px**（证明「读计算样式」这条链路是好的）。
   ⇒ **0 条不等于「判据是空的」**，但**必须**用故障注入证明它本来会报 ——
   见 `--render-self-test`（`evidence/t5_render_selftest.txt`）。
6. **`line-height: normal` 是已声明盲区**：算不出比率 ⇒ 静默丢弃。实测本项目
   叶子文本被继承成 `normal` 的比例为 **0%**（`tokens.css` 在 `html/body` 上设了数值行高），
   所以当前不产生假绿 —— 但盲区写在这里，不隐藏。

用法：
    python evidence/verify_font_scale.py --self-test          # 纯函数自测，不起浏览器
    python evidence/verify_font_scale.py --source-only        # 只扫源码层（秒级）
    python evidence/verify_font_scale.py                      # 源码层 + 渲染层
    python evidence/verify_font_scale.py --render-self-test   # 渲染层故障注入自检
    python evidence/verify_font_scale.py --why                # 打出处 + 阶梯全表
    python evidence/verify_font_scale.py --dump               # 打印原始量测

退出码：0 = 通过；1 = 产品缺陷；2 = 环境问题
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[0]
FE = ROOT / "frontend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

#: §8.5（§8.5「案号、金额、日期在移动端仍保持等宽字体」）明文写的移动端字号下限。
#: §3.2 的最小档也是 11px（caption）—— 两个出处互证。
MIN_FONT_PX = 11.0

#: §3.2（§3.2「字号阶梯」）的**语义档**（10 个）。
#: preset 里另有一组「**存量档**」（`xs/sm/base/lg/xl/2xl/3xl`），注释写明是
#: 「只调整行高以适配中文，不改字号，避免布局位移」——**也是有效档位**（同样带行高），
#: 所以判据的 px 集合取**两组之和**。
SEMANTIC_NAMES = frozenset({
    "caption", "label", "body-sm", "body", "body-lg",
    "h4", "h3", "h2", "h1", "display",
})

#: 预览页不算产品（口径与 `verify_component_wiring.py` 一致）。
PREVIEW_MARKER = "components-preview"

#: `text-[13px]` / `text-[1.5rem]` —— 只认**纯数字 + px**（变量引用不抓）。
ANY_FONT_RE = re.compile(r"\btext-\[(\d+(?:\.\d+)?)px\]")
#: `leading-[1.85]`
ANY_LEADING_RE = re.compile(r"\bleading-\[(\d+(?:\.\d+)?)\]")
#: `leading-5` / `leading-none` / `leading-tight`（命名档，用于判断「有没有显式行高」）
NAMED_LEADING_RE = re.compile(r"\bleading-(?:none|tight|snug|normal|relaxed|loose|\d+(?:\.\d+)?)\b")

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


# ─────────────────────────── 从 preset 解析阶梯（不硬编码） ───────────────────────────

def parse_scale(preset_src: str) -> dict[str, dict]:
    """从 `tailwind.preset.ts` 的 `fontSize` 块解析出字号阶梯。

    返回 `{"caption": {"px": 11.0, "leading": 1.5, "weight": 500}, ...}`。

    ⚠️ 必须**解析**而不是硬编码 —— 阶梯是**出处的载体**，改规格时门禁要跟着变。
    """
    m = re.search(r"fontSize\s*:\s*\{(.*?)\n\s{6}\}", preset_src, re.S)
    if not m:
        return {}
    body = m.group(1)
    out: dict[str, dict] = {}
    # `caption: ["11px", { lineHeight: "1.5", fontWeight: "500" }],`
    # ⚠️ 键名要允许**数字开头**（`2xl` / `3xl`），否则会漏档 —— 第一版就漏了 2 档。
    for mm in re.finditer(
        r'"?([A-Za-z0-9][\w-]*)"?\s*:\s*\[\s*"(\d+(?:\.\d+)?)px"\s*,\s*\{([^}]*)\}',
        body,
    ):
        name, px, opts = mm.group(1), float(mm.group(2)), mm.group(3)
        lm = re.search(r'lineHeight\s*:\s*"(\d+(?:\.\d+)?)"', opts)
        wm = re.search(r'fontWeight\s*:\s*"(\d+)"', opts)
        out[name] = {
            "px": px,
            "leading": float(lm.group(1)) if lm else None,
            "weight": int(wm.group(1)) if wm else None,
        }
    return out


def scale_px_set(scale: dict[str, dict]) -> set[float]:
    return {v["px"] for v in scale.values()}


def scale_leading_set(scale: dict[str, dict]) -> set[float]:
    return {v["leading"] for v in scale.values() if v["leading"] is not None}


def nearest_step(scale: dict[str, dict], px: float) -> tuple[str, float] | None:
    """最接近的档位（用于报告「你想用的可能是这一档」）。"""
    if not scale:
        return None
    name = min(scale, key=lambda k: abs(scale[k]["px"] - px))
    return name, scale[name]["px"]


# ─────────────────────────── 纯函数判据 ───────────────────────────

def judge_any_font(px: float, scale: dict[str, dict]) -> tuple[str, str]:
    """T1/T2/T3：给一个任意值字号 px，返回 `(级别, 说明)`。

    级别：`"red"` 缺陷 / `"note"` 提示 / `"ok"` 合规。
    """
    if px < MIN_FONT_PX:
        return ("red",
                f"`text-[{px:g}px]` 低于本项目字号下限 **{MIN_FONT_PX:g}px**"
                f"（§3.2 最小档 `caption` = 11px；§8.5 明文「可降至 11–12px」）")
    steps = scale_px_set(scale)
    if px in steps:
        name = next(k for k, v in scale.items() if v["px"] == px)
        return ("note",
                f"`text-[{px:g}px]` == `{name}` 档 ⇒ 建议改用 `text-{name}`"
                f"（令牌自带 lineHeight / fontWeight，任意值只有字号）")
    near = nearest_step(scale, px)
    if near:
        return ("note",
                f"`text-[{px:g}px]` 不在字号阶梯的 {len(scale)} 档里"
                f"（最接近的是 `{near[0]}` = {near[1]:g}px）⇒ 该字号没有对应的行高/字重")
    return ("note", f"`text-[{px:g}px]` 不在阶梯里")


def judge_leading(val: float, scale: dict[str, dict]) -> str:
    """T4（**只报不判**）：任意值行高与阶梯 / §3.3 的关系。

    ⚠️ 不判红：行高是**语境依赖**的（角标用 `leading-none`、标题用紧行高都合理），
       判据看不出「这是不是正文」。
    ⚠️ **§3.3 规则 2 明文写了两个区间**（`§3.3「中文排印三条硬规则」`）：
       「正文行高 ≥ 1.65 …… **法条原文用 1.85–2.0**」。
       第一版只拿**阶梯行高的上界 1.85** 当上界，于是 `leading-[1.95]` / `[1.9]`
       被报成「在区间外 ⇒ 可能违反」—— **那是在拿自己的判据错误指认产品**
       （它们正落在规范明文允许的法条区间里）。修法：显式实现那个区间。
    """
    lds = scale_leading_set(scale)
    if val in lds:
        name = next(k for k, v in scale.items() if v["leading"] == val)
        return f"`leading-[{val:g}]` == `{name}` 档的行高 ⇒ 可改用 `text-{name}`"
    if 1.85 <= val <= 2.0:
        return (f"`leading-[{val:g}]` 落在 §3.3 规则 2 的**法条原文区间 1.85–2.0** 内"
                f"⇒ 合规（阶梯行高集合里没有它，属刻意的长文行高）")
    if val >= 1.65:
        return f"`leading-[{val:g}]` ≥ 1.65 ⇒ 满足 §3.3 规则 2"
    return (f"`leading-[{val:g}]` < 1.65 ⇒ **若用于正文**则违反 §3.3 规则 2；"
            f"标题 / 角标 / 单行标签用紧行高是合理的 ⇒ 只报不判")


def filter_odd_rows(rows: list[dict], steps: set[float],
                    lds: set[float]) -> list[dict]:
    """T5 的筛选规则（**纯函数**，与浏览器解耦 ⇒ 可自测）。

    保留条件（全部满足才算「异常」）：
      1. 计算字号**不在**任何阶梯档；
      2. 计算字号 **< 18px**（标题类另行判定）；
      3. 行高比**算得出**（`line-height: normal` 的不算 —— 这是**已知盲区**，见下）；
      4. 行高比**不命中**任何阶梯档的行高。

    ⚠️ 条件 3 是**声明过的盲区**：`line-height: normal` 的元素算不出比率 ⇒ 静默丢弃。
    实测（`evidence/t5_vacuity_probe.txt`）：本项目**没有被继承到 `normal` 的**叶子文本
    （im `/` 与 web `/qa` 两页均为 **0%**）—— 因为 `tokens.css` 在 `html/body` 上设了
    数值行高。所以这条盲区在当前实现下**不产生假绿**，但它**存在**，故写在这里。
    """
    out: list[dict] = []
    for row in rows:
        fs = row.get("fs")
        if fs is None or fs in steps or fs >= 18:
            continue
        ratio = row.get("ratio")
        if ratio is None or ratio in lds:
            continue
        out.append(row)
    return out


# ─────────────────────────── 源码层扫描 ───────────────────────────

def collect_tsx(root: pathlib.Path) -> list[tuple[str, str]]:
    """收集 (显示路径, 源码)。**自测时传 fixture，不碰文件系统。**"""
    out: list[tuple[str, str]] = []
    for pat in ("apps/**/*.tsx", "packages/**/*.tsx"):
        for p in sorted(root.glob(pat)):
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            try:
                out.append((str(p.relative_to(root)).replace("\\", "/"),
                            p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def scan_any_values(files: list[tuple[str, str]]) -> list[dict]:
    """扫出所有任意值字号 / 行高的出现点。"""
    hits: list[dict] = []
    for rel, src in files:
        for i, line in enumerate(src.splitlines(), 1):
            for m in ANY_FONT_RE.finditer(line):
                hits.append({
                    "kind": "font", "path": rel, "line": i,
                    "px": float(m.group(1)), "raw": m.group(0),
                    "preview": PREVIEW_MARKER in rel,
                    "has_leading": bool(NAMED_LEADING_RE.search(line)
                                        or ANY_LEADING_RE.search(line)),
                    "src": line.strip()[:150],
                })
            for m in ANY_LEADING_RE.finditer(line):
                hits.append({
                    "kind": "leading", "path": rel, "line": i,
                    "val": float(m.group(1)), "raw": m.group(0),
                    "preview": PREVIEW_MARKER in rel,
                    "src": line.strip()[:150],
                })
    return hits


def run_source(files: list[tuple[str, str]], scale: dict[str, dict],
               dump: bool = False) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """返回 `(缺陷, 提示, 统计行)`。"""
    hits = scan_any_values(files)
    fonts = [h for h in hits if h["kind"] == "font"]
    leadings = [h for h in hits if h["kind"] == "leading"]
    bad: list[tuple[str, str, str]] = []
    notes: list[str] = []

    prod = [h for h in fonts if not h["preview"]]
    prev = [h for h in fonts if h["preview"]]
    print(f"  任意值字号 `text-[Npx]`：产品 {len(prod)} 处 · 预览页 {len(prev)} 处")
    print(f"  任意值行高 `leading-[X]`：{len(leadings)} 处")

    for h in prod:
        lvl, msg = judge_any_font(h["px"], scale)
        loc = f"{h['path']}:{h['line']}"
        if lvl == "red":
            bad.append((loc, "T1", msg))
        elif lvl == "note":
            notes.append(f"[T2/T3] {loc}  {msg}")
    for h in prev:
        lvl, msg = judge_any_font(h["px"], scale)
        if lvl == "red":
            notes.append(f"[预览页·不计缺陷] {h['path']}:{h['line']}  {msg}")
        elif lvl == "note":
            notes.append(f"[预览页] {h['path']}:{h['line']}  {msg}")
    for h in leadings:
        tag = "[预览页] " if h["preview"] else ""
        notes.append(f"[T4] {tag}{h['path']}:{h['line']}  {judge_leading(h['val'], scale)}")

    if dump:
        print("\n" + json.dumps(hits, ensure_ascii=False, indent=2))
    return bad, notes, [
        f"任意值字号：产品 {len(prod)} / 预览 {len(prev)}；任意值行高 {len(leadings)}"
    ]


# ─────────────────────────── 渲染层（验证「行高失控」） ───────────────────────────

SCAN_JS = r"""(async () => {
  const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  await frame();
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;                 // 只看叶子
    const t = (el.textContent || '').trim();
    if (!t || t.length > 24) continue;
    const c = getComputedStyle(el);
    const fs = parseFloat(c.fontSize);
    if (!fs) continue;
    // 只收「计算字号不是任何语义档」的 —— 语义档由 Python 侧给出
    const lh = c.lineHeight === 'normal' ? null : parseFloat(c.lineHeight);
    out.push({
      text: t.slice(0, 20),
      fs: fs,
      lh: lh,
      ratio: lh ? +(lh / fs).toFixed(3) : null,
      cls: (el.className || '').toString().slice(0, 80),
    });
  }
  return { vw: window.innerWidth, dark: document.documentElement.classList.contains('dark'), rows: out };
})()"""


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


async def run_render(scale: dict[str, dict], dump: bool = False) -> tuple[list[str], int]:
    """渲染层：实测「用了任意值字号的元素」的行高比，验证 T3 的论断。

    返回 `(提示列表, 环境问题数)`。
    """
    from cdp import Browser
    steps = scale_px_set(scale)
    lds = scale_leading_set(scale)
    notes: list[str] = []
    env = 0
    odd: list[dict] = []
    async with Browser(headless=True, width=390, height=844, device_scale_factor=2) as b:
        await b.apply_device(mobile=True)
        for app, spec in APPS.items():
            base = f"http://localhost:{spec['port']}"
            try:
                if not await login(b, base, spec["user"]):
                    notes.append(f"[ENV] [{app}] 登录失败")
                    env += 1
                    continue
            except Exception as e:                                     # noqa: BLE001
                notes.append(f"[ENV] [{app}] 登录异常：{type(e).__name__}")
                env += 1
                continue
            for path in spec["pages"]:
                await b.goto(f"{base}{path}", wait=1.5)
                await b.settle(extra=2.0)
                r = await b.cdp.evaluate(SCAN_JS)
                if not isinstance(r, dict):
                    continue
                for row in filter_odd_rows(r.get("rows") or [], steps, lds):
                    odd.append({"app": app, "path": path, **row})
    if odd:
        uniq: dict[tuple, list[str]] = {}
        for o in odd:
            uniq.setdefault((o["fs"], o["ratio"]), []).append(f"{o['app']}{o['path']}")
        notes.append(f"[T5] 渲染层：{len(odd)} 个元素的字号**不在任何语义档**"
                     f"（{len(uniq)} 种组合）：")
        for (fs, ratio), locs in sorted(uniq.items()):
            notes.append(f"        {fs:g}px / 行高比 {ratio:g}"
                         f"  @ {len(set(locs))} 页：{', '.join(sorted(set(locs))[:4])}")
    if dump:
        print("\n" + json.dumps(odd[:40], ensure_ascii=False, indent=2))
    return notes, env


# ─────────────────────── 渲染层自检：故障注入（证明 T5 有检出能力） ───────────────────────

#: 往**真实页面**里注入四个元素，验证 T5 的筛选规则**能把该报的报出来、
#: 不该报的不报**。全量跑出 0 条时，本自检是唯一能区分
#: 「产品干净」与「判据是空的」的证据。
INJECT_JS = r"""(() => {
  const mk = (id, style, text) => {
    const s = document.createElement('span');
    s.className = '__t5_probe ' + id;
    s.setAttribute('style', style);
    s.textContent = text;
    document.body.appendChild(s);
  };
  // ① 该被检出：字号不在阶梯 + 行高比不在阶梯
  mk('hit',   'display:inline-block;font-size:13.5px;line-height:1.6',  '13.5px注入');
  // ② 对照组：字号**在**阶梯上 ⇒ 不该报
  mk('clean', 'display:inline-block;font-size:13px;line-height:1.65',   '13px注入');
  // ③ 已知盲区：line-height:normal ⇒ 比率算不出 ⇒ 静默丢弃（**声明**，不隐藏）
  mk('nolh',  'display:inline-block;font-size:13.5px;line-height:normal','正常行高');
  // ④ 对照组：字号不在阶梯、但行高比**命中**某档 ⇒ 不该报
  mk('lhok',  'display:inline-block;font-size:13.5px;line-height:1.7',  '行高命中档');
  return document.querySelectorAll('.__t5_probe').length;
})()"""


async def render_self_test(scale: dict[str, dict]) -> int:
    """渲染层故障注入自检。返回 0 = 自检通过；2 = 环境问题。"""
    from cdp import Browser

    steps = scale_px_set(scale)
    lds = scale_leading_set(scale)
    base = "http://localhost:3003"
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
                print("[ENV] 渲染自检：注入失败（页面里找不到 __t5_probe）")
                return 2
            await b.settle(extra=0.8)
            r = await b.cdp.evaluate(SCAN_JS) or {}
            rows = r.get("rows") or []
    except Exception as e:                                              # noqa: BLE001
        print(f"[ENV] 渲染自检异常：{type(e).__name__}: {e}")
        return 2

    probes = [x for x in rows if "__t5_probe" in str(x.get("cls", ""))]
    odd = filter_odd_rows(rows, steps, lds)
    odd_ids = {m.group(1) for x in odd
               for m in [re.search(r"__t5_probe\s+(\w+)", str(x.get("cls", "")))] if m}

    print(f"渲染层自检（故障注入）：注入 {n} 个探针，扫回 {len(probes)} 个")
    for x in sorted(probes, key=lambda y: str(y.get("cls"))):
        cid = re.search(r"__t5_probe\s+(\w+)", str(x.get("cls", "")))
        cid = cid.group(1) if cid else "?"
        ratio = f"{x['ratio']:g}" if x.get("ratio") is not None else "—（normal）"
        print(f"  <{cid:>5}> fs={x['fs']:g}px 行高比={ratio}")

    checks = [
        ("① 检出能力：hit（13.5px / 1.6）**必须**被报出", "hit" in odd_ids, True),
        ("② 特异性：clean（13px 在阶梯上）**不得**被报出", "clean" in odd_ids, False),
        ("③ 已知盲区：nolh（normal 行高）**不得**被报出", "nolh" in odd_ids, False),
        ("④ 特异性：lhok（行高比命中档）**不得**被报出", "lhok" in odd_ids, False),
    ]
    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}（实际 {'报出' if got else '未报出'}）")
    print(f"渲染自检 {'通过' if not bad else f'失败 {bad} 项'}"
          f"（4 项：1 检出 + 2 特异性 + 1 已声明盲区）")
    return 0 if not bad else 1


# ─────────────────────────── 自测 ───────────────────────────

FIXTURE_SCALE: dict[str, dict] = {
    "caption": {"px": 11.0, "leading": 1.5, "weight": 500},
    "label": {"px": 12.0, "leading": 1.5, "weight": 500},
    "body-sm": {"px": 13.0, "leading": 1.65, "weight": None},
    "body": {"px": 14.0, "leading": 1.7, "weight": None},
    "body-lg": {"px": 15.0, "leading": 1.85, "weight": None},
    "h4": {"px": 16.0, "leading": 1.5, "weight": 600},
    "h3": {"px": 18.0, "leading": 1.45, "weight": 600},
    "h2": {"px": 22.0, "leading": 1.4, "weight": 600},
    "h1": {"px": 30.0, "leading": 1.3, "weight": 600},
    "display": {"px": 38.0, "leading": 1.25, "weight": 600},
}

PRESET_SNIPPET = """
      fontSize: {
        caption: ["11px", { lineHeight: "1.5", fontWeight: "500" }],
        label: ["12px", { lineHeight: "1.5", fontWeight: "500" }],
        "body-sm": ["13px", { lineHeight: "1.65" }],
        body: ["14px", { lineHeight: "1.7" }],
        display: ["38px", { lineHeight: "1.25", fontWeight: "600" }],
        "2xl": ["24px", { lineHeight: "1.4" }],
      },
"""


def self_test() -> int:
    n = 0
    fails: list[str] = []

    def arm(name: str, got, want) -> None:
        nonlocal n
        n += 1
        if got != want:
            fails.append(f"{name}: got={got!r} want={want!r}")

    # ── 解析 preset（期望值必须来自出处，不能硬编码）──
    sc = parse_scale(PRESET_SNIPPET)
    arm("解析档数", len(sc), 6)
    arm("caption px", sc["caption"]["px"], 11.0)
    arm("caption leading", sc["caption"]["leading"], 1.5)
    arm("caption weight", sc["caption"]["weight"], 500)
    arm("body-sm 无字重", sc["body-sm"]["weight"], None)
    arm("带引号的键 body-sm", "body-sm" in sc, True)
    # ⚠️ 数字开头的键（`2xl`/`3xl`）—— 第一版正则要求字母开头，**漏了 2 档**
    arm("数字开头的键 2xl 被解析", sc.get("2xl", {}).get("px"), 24.0)
    arm("px 集合", sorted(scale_px_set(sc)), [11.0, 12.0, 13.0, 14.0, 24.0, 38.0])
    arm("行高集合（去掉 None）", sorted(scale_leading_set(sc)), [1.25, 1.4, 1.5, 1.65, 1.7])

    # ── T1 红臂：低于下限 11px（实测 `text-[10px]` × 9）──
    lvl, msg = judge_any_font(10.0, FIXTURE_SCALE)
    arm("T1-a 10px ⇒ 红", lvl, "red")
    arm("T1-a2 说明里点出下限", "下限" in msg, True)
    # ── T1 边界：正好 11px 不算红（下限是闭区间）──
    lvl, _ = judge_any_font(11.0, FIXTURE_SCALE)
    arm("T1-b 11px ⇒ 不是红（== caption 档）", lvl, "note")
    lvl, _ = judge_any_font(10.9, FIXTURE_SCALE)
    arm("T1-c 10.9px ⇒ 红（门槛下方）", lvl, "red")

    # ── T3：等于某档 ⇒ 提示改用令牌 ──
    lvl, msg = judge_any_font(11.0, FIXTURE_SCALE)
    arm("T3-a 11px == caption ⇒ note", lvl, "note")
    arm("T3-a2 提示里给出 text-caption", "text-caption" in msg, True)
    lvl, msg = judge_any_font(13.0, FIXTURE_SCALE)
    arm("T3-b 13px == body-sm", "text-body-sm" in msg, True)
    # ── T2：不等于任何档 ⇒ 报「阶梯外」+ 最接近的档 ──
    lvl, msg = judge_any_font(28.0, FIXTURE_SCALE)
    arm("T2-a 28px ⇒ note（不是红）", lvl, "note")
    arm("T2-a2 报出不在阶梯里", "不在" in msg and "阶梯" in msg, True)
    arm("T2-a3 给出最接近档（h1 30 或 h2 22）",
        ("`h1`" in msg) or ("`h2`" in msg), True)
    lvl, msg = judge_any_font(40.0, FIXTURE_SCALE)
    arm("T2-b 40px ⇒ note", lvl, "note")
    arm("T2-b2 最接近 display(38)", "`display`" in msg, True)
    arm("T2-b3 40px 的 px 被原样报出", "40" in msg, True)

    # ── T4：行高只报不判 ──
    arm("T4-a 1.85 == body-lg", "`body-lg`" in judge_leading(1.85, FIXTURE_SCALE), True)
    arm("T4-b 1.0 < 1.65 ⇒ 若用于正文则违规（角标合理）",
        "若用于正文" in judge_leading(1.0, FIXTURE_SCALE), True)
    arm("T4-c 1.5 命中档位", "`caption`" in judge_leading(1.5, FIXTURE_SCALE)
        or "`label`" in judge_leading(1.5, FIXTURE_SCALE), True)
    # 🚨 出处臂：§3.3 规则 2 明文「法条原文用 1.85–2.0」⇒ 1.95 / 1.9 必须判**合规**
    #    （第一版拿阶梯上界 1.85 当上界 ⇒ 把它们报成「可能违反」= 假红）
    arm("T4-d 1.95 落法条区间 ⇒ 合规",
        "法条原文区间" in judge_leading(1.95, FIXTURE_SCALE), True)
    arm("T4-e 1.9 落法条区间 ⇒ 合规",
        "法条原文区间" in judge_leading(1.9, FIXTURE_SCALE), True)
    arm("T4-f 1.72（不在集合）≥1.65 ⇒ 满足",
        "满足" in judge_leading(1.72, FIXTURE_SCALE), True)

    # ── 扫描器：只认纯数字 px，变量引用不抓 ──
    files = [("apps/x/page.tsx",
              'a className="text-[11px] leading-5"\n'
              'b className="pb-[var(--safe-bottom)]"\n'
              'c className="text-[10px]"\n'
              'd className="text-ink-400"\n'
              'e className="leading-[1.85]"\n')]
    hits = scan_any_values(files)
    fonts = [h for h in hits if h["kind"] == "font"]
    arm("扫描：抓到 2 个任意值字号", len(fonts), 2)
    arm("扫描：变量引用不被抓", all("var" not in h["raw"] for h in fonts), True)
    arm("扫描：颜色类不被抓", all("ink" not in h["raw"] for h in fonts), True)
    arm("扫描：has_leading 认 leading-5", fonts[0]["has_leading"], True)
    arm("扫描：无 leading 的元素", fonts[1]["has_leading"], False)
    leadings = [h for h in hits if h["kind"] == "leading"]
    arm("扫描：抓到 1 个任意值行高", len(leadings), 1)
    arm("扫描：行高值", leadings[0]["val"], 1.85)

    # ── 预览页标记 ──
    files2 = [("apps/web/app/components-preview/page.tsx", 'x className="text-[10px]"')]
    h2 = scan_any_values(files2)
    arm("预览页被标记", h2[0]["preview"], True)

    # ── T5 筛选规则（纯函数）──
    # 🚨 「只会绿的防线不算防线」：全量跑 T5 出 0 条时，必须能证明**它本来会报**。
    #    这里锁住筛选规则的**检出能力**与**特异性**两侧；浏览器侧的端到端证明
    #    由 `--render-self-test`（故障注入）给出。
    st = scale_px_set(FIXTURE_SCALE)
    ld = scale_leading_set(FIXTURE_SCALE)

    def mk(fs: float, ratio: float | None) -> dict:
        return {"fs": fs, "ratio": ratio}

    kept = filter_odd_rows([
        mk(13.5, 1.6),      # 该报：字号不在阶梯 + 行高比不在阶梯
        mk(13.0, 1.65),     # 不报：字号在阶梯上
        mk(13.5, None),     # 不报：`normal` 行高（**已声明盲区**）
        mk(13.5, 1.7),      # 不报：行高比命中某档
        mk(28.0, 1.3),      # 不报：≥18px 标题类另行判定
    ], st, ld)
    arm("T5-a 五条只保留 1 条", len(kept), 1)
    arm("T5-b 保留的正是 hit", kept[0]["fs"] if kept else None, 13.5)
    arm("T5-c 阶梯内字号不报", any(r["fs"] == 13.0 for r in kept), False)
    arm("T5-d normal 行高不报（已声明盲区）", any(r["ratio"] is None for r in kept), False)
    arm("T5-e 行高比命中档不报", any(r["ratio"] == 1.7 for r in kept), False)
    arm("T5-f ≥18px 标题类不报", any(r["fs"] >= 18 for r in kept), False)
    arm("T5-g 空输入不炸", filter_odd_rows([], st, ld), [])

    # ── 控制臂：阶梯解析出来的最小档就是 11（与 MIN_FONT_PX 互证）──
    arm("控制臂：preset 最小档 == MIN_FONT_PX",
        min(scale_px_set(FIXTURE_SCALE)), MIN_FONT_PX)

    print(f"自测 {n - len(fails)}/{n} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    return 0 if not fails else 1


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="§3.2 字号阶梯门禁")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--render-self-test", action="store_true",
                    help="渲染层故障注入自检（证明 T5 有检出能力）")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--why", action="store_true")
    a = ap.parse_args()

    preset_path = FE / "tailwind.preset.ts"
    try:
        scale = parse_scale(preset_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        print(f"[ENV] 读不到 {preset_path}：{e}")
        return 2
    if not scale:
        print(f"[ENV] 从 {preset_path} 解析不出 fontSize 阶梯（选择器失效？）")
        return 2

    if a.why:
        print("出处：§3.2「字号阶梯」 §3.2 字号阶梯 + tailwind.preset.ts 的 fontSize")
        sem = [k for k in scale if k in SEMANTIC_NAMES]
        leg = [k for k in scale if k not in SEMANTIC_NAMES]
        print(f"解析到 {len(scale)} 档（语义 {len(sem)} + 存量 {len(leg)}）：")
        for k, v in scale.items():
            tag = "语义" if k in SEMANTIC_NAMES else "存量"
            print(f"  [{tag}] {k:<9} {v['px']:>5g}px  行高 {v['leading']}  字重 {v['weight']}")
        print(f"  px 集合：{sorted(scale_px_set(scale))}")
        print(f"\n下限：{MIN_FONT_PX:g}px"
              f"（§3.2 最小档 caption + §8.5「可降至 11–12px」）")
        print(f"预览页口径：路径含 `{PREVIEW_MARKER}` 的不计产品缺陷")
        return 0

    if a.self_test:
        return self_test()

    if a.render_self_test:
        return asyncio.run(render_self_test(scale))

    print("§3.2「字号阶梯」门禁")
    print(f"  从 preset 解析到 {len(scale)} 档，最小 {min(scale_px_set(scale)):g}px")
    print("\n── 源码层 T1/T2/T3/T4 ──")
    files = collect_tsx(FE)
    bad, notes, stats = run_source(files, scale, dump=a.dump)
    for s in stats:
        print(f"  {s}")

    if bad:
        groups: dict[tuple[str, str], list[str]] = {}
        for loc, crit, msg in bad:
            groups.setdefault((crit, msg), []).append(loc)
        print(f"\n  ✗ 源码层缺陷 {len(bad)} 条，聚为 {len(groups)} 条根因：")
        for (crit, msg), locs in sorted(groups.items()):
            print(f"    [{crit}] {msg}")
            print(f"          @ {len(set(locs))} 处：{', '.join(sorted(set(locs))[:6])}"
                  + (" …" if len(set(locs)) > 6 else ""))
    else:
        print("\n  ✓ 源码层未发现 T1 缺陷")

    if notes:
        print(f"\n  · 提示 {len(notes)} 条（**不是缺陷**，供拍板）：")
        for t in notes:
            print(f"    {t}")

    if a.source_only:
        return 1 if bad else 0

    print("\n── 渲染层 T5（验证「任意值字号 ⇒ 行高脱离阶梯」）──")
    rnotes, env = asyncio.run(run_render(scale, dump=a.dump))
    for t in rnotes:
        print(f"  {t}")
    if env and not bad:
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

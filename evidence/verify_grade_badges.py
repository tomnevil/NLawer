#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§2.4「案件等级」门禁 —— 等级徽章的「颜色」通道是否符合规范。

## 为什么这个维度之前没有门禁

第七轮覆盖率审计建 `verify_buttons.py` 之后又盘了一次剩余章节，发现 §2.4 是
**唯一一个「规范写了、代码也实现了、却没有任何门禁管」**的维度：

    $ grep -l "案件等级" evidence/*.py      # 只命中本目录的临时探针
    $ grep -l "双通道"   evidence/*.py      # 0
    $ grep -l "GRADE_TONE" evidence/*.py    # 0

⚠️ **「关键词命中 ≠ 有判据」**：`grep -l grade evidence/*.py` 会命中 3 个文件，
   但逐一打开全是 `alembic upgrade` 里的 `grade` **子串** —— 假命中。
   ⇒ 覆盖率审计必须看**命中上下文**，不能只看计数。

## 出处（每条判据的期望值都能指到行）

| 判据 | 出处 |
|---|---|
| G1 | `§2.4「案件等级」` §2.4 表格：S=`#B08A4F` 金 / A=`#C0392B` 朱砂 / B=`#274C93` 墨蓝 / C=`#9AA3AE` 中性 |
| G1′ | 四个色值**精确等于**现有令牌：`tokens.css:101` `--gold-500` / `:91` `--danger-500` / `:49` `--brand-600` / `:34` `--ink-400` |
| G2 | 同一语义不应有 N 份来源（本项目已在焦点环上踩过「定义了没人用」） |
| G3 | `§2.4「徽章采用字母 + 颜色双通道编码，色盲与黑白打印可辨。」`「徽章采用**字母 + 颜色**双通道编码，色盲与黑白打印可辨」 |
| G4 | `Badge.tsx:39-56` 的变体**明确定义了文字色**（`text-info-600` 等）⇒ 必须生效 |
| G5 | `tokens.css:34` `--ink-400`「占位符/图标/装饰，**禁用于正文**」 × §2.4 把同一个 `#9AA3AE` 分配给 C 级徽章 ⇒ **规范内部 tension，只报不判** |

## 🚨 判据设计的坑（建这个门禁时实测踩到的）

1. **不能写死 hex。** 令牌在 `.dark` 里整体翻转
   （`--brand-600` `#274C93`→`#3A63B0`、`--gold-500` `#B08A4F`→`#C49E62`、
    `--danger-500` `#C0392B`→`#D65448`、`--ink-400` `#9AA3AE`→`#7E8795`、`--link` `#274C93`→`#8AA8E0`）。
   写死 hex 的判据会在深色模式下**全体误报**。
   ⇒ **期望值必须从页面上读令牌算出来**
     （`getComputedStyle(document.documentElement).getPropertyValue('--gold-500')`），
     这样浅色/深色自动适配。自测里有一条控制臂专门锁这个（同一断言在两套令牌下都过）。
2. **`default` 与 `neutral` 两个变体底色完全相同**（都是 `bg-surface-subtle`），
   只有文字色不同（`ink-600` / `ink-500`）⇒ 从渲染层**无法靠底色区分**
   ⇒ G4 对这两个变体**只报不判**（与 `verify_contrast.py` 的「区间内不判」同族）。
3. **`--ink-400` 是 §2.1 的「禁用于正文」色**，而 §2.4 把它给了 C 级徽章。
   **规范自己内部冲突**，不能替用户裁决 ⇒ G5 **report-only**。
4. **class 里有 ≠ 渲染出来就是**（自我纠错纪律 2）。DOM 上保留着 `bg-info-500/15`
   这样的原始 class，但它只能用来**定位元素/反查变体**，判据必须落在
   `getComputedStyle` 的渲染值上。
5. **`--link` 与 `--brand-600` 浅色同值、深色不同值**（`#274C93`/`#8AA8E0` vs `#274C93`/`#3A63B0`）
   ⇒ 判 primary 变体的文字色必须用 `--link`，不能拿 `--brand-600` 顶替。

用法：
    python evidence/verify_grade_badges.py --self-test     # 纯函数自测，不起浏览器
    python evidence/verify_grade_badges.py --source-only   # 只扫源码层（G2）
    python evidence/verify_grade_badges.py                 # 全量：源码层 + 渲染层（26 页）
    python evidence/verify_grade_badges.py --dump          # 打印原始量测 JSON

退出码：0 = 通过；1 = 产品缺陷；2 = 环境问题（跑不成）
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

# ─────────────────────────── 出处常量 ───────────────────────────

#: §2.4（§2.4「案件等级」）等级 → 令牌角色。
#: 注意这四行是**规范**给的，不是我从代码里读出来的。
SPEC_GRADE_TOKEN: dict[str, str] = {
    "S": "gold-500",    # #B08A4F 金   → tokens.css:101
    "A": "danger-500",  # #C0392B 朱砂 → tokens.css:91
    "B": "brand-600",   # #274C93 墨蓝 → tokens.css:49
    "C": "ink-400",     # #9AA3AE 中性 → tokens.css:34
}

#: `Badge.tsx:39-56` 的变体 → 它声明的**文字色令牌**。
#: ⚠️ 这张表必须从组件源码抄，**不能**从 DOM 反查 —— 因为被 `tailwind-merge`
#:    吞掉之后 DOM 上根本不存在 `text-info-600` 这个 class（见 G4 说明）。
BADGE_VARIANT_FG_TOKEN: dict[str, str] = {
    "default": "ink-600",
    "verified": "verified-600",
    "pending": "pending-600",
    "danger": "danger-600",
    "info": "info-600",
    "primary": "link",          # Badge.tsx:48 用 text-link（≠ brand-600 深色值）
    "ai": "ai-600",
    "gold": "gold-700",
    "neutral": "ink-500",
}

#: 底色 class → 变体名。只有**底色唯一**的变体能进这张表；
#: `default`/`neutral` 共用 `bg-surface-subtle` ⇒ 故意不收（坑 2）。
VARIANT_BY_BG_CLASS: dict[str, str] = {
    "bg-verified-500/15": "verified",
    "bg-pending-500/15": "pending",
    "bg-danger-500/15": "danger",
    "bg-info-500/15": "info",
    "bg-brand-500/15": "primary",
    "bg-ai-500/15": "ai",
    "bg-gold-500/15": "gold",
}

#: 页面里要抓的令牌快照（浅色/深色两套由浏览器给出，判据不关心是哪套）。
TOKEN_SNAPSHOT = (
    "gold-500", "danger-500", "brand-600", "brand-500", "ink-400",
    "ink-500", "ink-600", "ink-700", "info-600", "danger-600",
    "pending-600", "verified-600", "ai-600", "gold-700", "link",
    "surface-subtle",
)

#: 徽章的透明度修饰符：`bg-gold-500/15` ⇒ alpha 0.15
BADGE_BG_ALPHA = 0.15

APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin",
            "pages": ("/", "/qa", "/documents", "/contract-review",
                      "/compliance", "/knowledge", "/billing")},
    "lawyer": {"port": 3001, "user": "lawyer_wang",
               "pages": ("/", "/dispatches", "/cases", "/cases/@first-case",
                         "/reviews", "/archives", "/notifications")},
    "admin": {"port": 3002, "user": "admin",
              "pages": ("/", "/reviews", "/cases", "/dispatches",
                        "/compliance", "/billing", "/complaints", "/audit")},
    "im": {"port": 3003, "user": "client",
           "pages": ("/", "/chat", "/cases", "/cases/@first-case", "/me")},
}

#: 等级徽章文本形态：`S` / `S级` / `S 级` / `等级 S` / `等级S`
GRADE_TEXT_RE = re.compile(r"^(?:等级\s*)?([SABC])(?:\s*级)?$")


# ─────────────────────────── 纯函数（可自测） ───────────────────────────

def parse_token(raw: str) -> tuple[int, int, int] | None:
    """`"176 138 79"` → `(176, 138, 79)`；解析不出返回 None。"""
    parts = raw.strip().split()
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def expect_color(token_raw: str, alpha: float | None = None) -> str | None:
    """把令牌值算成浏览器 `getComputedStyle` 会给出的颜色串。

    ⚠️ 这是「不写死 hex」的落点：令牌变了（深色模式），期望值跟着变。
    """
    rgb = parse_token(token_raw)
    if rgb is None:
        return None
    r, g, b = rgb
    if alpha is None or alpha >= 1:
        return f"rgb({r}, {g}, {b})"
    return f"rgba({r}, {g}, {b}, {alpha})"


def variant_of_class(cls: str) -> str | None:
    """从 DOM class 反查 Badge 变体（只认底色唯一的那些）。"""
    for bg, variant in VARIANT_BY_BG_CLASS.items():
        if bg in cls:
            return variant
    return None


def judge_grade_bg(grade: str, actual_bg: str, tokens: dict[str, str]) -> tuple[bool, str]:
    """G1：等级徽章的底色必须等于 §2.4 指定令牌的 /15 版本。"""
    tok = SPEC_GRADE_TOKEN.get(grade)
    if tok is None:
        return True, ""                      # 不是 S/A/B/C，不判
    want = expect_color(tokens.get(tok, ""), BADGE_BG_ALPHA)
    if want is None:
        return False, f"令牌 --{tok} 读不到（期望值算不出来）"
    if actual_bg == want:
        return True, ""
    return False, (f"{grade} 级徽章底色 {actual_bg} ≠ §2.4 期望 "
                   f"rgba(--{tok} / {BADGE_BG_ALPHA}) = {want}")


def judge_badge_fg(cls: str, actual_color: str, tokens: dict[str, str]
                   ) -> tuple[bool | None, str]:
    """G4：Badge 的文字色必须等于其变体声明的令牌值。

    返回 `(None, ...)` 表示**量不到/不判**（default/neutral 无法区分，见坑 2）。
    """
    variant = variant_of_class(cls)
    if variant is None:
        return None, ""
    tok = BADGE_VARIANT_FG_TOKEN[variant]
    want = expect_color(tokens.get(tok, ""))
    if want is None:
        return False, f"令牌 --{tok} 读不到"
    if actual_color == want:
        return True, ""
    return False, (f"`{variant}` 变体徽章文字色 {actual_color} ≠ "
                   f"Badge.tsx 声明的 --{tok} = {want}")


def judge_letter(text: str, grade: str) -> tuple[bool, str]:
    """G3：双通道的「字母」那一半 —— 徽章文本必须含等级字母。"""
    if grade and grade in text:
        return True, ""
    return False, f"等级徽章文本 {text!r} 里没有字母 `{grade}`（双通道只剩颜色）"


def grade_of_text(text: str) -> str | None:
    m = GRADE_TEXT_RE.match(text.strip())
    return m.group(1) if m else None


# ─────────────────────────── 源码层 G2 ───────────────────────────

def scan_grade_tone_sources(root: pathlib.Path) -> list[tuple[str, int, str]]:
    """找出所有 `GRADE_TONE` 定义点，返回 [(相对路径, 行号, 变体映射体)]。"""
    out: list[tuple[str, int, str]] = []
    pats = ("apps/**/*.ts", "apps/**/*.tsx", "packages/**/*.ts", "packages/**/*.tsx")
    seen: set[pathlib.Path] = set()
    for pat in pats:
        for p in root.glob(pat):
            if p in seen or "node_modules" in p.parts or ".next" in p.parts:
                continue
            seen.add(p)
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"(?:export\s+)?const\s+GRADE_TONE\s*[:=]", src):
                line = src[:m.start()].count("\n") + 1
                body = src[m.start():m.start() + 400]
                out.append((str(p.relative_to(root)).replace("\\", "/"), line,
                            " ".join(body.split())[:180]))
    return sorted(out)


def grade_tone_variants(body: str) -> dict[str, str]:
    """从 `GRADE_TONE` 定义体里抽出 {等级: 变体名}。"""
    return {m.group(1): m.group(2)
            for m in re.finditer(r'([SABC])\s*:\s*"([a-z]+)"', body)}


# ─────────────────────────── 渲染层 ───────────────────────────

SCAN_JS = r"""(async () => {
  const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  await frame();
  const rootCS = getComputedStyle(document.documentElement);
  const tok = (n) => rootCS.getPropertyValue('--' + n).trim();
  const isBadge = (el) => {
    if (el.tagName !== 'SPAN') return false;
    const c = (el.className || '').toString();
    return c.includes('inline-flex') && c.includes('rounded-r1') && c.includes('border');
  };
  const vis = (el) => {
    const c = getComputedStyle(el);
    if (c.display === 'none' || c.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  };
  const rows = [];
  for (const el of document.querySelectorAll('span')) {
    if (!isBadge(el) || !vis(el)) continue;
    const c = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const cls = (el.className || '').toString();
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    rows.push({
      text: text,
      cls: cls.slice(0, 200),
      color: c.color,
      bg: c.backgroundColor,
      border: c.borderTopColor,
      box: Math.round(r.width) + 'x' + Math.round(r.height),
    });
  }
  const tokens = {};
  for (const n of %TOKENS%) tokens[n] = tok(n);
  return {
    dark: document.documentElement.classList.contains('dark'),
    href: location.href,
    tokens: tokens,
    rows: rows,
  };
})()""".replace("%TOKENS%", json.dumps(list(TOKEN_SNAPSHOT)))

RESOLVE_FIRST_CASE_JS = r"""(() => {
  // 路径 ①：列表里的 <a href="/cases/{id}">（im 端就是这样）
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href') || '';
    const m = /^\/cases\/([^\/?#]+)$/.exec(h);
    if (m && m[1] && m[1] !== '@first-case') return { id: m[1], how: 'anchor' };
  }
  // 路径 ②：**点第一个数据行**。
  //   lawyer 的 DataTable 用 React onClick 导航，DOM 里**没有 <a>**、也没有 id
  //   （实测：anchors=0、uuidLike=0，行是裸 <tr> + cursor:pointer）
  //   ⇒ 猜 id 会变成「编数据」；点真实入口才是**用户真实路径**，且失败有明确信号。
  const trs = [...document.querySelectorAll('tr')].filter(t => t.querySelector('td'));
  if (trs.length) {
    trs[0].click();
    return { id: null, how: 'clicked-row' };
  }
  return { id: null, how: 'none' };
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


async def resolve_path(b, base: str, path: str) -> str | None:
    """把 `@first-case` 占位符换成真实 id。

    ⚠️ 两端的列表结构**不一样**（实测）：
      · im：`<a href="/cases/10">` ⇒ 直接读 href
      · lawyer：裸 `<tr>` + React onClick，DOM 里**没有任何 id** ⇒ 只能**点行**
    第一版只认 `<a>`，于是在 lawyer 上报了 ENV（「读不到真实 id」）——
    那是**「我没测成」**，不是产品缺陷，所以如实报了 ENV 而不是编一个 id。
    现在补上「点行」这条路，两端都能覆盖。
    """
    if "@first-case" not in path:
        return path
    await b.goto(f"{base}/cases", wait=1.5)
    await b.settle(extra=2.0)
    r = await b.cdp.evaluate(RESOLVE_FIRST_CASE_JS) or {}
    how = r.get("how")
    cid = r.get("id")
    if how == "clicked-row":
        await asyncio.sleep(2.0)
        href = await b.cdp.evaluate("location.href") or ""
        m = re.search(r"/cases/([^/?#]+)/?$", href)
        cid = m.group(1) if m else None
    if not cid:
        return None
    return path.replace("@first-case", str(cid))


def judge_page(payload: dict) -> tuple[list[tuple[str, str]], dict]:
    """对一页的量测结果跑 G1/G3/G4，返回 (缺陷列表, 统计)。"""
    tokens = payload.get("tokens") or {}
    bad: list[tuple[str, str]] = []
    stat = {"badges": 0, "grades": 0, "g1_ok": 0, "g3_ok": 0,
            "g4_ok": 0, "g4_bad": 0, "g4_skip": 0}
    for r in payload.get("rows") or []:
        stat["badges"] += 1
        grade = grade_of_text(r.get("text", ""))
        if grade:
            stat["grades"] += 1
            ok, msg = judge_grade_bg(grade, r.get("bg", ""), tokens)
            if ok:
                stat["g1_ok"] += 1
            else:
                bad.append(("G1", f"「{r['text']}」{msg}"))
            ok, msg = judge_letter(r.get("text", ""), grade)
            if ok:
                stat["g3_ok"] += 1
            else:
                bad.append(("G3", msg))
        ok, msg = judge_badge_fg(r.get("cls", ""), r.get("color", ""), tokens)
        if ok is None:
            stat["g4_skip"] += 1
        elif ok:
            stat["g4_ok"] += 1
        else:
            # ⚠️ 失败必须计数：第一版只 append 不计数，导致「0 通过 / 51 不判」
            #    看起来像「几乎全没判」，实际是 74 个可识别变体**全部失败**。
            stat["g4_bad"] += 1
            bad.append(("G4", msg))
    return bad, stat


async def run_render(dump: bool) -> int:
    from cdp import Browser
    all_bad: list[tuple[str, str, str]] = []
    env_issues: list[str] = []
    tot = {"pages": 0, "pages_with_badge": 0, "badges": 0, "grades": 0,
           "g1_ok": 0, "g3_ok": 0, "g4_ok": 0, "g4_bad": 0, "g4_skip": 0}
    dumped: list[dict] = []

    async with Browser(headless=True, width=1280, height=900, device_scale_factor=1) as b:
        for app, spec in APPS.items():
            base = f"http://localhost:{spec['port']}"
            try:
                ok = await login(b, base, spec["user"])
            except Exception as e:                                    # noqa: BLE001
                env_issues.append(f"[{app}] 登录异常：{type(e).__name__}: {e}")
                continue
            if not ok:
                env_issues.append(f"[{app}] 登录失败（端口 {spec['port']} 未就绪？）")
                continue
            for path in spec["pages"]:
                real = await resolve_path(b, base, path)
                if real is None:
                    env_issues.append(f"[{app}] {path}：读不到真实 id（占位符解析失败）")
                    continue
                await b.goto(f"{base}{real}", wait=1.5)
                await b.settle(extra=2.2)
                payload = await b.cdp.evaluate(SCAN_JS)
                if not isinstance(payload, dict):
                    env_issues.append(f"[{app}] {path}：量测脚本没返回对象")
                    continue
                tot["pages"] += 1
                if dump:
                    dumped.append({"app": app, "path": path, **payload})
                bad, stat = judge_page(payload)
                for k in ("badges", "grades", "g1_ok", "g3_ok", "g4_ok", "g4_bad", "g4_skip"):
                    tot[k] += stat[k]
                if stat["badges"]:
                    tot["pages_with_badge"] += 1
                for crit, msg in bad:
                    all_bad.append((f"{app}{path}", crit, msg))
                flag = "✗" if bad else "·"
                print(f"  {flag} [{app}] {path:<22} Badge={stat['badges']:>3} "
                      f"等级={stat['grades']:>2} G1✓{stat['g1_ok']:>2} "
                      f"G3✓{stat['g3_ok']:>2} G4✓{stat['g4_ok']:>2}✗{stat['g4_bad']:>2}"
                      f"/不判{stat['g4_skip']:>2}  缺陷={len(bad)}")

    if dump:
        print("\n" + json.dumps(dumped, ensure_ascii=False, indent=2))

    print(f"\n跑成 {tot['pages']} 页（其中 {tot['pages_with_badge']} 页有 Badge）"
          f" · Badge {tot['badges']} 个 · 等级徽章 {tot['grades']} 个")
    print(f"  G1 底色符 §2.4：{tot['g1_ok']}/{tot['grades']}")
    print(f"  G3 字母通道：{tot['g3_ok']}/{tot['grades']}")
    print(f"  G4 文字色由变体决定：{tot['g4_ok']} 通过 / **{tot['g4_bad']} 失败**"
          f" / {tot['g4_skip']} 不判（default·neutral 底色相同，无法区分）")

    if env_issues:
        print(f"\n[ENV] 环境问题 {len(env_issues)} 条（**不算产品缺陷**）：")
        for e in env_issues:
            print(f"  ! {e}")

    if all_bad:
        # 按「判据 + 根因」聚类 —— 同一条根因会在很多页重复出现。
        # ⚠️ G1 的消息里带着徽章文本（`「B 级」` vs `「B」`），直接拿整条消息当 key
        #    会把同一根因拆成好几簇 ⇒ 必须先把文本差异归一掉。
        def cluster_key(crit: str, msg: str) -> str:
            if crit == "G1":
                m = re.search(r"([SABC])\s*级徽章底色", msg)
                return f"{m.group(1)} 级徽章的底色" if m else msg
            return msg

        groups: dict[tuple[str, str], list[str]] = {}
        for loc, crit, msg in all_bad:
            groups.setdefault((crit, cluster_key(crit, msg)), []).append(loc)
        print(f"\n  ✗ 渲染层缺陷 {len(all_bad)} 条，聚为 {len(groups)} 条根因：")
        for (crit, key), locs in sorted(groups.items()):
            uniq = sorted(set(locs))
            print(f"    [{crit}] {key}")
            print(f"          @ {len(uniq)} 处：{', '.join(uniq[:6])}"
                  + (" …" if len(uniq) > 6 else ""))
    else:
        print("\n  ✓ 渲染层未发现缺陷")

    if env_issues and tot["pages"] == 0:
        return 2
    return 1 if all_bad else 0


# ─────────────────────────── 源码层执行 ───────────────────────────

def badge_variant_names(root: pathlib.Path) -> set[str]:
    """`Badge.tsx` 里实际存在的变体名（用于判断「§2.4 的色有没有现成变体可用」）。"""
    p = root / "packages/ui/src/components/Badge.tsx"
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    m = re.search(r"const variantStyles[^{]*\{(.*?)\n\};", src, re.S)
    if not m:
        return set()
    return set(re.findall(r"^\s*([A-Za-z][\w-]*)\s*:", m.group(1), re.M))


def run_source(root: pathlib.Path) -> tuple[list[tuple[str, str, str]], dict]:
    bad: list[tuple[str, str, str]] = []
    sites = scan_grade_tone_sources(root)
    print(f"  GRADE_TONE 定义点 {len(sites)} 处：")
    for rel, line, body in sites:
        print(f"    · {rel}:{line}")

    if len(sites) > 1:
        in_ui = [s for s in sites if s[0].startswith("packages/ui/")]
        msg = (f"`GRADE_TONE`（等级→变体映射）有 {len(sites)} 份定义，"
               f"其中 `packages/ui` 里 {len(in_ui)} 份"
               f"（应集中到 packages/ui，避免同一语义 N 份来源）")
        bad.append(("·", "G2", msg))

    if not sites:
        return bad, {"sites": 0}

    # 6 份之间是否已经漂移？（当前实测一致 —— 但要能检出漂移）
    all_maps = [grade_tone_variants(s[2]) for s in sites]
    distinct = {tuple(sorted(d.items())) for d in all_maps}
    if len(distinct) > 1:
        bad.append(("·", "G2", f"`GRADE_TONE` 的 {len(sites)} 份定义**已经互相漂移**："
                               f"{[dict(d) for d in distinct]}"))
        print(f"    ✗ 已漂移：{len(distinct)} 种不同的映射")
    else:
        print(f"    ✓ {len(sites)} 份内容一致（尚未漂移）")

    variants = all_maps[0]
    print(f"    映射体：{variants}")
    badge_vs = badge_variant_names(root)
    print(f"    Badge.tsx 可用变体：{sorted(badge_vs)}")

    # 与 §2.4 对照。
    # ⚠️ 规范**只规定了颜色**，没规定「用哪个 Badge 变体」——
    #    所以「期望变体名」是从「变体名 = 令牌前缀」这个**约定**推出来的，
    #    不能当成规范原文来断言。这里改成**报缺口**：§2.4 的色在组件层有没有现成变体。
    for grade, tok in SPEC_GRADE_TOKEN.items():
        want_variant = tok.split("-")[0]     # gold-500→gold / brand-600→brand / ink-400→ink
        got = variants.get(grade)
        if got is None:
            continue
        if got == want_variant:
            print(f"    ✓ {grade} 级：映射到 `{got}`，与 §2.4 的 --{tok} 同前缀")
        elif got == "primary" and tok == "brand-600":
            print(f"    △ {grade} 级：映射到 `primary`（底色 brand-500/15，非 §2.4 的 brand-600）")
        else:
            exists = want_variant in badge_vs
            note = "" if exists else " ← 组件层**没有**该变体（缺口）"
            print(f"    ✗ {grade} 级：映射到 `{got}`，§2.4 要求 --{tok} 系"
                  f"（变体 `{want_variant}`{'' if exists else '，不存在'}）{note}")
    return bad, {"sites": len(sites)}


# ─────────────────────────── 自测 ───────────────────────────

LIGHT_TOKENS: dict[str, str] = {
    "gold-500": "176 138 79", "danger-500": "192 57 43", "brand-600": "39 76 147",
    "brand-500": "54 96 176", "ink-400": "154 163 174", "ink-500": "107 116 128",
    "ink-600": "74 82 92", "ink-700": "52 59 68", "info-600": "34 92 147",
    "danger-600": "166 47 35", "pending-600": "143 95 20", "verified-600": "22 112 85",
    "ai-600": "91 69 176", "gold-700": "112 86 46", "link": "39 76 147",
    "surface-subtle": "239 241 243",
}
DARK_TOKENS: dict[str, str] = {
    "gold-500": "196 158 98", "danger-500": "214 84 72", "brand-600": "58 99 176",
    "brand-500": "78 119 196", "ink-400": "126 135 149", "ink-500": "154 163 174",
    "ink-600": "180 188 198", "ink-700": "205 211 218", "info-600": "132 186 230",
    "danger-600": "232 122 110", "pending-600": "218 168 78", "verified-600": "82 196 160",
    "ai-600": "176 160 238", "gold-700": "230 204 160", "link": "138 168 224",
    "surface-subtle": "26 31 38",
}


def self_test() -> int:
    n = 0
    fails: list[str] = []

    def arm(name: str, got, want) -> None:
        nonlocal n
        n += 1
        if got != want:
            fails.append(f"{name}: got={got!r} want={want!r}")

    # ── 纯函数：expect_color ──
    arm("expect_color 不透明", expect_color("239 241 243"), "rgb(239, 241, 243)")
    arm("expect_color /15", expect_color("176 138 79", 0.15), "rgba(176, 138, 79, 0.15)")
    arm("expect_color 坏输入", expect_color("bogus"), None)
    arm("parse_token 三段", parse_token("1 2 3"), (1, 2, 3))
    arm("parse_token 少段", parse_token("1 2"), None)

    # ── G1 红臂：S 级当前用 danger 底色（实测就是这个）──
    ok, _ = judge_grade_bg("S", expect_color(LIGHT_TOKENS["danger-500"], 0.15), LIGHT_TOKENS)
    arm("G1-a S 用 danger 底色 ⇒ 红", ok, False)
    # ── G1 绿臂：S 级用 gold-500/15 ──
    ok, _ = judge_grade_bg("S", expect_color(LIGHT_TOKENS["gold-500"], 0.15), LIGHT_TOKENS)
    arm("G1-b S 用 gold 底色 ⇒ 绿", ok, True)
    # ── G1 红臂：B 级用 primary(brand-500) 底色 —— 接近但仍不符 §2.4 的 brand-600 ──
    ok, msg = judge_grade_bg("B", expect_color(LIGHT_TOKENS["brand-500"], 0.15), LIGHT_TOKENS)
    arm("G1-c B 用 brand-500 ⇒ 红", ok, False)
    arm("G1-c2 报出的是 brand-600", "brand-600" in msg, True)
    # ── G1 绿臂：C 级用 ink-400/15 ──
    ok, _ = judge_grade_bg("C", expect_color(LIGHT_TOKENS["ink-400"], 0.15), LIGHT_TOKENS)
    arm("G1-d C 用 ink-400 底色 ⇒ 绿", ok, True)
    # ── 🚨 控制臂：同一断言在深色令牌下**也必须过**（证明没写死 hex）──
    ok, _ = judge_grade_bg("S", expect_color(DARK_TOKENS["gold-500"], 0.15), DARK_TOKENS)
    arm("G1-e 控制臂：深色下 S 用深色 gold-500 ⇒ 绿", ok, True)
    ok, _ = judge_grade_bg("S", expect_color(LIGHT_TOKENS["gold-500"], 0.15), DARK_TOKENS)
    arm("G1-f 控制臂：拿浅色 gold 值喂深色令牌 ⇒ 红（证明真在按令牌判）", ok, False)
    # ── G1 未知等级不判 ──
    ok, _ = judge_grade_bg("Z", "rgb(0, 0, 0)", LIGHT_TOKENS)
    arm("G1-g 非 S/A/B/C 不判", ok, True)

    # ── G3 字母通道 ──
    arm("G3-a 'S 级' 含字母", judge_letter("S 级", "S")[0], True)
    arm("G3-b '等级 B' 含字母", judge_letter("等级 B", "B")[0], True)
    arm("G3-c 纯色块无字母 ⇒ 红", judge_letter("", "A")[0], False)
    arm("G3-d 文本不含该字母 ⇒ 红", judge_letter("等级", "C")[0], False)

    # ── grade_of_text ──
    arm("text 'S'", grade_of_text("S"), "S")
    arm("text 'S级'", grade_of_text("S级"), "S")
    arm("text 'S 级'", grade_of_text("S 级"), "S")
    arm("text '等级 A'", grade_of_text("等级 A"), "A")
    arm("text '待复核' 不是等级", grade_of_text("待复核"), None)
    arm("text 'S 级案件' 不是等级徽章", grade_of_text("S 级案件"), None)

    # ── variant_of_class ──
    cls_info = ("inline-flex items-center whitespace-nowrap border font-medium rounded-r1 "
                "bg-info-500/15 border-info-500/30 px-1.5 py-0.5 text-caption gap-1")
    arm("variant_of_class info", variant_of_class(cls_info), "info")
    cls_gold = "inline-flex rounded-r1 border bg-gold-500/15 text-caption"
    arm("variant_of_class gold", variant_of_class(cls_gold), "gold")
    cls_neutral = "inline-flex rounded-r1 border bg-surface-subtle text-caption"
    arm("variant_of_class neutral ⇒ None（不判）", variant_of_class(cls_neutral), None)

    # ── G4 红臂：info 变体文字色是继承的 ink-700（实测就是这个）──
    ok, _ = judge_badge_fg(cls_info, expect_color(LIGHT_TOKENS["ink-700"]), LIGHT_TOKENS)
    arm("G4-a info 变体文字色=继承 ink-700 ⇒ 红", ok, False)
    # ── G4 绿臂：info 变体文字色 = info-600 ──
    ok, _ = judge_badge_fg(cls_info, expect_color(LIGHT_TOKENS["info-600"]), LIGHT_TOKENS)
    arm("G4-b info 变体文字色=info-600 ⇒ 绿", ok, True)
    # ── 🚨 控制臂：深色下同一断言也必须自洽 ──
    ok, _ = judge_badge_fg(cls_info, expect_color(DARK_TOKENS["info-600"]), DARK_TOKENS)
    arm("G4-c 控制臂：深色 info-600 ⇒ 绿", ok, True)
    ok, _ = judge_badge_fg(cls_info, expect_color(LIGHT_TOKENS["info-600"]), DARK_TOKENS)
    arm("G4-d 控制臂：浅色值喂深色令牌 ⇒ 红", ok, False)
    # ── G4 primary 用 --link 而非 --brand-600（坑 5）──
    cls_pri = "inline-flex rounded-r1 border bg-brand-500/15 text-caption"
    ok, _ = judge_badge_fg(cls_pri, expect_color(LIGHT_TOKENS["link"]), LIGHT_TOKENS)
    arm("G4-e primary 文字色=--link ⇒ 绿", ok, True)
    # 坑 5：primary 的文字色令牌是 `--link`；浅色下 --link == --brand-600（都是 #274C93），
    #       深色下才分叉（--link #8AA8E0 vs --brand-600 #3A63B0）
    #       ⇒ 这条臂**必须用深色令牌**才测得出「走的是 link 而不是 brand-600」。
    ok, _ = judge_badge_fg(cls_pri, expect_color(DARK_TOKENS["brand-600"]), DARK_TOKENS)
    arm("G4-f 深色下 primary 文字色=brand-600 ⇒ 红（必须走 --link）", ok, False)
    ok, _ = judge_badge_fg(cls_pri, expect_color(DARK_TOKENS["link"]), DARK_TOKENS)
    arm("G4-f2 深色下 primary 文字色=--link ⇒ 绿", ok, True)
    # ── G4 不判臂 ──
    ok, _ = judge_badge_fg(cls_neutral, "rgb(0, 0, 0)", LIGHT_TOKENS)
    arm("G4-g neutral ⇒ 不判(None)", ok, None)
    # ── G4 gold ──
    ok, _ = judge_badge_fg(cls_gold, expect_color(LIGHT_TOKENS["gold-700"]), LIGHT_TOKENS)
    arm("G4-h gold 变体文字色=gold-700 ⇒ 绿", ok, True)

    # ── 源码层：GRADE_TONE 解析 ──
    body = ('const GRADE_TONE: Record<string, BadgeProps["variant"]> = '
            '{ S: "danger", A: "pending", B: "info", C: "neutral", };')
    arm("grade_tone_variants", grade_tone_variants(body),
        {"S": "danger", "A": "pending", "B": "info", "C": "neutral"})

    print(f"自测 {n - len(fails)}/{n} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    return 0 if not fails else 1


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="§2.4 案件等级徽章门禁")
    ap.add_argument("--self-test", action="store_true", help="纯函数自测，不起浏览器")
    ap.add_argument("--source-only", action="store_true", help="只扫源码层")
    ap.add_argument("--dump", action="store_true", help="打印原始量测 JSON")
    ap.add_argument("--why", action="store_true", help="打印判据出处")
    a = ap.parse_args()

    if a.why:
        print("§2.4 出处（§2.4「案件等级」）：")
        for g, tok in SPEC_GRADE_TOKEN.items():
            print(f"  {g} → --{tok}")
        print("\nBadge 变体文字色出处（Badge.tsx:39-56）：")
        for v, tok in BADGE_VARIANT_FG_TOKEN.items():
            print(f"  {v} → --{tok}")
        vs = badge_variant_names(FE)
        print(f"\nBadge.tsx 实际可用变体：{sorted(vs)}")
        print("§2.4 四色在组件层的可用性（★ = 有现成变体）：")
        for g, tok in SPEC_GRADE_TOKEN.items():
            pre = tok.split("-")[0]
            print(f"  {g} 级 --{tok} → 变体 `{pre}`："
                  f"{'★ 有' if pre in vs else '✗ 组件层没有（缺口）'}")
        return 0

    if a.self_test:
        return self_test()

    print("§2.4「案件等级」门禁")
    print("\n── 源码层 G2：等级→变体映射的来源数 ──")
    src_bad, _ = run_source(FE)
    if src_bad:
        for loc, crit, msg in src_bad:
            print(f"  ✗ [{crit}] {msg}")
    else:
        print("  ✓ 单一来源")

    if a.source_only:
        return 1 if src_bad else 0

    print("\n── 渲染层 G1/G3/G4（26 页）──")
    rc = asyncio.run(run_render(a.dump))
    if rc == 2:
        return 2
    return 1 if (src_bad or rc == 1) else 0


if __name__ == "__main__":
    sys.exit(main())

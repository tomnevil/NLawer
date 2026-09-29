"""像素级视觉基线回归（真实 Chromium + CDP）。

**为什么需要**：`verify_render_e2e.py` 是**单点断言**（computed style + DOM 文本），
样式重构导致的「整体位移 / 间距漂移」它一条都抓不到。本脚本做逐像素比对。

**基线有效性边界（不理解就会天天误报）**：

1. **只在同一数据集下可比**。数据一换画面必然变，但那不是样式漂移
   ⇒ 基线里存了**数据指纹**，指纹不一致时**退出码 2**，不判定为产品缺陷。
2. 只截**视口**（不截整页）：整页高度随内容变化，尺寸一变比对就失去意义。
3. 已冻结动画 / 过渡 / 光标 / 滚动条，并置 `prefers-reduced-motion: reduce`。
4. 跨机器仍可能因**字体差异**失配 —— 基线请在同一台机器上重建。
5. 🚨 **必须同一种服务模式**（`next dev` vs `next start`）。dev 会挂一个**开发工具徽标**
   （`<nextjs-portal>`，左下角 ~38×38 CSS px），生产构建**没有** ⇒ 拿 dev 的基线
   与 prod 的当前比对，**每一页**都会产生 0.3–0.5% 的漂移。
   **实测（2026-09-26）：9/16 页被判成「产品缺陷」，逐页裁图确认全部只是那个徽标**
   —— 这是一次**假红**（把「测量模式不同」报成了「产品坏了」）。
   ⇒ 模式写进清单（`server_mode`）并在比对时**断言**，不一致时 **exit 2**（不可比）。

用法：
    python evidence/visual_baseline.py --capture     # 建立 / 刷新基线
    python evidence/visual_baseline.py               # 与基线比对
    python evidence/visual_baseline.py --self-test   # **自检**：注入已知变化，断言必须 FAIL

退出码：`0` 通过 / `1` 视觉漂移超阈值 / `2` 环境问题（服务未起、无基线、指纹不一致）

> **`--self-test` 为什么必要**：只证明「16/16 一致」是不够的——
> 一个永远返回「一致」的比对器同样会打印 16/16。必须先证明**它真的会 FAIL**，
> 「一致」才含信息量。（同 G.6 安全区对照组 / F.7 非空锁 / H.7 前提锁。）
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402
from envprobe import ADMIN, check_services, describe_fingerprint, fingerprint  # noqa: E402

BASE_DIR = HERE / "visual_baseline"
CUR_DIR = BASE_DIR / "_current"
MANIFEST = BASE_DIR / "manifest.json"

# 逐像素灰度差 ≤ TOL 视为噪声（抗锯齿 / 亚像素渲染）
TOL = 12
# 允许的差异像素占比上限（0.2%）
MAX_RATIO = 0.002

PROFILES: dict[str, dict] = {
    "mobile": {"width": 390, "height": 844, "dsf": 3, "mobile": True},
    "desktop": {"width": 1280, "height": 900, "dsf": 2, "mobile": False},
}

# 登录页要在登录**之前**截，所以单独列出来
LOGIN = ("login", "/login")
APP_PAGES = (
    ("cockpit", "/"),
    ("reviews", "/reviews"),
    ("cases", "/cases"),
    ("dispatches", "/dispatches"),
    ("compliance", "/compliance"),
    ("billing", "/billing"),
    ("complaints", "/complaints"),
)

# ⚠️ **刻意排除的页面**（不是漏了）。
#
# `/audit` —— 页面上「审计记录总量」会被**采集动作自己**推高，形成观察者效应：
# 实测（`evidence/probe_audit_effect.py`）导航前 total=96、导航后 97，
# 而且**只有第一次导航增加了 1**，后续 7 次导航不再变化
# ⇒ 写审计日志的是**登录**，不是每次页面加载。
# 于是每跑一次采集（2 个视口 = 2 次登录）这一页就多几行，**自己把自己测脏**。
# 要覆盖它得先把那个计数区域 mask 掉——那是另一件事，别在这里假装它稳定。
EXCLUDED = {
    "/audit": "登录会写审计日志 ⇒ 「审计记录总量」随每次采集单调增长（观察者效应）",
}

# ── 自检（fault injection）──────────────────────────────────────────
#
# **为什么需要**：只证明「16/16 一致」是不够的——一个永远返回「一致」的比对器
# 同样会打印 16/16。必须证明**它真的会 FAIL**，否则「与基线一致」不含信息量。
# 这就是本项目在别处反复用的「对照组」思路（G.6 安全区对照组 / F.7 非空锁 / H.7 前提锁）。
#
# 注入一段**局部且明显**的变化：顶栏变红。
# 选它是因为它既能触发 FAIL，又能验证**定位**——报告的 bbox 应当落在顶栏那一条。
SELF_TEST_CSS = "header{background:#ff2d55!important}"

#: 模式前提锁的**纯函数自检臂**：`(基线模式, 当前模式, 说明, 期望是否报出)`。
#:
#: 🚨 **两个方向都要有**：只测 `dev→prod` 的话，把比较写成 `!=` 或 `==` 都过不了；
#: 但若只留一个方向，把条件写反（`base == cur` 时报警）**照样全绿**。
#: 另外 `unknown` 两臂是**防「不猜」被改坏**：探测失败时必须**不判**。
MODE_SELFTEST: tuple[tuple[str, str, str, bool], ...] = (
    ("dev", "prod", "dev↔prod 必须报出（**本次 9/16 假红的根因**）", True),
    ("prod", "dev", "反向同样必须报出（不能只判一个方向）", True),
    ("prod", "prod", "同模式 ⇒ 必须不报（对照）", False),
    ("unknown", "prod", "基线模式未知 ⇒ **不报**（不猜）", False),
    ("prod", "unknown", "当前模式未知 ⇒ **不报**（不猜）", False),
)


def _css_script(css: str) -> str:
    """把一段 CSS 注入每个新文档（与 `cdp.FREEZE_JS` 同法）。"""
    return (
        "(() => { const add = () => { const s = document.createElement('style');"
        " s.setAttribute('data-injected', '1'); s.textContent = %s;"
        " (document.head || document.documentElement).appendChild(s); };"
        " if (document.head) add(); else document.addEventListener('DOMContentLoaded', add); })()"
        % json.dumps(css)
    )


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ── 服务模式探测（前提锁用）────────────────────────────────────────
#
# **为什么需要**：dev 与 prod 的**画面本来就不同**（dev 挂开发工具徽标），
# 拿一种模式的基线去比另一种模式的当前，得到的差异**不是样式漂移**。
# 实测（2026-09-26）：9/16 页被误判成「产品缺陷」，裁图确认**全部只是那个徽标**。
#
# 标记怎么选出来的（**实测，不是猜**）：
#   · `document.querySelector('nextjs-portal')` ⇒ dev **true** / prod **false** ✅ 采用
#   · `GET /_next/webpack-hmr` 与 `GET /__nextjs_original-stack-frame`
#     ⇒ dev 下**都是 404**（与 prod 无区别）⇒ **HTTP 探测这条路不通**，别改回去
SERVER_MODE_JS = (
    "(() => (document.querySelector('nextjs-portal') ? 'dev' : 'prod'))()"
)


async def detect_server_mode() -> str:
    """返回 `'dev'` / `'prod'` / `'unknown'`。

    ⚠️ 探测失败返回 **`unknown`**，**不猜** —— 猜错会让前提锁**静默失效**
    （或者更糟：把「我没测成」当成「模式一致」）。`unknown` 时两边都不断言。
    """
    try:
        async with Browser(headless=True, width=1280, height=900) as b:
            await b.goto(f"{ADMIN}/login", wait=2.0)
            await b.settle(extra=1.5)
            v = await b.cdp.evaluate(SERVER_MODE_JS)
            return v if v in ("dev", "prod") else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def mode_mismatch(base_mode: str, cur_mode: str) -> str | None:
    """模式前提锁的**纯判定**（抽出来才能被自检直接喂数据）。

    返回 `None` = 不断言（一致，或任一侧 `unknown`）；否则返回**该报出的原因**。

    ⚠️ **`unknown` 一律不断言**，这是刻意的：探测失败时「断言一致」是**假绿**
    （把「我没测成」读成「模式相同」），断言不一致是**假红**。两条都比「不判」更糟。
    """
    if base_mode == "unknown" or cur_mode == "unknown":
        return None
    if base_mode == cur_mode:
        return None
    return (f"基线是 `{base_mode}`、当前是 `{cur_mode}` ⇒ 画面差异**无法归因于样式**"
            f"（dev 会挂开发工具徽标 `<nextjs-portal>`，生产构建没有）")


async def login(b: Browser) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    await b.goto(f"{ADMIN}/login", wait=2.0)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def shoot(b: Browser, path: str, dest: pathlib.Path) -> None:
    await b.goto(f"{ADMIN}{path}", wait=1.5)
    await b.settle(extra=2.0)
    await b.screenshot(dest, full=False)


async def capture_profile(
    pname: str, prof: dict, out_dir: pathlib.Path, inject_css: str = ""
) -> dict[str, str]:
    """采集一个视口下的全部页面，返回 `{key: 文件名}`。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: dict[str, str] = {}

    async with Browser(
        headless=True,
        width=prof["width"],
        height=prof["height"],
        device_scale_factor=prof["dsf"],
    ) as b:
        await b.apply_device(mobile=prof["mobile"])
        await b.freeze_animations()  # ⚠️ 必须在首次 goto 之前
        if inject_css:
            await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=_css_script(inject_css))

        name, path = LOGIN
        dest = out_dir / f"{pname}__{name}.png"
        await shoot(b, path, dest)
        shots[f"{pname}__{name}"] = dest.name

        if not await login(b):
            raise RuntimeError(f"{pname}: 登录失败，受保护页面无法采集")

        for name, path in APP_PAGES:
            dest = out_dir / f"{pname}__{name}.png"
            await shoot(b, path, dest)
            shots[f"{pname}__{name}"] = dest.name

    return shots


def diff_one(base: pathlib.Path, cur: pathlib.Path) -> dict:
    from PIL import Image, ImageChops

    a = Image.open(base).convert("RGB")
    c = Image.open(cur).convert("RGB")
    if a.size != c.size:
        return {"size_changed": True, "old": a.size, "new": c.size, "ratio": 1.0, "count": -1, "bbox": None}

    d = ImageChops.difference(a, c).convert("L")
    mask = d.point(lambda v: 255 if v > TOL else 0)
    count = mask.histogram()[255]
    total = a.size[0] * a.size[1]
    return {
        "size_changed": False,
        "ratio": count / total,
        "count": count,
        "total": total,
        "bbox": mask.getbbox(),
    }


def write_artifact(key: str, base: pathlib.Path, cur: pathlib.Path,
                   out_dir: pathlib.Path | None = None) -> pathlib.Path:
    """并排（基线 / 当前 / 差异热力图），便于人眼确认漂移在哪。

    ⚠️ `out_dir` **必须由调用方给**：自检（注入已知变化）的产物**不能写进基线目录** ——
    那会在「刚刚重建的干净基线」里留一张**假差异图**（红顶栏是注入的，不是真漂移），
    下一个人打开目录会把它读成真实证据。

    ⚠️ 也**不要直接落 `_current/`**（2026-09-26 端到端实测踩到）：`_current` 的语义是
    「**最近一次运行的当前截图**」，而 `compare` 全绿时**不会**清理旧的 `diff_*.png`
    ⇒ 那张注入产生的假差异图会**长期滞留**，被下一个人读成「当前有漂移」。
    ⇒ 自检产物落 `_current/_selftest/`（子目录语义自明：这批图是注入出来的）。
    """
    from PIL import Image, ImageChops

    a = Image.open(base).convert("RGB")
    c = Image.open(cur).convert("RGB")
    if a.size != c.size:
        c = c.resize(a.size)
    d = ImageChops.difference(a, c).convert("L").point(lambda v: 255 if v > TOL else 0)
    heat = Image.merge("RGB", (d, Image.new("L", a.size), Image.new("L", a.size)))

    gap = 16
    canvas = Image.new("RGB", (a.width * 3 + gap * 4, a.height + gap * 2), (24, 24, 28))
    for i, img in enumerate((a, c, heat)):
        canvas.paste(img, (gap + i * (a.width + gap), gap))

    out = (out_dir or BASE_DIR) / f"diff_{key}.png"
    out.parent.mkdir(parents=True, exist_ok=True)   # 自检落 `_current/_selftest/`，可能尚不存在
    canvas.save(out)
    return out


def write_manifest(fp: dict, shots: dict[str, str], out_dir: pathlib.Path,
                   server_mode: str = "unknown") -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    # 清掉不在新集合里的旧基线图。**只动本目录、只删自己生成的 `.png`**，
    # 数量 = 被移除的页面数（1~2 个），与「禁止批量删除」的硬规则不冲突。
    keep = set(shots.values())
    for p in sorted(BASE_DIR.glob("*.png")):
        if p.name not in keep:
            print(f"  移除过期基线图：{p.name}")
            p.unlink()
    MANIFEST.write_text(
        json.dumps(
            {
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "admin": ADMIN,
                "profiles": PROFILES,
                "tol": TOL,
                "max_ratio": MAX_RATIO,
                # 🚨 服务模式（`dev` / `prod` / `unknown`）。**必须写进清单**：
                #    不写，dev↔prod 的模式差就会在每次比对里伪装成「产品缺陷」（见文件头边界 5）。
                "server_mode": server_mode,
                "fingerprint": fp,
                "pages": shots,
                "sha256": {k: sha256(out_dir / v) for k, v in shots.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def run(capture: bool, inject_css: str = "", self_test: bool = False) -> int:
    # ── 自检 A：**纯函数**（模式前提锁）。不需要浏览器/服务 ⇒ 先跑。──
    # 为什么必须有这一节：模式锁是本次 9/16 假红的修复，**它自己坏了没人知道**
    # （一个恒返回 None 的 mode_mismatch 会让假红原样复发）。
    if self_test:
        print("── 自检 A：纯函数（模式前提锁）──")
        bad_mode: list[str] = []
        for base, cur, desc, expect in MODE_SELFTEST:
            got = mode_mismatch(base, cur) is not None
            ok = got == expect
            print(f"  [{'OK  ' if ok else 'FAIL'}] 基线={base:7s} 当前={cur:7s} "
                  f"期望{'报出' if expect else '不报'} —— {desc}")
            if not ok:
                bad_mode.append(f"{base}/{cur}")
        if bad_mode:
            print(f"\n❌ 自检 A 失败：{len(bad_mode)} 臂 ⇒ **模式前提锁逻辑坏了**。")
            print("   它坏了不会自己报警，只会让「dev↔prod 假红」原样复发。")
            return 1
        print(f"  ✅ 自检 A 通过（{len(MODE_SELFTEST)} 臂）\n")

    print("── 前置检查 ──")
    problems = check_services()
    if problems:
        for p in problems:
            print(f"  [ENV ] {p}")
        print("\n服务未就绪 ⇒ 不启动浏览器（跑下去产出的差异全是假的）。")
        return 2

    try:
        fp = fingerprint()
    except Exception as e:  # noqa: BLE001
        print(f"  [ENV ] 数据指纹获取失败：{type(e).__name__}: {e}")
        return 2
    print(f"  [data] {describe_fingerprint(fp)}")

    manifest: dict | None = None
    if not capture:
        if not MANIFEST.exists():
            print("\n[ENV ] 还没有基线 ⇒ 先跑 `--capture` 建立基线。")
            return 2
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("fingerprint") != fp:
            print("\n[ENV ] 数据指纹与基线不一致 ⇒ 画面变化**无法归因于样式**，不判定为产品缺陷。")
            print(f"        基线: {describe_fingerprint(manifest.get('fingerprint') or {})}")
            print(f"        当前: {describe_fingerprint(fp)}")
            print("        若确认是有意的数据变更，请重建基线：`--capture`")
            return 2
        print(f"  [ ok ] 数据指纹与基线一致（基线建于 {manifest.get('created_at')}）")

    # 🚨 **前提锁 2：服务模式必须一致**（见文件头边界 5）。
    #    dev 挂开发工具徽标、prod 没有 ⇒ 模式不同时**每一页**都会漂移 0.3–0.5%，
    #    那是「测量模式不同」，**不是产品变了**。实测一次 9/16 页假红。
    cur_mode = await detect_server_mode()
    base_mode = "unknown" if capture else (manifest or {}).get("server_mode") or "unknown"
    print(f"  [mode] 服务模式：基线={base_mode} · 当前={cur_mode}")
    why = mode_mismatch(base_mode, cur_mode)
    if why:
        print("\n[ENV ] **服务模式与基线不一致** ⇒ 画面差异**无法归因于样式**，不判定为产品缺陷。")
        print(f"        {why}")
        print("        ⇒ 用**与基线相同的模式**重跑；若确认是有意的模式切换，重建基线：`--capture`")
        return 2

    out_dir = BASE_DIR if capture else CUR_DIR
    label = "建立基线" if capture else ("自检（注入已知变化）" if self_test else "与基线比对")
    print(f"\n── 采集截图（{label}）──")
    if inject_css:
        print(f"  ⚠️ 已注入额外 CSS：{inject_css}")
    shots: dict[str, str] = {}
    for pname, prof in PROFILES.items():
        print(f"  [{pname}] {prof['width']}x{prof['height']} @{prof['dsf']}x")
        got = await capture_profile(pname, prof, out_dir, inject_css)
        shots.update(got)
        print(f"           {len(got)} 张")

    if EXCLUDED:
        print("\n  ⚠️ 刻意未纳入基线的页面（不是漏了）：")
        for path, why in EXCLUDED.items():
            print(f"     {path} —— {why}")

    if capture:
        write_manifest(fp, shots, out_dir, cur_mode)
        print(f"\n基线已写入 {MANIFEST}（{len(shots)} 张 · 服务模式 `{cur_mode}`）")
        return 0

    print("\n── 逐像素比对 ──")
    base_pages: dict[str, str] = (manifest or {}).get("pages", {})
    failed: list[tuple[str, str]] = []
    for key in sorted(shots):
        if key not in base_pages:
            print(f"  [FAIL] {key}  基线里没有这一页（新增页面）")
            failed.append((key, "基线里没有这一页（新增页面）"))
            continue
        r = diff_one(BASE_DIR / base_pages[key], CUR_DIR / shots[key])
        if r["size_changed"]:
            print(f"  [FAIL] {key}  尺寸变化 {r['old']} → {r['new']}")
            failed.append((key, f"尺寸变化 {r['old']} → {r['new']}"))
            continue
        pct = r["ratio"] * 100
        if r["ratio"] > MAX_RATIO:
            print(f"  [FAIL] {key}  差异 {pct:.3f}%  ({r['count']}/{r['total']} px)  bbox={r['bbox']}")
            # 自检会**故意**让十几页同时失败；只留第一张对比图作证据，
            # 免得刷出十几 MB 噪声（那些图还画着假的红色顶栏，容易误导）。
            if not self_test or not failed:
                art = write_artifact(key, BASE_DIR / base_pages[key], CUR_DIR / shots[key],
                                     out_dir=CUR_DIR / "_selftest" if self_test else BASE_DIR)
                print(f"         对比图：{art}")
            failed.append((key, f"差异 {pct:.3f}%  bbox={r['bbox']}"))
        else:
            print(f"  [ ok ] {key}  差异 {pct:.4f}%")

    if self_test:
        print("\n" + "=" * 68)
        print(f"自检：已注入已知视觉变化 —— {SELF_TEST_CSS}")
        if failed:
            print(f"✅ 自检通过：{len(failed)}/{len(shots)} 页被判为漂移 ⇒ **这个比对器真的会 FAIL**。")
            print("   （只证明「一致」是不够的：永远返回「一致」的比对器也会打印 16/16。）")
            print("=" * 68)
            return 0
        print("❌ 自检失败：注入了已知变化，却**没有任何一页被判为漂移**。")
        print("   ⇒ 比对逻辑失效（阈值过大 / 注入没生效 / 截的不是同一个页面）,")
        print("     此时「与基线一致」这个结论**不可信**。")
        print("=" * 68)
        return 1

    print("\n" + "=" * 68)
    print(f"结果：{len(shots) - len(failed)}/{len(shots)} 页与基线一致")
    if failed:
        print("\n视觉漂移（**产品缺陷**）：")
        for k, why in failed:
            print(f"  - {k}: {why}")
        print("=" * 68)
        print("=> 退出码 1：视觉漂移超阈值")
        return 1
    print("=" * 68)
    print("=> 退出码 0：与基线一致")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="像素级视觉基线回归")
    ap.add_argument("--capture", action="store_true", help="建立/刷新基线（会覆盖旧基线）")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="自检：注入已知视觉变化，断言比对**必须** FAIL（证明这个工具真的会失败）",
    )
    ap.add_argument("--inject-css", default="", help="调试用：向每个页面注入额外 CSS")
    args = ap.parse_args()

    if args.self_test and args.capture:
        print("`--self-test` 与 `--capture` 互斥（自检必须比对已有基线）。")
        return 2

    inject = args.inject_css or (SELF_TEST_CSS if args.self_test else "")
    return asyncio.run(run(args.capture, inject, args.self_test))


if __name__ == "__main__":
    sys.exit(main())

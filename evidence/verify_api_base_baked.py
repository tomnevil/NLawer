"""门禁：**构建产物里必须烘入正确的 API 基址**。

## 为什么需要这条门禁

`packages/sdk/src/index.ts:28`：

    export const API_BASE =
      (process.env.NEXT_PUBLIC_API_BASE) || "http://localhost:8000";

`NEXT_PUBLIC_*` 是**构建期内联**的。若 `.env.local` 缺失（或被删），
Next **不会报错**：`next build` 依然 `exit 0`、路由依然 HTTP 200、
所有静态门禁（`!important` / 色系 / 令牌）**全部照过**——
但整个产物指向 `:8000`（本仓库后端在 **:8001**），
表现为**浏览器里点「登录」毫无反应**。

**2026-09-19 实测**：`apps/im/.env.local` **从未被创建**（另三端都有）。
im 的 dev server 之所以正常，是因为**启动命令里 export 了该变量**；
而 `_prev_build/im-prod-build-102012` 那份「已验证通过」的生产产物
其实**烘的是 `:8000`** —— 它从来没有能力连上后端。

> ⇒ **「构建 exit 0 + 路由 200 + 门禁全绿」证明不了「产物能连上后端」。**
> 这与 `evidence/README.md` 里「源码里写了 ≠ 运行时生效」是同一族：
> 这里更进一步——**「构建成功」≠「产物能用」**。

## 判据（**两条**）

1. **值本身对不对**：`.env.local` 的基址**不得等于** SDK 兜底串（`SDK_FALLBACK`）。
   🚨 **2026-10-10 补**：少了这一条，判据 2 的**期望值取自被测对象本身** ⇒
   四端 `.env.local` 被写成 `:8000` 时它照旧 `exit 0`（见 `is_sdk_fallback` 的说明）。
2. **有没有烘进去**：断言该基址在 `.next/static/chunks` 里**出现**。

⚠️ **不能反过来断言「`localhost:8000` 不出现」**：实测四端**正确的**产物里
都含 `localhost:8000`（SDK 那个字面量兜底串被 minifier 保留了）。
「兜底串在」不是缺陷信号，「**期望值在**」才是 —— 判据 1 判的是 `.env.local` 那一侧。

## 退出码

`0` 通过 / `1` 产品缺陷（缺 `.env.local`、**值等于 SDK 兜底串**、或产物没烘进去）/
`2` 环境问题（该端还没构建）

用法：
    python evidence/verify_api_base_baked.py
    python evidence/verify_api_base_baked.py --self-test   # 用归档的「坏产物」证明它真的会红

## 它在 CI 里怎么跑（2026-09-23 接入 · 待裁定 ⑥）

**不在 `evidence` job**（那里不装 pnpm、不构建 ⇒ 没有前端产物），
而在 `ci.yml` 的 **`frontend` job**，三步连跑：

    Seed build-time env (NEXT_PUBLIC_API_BASE)  →  Build (production)  →  本探针

⚠️ **为什么要「播种」`.env.local`**：它被根 `.gitignore` 忽略 ⇒ **CI 里本来不存在**。
不播种就构建 ⇒ 产物烘的是 SDK 兜底的 `:8000`，而**构建 exit 0 / 路由 HTTP 200 /
全部静态门禁绿**一个都不会报警（正是本文件开头描述的那个场景）。

⚠️ **诚实标注边界**：CI 自己播种之后，这条门禁在 CI 里就**查不出**
「某端本地忘了建 `.env.local`」了（那属**本机**缺陷）。CI 里它守的是
「**内联机制**有没有被破坏」——例如有人把 SDK 改成读非 `NEXT_PUBLIC_` 变量、
或改成运行时配置。两条都要有人守，**别把 CI 的绿读成本机的绿**。

⚠️ **本机未做端到端验证**：四端 dev server 正在跑（3000–3003），
`next build` 会覆盖它们正在用的 `.next` ⇒ **首次 CI 运行即为这条接线的首次端到端验证**。
本地已实测的是：`pnpm typecheck` 绿、四端 `.env.local` 齐、本探针对**当前**产物 `exit 0`。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
FRONTEND = HERE.parent / "frontend"
APPS = ("web", "lawyer", "admin", "im")
ENV_KEY = "NEXT_PUBLIC_API_BASE"

#: `packages/sdk/src/index.ts` 里的兜底串。**它必须与 SDK 里那一行保持一致**。
SDK_FALLBACK = "http://localhost:8000"


def is_sdk_fallback(base: str) -> bool:
    """`.env.local` 的值是否**恰好等于** SDK 兜底串。

    🚨 **为什么必须单独判这一条**（2026-10-10 实测）：

    本探针的判据是「从 `.env.local` 读期望值 ⇒ 断言它出现在产物里」——
    **期望值取自被测对象本身**。若 `.env.local` **自己**写的就是兜底串，
    那么「烘进去了」与「根本没烘、只剩兜底串」在产物里**完全同形**
    （兜底串在**正确**产物里也照留，见文件头那条边界）⇒ **判据恒绿**。

    实测：四端 `.env.local` 被写成 `:8000`（后端在 `:8001`，
    `ci.yml` 播种的也是 `:8001`），而本探针照旧 `exit 0`。
    这与「判据把自己算成证据」同族（`verify_component_wiring.count_equivalent`
    未排除组件自己的定义文件）。
    """
    return host_port(base) == host_port(SDK_FALLBACK)


def expected_base(app: str) -> str | None:
    """从 `apps/<app>/.env.local` 读期望基址；文件缺失或没有该键 ⇒ None。"""
    f = FRONTEND / "apps" / app / ".env.local"
    if not f.exists():
        return None
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{ENV_KEY}="):
            return line.split("=", 1)[1].strip()
    return None


def host_port(url: str) -> str:
    """`http://localhost:8001` → `localhost:8001`。"""
    return url.split("://", 1)[-1].rstrip("/")


def count_in_chunks(app: str, needle: str) -> int:
    """产物客户端 chunk 里 needle 出现的次数。"""
    chunks = FRONTEND / "apps" / app / ".next" / "static" / "chunks"
    if not chunks.is_dir():
        return -1  # 还没构建
    n = 0
    for p in chunks.rglob("*"):
        if not p.is_file():
            continue
        try:
            n += p.read_text(encoding="utf-8", errors="replace").count(needle)
        except OSError:
            continue
    return n


def self_test() -> int:
    """两条自检：

    ① **纯函数臂**（合成夹具，不依赖仓库里任何产物 ⇒ **永远可跑**）：
       `.env.local` 的值等于 SDK 兜底串时，`is_sdk_fallback` 必须判 True。
       —— 2026-10-10 补，起因是「**期望值取自被测对象**」这处盲区。
    ② **归档的真实坏产物**：证明判据「期望值必须出现」真的会红。
       需要 `frontend/_prev_build/im-prod-build-*`；**该目录不在仓库里时本组无法执行**
       ⇒ 退 2（不许静默当成通过）。
    """
    # —— ① 纯函数臂（**先跑**：② 依赖归档目录，缺了就整组跳过）——
    print("── 自检 ①：`.env.local` 的值等于 SDK 兜底串 ⇒ **必须判缺陷** ──\n")
    arms_bad = 0
    for key, val, expect in (
        ("S1", "http://localhost:8001", False),
        ("S2", "http://localhost:8000", True),
        ("S3", "http://localhost:8000/", True),
    ):
        got = is_sdk_fallback(val)
        ok = got == expect
        print(f"  [{key}] `.env.local` = `{val}` ⇒ 判为兜底串 {got}"
              f"（期望 {expect}） {'✓' if ok else '✗'}")
        if not ok:
            arms_bad += 1
    if arms_bad:
        print(f"\n  ✗ 自检 ① 失败 {arms_bad} 臂 ⇒ 工具坏了（退 2）")
        return 2
    print("\n  ✓ 自检 ① 通过：`:8001` 与兜底串 `:8000` 被正确区分")

    # —— ② 归档的真实坏产物 ——
    print("\n── 自检 ②：拿归档的「坏产物」验证「期望值必须出现」真的会红 ──\n")
    cands = sorted(FRONTEND.glob("_prev_build/im-prod-build-*"))
    if not cands:
        print("  ⚠️ 找不到归档产物 `frontend/_prev_build/im-prod-build-*` ⇒ 本组**未能执行**")
        print("     （环境问题 ⇒ 退 2。**不许静默当成通过**）")
        return 2

    bad = 0
    for d in cands:
        chunks = d / "static" / "chunks"
        if not chunks.is_dir():
            continue
        blob = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in chunks.rglob("*")
            if p.is_file()
        )
        n_8001 = blob.count("localhost:8001")
        n_8000 = blob.count("localhost:8000")
        verdict = "会红 ✓" if n_8001 == 0 else "会绿 ✗"
        print(f"  {d.name}")
        print(f"      localhost:8001 出现 {n_8001} 次    localhost:8000 出现 {n_8000} 次   ⇒ 门禁{verdict}")
        if n_8001 == 0:
            bad += 1

    if bad == 0:
        print("\n  ✗ 归档里找不到「烘了 :8000」的产物 —— 自检失去对照，退 2")
        return 2
    print(f"\n  ✓ 已证明：这 {bad} 份产物的客户端 bundle 里**没有** 8001")
    print("    ⇒ 判据（「期望值必须出现」）确实能区分好坏，不是一条只会绿的防线")
    print("\nEXIT=0  自检通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="用归档坏产物证明门禁会红")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    print("── 门禁：四端构建产物里烘入的 API 基址 ──\n")
    defects: list[str] = []
    env_issues: list[str] = []

    for app in APPS:
        base = expected_base(app)
        if base is None:
            print(f"  {app:<7}  .env.local: **缺失或没有 {ENV_KEY}**")
            defects.append(
                f"{app}: 没有 `apps/{app}/.env.local`（或缺少 `{ENV_KEY}`）"
                f" ⇒ 产物会烘入 SDK 兜底串 `http://localhost:8000`，"
                f"构建仍 exit 0 但**连不上后端**"
            )
            continue

        needle = host_port(base)
        if is_sdk_fallback(base):
            print(f"  {app:<7}  期望 {needle:<18} **等于 SDK 兜底串**")
            defects.append(
                f"{app}: `apps/{app}/.env.local` 的值就是 SDK 兜底串 `{SDK_FALLBACK}` ⇒ "
                f"本判据**无法与「根本没烘进去」区分**（兜底串在正确产物里也照留 ⇒ 恒绿）；"
                f"且本仓库后端在 **:8001**（`ci.yml` 播种的也是 8001）"
            )
            continue
        n = count_in_chunks(app, needle)
        if n < 0:
            print(f"  {app:<7}  期望 {needle:<18} 产物未构建（跳过）")
            env_issues.append(f"{app}: `.next/static/chunks` 不存在（还没构建）")
            continue

        ok = n > 0
        print(f"  {app:<7}  期望 {needle:<18} 产物命中 {n} 次   {'✓' if ok else '✗'}")
        if not ok:
            defects.append(
                f"{app}: 期望基址 `{needle}` 在 `.next/static/chunks` 里出现 0 次"
                f" ⇒ 该端产物连不上后端（需重跑 `next build`）"
            )

    print()
    if env_issues:
        for e in env_issues:
            print(f"  ⚠ 环境：{e}")
    if defects:
        print("  ✗ 发现缺陷：")
        for d in defects:
            print(f"      · {d}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1
    if env_issues:
        print("  ⚠ 有端未构建，门禁未能全覆盖（退 2，不当成通过）")
        return 2
    print("  ✓ 四端产物都烘入了与 `.env.local` 一致的 API 基址")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

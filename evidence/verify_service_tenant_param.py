#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「服务层不收 `tenant_id`」模式横扫（`round15b-fixed-but-unguarded-sweep-2026-09-20.md` §3.29.3 派生）。

> ⚠️ **本节号必须点名文档**：`§3.27` / `§3.28` / `§3.29` / `§3.29.3` 出自上述
> **矩阵外**的清扫记录稿（**不是** `design-spec.md` —— 它连 §3.2x 都没有）。
> 不点名的话，覆盖率报表会把它们记成「**无主引用**」：一个真实的**归属缺口**
> 被伪装成「提到而非判」，于是**看起来像已经解释过了**。

## 为什么要有这个探针

§3.28 派单与 §3.29 文书查出的是**两个独立模块、同一个缺口形状**：

| | 派单 | 文书 |
|---|---|---|
| 服务层 `_get()` | `db.get(Dispatch, id)` 无租户过滤 | `db.get(Document, id)` 无租户过滤 |
| 服务层方法收 `tenant_id` 吗 | ❌ | ❌ |
| 端点层校验 | ❌ | 仅详情有 |

这已经不是「某一处漏了」，而是**一个可复现的模式**。逐路由试错的命中率约
16%（37 条查出 6 个），而按模式横扫可以直接点名**候选**。

## 扫什么

`app/services/**` 里的**服务类方法**：

- 签名里**有** `id` / `*_id` 这类「按主键取资源」的参数；
- 签名里**没有** `tenant_id`（含 keyword-only）；
- 且该方法名在 `app/api/v1/**` 里**真被调用过**（否则是死代码，不算风险）。

⇒ 三条全中即列为**候选**，需要人工确认端点是否另有归属守卫。

## 棘轮

**候选条数只许降、不许涨**（基线 `BASELINE_CANDIDATES`）。
新增一个「按主键取资源但不收 `tenant_id`」的服务方法 ⇒ **红**，
逼你三选一：① 给方法加 `tenant_id`；② 在端点层补归属守卫并说明理由；
③ 确认与租户无关、从候选里显式排除。

⚠️ **判的是「新增」不是「存量」**：现存的 20 条并不都是缺陷
（很多方法的租户判据在端点层，§3.27 的复核、§3.28 的派单都是这样修的），
所以这个探针**不对存量判红**——那会造出一堆假红，
而一条天天红的门禁两天内必被注释掉。
"""
from __future__ import annotations

import ast
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"
SERVICES = BACKEND / "app" / "services"
API = BACKEND / "app" / "api" / "v1"

#: 视为「按主键取资源」的参数名
ID_LIKE = {"id", "doc_id", "case_id", "dispatch_id", "review_id", "scan_id",
           "analysis_id", "evidence_id", "job_id", "conversation_id", "archive_id"}

#: 棘轮基线（2026-09-21 首扫实测）。**只许降，不许涨。**
BASELINE_CANDIDATES = 20


def _service_methods() -> list[tuple[str, str, list[str], bool]]:
    """返回 [(文件, 类名.方法名, 参数名列表, 是否含 tenant_id)]。"""
    out: list[tuple[str, str, list[str], bool]] = []
    for f in sorted(SERVICES.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if not cls.name.endswith(("Service", "Engine", "Copilot")):
                continue
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name.startswith("_"):
                    continue  # 私有方法由公开方法担保，不单独列
                names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
                if not (names & ID_LIKE):
                    continue
                has_tenant = "tenant_id" in names
                out.append((f.relative_to(BACKEND).as_posix(),
                            f"{cls.name}.{fn.name}", sorted(names), has_tenant))
    return out


def _called_from_api(method_simple_name: str) -> bool:
    for f in API.glob("*.py"):
        try:
            if method_simple_name in f.read_text(encoding="utf-8"):
                return True
        except OSError:  # pragma: no cover
            continue
    return False


def main() -> int:
    try:
        rows = _service_methods()
    except Exception as exc:  # noqa: BLE001 - 依赖/路径缺失属环境问题
        print(f"[skip] 无法扫描：{type(exc).__name__}: {exc}")
        return 2

    cands = [
        (f, m, names) for f, m, names, has_t in rows
        if not has_t and _called_from_api(m.split(".")[-1])
    ]

    print("## 「服务层方法不收 `tenant_id`，但端点确实在调用」候选清单\n")
    print("> 只报不判：签名里没有 `tenant_id` **不等于**有缺陷——")
    print("> 租户判据可能在端点层。本清单的用途是**点名候选**，由人工确认。\n")
    print("| 文件 | 方法 | 参数 |")
    print("|---|---|---|")
    for f, m, names in cands:
        print(f"| `{f}` | `{m}` | {', '.join(names)} |")
    print(f"\n**候选 {len(cands)} 条**（服务方法总数 {len(rows)}；棘轮基线 {BASELINE_CANDIDATES}）")

    if len(cands) > BASELINE_CANDIDATES:
        print("\n🚨 **棘轮突破**：候选变多了。新增的服务方法要么补 `tenant_id`，"
              "要么在端点层补归属守卫并在此登记理由，不得静默增加。")
        return 1
    print("\n✅ 未突破棘轮（存量不判红，只防新增）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

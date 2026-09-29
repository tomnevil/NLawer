"""路由 × 端点层覆盖扫描：79 条路由里有多少条**从未被 `tests/` 请求过**。

## 为什么要有这个脚本

2026-09-20 的「已修复 ≠ 有判据」清扫里反复撞到同一个形状：某个端点的
**服务层**测得很扎实，但**端点本身**一次都没被请求过——于是「端点有没有把
租户身份传进去」这类装配层缺陷无人看守。逐个函数去猜太慢，所以把尺子抬高一层：
枚举全部路由，逐条数它在 `tests/` 下被请求过几次。

首扫结论：**79 条路由，55 条零覆盖**。其中风险最高的一条
（`GET /api/v1/files/{tenant_id}/{subdir}/{filename}`，全站唯一直接吐磁盘文件的
接口）当轮就查出两个真实缺陷：

1. 用 `user.tenant_id` 而不是 `ctx.tenant_id` 鉴权 ⇒ 平台管理员切换租户后
   列表接口看得见、下载接口 404；
2. 只校验「在存储根内」、不校验「在租户目录内」 ⇒
   `/files/{自己的租户}/%2e%2e/secret.pdf` 能读到存储根下的文件。

两者都由 `backend/tests/test_file_download_endpoint.py` 钉住。

## 匹配方法（以及为什么不能用朴素子串）

tests 里大量路径是 f-string 拼的，例如 `f"/api/v1/evidence/cases/{cid}/missing"`，
源码里的字面量只有 `"/api/v1/evidence/cases/"` 和 `"/missing"` 两段。
拿整条路由去匹配必然落空 ⇒ 把已覆盖误报成零覆盖（朴素版实测报 67/79，虚高）。

这里改成**按静态片段**双向匹配取并集：

| 方向 | 做法 | 覆盖的写法 |
|---|---|---|
| A | 路由的 `{param}` 当通配，去匹配测试字面量 | 写死 id：`"/cases/7/evidence"` |
| B | 测试字面量的插值当通配，去匹配路由规范化串 | f-string：`f"/cases/{cid}/evidence"` |

并集只会**缩小**零覆盖清单（假阳性变少），而本脚本只是**候选生成器**——
任何要据以行动的条目，仍须靠故障注入或行为核查复核（`methodology.md` 73）。

## 棘轮

`BASELINE_ZERO` 当前为 0（2026-09-21 收口）。零覆盖路由数**只能降、不能涨**（新端点必须带端点层判据）。
涨了就退 1，并把新增的那几条打出来。

退出码：`0` 通过 · `1` 棘轮被突破 · `2` 环境问题（依赖/引擎导入失败 ⇒ 由
`run_ci_probes.py` 判为「跳过」，避免把环境噪声报成产品缺陷）。
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[0] / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

# 自带临时 SQLite：本探针只读源码，绝不连真实库
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_tmp_tests/routecov.db")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./_tmp_tests/routecov.db")

TESTS = BACKEND / "tests"

#: 棘轮基线（2026-09-21 实测）。**只能降，不能涨**。
#: 55（首扫）→ 49（清完 `notifications/*` 6 条）
#:          → 39（清完 `conversations/*` 3 条 + `reviews/*` 7 条）
#:          → 29（清完 `dispatches/*` 4 条 + `analyses/*` 6 条）
#:          → 22（清完 `documents/*` 5 条 + `compliance/*` 2 条；B 类归零）
#:          → 16（清完 `jobs/*` 2 条 + `archives/*` 4 条；两批都无产品缺陷）
#:          → 11（清完 `audit/retention/*` 5 条；**查出一个合规级缺陷**）
#:          ⚠️ 本节号出自 `round15b-fixed-but-unguarded-sweep-2026-09-20.md`（**必须点名文档**：
#:             它是**矩阵外**的清扫记录稿，不点名的话覆盖率报表会把 §3.32/§3.33 记成
#:             「**无主引用**」—— 一个真实的归属缺口会被伪装成「提到而非判」）。
#:          → 4（清完 `billing/*` 3 条 + `complaints/*` 4 条；§3.32 查出 1 个真实缺陷
#:               + 登记 Q-AA；剩余 `auth/me` / `qa/stream` / `knowledge/docs`(+`{doc_id}`)）
#:          → 0（清完最后 4 条：§3.33 补 `auth/me`(A1–A3) + `knowledge/docs`(+`{doc_id}`)(KD1–KD5)
#:               + `qa/stream`(QS1–QS4)；全部经 8 臂故障注入自证；B 类全程为 0）
BASELINE_ZERO = 0

VERBS = ("get", "post", "put", "patch", "delete")
_PARAM = re.compile(r"\{[^}]+\}")


def route_chunks(path: str) -> list[str]:
    return [seg for seg in _PARAM.split(path)]


def joinedstr_chunks(node: ast.AST) -> list[str] | None:
    """取字符串字面量的静态片段；f-string 的插值当通配缺口。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            else:
                out.append("")  # 插值位置：交由正则里的 .*? 吸收
        return out
    return None


def rx_from(chunks: list[str]) -> re.Pattern[str]:
    return re.compile("^" + ".*?".join(re.escape(c) for c in chunks) + "$")


def _service_funcs(root: pathlib.Path, router_rel: str) -> set[str]:
    try:
        tree = ast.parse((root / router_rel).read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    mods = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and (n.module or "").startswith("app.services")
    }
    for m in mods:
        fp = root / (m.replace(".", "/") + ".py")
        if not fp.exists():
            continue
        try:
            t2 = ast.parse(fp.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(t2):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(n.name)
    return names


def collect() -> tuple[list[tuple[str, str, str, bool]], int]:
    """返回 (零覆盖路由, 路由总数)。每项 = (方法, 路径, router 模块, 服务层有测试)。"""
    from app.main import create_app

    app = create_app()

    defmap: dict[str, str] = {}
    for p in (BACKEND / "app").rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defmap.setdefault(
                    node.name, str(p.relative_to(BACKEND)).replace("\\", "/")
                )

    files = sorted(TESTS.glob("*.py"))  # ⚠️ 含 conftest.py：前缀常量常放这儿
    lits: list[tuple[str, list[str]]] = []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            chunks = joinedstr_chunks(node)
            if chunks is None:
                continue
            # ⚠️ 不能要求「首片段以 / 开头」：f"{BASE}/x" 的首片段是空串
            if not any("/" in c for c in chunks):
                continue
            lits.append((p.name, chunks))
    test_blob = "\n".join(
        p.read_text(encoding="utf-8") for p in files if p.suffix == ".py"
    )

    zero: list[tuple[str, str, str, bool]] = []
    total = 0
    # ⚠️ 不能用 `app.routes`：starlette 1.3.1 把 include_router 包成惰性
    # `_IncludedRouter`，v1 的路由不会出现在里面（实测只有 10 条）。
    # `app.openapi()` 会强制展开，是唯一可靠的枚举源。
    for path, ops in app.openapi().get("paths", {}).items():
        methods = sorted(m.upper() for m in ops if m.lower() in set(VERBS))
        if not path.startswith("/api") or not methods:
            continue
        total += 1

        a = route_chunks(path)
        canonical_route = "X".join(a)
        rx_a = rx_from(a)
        hit = False
        for _name, chunks in lits:
            chunks = [c.split("?", 1)[0] if "?" in c else c for c in chunks]
            if rx_a.fullmatch("X".join(chunks)):
                hit = True
                break
            if rx_from(chunks).fullmatch(canonical_route):
                hit = True
                break
        if hit:
            continue

        funcs = [
            ops[m].get("operationId", "").split("_api_")[0]
            for m in ops
            if m.lower() in {x.lower() for x in methods}
        ]
        where = defmap.get(funcs[0], "?") if funcs else "?"
        svc_hit = any(
            re.search(rf"\b{n}\b", test_blob)
            for n in _service_funcs(BACKEND, where)
        )
        zero.append((",".join(methods), path, where, svc_hit))
    return zero, total


def main() -> int:
    try:
        zero, total = collect()
    except Exception as exc:  # noqa: BLE001 - 依赖/引擎缺失属环境问题
        print(f"[skip] 无法枚举路由：{type(exc).__name__}: {exc}")
        return 2

    print(f"# 路由总数 {total}，端点层零覆盖 {len(zero)}（棘轮基线 {BASELINE_ZERO}）\n")
    for title, group in (
        ("A 类：服务层有测试、端点层零请求（零件测过、装配没测）",
         [z for z in zero if z[3]]),
        ("B 类：服务层同样零引用", [z for z in zero if not z[3]]),
    ):
        print(f"## {title}（{len(group)}）\n")
        print("| 方法 | 路径 | router 模块 |")
        print("|---|---|---|")
        for methods, path, where, _s in group:
            print(f"| {methods} | `{path}` | `{where}` |")
        print()

    if len(zero) > BASELINE_ZERO:
        print(f"❌ 棘轮被突破：{len(zero)} > {BASELINE_ZERO}")
        print("   新增端点必须带端点层判据，或下调基线并在交付文档里写明理由。")
        return 1
    print(f"✅ 未突破棘轮（{len(zero)} ≤ {BASELINE_ZERO}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

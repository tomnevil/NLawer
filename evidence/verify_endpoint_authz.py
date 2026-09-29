"""端点层授权证据扫描：多少条路由（**写 + 读**）在端点层**完全没有任何授权动作**。

> 2026-09-22 补上**读侧**：§4.1/§4.2 那两个真缺陷（`case_events` 任意已登录用户可
> 遍历 `case_id` 读他人案件时间线）都是**读**侧泄露，而首版只量写路由 ⇒
> 泄露面留在门外。读侧 45 条：GATE 9 / OWNER 12 / TENANT 17 / NONE 4（全部是设计豁免）。

## 为什么要有这个脚本

2026-09-21 的 Q-R 是人工审出来的：证据模块 6 条路由**一条权限门控都没有**
（`APIRouter()` 无 `dependencies=`）。而同一时期已有的两把尺子都**量不到它**：

| 已有仪器 | 量什么 | 为什么量不到 Q-R |
|---|---|---|
| `verify_route_coverage.py` | 路由**有没有被请求过** | 请求过 ≠ 请求带对了身份；它只看「有没有打过」 |
| `verify_service_tenant_param.py` | **服务层**函数收不收 `tenant_id` | 是服务层的尺子；端点层的门控它一个字都不看 |

⇒ 于是「端点层到底有没有做授权决策」这个维度**至今零判据**。Q-R 能发现，
靠的是人读代码时顺手看见 —— 而 §4.1/4.2 已经证明**风险排序本身会漏**
（那两个真缺陷在风险排序里都不靠前）。

## 判什么（以及刻意不判什么）

只数**写路由**（`POST` / `PUT` / `PATCH` / `DELETE`）—— 读路由的越权是「看见」，
写路由的越权是「改到」，后者才是 Q-R / §4.1 那一类。每条写路由分三档：

| 档 | 判据 | 说明 |
|---|---|---|
| `GATE` | 端点装饰器**或**本模块 `APIRouter(dependencies=...)` 里出现 `require_permissions` | 权限码门控 |
| `OWNER` | 函数体调用 `*_or_404` 归属守卫，或引用 `client_user_id` / `ctx.user_id` / `current_user.id` | 归属校验（无权限码也行） |
| `TENANT` | 只出现 `tenant_id`，没有门控也没有到人引用 | 跨租户已堵、**同租户横向没堵**（**只报不判**） |
| `NONE` | 门控 / 到人 / 租户**三者皆无** ⇒ 🎯 棘轮管这一档 | 端点层零授权证据 |

两条判据，缺一不可：

1. `NONE` 条数 ≤ `BASELINE_UNGUARDED`（防「新增写端点不带授权」）；
2. 🚨 **门控清单 `GATE_BASELINE` 不得减少**（防「把已有门控摘掉」）——
   只有第 1 条的话，摘掉的端点会掉进 `TENANT`/`OWNER` 两个「只报」档 ⇒ **不红、没人发现**，
   而 Q-R 那一类缺陷的形状恰恰就是「门控不存在」。

⚠️ **刻意不做的事**：
- **不判「门控挂对了没有」** —— 那是 Q-P 那种语义问题，静态扫不出来，得靠行为判据。
  本脚本只是**候选生成器**（`methodology.md` 73）。
- **不把 `tenant_id` 当授权证据** —— 只查租户、不查人，正是 Q-S 修掉的那个形状
  （同租户客户乙能下客户甲的卷宗）。所以 `OWNER` 只认**到人**的引用。
- **不读 `app.routes`** —— starlette 1.3.1 包成惰性 `_IncludedRouter`，
  只有 `app.openapi()` 会展开（实测过，见 `verify_route_coverage.py`）。

## 棘轮

`BASELINE_UNGUARDED` **只能降、不能涨**：新增写端点必须带端点层授权证据，
否则要么补、要么下调基线并在交付文档里写明理由（照抄 Q-F 的做法）。

退出码：`0` 通过 · `1` 棘轮被突破 · `2` 环境问题。
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
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_tmp_tests/authz.db")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./_tmp_tests/authz.db")

#: 棘轮基线（2026-09-22 首扫实测 **2**）。只降不涨。
#: 当前这 2 条是**天然豁免**：`POST /auth/login` 与 `POST /auth/refresh`
#: 发生在**拿到身份之前**，端点层不可能有授权动作（它们的防线是限流与口令校验）。
#: ⇒ 新写端点只要落到 `NONE` 就一定红；`TENANT` 档**只报不判**。
BASELINE_UNGUARDED = 2

#: 🚨 第二条判据：**已有的门控一条都不许少**。
#: 光有上面那条棘轮不够 —— 它只管 `NONE`。若有人把某个端点的门控**摘掉**，
#: 该端点会掉进 `TENANT`/`OWNER` 档（这两个档是「只报」）⇒ **棘轮不红、没人发现**。
#: 而 Q-R 那一类缺陷的形状恰恰就是「门控不存在」。
#: ⇒ 逐条钉住当前挂了门控的路由（`"METHOD /path"`），少一条就红并**点名**。
#: 格式刻意用「方法 + 空格 + 路径」，让报错信息能直接粘去 grep。
#: 读侧棘轮基线（2026-09-22 首扫实测 **7**）。只降不涨（与写侧同规则）。
#: 这 7 条**都是文档化的设计豁免**，逐条写明出处（「无授权」不等于「有缺陷」）：
#:   ①–③ `GET /api/health` · `/health/livez` · `/health/readyz` —— 存活/就绪探针，
#:      定义在 `app/main.py`，必须免鉴权（负载均衡与 k8s 要打）
#:   ④ `GET /audit/retention/policy` —— 保留策略说明，端点 summary 自带「（公开）」
#:   ⑤ `GET /auth/me` —— 只返回**当前用户自己**，身份来自令牌，无需再查租户
#:   ⑥ `GET /complaints/policy` —— 投诉政策说明（公开）
#:   ⑦ `GET /complaints/{ticket_no}` —— **工单号即凭证**：`complaints.py:11` 的模块
#:      docstring 明写「无鉴权，凭工单号回查」（投诉是匿名提交的，没有别的凭证可用）
#: ⚠️ 改动其中任何一条的鉴权口径 ⇒ 要么它会离开 NONE 档（基线自动变松，无害），
#:    要么新增别的 NONE 读端点 ⇒ 这里会红，逼人说明「为什么这条不需要授权」。
BASELINE_UNGUARDED_READ = 7

#: 写侧门控清单（详见上）
GATE_BASELINE: frozenset[str] = frozenset({
    "POST /api/v1/audit/retention/archive",
    "POST /api/v1/audit/retention/archive-then-purge",
    "POST /api/v1/audit/retention/purge",
    "POST /api/v1/billing/consume",
    "POST /api/v1/billing/project-revenue",
    "POST /api/v1/billing/quota/adjust",
    "POST /api/v1/complaints/{complaint_id}/handle",
    # ↓ 2026-09-22 收口 TENANT 档时新增（`knowledge:write` / `compliance:scan`）
    "POST /api/v1/compliance/scans",
    "DELETE /api/v1/knowledge/docs/{doc_id}",
    "POST /api/v1/evidence/cases/{case_id}",
    "POST /api/v1/evidence/{evidence_id}/parse",
    "POST /api/v1/knowledge/docs",
    # ↓ 读侧（2026-09-22 把尺子补到 GET）
    "GET /api/v1/audit/retention/stats",
    "GET /api/v1/billing/dashboard",
    "GET /api/v1/billing/work-orders",
    "GET /api/v1/complaints",
    "GET /api/v1/complaints/stats",
    "GET /api/v1/evidence/cases/{case_id}",
    "GET /api/v1/evidence/cases/{case_id}/missing",
    "GET /api/v1/evidence/cases/{case_id}/rounds",
    "GET /api/v1/evidence/cases/{case_id}/timeline",
})

WRITE_VERBS = ("post", "put", "patch", "delete")
#: 2026-09-22 补上**读侧**：§4.1/§4.2 那两个真缺陷（`case_events` 任意已登录用户
#: 可遍历 `case_id` 读他人案件时间线）都是**读**侧泄露，而写侧已先有门禁 ⇒
#: 只量写侧等于把泄露面留在门外。
READ_VERBS = ("get",)

#: 🚨 两种守卫工厂**都要认**：`require_permissions(*codes)` 与 `require_roles(*roles)`。
#: 首扫只认前者 ⇒ `audit/retention/*` 三条（用 `require_roles(PLATFORM_ADMIN)`）
#: 被误报成零授权 —— **假红**。实测 `app/api/` 下两种各 8 处。
_RE_GATE = re.compile(r"require_(permissions|roles)")
#: 「到人」的引用 —— 刻意不含 `tenant_id`（见模块 docstring）：只查租户不查人
#: 正是 Q-S 修掉的形状。
_RE_OWNER_CALL = re.compile(r"\b\w*or_404\b")
_RE_OWNER_REF = re.compile(r"\b(client_user_id|ctx\.user_id|current_user\.id|user\.id)\b")
_RE_ROUTER = re.compile(r"APIRouter\s*\(")
#: 「只到租户」——查了 `tenant_id` 但没查人、也没挂角色/权限门控。
#: ⚠️ 这一档**不是** `NONE`：跨租户已经堵住了，但**同租户内的横向越权**没堵
#: （Q-S 修掉的就是这个形状）。**只报不判** —— 不少端点按设计就是租户级的。
_RE_TENANT = re.compile(r"\btenant_id\b")


def classify(src: str, func: str) -> str:
    """给单个写端点的授权证据分档：`GATE` / `OWNER` / `NONE` / `?`（找不到函数）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "?"

    # ① 模块级：APIRouter(dependencies=[Depends(require_permissions(...))])
    router_level_gate = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            seg = ast.unparse(node.value)
            if _RE_ROUTER.search(seg) and _RE_GATE.search(seg):
                router_level_gate = True

    # ② 端点级
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func:
            continue
        # ⚠️ 三个位置都要看，**少一个就是假红**：
        #   ① 装饰器 `@router.post(..., dependencies=[Depends(require_permissions(...))])`
        #   ② **签名的默认值** `ctx=Depends(require_roles(Role.PLATFORM_ADMIN))`
        #      —— FastAPI 的依赖可以写在参数默认值里，`audit/retention/*` 三条
        #      就是这么挂的，首扫漏掉它们正是栽在这里（自检 Q3b 抓到）
        #   ③ 模块级 `APIRouter(dependencies=...)`
        # 另外：装饰器必须用 `node.decorator_list` 取 —— `ast.get_source_segment`
        # 不含装饰器（methodology 127）。
        decs = "".join(ast.unparse(d) for d in node.decorator_list)
        sig = ast.unparse(node.args)
        if _RE_GATE.search(decs) or _RE_GATE.search(sig) or router_level_gate:
            return "GATE"
        body = "\n".join(ast.unparse(s) for s in node.body)
        if _RE_OWNER_CALL.search(body) or _RE_OWNER_REF.search(body):
            return "OWNER"
        if _RE_TENANT.search(body) or _RE_TENANT.search(sig):
            return "TENANT"
        return "NONE"
    return "?"


def collect() -> tuple[list[tuple[str, str, str, str, str]], int]:
    """返回 (路由明细, 路由总数)。明细项 = (读写, 方法, 路径, router 模块, 档位)。"""
    from app.main import create_app  # noqa: PLC0415 - 延迟导入：环境缺失要能退 2

    app = create_app()

    # 两遍扫描：**`app/api/` 优先**，`app/` 其余部分只用来补缺。
    # 🚨 顺序不能反 —— 处理函数名常与服务层方法同名
    # （`consume` / `register` / `archive_case` …），而 `rglob` 的遍历顺序不保证
    # ⇒ 服务层文件会抢先命中，`classify` 读到的是**服务层**源码，端点层的门控
    # 自然一个字都没有 ⇒ **稳定假红**。首扫实测有 4 条被这样误判
    # （`billing/consume` · `billing/project-revenue` · `compliance/scans` ·
    #   `archives/cases/{case_id}` 与其 hearing-pack）。
    defmap: dict[str, str] = {}
    for p in (BACKEND / "app" / "api").rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defmap.setdefault(
                    node.name, str(p.relative_to(BACKEND)).replace("\\", "/")
                )

    # 第二遍：补 `app/api/` 之外的处理函数（如 `app/main.py` 的 `/api/health/readyz`）。
    # 只用 `setdefault` ⇒ **不会覆盖** 第一遍的 `app/api/` 结果，服务层撞名问题不复现。
    for p in (BACKEND / "app").rglob("*.py"):
        rel = str(p.relative_to(BACKEND)).replace("\\", "/")
        if rel.startswith("app/api/"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defmap.setdefault(node.name, rel)

    rows: list[tuple[str, str, str, str, str]] = []
    for path, ops in app.openapi().get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        write_m = sorted(m.upper() for m in ops if m.lower() in WRITE_VERBS)
        read_m = sorted(m.upper() for m in ops if m.lower() in READ_VERBS)
        if not write_m and not read_m:
            continue
        for kind, methods in (("写", write_m), ("读", read_m)):
            if not methods:
                continue
            funcs = [
                ops[m.lower()].get("operationId", "").split("_api_")[0]
                for m in methods
            ]
            where = defmap.get(funcs[0], "?") if funcs else "?"
            tag = "?" if where == "?" else classify(
                (BACKEND / where).read_text(encoding="utf-8"), funcs[0])
            rows.append((kind, ",".join(methods), path, where, tag))
    rows.sort(key=lambda r: (r[0], r[4], r[2]))
    return rows, len(rows)


def main() -> int:
    try:
        rows, total = collect()
    except Exception as exc:  # noqa: BLE001 - 依赖/引擎缺失属环境问题
        print(f"[skip] 无法枚举路由：{type(exc).__name__}: {exc}")
        return 2

    w_rows = [r for r in rows if r[0] == "写"]
    r_rows = [r for r in rows if r[0] == "读"]
    print(f"# 路由 {total} 条（写 {len(w_rows)} / 读 {len(r_rows)}）\n")

    def dump(title: str, group: list[tuple[str, str, str, str, str]]) -> None:
        print(f"## {title}（{len(group)}）\n")
        if group:
            print("| 方法 | 路径 | router 模块 |")
            print("|---|---|---|")
            for _kind, methods, path, where, _tag in group:
                print(f"| {methods} | `{path}` | `{where}` |")
        print()

    for kind, group_rows, baseline in (
        ("写", w_rows, BASELINE_UNGUARDED),
        ("读", r_rows, BASELINE_UNGUARDED_READ),
    ):
        print(f"# ── {kind}路由（{len(group_rows)}）──  棘轮基线 {baseline}\n")
        for tag, g in (
            ("GATE 权限码门控", [r for r in group_rows if r[4] == "GATE"]),
            ("OWNER 归属校验（无权限码）", [r for r in group_rows if r[4] == "OWNER"]),
            ("TENANT 只到租户（同租户内横向越权未堵，只报不判）",
             [r for r in group_rows if r[4] == "TENANT"]),
            ("NONE 🎯 连租户都没查（棘轮管这一档）",
             [r for r in group_rows if r[4] == "NONE"]),
            ("? 无法定位处理函数", [r for r in group_rows if r[4] == "?"]),
        ):
            dump(f"{tag}", g)

    rc = 0

    # ---- 判据 1a：写 / 读各自的 NONE 棘轮（防「新增端点不带授权」）----
    for kind, group_rows, baseline in (
        ("写", w_rows, BASELINE_UNGUARDED),
        ("读", r_rows, BASELINE_UNGUARDED_READ),
    ):
        n = sum(1 for r in group_rows if r[4] == "NONE")
        if n > baseline:
            print(f"❌ {kind}路由 NONE 棘轮被突破：{n} > {baseline}")
            print("   新增端点必须带端点层授权证据（权限码门控或归属校验）。")
            rc = 1
        else:
            print(f"✅ {kind}路由 NONE 未突破棘轮（{n} ≤ {baseline}）")

    # ---- 判据 1b：门控清单不得减少（防「摘掉已有门控」—— Q-R 的形状）----
    now_gate = {f"{m} {p}" for _k, m, p, _w, tag in rows if tag == "GATE"}
    lost = sorted(GATE_BASELINE - now_gate)
    added = sorted(now_gate - GATE_BASELINE)
    if lost:
        print(f"\n❌ 有 {len(lost)} 条**原本有门控的端点失去了门控**：")
        for k in lost:
            print(f"  · {k}")
        print("   摘门控必须是有意为之 ⇒ 有意的话请同步改 `GATE_BASELINE` 并写明理由。")
        rc = 1
    if added:
        print(f"\nℹ️ 新增 {len(added)} 条挂了门控的端点 ⇒ **请把它们补进 `GATE_BASELINE`**：")
        for k in added:
            print(f"  · {k}")
        print("   不补的话，将来它们被摘掉时这条判据不会红。")
        rc = 1
    if not lost and not added:
        print(f"✅ 门控清单完整（{len(now_gate)} 条，与基线一致）")

    return rc


# ---------------------------------------------------------------------------
# 自检：**合成源码**，不读真实仓库、不启服务 ⇒ 可进 CI 的 SELFTESTABLE。
# ---------------------------------------------------------------------------
SYN_GATE_DECORATOR = '''
from fastapi import APIRouter, Depends
from app.core.deps import require_permissions
router = APIRouter(prefix="/x", tags=["x"])

@router.post("", dependencies=[Depends(require_permissions("x:create"))])
async def create_x(db=Depends(get_db)):
    return {"ok": True}
'''

SYN_GATE_ROUTER_LEVEL = '''
from fastapi import APIRouter, Depends
from app.core.deps import require_permissions
router = APIRouter(prefix="/x", tags=["x"],
                   dependencies=[Depends(require_permissions("x:read"))])

@router.post("")
async def create_x(db=Depends(get_db)):
    return {"ok": True}
'''

#: 🚨 假阳性对照（首扫真踩到）：`require_roles(Role.PLATFORM_ADMIN)` 也是门控，
#: 只认 `require_permissions` 会把 `audit/retention/*` 三条误报成零授权。
SYN_GATE_ROLES = '''
from fastapi import APIRouter, Depends
from app.core.deps import require_roles
from app.core.rbac import Role
router = APIRouter(prefix="/x", tags=["x"])

@router.post("")
async def create_x(db=Depends(get_db), ctx=Depends(require_roles(Role.PLATFORM_ADMIN))):
    return {"ok": True}
'''

SYN_OWNER = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/x", tags=["x"])

@router.post("")
async def create_x(db=Depends(get_db), ctx=Depends(get_tenant_context)):
    ev = await _evidence_or_404(db, ctx.tenant_id, payload.id)
    return ev
'''

#: 🎯 连 `tenant_id` 都没出现 —— 夹里**不能**出现 `tenant_id` 这个字面量，
#: 否则会被 `TENANT` 档吸走（首版夹具就写歪了，自检 Q4 当场抓到）。
SYN_NONE = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/x", tags=["x"])

@router.post("")
async def create_x(db=Depends(get_db)):
    row = X(name=payload.name)
    db.add(row)
    await db.commit()
    return row
'''

#: 🚨 关键假阳性对照：只查 `tenant_id`、不查人 ⇒ **不得**算 OWNER
#: （这正是 Q-S 修掉的形状）。
SYN_TENANT_ONLY = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/x", tags=["x"])

@router.post("")
async def create_x(db=Depends(get_db), ctx=Depends(get_tenant_context)):
    rows = await db.execute(select(X).where(X.tenant_id == ctx.tenant_id))
    return rows
'''


def run_self_test() -> int:
    cases: tuple[tuple[str, str, str, str], ...] = (
        ("Q1", "端点装饰器挂门控 ⇒ GATE", SYN_GATE_DECORATOR, "create_x", "GATE"),
        ("Q2", "模块级 APIRouter 挂门控 ⇒ GATE", SYN_GATE_ROUTER_LEVEL, "create_x", "GATE"),
        ("Q3", "调用 *_or_404 归属守卫 ⇒ OWNER", SYN_OWNER, "create_x", "OWNER"),
        ("Q3b", "🚨 `require_roles(...)` 也是门控 ⇒ GATE（防首扫那 3 条假红）",
         SYN_GATE_ROLES, "create_x", "GATE"),
        ("Q4", "两者皆无 ⇒ NONE（🎯 本脚本要数的）", SYN_NONE, "create_x", "NONE"),
        ("Q5", "🚨 只查 tenant_id 不查人 ⇒ **TENANT**（不是 OWNER，也不是 NONE："
         "跨租户已堵、同租户横向没堵 —— Q-S 修掉的就是这一档）",
         SYN_TENANT_ONLY, "create_x", "TENANT"),
        ("Q6", "找不到同名函数 ⇒ ?", SYN_NONE, "nope", "?"),
    )
    bad: list[str] = []
    for key, desc, src, func, want in cases:
        got = classify(src, func)
        ok = got == want
        print(f"  [{key}] {desc}\n        期望 {want}  实测 {got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望 {want} 实测 {got}")

    # Q7：两条判据的常量都在位且不为空（空清单 = 判据恒绿 = 没有判据）
    if BASELINE_UNGUARDED < 0 or not GATE_BASELINE:
        print("  [Q7] ❌ 基线常量异常：棘轮为负或门控清单为空（空清单 ⇒ 判据恒绿）")
        bad.append("[Q7] 基线常量异常")
    else:
        print(f"  [Q7] 基线就位：写 NONE ≤ {BASELINE_UNGUARDED} · "
              f"读 NONE ≤ {BASELINE_UNGUARDED_READ} · 门控清单 {len(GATE_BASELINE)} 条")

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **探针坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print("\n自检通过（7 臂：Q1/Q2/Q3/Q3b/Q4/Q5/Q6 + 基线常量臂 Q7）。")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(run_self_test())
    sys.exit(main())

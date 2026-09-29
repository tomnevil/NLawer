"""证据脚本共用的**环境探测**（HTTP + 种子规模指纹 + **运行库定位**）。

为什么抽成单点：
`verify_render_e2e.py` 与 `visual_baseline.py` 都要「打一次真实 HTTP，拿数据规模」，
而这段代码里藏着一个**必须两处完全一致**的坑——本机 `HTTP_PROXY/HTTPS_PROXY`
指向沙箱代理且**没有设 `NO_PROXY`**，`urllib` 会把 `localhost` 也发去代理，
表现为「服务明明起着却连不上」。两份拷贝迟早会漂移，所以抽出来。

`resolve_live_db()` 同理（2026-09-25 抽入）：`verify_task_center_e2e.py` 与
`verify_ws_msg_type_bypass.py` 都要「找到**后端正在用的**那张库」去造数 / 读回。
两份拷贝一旦漂移，就会一边写错库、另一边读错库 —— 而**症状是「环境问题」或假绿**。
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import urllib.request

ADMIN = "http://localhost:3002"
BACKEND = "http://localhost:8001"

# 与前端 `app/(app)/page.tsx` 的 `PIPELINE` 同序；
# 驾驶舱「在办案件」= 这 8 个状态计数之和（`cumulative[0]`）。
CASE_STATUSES = (
    "INTAKE",
    "PENDING_DISPATCH",
    "DISPATCHED",
    "ACCEPTED",
    "IN_REVIEW",
    "CONFIRMED",
    "ARCHIVED",
    "CLOSED",
)


def _opener() -> urllib.request.OpenerDirector:
    """⚠️ **必须绕开代理**：显式空 `ProxyHandler`（见模块 docstring）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, timeout: float = 4.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Origin": ADMIN})
    with _opener().open(req, timeout=timeout) as r:  # noqa: S310
        return r.status, r.read().decode("utf-8", "replace")


def check_services() -> list[str]:
    """服务可达性。返回**问题列表**（空 = 都通）。"""
    problems: list[str] = []
    for name, url in (("admin dev server (3002)", f"{ADMIN}/login"), ("backend (8001)", f"{BACKEND}/")):
        try:
            st, _ = http_get(url)
            print(f"  [ ok ] {name}  HTTP {st}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name} 不可达：{type(e).__name__}: {e}")
    return problems


def api_token() -> str:
    """用种子里的 admin 账号换 token（凭据来自 `app.seed.data`，不硬编码）。"""
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    body = json.dumps({"username": u["username"], "password": u["password"]}).encode()
    req = urllib.request.Request(
        f"{BACKEND}/api/v1/auth/login",
        data=body,
        headers={"Content-Type": "application/json", "Origin": ADMIN},
    )
    with _opener().open(req, timeout=6.0) as r:  # noqa: S310
        return json.loads(r.read().decode())["data"]["access_token"]


def api(path: str, tok: str, tenant: str) -> dict:
    req = urllib.request.Request(
        f"{BACKEND}{path}",
        headers={"Authorization": f"Bearer {tok}", "X-Tenant-Id": tenant, "Origin": ADMIN},
    )
    with _opener().open(req, timeout=6.0) as r:  # noqa: S310
        return json.loads(r.read().decode())


def tenant_scale(tok: str, tenant: str) -> dict:
    """某租户的数据规模（**驾驶舱口径**：在办 = 8 个状态之和）。"""
    cases = sum(
        int(api(f"/api/v1/cases?status={s}&page_size=1", tok, tenant).get("data", {}).get("total", 0) or 0)
        for s in CASE_STATUSES
    )
    wo = api("/api/v1/billing/dashboard", tok, tenant).get("data", {}).get("work_orders", {}) or {}
    return {"in_progress": cases, "work_orders_pending": wo.get("pending"), "work_orders_total": wo.get("total")}


def fingerprint() -> dict:
    """**数据指纹**：判断「两次测量是不是同一个东西」。

    用途：像素基线只在**同一数据集**下可比。数据一换，画面必然变，
    但那不是样式漂移——必须能区分，否则基线会天天误报。
    """
    tok = api_token()
    return {t: tenant_scale(tok, t) for t in ("platform", "firm_hlw")}


def describe_fingerprint(fp: dict) -> str:
    return " | ".join(
        f"{t}: 在办={v.get('in_progress')} 工单={v.get('work_orders_pending')}" for t, v in sorted(fp.items())
    )


# ---------------------------------------------------------------------------
# 运行库定位
# ---------------------------------------------------------------------------
def db_path_from_env() -> pathlib.Path | None:
    """从 `DATABASE_URL_SYNC` / `DATABASE_URL` 解析出 SQLite 文件路径。

    🚨 **这是唯一权威来源**：后端到底用哪张库，只有它的**启动环境**说了算。
    接受三种形态（都是本仓库真实出现过的）：
        `sqlite+aiosqlite:///C:/tmp/x.db`（编排器，Windows 绝对路径）
        `sqlite:////tmp/x.db`（POSIX 绝对路径 ⇒ 四斜杠）
        `sqlite:///./_tmp_tests/x.db`（相对路径，按 cwd 解析）
    ⚠️ `:memory:` 与空值一律跳过 —— 那不是可被另一个进程读到的库。
    """
    for key in ("DATABASE_URL_SYNC", "DATABASE_URL"):
        raw = (os.environ.get(key) or "").strip()
        if not raw.startswith("sqlite"):
            continue
        parts = raw.split("///", 1)
        if len(parts) != 2:
            continue
        tail = parts[1].strip()
        if not tail or tail == ":memory:":
            continue
        cand = pathlib.Path(tail)
        if cand.exists():
            return cand
    return None


def _count_jobs(p: pathlib.Path, tenant: str = "ent_acme") -> int:
    """`p` 里该租户的 jobs 条数；不是库 / 没这张表 / 读不了 ⇒ `-1`。"""
    try:
        con = sqlite3.connect(str(p), timeout=10)
    except Exception:  # noqa: BLE001 - 打不开就是「不是库」
        return -1
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE tenant_id=?", (tenant,)
        ).fetchone()
        return int(row[0])
    except sqlite3.Error:
        return -1
    finally:
        con.close()


def resolve_live_db() -> pathlib.Path | None:
    """定位**后端正在用的**那张库。**先读真源，再猜路径。**

    🚨 为什么必须先读 `DATABASE_URL`（与 README 坑 64 同族：检查集合也要读真源）：
       此前只按**硬编码候选路径**猜（`storage/nlawer.db` / `backend/demo_8001.db` …），
       在**隔离临时库**场景下**必然猜错** —— `run_browser_job.py` 起后端时设
       `DATABASE_URL=sqlite+aiosqlite:///<tmp>/browser_job.db`，而探针照样去写
       `backend/demo_8001.db` ⇒ 后端看不到造的数据 ⇒ 症状是
       **「环境问题（rc=2）」或「假绿」**，而不是「我写错库了」。
       实测：2026-09-25 Phase 3 四端摸底，`verify_task_center_e2e.py` 因此 rc=2。

    兜底（本机手工起后端、且**没设** `DATABASE_URL` 时才会走到）：在常见路径里挑
    「该租户任务最多」的那份 —— 而不是写死某一条。
    """
    from_env = db_path_from_env()
    if from_env is not None and _count_jobs(from_env) >= 0:
        return from_env

    repo = pathlib.Path(__file__).resolve().parents[1]
    cands = [
        repo / "storage" / "nlawer.db",
        repo / "backend" / "storage" / "nlawer.db",
        repo / "backend" / "demo_8001.db",
    ]
    best, best_n = None, -1
    for p in cands:
        if not p.exists():
            continue
        n = _count_jobs(p)
        if n > best_n:
            best_n, best = n, p
    return best

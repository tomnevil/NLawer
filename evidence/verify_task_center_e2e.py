"""任务中心（B2）的**浏览器端到端**判据（真实 Chromium + CDP）。

## 为什么这个探针必须存在

任务中心散在三个地方，且都是「列表从 `GET /api/v1/jobs` 取数 → 渲染」：
- web 合同审查页里的 `TaskCenterDrawer`（按钮文字「任务中心」）；
- 律师端 `/jobs` 页（`apps/lawyer/app/(app)/jobs/page.tsx`）；
- 管理端 `/jobs` 页（`apps/admin/app/(app)/jobs/page.tsx`）。

三者共用 `JobRow` + `GET /api/v1/jobs` + `POST /api/v1/jobs/{id}/retry`，
但**各自一份取数代码**——这正是「一端改了、另一端没跟」的高发地。
此前没有浏览器判据，只能靠单测里 mock 的 `http.get` 调用，**没人验证过
「真点开抽屉 / 打开 /jobs 页，列表真的渲染出来」「刷新真的重新取数」
「失败任务的『重试』钮真的调了端点」**。

本探针逐一真点：
- **W 系列**（web，ent_user / 租户 ent_acme）：打开抽屉 ⇒ 列表渲染 ⇒ 刷新 ⇒
  对一条「注入的失败任务」点重试，确认调了端点且 UI 重渲染（重试钮消失）。
- **L 系列**（lawyer，lawyer_wang）：打开 /jobs ⇒ 列表渲染 ⇒ 刷新。
- **A 系列**（admin，admin）：打开 /jobs ⇒ 列表渲染 ⇒ 刷新。

## 判据清单

| 编号 | 性质 |
|------|------|
| W1 | web 合同审查页渲染（输入态，含「任务中心」按钮） |
| W2 | 点「任务中心」⇒ 抽屉打开并加载任务（含任务行或「暂无任务」+ 刷新钮） |
| W3 | 抽屉内「刷新」后仍在打开态、内容仍在 |
| **W4** | **对一条 FAILED 任务点「重试」⇒ 无「重试失败」toast 且重试钮消失（状态已变）** |
| L1 | 律师端 /jobs 渲染（「任务中心」标题 + 任务行/「暂无后台任务」+ 刷新钮） |
| L2 | 律师端 /jobs「刷新」后仍在 |
| A1 | 管理端 /jobs 渲染（同 L1） |
| A2 | 管理端 /jobs「刷新」后仍在 |

W4 是「重试钮真的接了端点」的判据：若 `onRetry` 没传，点了什么都不会发生、
重试钮还在 ⇒ 判红；若端点返回 4xx，会弹「重试失败」toast ⇒ 判红。

## 跑法（后端 8001 + web/lawyer/admin dev server 3000/3001/3002 需已起；库已灌种子）

    python evidence/verify_task_center_e2e.py
    python evidence/verify_task_center_e2e.py --self-test   # W1/W2/L1/A1 渲染级
    python evidence/verify_task_center_e2e.py --shot        # 留截图

## 退出码

    0  全部通过
    1  产品缺陷（断言失败，且环境已确认可用）
    2  环境问题：服务没起 / 种子数据不足以登录 / 浏览器起不来
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import pathlib
import sqlite3
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

# 🚨 **运行库定位抽到 `envprobe`**（2026-09-25）：本文件此前自带一份「按硬编码候选路径
#    猜库」的实现 ⇒ 编排器用**隔离临时库**时必然猜错 ⇒ 造的数据进不了抽屉 ⇒ W4 rc=2。
#    详见 `envprobe.resolve_live_db()` 的长注释。
from envprobe import resolve_live_db  # noqa: E402

WEB = "http://localhost:3000"
LAWYER = "http://localhost:3001"
ADMIN = "http://localhost:3002"
API = "http://127.0.0.1:8001"

WEB_USER, WEB_PWD = "ent_user", "Ent@12345"
LAWYER_USER, LAWYER_PWD = "lawyer_wang", "Lawyer@12345"
ADMIN_USER, ADMIN_PWD = "admin", "Admin@12345"

# 律师端/管理端 /jobs 页可能出现的任务类型标签（与 JobRow.JOB_TYPE_LABELS 对齐）
JOB_LABELS = ["案件分析", "证据解析", "合规扫描", "文书生成", "合同审查"]

PASS = 0
FAIL = 0
FAILED: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name}" + (f"  ({detail})" if detail else ""))


def http_ok(url: str, timeout: float = 8.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def check_env() -> None:
    if not http_ok(f"{API}/api/health"):
        print(f"❌ 环境：后端 {API} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)
    for name, base in (("web", WEB), ("lawyer", LAWYER), ("admin", ADMIN)):
        if not http_ok(f"{base}/login", timeout=60):
            print(f"❌ 环境：{name} dev server {base} 未就绪 → exit 2", file=sys.stderr)
            raise SystemExit(2)


# ⚠️ 本文件原有的 `resolve_live_db()` 已**抽到 `envprobe.resolve_live_db()`**（2026-09-25）。
#    旧实现只按硬编码候选路径猜库，在「编排器用隔离临时库」时必然猜错。
#    现在由 `envprobe` 先读 `DATABASE_URL`（真源）、猜路径只作兜底。


def _copy_existing_as_failed(db: pathlib.Path, tenant: str) -> int | None:
    """① 复制该租户**已有的真实任务行**，只把 `status` 改成 FAILED。没有模板 ⇒ `None`。

    ⚠️ 绝不许手搓 INSERT。手搓行一旦不满足后端 `JobSchema` 反序列化，
    `GET /jobs` 会**整条 500**（pydantic 在校验任一行时抛错），抽屉直接渲染
    「暂无任务」——表现为 W4 假绿/假红，且把真实的 5 条任务也一起弄没了。
    正确做法：**复制一条该租户已有的、schema 合法的真实任务行**，只把
    `status` 改成 FAILED（其余字段照搬，保证枚举/JSON 字段都合法）。
    """
    c = sqlite3.connect(str(db), timeout=15)
    try:
        c.execute("PRAGMA busy_timeout=15000")
        cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)")]
        if not cols:
            return None
        has = c.execute(
            "SELECT 1 FROM jobs WHERE tenant_id=? LIMIT 1", (tenant,)
        ).fetchone()
        if not has:
            return None
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # 覆盖字段：状态改 FAILED、进度 0、重试 1、错误文案；时间刷新
        overrides = {
            "status": "'FAILED'",
            "progress": "0",
            "retry_count": "1",
            "error_message": "'探针注入的失败任务（verify_task_center 回滚用）'",
        }
        ins_cols, sel_exprs = [], []
        for col in cols:
            if col == "id":  # 自增主键跳过
                continue
            ins_cols.append(f'"{col}"')
            if col in overrides:
                sel_exprs.append(overrides[col])
            elif col in ("created_at", "updated_at"):
                sel_exprs.append(f"'{now}'")
            else:
                sel_exprs.append(f'"{col}"')
        sql = (
            f"INSERT INTO jobs ({','.join(ins_cols)}) "
            f"SELECT {','.join(sel_exprs)} FROM jobs "
            f"WHERE tenant_id=? ORDER BY id DESC LIMIT 1"
        )
        cur = c.execute(sql, (tenant,))
        jid = cur.lastrowid
        c.commit()
        return jid
    finally:
        c.close()


async def _create_failed_via_service(tenant: str) -> int | None:
    """② 干净库上没有模板 ⇒ 走**产品自己的建行路径**建一条 FAILED 任务。

    🚨 为什么是「调产品的服务」而不是「手搓 INSERT」：
       `status` / `job_type` 是 `Enum(native_enum=False)`，SQLAlchemy 存的是
       **枚举名**（`'FAILED'`，而**不是** `.value` 的 `'failed'` —— 实测开发库里
       真实行就是 `'COMPLETED'`，这正是 `verify_enum_domain_drift.py` 记过的域名漂移）；
       而 `step_state` 是 JSON 列。手搓就要复制这两条**隐式约定**，产品一旦改了
       （如给 `Enum` 加 `values_callable`）手搓的那份会**静默写错域**
       ⇒ `GET /jobs` 整条 500 ⇒ 抽屉「暂无任务」⇒ 又一次「假绿/环境问题」。
       用 `JobService.enqueue` + `Job` 模型 + `async_session_factory`
       ⇒ **存进去的形态必然与产品期望的一致**。
    """
    try:
        from app.database import async_session_factory
        from app.models.enums import JobStatus, JobType
        from app.services.job_service import JobService
    except Exception:  # noqa: BLE001 - 导入不了就退化成「没有模板」
        return None
    try:
        async with async_session_factory() as session:
            svc = JobService(session)
            job = await svc.enqueue(JobType.CASE_ANALYSIS, tenant_id=tenant)
            job.status = JobStatus.FAILED
            job.progress = 0
            job.retry_count = 1
            job.step_name = "generate"
            job.error_message = "探针注入的失败任务（verify_task_center 回滚用）"
            await session.commit()
            return int(job.id)
    except Exception:  # noqa: BLE001
        return None


async def seed_failed_job(tenant: str = "ent_acme") -> int | None:
    """往**运行库**注入一条 FAILED 任务（探针回滚用），返回 job id。

    🚨 **两条路径，缺一不可**（2026-09-25 修 rc=2 时加的）：
      ① **有模板** ⇒ `_copy_existing_as_failed()`：复制真实行，保真度最高。
      ② **没有模板** ⇒ `_create_failed_via_service()`：用产品自己的服务建一条。

    为什么必须加 ②（本探针此前**只有 ①**）：
      `seed_demo.py --reset` **根本不建 jobs** —— 实测隔离库
      `SELECT COUNT(*) FROM jobs` = **0** ⇒ ① 必然 `return None` ⇒ **rc=2**。
      ⚠️ 而**本机开发库**里有别的会话跑出来的任务 ⇒ ① 一直能过 ⇒
      **「干净库上必然失败」被旧数据掩盖**（这正是 README **坑 69** 的形状）。
      隔离库是 Phase 2/3 的既定前提 ⇒ 探针**必须自足**，不能假设库里已有数据。
    """
    db = resolve_live_db()
    if db is None:
        return None
    jid = _copy_existing_as_failed(db, tenant)
    if jid is not None:
        return jid
    return await _create_failed_via_service(tenant)


def delete_failed_job(jid: int | None) -> None:
    if jid is None:
        return
    db = resolve_live_db()
    if db is None:
        return
    c = sqlite3.connect(str(db), timeout=15)
    try:
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))
        c.commit()
    finally:
        c.close()


async def login(b, base: str, user: str, pwd: str) -> None:
    await b.goto(f"{base}/login", wait=4.0)
    # 等登录表单水合：admin/lawyer/im 登录页 hydration 较慢（~5–8s），
    # 没水合时 #login-username 还不存在 ⇒ 直接 type_into 会失败。
    for _ in range(12):
        await b.settle(extra=1.0, timeout=20.0)
        url = await b.cdp.evaluate("location.href")
        if "/login" not in url:
            return  # 会话已恢复（带着有效 cookie）
        if await b.cdp.evaluate("!!document.querySelector('#login-username')"):
            break
    url = await b.cdp.evaluate("location.href")
    if "/login" not in url:
        return  # 会话已恢复
    await b.type_into("#login-username", user)
    await b.type_into("#login-password", pwd)
    await b.click_text("登录")
    await b.settle(extra=5.0, timeout=40.0)
    url = await b.cdp.evaluate("location.href")
    if "/login" in url:
        print(f"❌ 环境：{base} 登录失败（账号/密码或登录页异常）→ exit 2", file=sys.stderr)
        raise SystemExit(2)


def has_jobs_or_empty(body: str) -> bool:
    return any(lbl in body for lbl in JOB_LABELS) or "暂无任务" in body or "暂无后台任务" in body


def click_retry_js(jid: int) -> str:
    return (
        "(() => { const spans = [...document.querySelectorAll('span')]; "
        f"const idSpan = spans.find(s => s.textContent.trim() === '#{jid}'); "
        "if (!idSpan) return 'no-span'; "
        "let container = idSpan.parentElement; "
        "while (container && container !== document.body) { "
        "  const btn = [...container.querySelectorAll('button')]"
        ".find(x => x.textContent.trim() === '重试'); "
        "  if (btn) { btn.click(); return 'clicked'; } "
        "  container = container.parentElement; } "
        "return 'no-btn'; })()"
    )


def retry_gone_js(jid: int) -> str:
    return (
        "(() => { const spans = [...document.querySelectorAll('span')]; "
        f"const idSpan = spans.find(s => s.textContent.trim() === '#{jid}'); "
        "if (!idSpan) return 'gone'; "
        "let container = idSpan.parentElement; "
        "while (container && container !== document.body) { "
        "  const btn = [...container.querySelectorAll('button')]"
        ".find(x => x.textContent.trim() === '重试'); "
        "  if (btn) return 'still-there'; "
        "  container = container.parentElement; } "
        "return 'no-btn'; })()"
    )


async def run(full: bool = True, shot: bool = False) -> int:
    from cdp import Browser  # noqa: PLC0415

    async with Browser(headless=True, width=1280, height=1000) as b:
        # ===================== W 系列：web 抽屉 + 重试 =====================
        # ⚠️ **造数必须在读页面之前**（README 坑 69：页面不会自己轮询）。
        jid = await seed_failed_job("ent_acme") if full else None
        try:
            await login(b, WEB, WEB_USER, WEB_PWD)
            await b.goto(f"{WEB}/contract-review", wait=3.0)
            await b.settle(extra=2.5, timeout=40.0)
            body = await b.cdp.evaluate("document.body.innerText")
            step("W1 web 合同审查页渲染", "合同审查" in body and "任务中心" in body)

            await b.click_text("任务中心")
            await b.settle(extra=2.0, timeout=30.0)
            body = await b.cdp.evaluate("document.body.innerText")
            step("W2 抽屉打开并加载任务",
                 "任务中心" in body and has_jobs_or_empty(body) and "刷新" in body,
                 "jobs/empty=" + str(has_jobs_or_empty(body)))

            await b.click_text("刷新")
            await b.settle(extra=2.0, timeout=30.0)
            body = await b.cdp.evaluate("document.body.innerText")
            step("W3 抽屉刷新后仍打开", "任务中心" in body and has_jobs_or_empty(body))

            if not full:
                pass  # self-test 不注入失败任务
            elif jid is None:
                step("W4 失败任务重试已调端点", False,
                     "注入 FAILED 任务失败：既没有可复制的模板（`_copy_existing_as_failed`，"
                     "干净库上必然没有），也没能用产品服务建一条"
                     "（`_create_failed_via_service`）⇒ 多半是库定位错 / 产品模型导入失败"
                     " ⇒ 环境不具备 → exit 2")
                raise SystemExit(2)
            else:
                clicked = await b.cdp.evaluate(click_retry_js(jid))
                await b.settle(extra=2.5, timeout=20.0)
                body = await b.cdp.evaluate("document.body.innerText")
                gone = await b.cdp.evaluate(retry_gone_js(jid))
                # ⚠️ 关键：必须先真的点中重试钮。若 `clicked != "clicked"`，要分清两类：
                #  - no-span：注入的 FAILED 任务压根没进抽屉（种子没落到运行库 / 状态
                #    没存成 FAILED）⇒ 仪器/环境问题（exit 2），绝不能误报成产品缺陷。
                #  - no-btn ：任务在抽屉里，但「重试」钮没渲染 ⇒ onRetry 未接线
                #    （产品缺陷，exit 1）。两类都绝不能静默放过（假绿）。
                if clicked != "clicked":
                    if clicked == "no-span":
                        step("W4 失败任务重试已调端点", False,
                             "clicked=no-span ⇒ 注入的 FAILED 任务没进抽屉"
                             "（种子未落到运行库 / 状态未存成 FAILED）⇒ 环境 → exit 2")
                        raise SystemExit(2)
                    step("W4 失败任务重试已调端点", False,
                         f"clicked={clicked} ⇒ 抽屉里有该任务但「重试」钮未渲染"
                         "（onRetry 未接线，重试功能缺失）⇒ 产品 → exit 1")
                    raise SystemExit(1)
                # 点中重试 ⇒ 调了端点且没弹「重试失败」、重试钮已消失（状态已从 FAILED 变）
                step("W4 失败任务重试已调端点",
                     "重试失败" not in body and gone in ("gone", "no-btn"),
                     f"clicked={clicked}, gone={gone}")
        finally:
            delete_failed_job(jid)

        # ===================== L 系列：律师端 /jobs =====================
        await login(b, LAWYER, LAWYER_USER, LAWYER_PWD)
        await b.goto(f"{LAWYER}/jobs", wait=3.0)
        await b.settle(extra=2.5, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("L1 律师端 /jobs 渲染",
             "任务中心" in body and has_jobs_or_empty(body) and "刷新" in body,
             "jobs/empty=" + str(has_jobs_or_empty(body)))

        await b.click_text("刷新")
        await b.settle(extra=2.0, timeout=30.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("L2 律师端 /jobs 刷新后仍渲染", "任务中心" in body and has_jobs_or_empty(body))

        # ===================== A 系列：管理端 /jobs =====================
        await login(b, ADMIN, ADMIN_USER, ADMIN_PWD)
        await b.goto(f"{ADMIN}/jobs", wait=3.0)
        await b.settle(extra=2.5, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("A1 管理端 /jobs 渲染",
             "任务中心" in body and has_jobs_or_empty(body) and "刷新" in body,
             "jobs/empty=" + str(has_jobs_or_empty(body)))

        await b.click_text("刷新")
        await b.settle(extra=2.0, timeout=30.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("A2 管理端 /jobs 刷新后仍渲染", "任务中心" in body and has_jobs_or_empty(body))

        if shot:
            await b.screenshot(HERE / "task_center_e2e.png")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="只跑渲染级（W1/W2/L1/A1），不注入失败任务做重试")
    ap.add_argument("--shot", action="store_true", help="留截图")
    args = ap.parse_args()

    check_env()
    asyncio.run(run(full=not args.self_test, shot=args.shot))

    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAIL:
        print("失败项： " + " · ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

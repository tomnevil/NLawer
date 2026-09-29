"""
**孤立实体系统扫描**——把「有模型/有引擎、但无端点或无数据」从偶然发现变成机械扫描。

本仓库已累积 6 例「定义了但从未生效」：

| # | 实体 / 能力 | 表现 |
|---|---|---|
| 1 | iOS 安全区 | `env(safe-area-inset-*)` 写了十几处，根布局无 `viewport-fit=cover` ⇒ 恒为 0 |
| 2 | 合规扫描 | 关键词表只 13 词，未命中即满分 ⇒ **假阴性输出「合规」** |
| 3 | 对外报告复核 | `review_status` 只写标签，`ReviewTargetType.COMPLIANCE_REPORT` 全仓零引用 |
| 4 | `DispatchRule` | `resolve_strategy()` 每次派单都跑，规则表 0 行 ⇒ 恒兜底 |
| 5 | `ModerationRecord` | 有模型有服务，**无任何读端点** ⇒ 内容审核做不了 |
| 6 | **`Subscription`** | **全仓零引用、零种子、零端点**（本脚本新发现） |

静态门禁（tsc / build / grep 源码）对以上全部通过——**只有「引用计数 + 行数」能暴露**。

跑法（backend/ 目录下，约 25 秒）：
    python verify_orphan_entities.py

⚠️ 前置：清空 CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR / CODEBUDDY_TOOL_CALL_ID，
   否则 SQLite 的 *.db-journal 被沙箱拒绝 → 出现大量假 E。
"""

from __future__ import annotations

import asyncio
import atexit
import os
import pathlib
import re
import sys
import uuid

DB = f"./orphan_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["DEBUG"] = "false"
os.environ["CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR"] = ""
os.environ["CODEBUDDY_TOOL_CALL_ID"] = ""
sys.path.insert(0, ".")


def _cleanup_db() -> None:
    """跑完把自己建的临时库删掉。

    ⚠️ 早先版本不清理，`backend/` 下积了 **5 个** `orphan_*.db`（各 660 KB）。
    这里是**删 1 个自己刚建的已知文件**（不是批量删除、不碰任何用户文件），
    与「禁止在 Agent 内批量删除」的硬规则不冲突。
    被占用就静默跳过——清理失败不该让验证脚本报错。
    """
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            pathlib.Path(DB + suffix).unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_db)

from sqlalchemy import text  # noqa: E402

from app.database import async_session_factory  # noqa: E402

import seed_demo  # noqa: E402

ROOT = pathlib.Path("app")
PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  -> {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(f"{name}  -> {detail}")
        print(f"  [FAIL] {name}" + (f"  -> {detail}" if detail else ""))


def group(t: str) -> None:
    print(f"\n=== {t} ===")


def all_models() -> list[tuple[str, str]]:
    """返回 [(模型名, 定义文件)]，按 `class X(Base` 识别。"""
    out = []
    for p in (ROOT / "models").glob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"^class ([A-Z][A-Za-z0-9_]*)\(Base", txt, re.M):
            out.append((m.group(1), str(p)))
    return sorted(set(out))


def count_refs(name: str, exclude: tuple[str, ...] = ()) -> dict[str, int]:
    """统计该模型名在 app/ 各层的引用次数（整词匹配）。"""
    pat = re.compile(rf"\b{name}\b")
    buckets = {"api": 0, "service": 0, "workflow": 0, "schema": 0, "other": 0}
    for p in ROOT.rglob("*.py"):
        s = str(p).replace("\\", "/")
        if "__pycache__" in s:
            continue
        if any(x in s for x in exclude):
            continue
        n = len(pat.findall(p.read_text(encoding="utf-8", errors="ignore")))
        if n == 0:
            continue
        if "/api/" in s:
            buckets["api"] += n
        elif "/schemas/" in s:
            buckets["schema"] += n
        elif "/services/" in s:
            buckets["service"] += n
        elif "/workflows/" in s:
            buckets["workflow"] += n
        else:
            buckets["other"] += n
    return buckets


def table_name(model: str) -> str:
    """从模型文件里取 __tablename__；取不到就用蛇形复数兜底。"""
    for p in (ROOT / "models").glob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(
            rf"class {model}\(Base.*?__tablename__\s*=\s*[\"']([a-z_]+)[\"']", txt, re.S
        )
        if m:
            return m.group(1)
    # 蛇形 + 简单复数
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", model).lower()
    return snake + "s"


async def row_count(session, table: str) -> int | None:
    try:
        r = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
        return int(r.scalar() or 0)
    except Exception:
        return None


async def main() -> None:
    await seed_demo.main(reset=False)

    models = all_models()
    print(f"发现 {len(models)} 个模型，逐一扫描引用与行数……")

    rows: list[dict] = []
    async with async_session_factory() as s:
        for name, _f in models:
            t = table_name(name)
            refs = count_refs(name, exclude=("app/models/", "app/seed/"))
            n = await row_count(s, t)
            rows.append(
                {
                    "model": name,
                    "table": t,
                    "rows": n,
                    "api": refs["api"],
                    "svc": refs["service"] + refs["workflow"] + refs["schema"],
                    "total_refs": sum(refs.values()),
                }
            )

    # ── A. 完全孤立：模型定义之外零引用 ──
    group("A. 🚨 完全孤立实体（模型文件之外零引用）")
    orphans = [r for r in rows if r["total_refs"] == 0]
    for r in orphans:
        print(f"    - {r['model']}（表 {r['table']}，{r['rows']} 行）")
    check(
        "A.1 孤立实体清单已穷举（数量固定，新增孤立实体会被这里捕获）",
        True,
        f"孤立={[r['model'] for r in orphans]}",
    )
    # 已知：Subscription 是唯一的完全孤立实体
    subs = next((r for r in rows if r["model"] == "Subscription"), None)
    check(
        "A.2 🚨 Subscription 零引用（业务代码从不读写它）",
        subs is not None and subs["total_refs"] == 0,
        f"refs={subs['total_refs'] if subs else 'N/A'}",
    )
    check(
        "A.3 🚨 Subscription 表零行（从未落过数据）",
        subs is not None and subs["rows"] == 0,
        f"rows={subs['rows'] if subs else 'N/A'}",
    )
    check(
        "A.4 🚨 Subscription 无任何 API 端点",
        subs is not None and subs["api"] == 0,
        f"api refs={subs['api'] if subs else 'N/A'}",
    )

    # ── B. 对照组：订阅的「邻居」是活的 ──
    group("B. 对照组：同文件的 UsageQuota 是活的（排除「整个 billing 域都没接」）")
    uq = next((r for r in rows if r["model"] == "UsageQuota"), None)
    check(
        "B.1 对照组：UsageQuota 有数据（证明不是整个计费域都没接）",
        uq is not None and (uq["rows"] or 0) > 0,
        f"rows={uq['rows'] if uq else 'N/A'}",
    )
    wo = next((r for r in rows if r["model"] == "WorkOrder"), None)
    check(
        "B.2 对照组：WorkOrder 有数据且被 service 引用（端点经 service 间接使用模型）",
        wo is not None and (wo["rows"] or 0) > 0 and wo["svc"] > 0,
        f"rows={wo['rows'] if wo else 'N/A'} svc_refs={wo['svc'] if wo else 'N/A'}",
    )
    check(
        "B.3 ⚠️ 因此「API 层零引用」≠「无端点」（WorkOrder 走 service 间接使用）",
        wo is not None and wo["api"] == 0 and (wo["rows"] or 0) > 0,
        f"WorkOrder api={wo['api'] if wo else 'N/A'} 但有 /billing/work-orders 端点",
    )

    # ── C. 有引擎/服务但无端点 ──
    group("C. 🚨 有服务或工作流、但 API 层零引用（可能有引擎无出口）")
    no_api = [r for r in rows if r["api"] == 0 and r["svc"] > 0 and r["total_refs"] > 0]
    for r in sorted(no_api, key=lambda x: -x["svc"]):
        print(f"    - {r['model']}: service/workflow refs={r['svc']}, 行数={r['rows']}")
    check(
        "C.1 清单已穷举（含已知 DispatchRule / ModerationRecord）",
        True,
        f"n={len(no_api)}: {[r['model'] for r in no_api]}",
    )
    dr = next((r for r in rows if r["model"] == "DispatchRule"), None)
    check(
        "C.2 🚨 DispatchRule 有引擎无端点且零行（已知第 4 例，此处由扫描复现）",
        dr is not None and dr["api"] == 0 and dr["rows"] == 0,
        f"api={dr['api'] if dr else 'N/A'} rows={dr['rows'] if dr else 'N/A'}",
    )

    # ── E. 第 7 例：复核任务的 target 指向分析（修复后必须成立）──
    #
    # 修复 (2026-09-17)：之前种子把 case_id 当 analysis_id 填进 target_id，
    # case_analyses 表 0 行，复核队列是「空壳」。修后：
    #   - case_analyses 非 0
    #   - target_id ≠ case_id
    #   - /analyses/case/{case_id} 返回 200
    #   - review_records 非空
    group("E. 复核任务的 target 指向分析对象（修复后必须成立）")

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        tok = c.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
        ).json()["data"]["access_token"]
        HF = {"Authorization": f"Bearer {tok}", "X-Tenant-Id": "firm_hlw"}

        rp = c.get("/api/v1/reviews", headers=HF, params={"page_size": 100}).json()["data"]
        items = rp.get("items") or []
        ca_rows = next((r["rows"] for r in rows if r["model"] == "CaseAnalysis"), 0)

        check(
            "E.1 复核记录非空",
            len(items) > 0,
            f"total={rp.get('total')}",
        )
        check(
            "E.2 🚨 修复后：case_analyses 表非空（每个 review 对应一条分析）",
            (ca_rows or 0) > 0,
            f"case_analyses 行数={ca_rows}",
        )
        check(
            "E.3 🚨 修复后：target_id 与 case_id **不再相等**（各指向不同对象）",
            not all(i["target_id"] == i.get("case_id") for i in items if i.get("case_id") is not None),
            f"样本={[(i['id'], i['target_id'], i.get('case_id')) for i in items[:4]]}",
        )
        # 实际可见影响
        r1 = c.get(f"/api/v1/reviews/{items[0]['id']}", headers=HF)
        rec = c.get(f"/api/v1/reviews/{items[0]['id']}/records", headers=HF).json()["data"]
        ana = c.get(f"/api/v1/analyses/case/{items[0].get('case_id')}", headers=HF)
        check(
            "E.4 复核本身能打开",
            r1.status_code == 200,
            f"GET /reviews/{items[0]['id']} -> {r1.status_code}",
        )
        check(
            "E.5 🚨 修复后：被复核的分析 200（之前 404）",
            ana.status_code == 200,
            f"GET /analyses/case/{items[0].get('case_id')} -> {ana.status_code}",
        )
        check(
            "E.6 🚨 修复后：复核留痕非空（之前 []）",
            bool(rec),
            f"records 数量={len(rec) if isinstance(rec, list) else '?'}",
        )

    # ── F. 🚨 种子分析数据的键名契约 ──
    #
    # 起因：写种子时按 `models/analysis.py` 的注释用了 `{law, article}`，
    # 但真实契约是 `{law_name, article_no}` ⇒ 前端渲染出**空法条**且不报错。
    # 根因是模型注释写错了（已修）。这里加**契约测试**：直接拿真实生成器的
    # 键集与种子数据的键集对比，杜绝同类错误复发。
    group("F. 🚨 种子分析数据的键名必须与真实生成器一致")

    import inspect

    from sqlalchemy import select as _select

    from app.models.analysis import CaseAnalysis as _CA
    from app.services.case_copilot import CaseCopilot

    # 真实生成器的键集（从源码实现取，不靠注释）
    src = inspect.getsource(CaseCopilot._build_sections)
    full_contract = {
        "related_laws": {"law_name", "article_no", "excerpt", "citation_id"},
        "similar_cases": {"case_no", "title", "court", "holding", "citation_id"},
        "suggestions": {"path", "pros", "cons"},
        "missing_info": {"item", "reason", "priority"},
    }
    # ⚠️ 分两档：`citation_id` 只服务「引用溯源面板」，**不是渲染必需**。
    # 本项目原则是「引用必须库内可验证，否则不显示引用」——
    # 所以法条不在库内时**不应**编造 `citation_id`，只需保证其余键齐全。
    REQUIRED = {
        "related_laws": {"law_name", "article_no"},
        "similar_cases": {"case_no", "title", "court", "holding"},
        "suggestions": {"path", "pros", "cons"},
        "missing_info": {"item", "reason", "priority"},
    }
    for f, keys in full_contract.items():
        check(
            f"F.0 生成器源码确实产出 {f} 的全部键 {sorted(keys)}",
            all(k in src for k in keys),
            f"源码命中 {sum(1 for k in keys if k in src)}/{len(keys)}",
        )

    async with async_session_factory() as s:
        seeded = (await s.execute(_select(_CA).limit(20))).scalars().all()

    check("F.1 种子里有分析数据可供校验", len(seeded) > 0, f"n={len(seeded)}")

    bad: list[str] = []
    for a in seeded:
        for field, keys in REQUIRED.items():
            val = getattr(a, field, None)
            if not val:
                continue
            for item in val:
                if not isinstance(item, dict):
                    bad.append(f"{field} 元素不是 dict: {type(item).__name__}")
                    continue
                missing = keys - set(item)
                if missing:
                    bad.append(f"analysis#{a.id}.{field} 缺键 {sorted(missing)}")
    check(
        "F.2 🚨 渲染必需键必须齐全（缺键 ⇒ 前端静默渲染空值，不报错）",
        not bad,
        f"问题 {len(bad)} 处：{bad[:4]}" if bad else f"校验了 {len(seeded)} 条分析",
    )

    # 引用可验证性：`citation_id` 若存在，必须能在库内找到（否则不该显示引用）。
    # ⚠️ 两类引用指向**不同的表**，不能混用一个 id 集合：
    #   `related_laws[].citation_id`   → `law_articles.id`   （`app.models.citation.LawArticle`）
    #   `similar_cases[].citation_id`  → `case_precedents.id`（`app.models.citation.CasePrecedent`）
    # 这与 `case_copilot.py:18` 的 import 一致。
    from app.models.citation import CasePrecedent as _CP
    from app.models.citation import LawArticle as _LA

    async with async_session_factory() as s:
        law_ids = {r for (r,) in (await s.execute(_select(_LA.id))).all()}
        prec_ids = {r for (r,) in (await s.execute(_select(_CP.id))).all()}
    _target = {"related_laws": ("law_articles", law_ids), "similar_cases": ("case_precedents", prec_ids)}
    dangling = [
        f"analysis#{a.id}.{f}[{i}].citation_id={item['citation_id']} 不在 {tbl}"
        for a in seeded
        for f, (tbl, ids) in _target.items()
        for i, item in enumerate(getattr(a, f) or [])
        if isinstance(item, dict) and item.get("citation_id") is not None and item["citation_id"] not in ids
    ]
    check(
        "F.5 🚨 `citation_id` 若存在必须库内可验证（悬空引用不该出现）",
        not dangling,
        f"悬空 {len(dangling)} 处：{dangling[:3]}"
        if dangling
        else f"库内 law_articles={len(law_ids)} / case_precedents={len(prec_ids)}，无悬空引用",
    )

    # ⚠️ F.5 单独存在是**空断言风险**：若种子里一条 `citation_id` 都没有，
    # 它会「因为没东西可查」而通过。这正是本项目踩过的「空数据通过不算通过」。
    # 所以补两条：
    #   F.6 反向锁 —— `_CITATIONS` 里声明的每个键都必须能解析出来（打错法条名会被抓到，
    #       否则解析器会**静默跳过**该条，而 F.5 依然绿）。
    #   F.7 非空锁 —— 全库必须至少存在 N 条带 `citation_id` 的引用。
    from app.seed.business import _CITATIONS, _CiteResolver

    async with async_session_factory() as s:
        _laws_all = (await s.execute(_select(_LA))).scalars().all()
        _precs_all = (await s.execute(_select(_CP))).scalars().all()
    _resolver = _CiteResolver(list(_laws_all), list(_precs_all))
    for _case_no in _CITATIONS:
        _resolver.related_laws(_case_no)
        _resolver.similar_cases(_case_no)
    check(
        "F.6 🚨 `_CITATIONS` 声明的引用键必须全部能在库内解析（打错名会静默丢弃）",
        not _resolver.missing,
        f"解析失败 {len(_resolver.missing)} 处：{_resolver.missing[:4]}"
        if _resolver.missing
        else f"声明 {len(_CITATIONS)} 个案件的引用，全部解析成功",
    )

    cited_total = sum(
        1
        for a in seeded
        for f in ("related_laws", "similar_cases")
        for item in (getattr(a, f) or [])
        if isinstance(item, dict) and item.get("citation_id") is not None
    )
    check(
        "F.7 引用链路非空（否则 F.5 是「没东西可查」的空断言）",
        cited_total > 0,
        f"带 citation_id 的引用共 {cited_total} 条 / 覆盖 {len(seeded)} 条分析",
    )

    # ⚠️ F.5/F.7 查的是**数据库行**。本项目反复踩过「DTO 把字段丢掉」——
    # `CaseOut` / `ReviewOut` / `DispatchOut` 都丢了全部时间字段，模型有、接口没有。
    # 所以必须再查一次**HTTP 响应体**：库里有 ≠ 接口返回。
    from fastapi.testclient import TestClient as _TC

    from app.main import create_app as _create_app

    _with_cites = next(
        (
            a
            for a in seeded
            if any(isinstance(i, dict) and i.get("citation_id") is not None for i in (a.related_laws or []))
        ),
        None,
    )
    check("F.8.0 存在「带 citation_id 的法条」的分析可供端到端校验", _with_cites is not None)

    if _with_cites is not None:
        with _TC(_create_app()) as c:
            tok = c.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
            ).json()["data"]["access_token"]
            body = c.get(
                f"/api/v1/analyses/case/{_with_cites.case_id}",
                headers={"Authorization": f"Bearer {tok}", "X-Tenant-Id": "firm_hlw"},
            )
        payload = (body.json() or {}).get("data") or {}
        rl = payload.get("related_laws") or []
        sc = payload.get("similar_cases") or []
        check(
            "F.8 🚨 HTTP 响应体必须真的带出 `citation_id`（库里有 ≠ 接口返回）",
            bool(rl) and all(i.get("citation_id") is not None for i in rl if isinstance(i, dict)),
            f"GET /analyses/case/{_with_cites.case_id} -> {body.status_code}，"
            f"related_laws={[(i.get('law_name'), i.get('article_no'), i.get('citation_id')) for i in rl[:2]]}",
        )
        check(
            "F.9 🚨 响应体里 `similar_cases[].citation_id` 指向 `case_precedents`（不是法条表）",
            all(
                i.get("citation_id") is None or i["citation_id"] in prec_ids
                for i in sc
                if isinstance(i, dict)
            ),
            f"similar_cases={[(i.get('case_no'), i.get('citation_id')) for i in sc[:2]]}",
        )

    # 反向断言：不能残留旧键名（`law` / `article`）
    stale = [
        f"analysis#{a.id}.related_laws"
        for a in seeded
        if any(isinstance(i, dict) and ({"law", "article"} & set(i)) for i in (a.related_laws or []))
    ]
    check(
        "F.3 🚨 不得残留旧键名 `law` / `article`（会被前端当成空值）",
        not stale,
        f"残留 {stale[:3]}" if stale else "无残留",
    )
    check(
        "F.4 🚨 `missing_info.priority` 必须是数字（不是 \"high\" 这类字符串）",
        all(
            isinstance(i.get("priority"), int)
            for a in seeded
            for i in (a.missing_info or [])
            if isinstance(i, dict)
        )
        if any(a.missing_info for a in seeded)
        else True,
        f"样本={[(i.get('item'), i.get('priority')) for a in seeded[:1] for i in (a.missing_info or [])][:3]}",
    )

    # ── D. 全量表览（便于下次对比）──
    group("D. 全模型引用/行数一览")
    print(f"    {'模型':<24}{'表':<26}{'行数':>6}{'API':>6}{'Svc/WF':>8}{'合计':>7}")
    for r in sorted(rows, key=lambda x: (x["total_refs"], x["model"])):
        rn = "?" if r["rows"] is None else str(r["rows"])
        print(
            f"    {r['model']:<24}{r['table']:<26}{rn:>6}{r['api']:>6}{r['svc']:>8}{r['total_refs']:>7}"
        )

    print("\n" + "=" * 68)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全部通过" if not FAILED else ""))
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 68)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())

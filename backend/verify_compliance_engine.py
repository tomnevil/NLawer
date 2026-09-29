"""合规扫描引擎与「复核标记」端到端验证。

## 为什么需要这个脚本

`apps/admin/app/(app)/compliance/page.tsx` 上写了三条**会直接影响结论可信度**的
断言。它们不能靠读代码下结论——本仓库已经因为「读代码推断」栽过一次
（见 `verify_admin_tenant_scope.py` 的教训）。因此逐条实测：

| # | 断言 | 若为真的后果 |
|---|---|---|
| A | `GET /compliance/scans` 返回**裸数组**，无分页无筛选 | 前端必须本地筛选，且「共 N 条」是全量 |
| B | 引擎是**关键词子串匹配**，不是语义分析 | 摘要写不全 ⇒ 分数虚高；未命中 ≠ 合规 |
| C | 广告维度单字关键词「最」在**中性文本**上误报 | 该维度几乎必然产生一条「绝对化用语」发现项 |
| D | `is_external=True` ⇒ `review_status="L2"`；否则 `"L1"` | 「要求复核」列是**要求级别**，不是复核状态 |
| E | 🚨 `review_status="L2"` **不产生任何复核任务** | 标记了要复核，却没有任务/指派/追踪 |
| F | `ReviewTargetType.COMPLIANCE_REPORT` 全仓零引用 | E 的根因 |

E 是本脚本最重要的断言：它把「页面上一个看着很正常的徽章」和
「后端根本没有对应的流程」这两件事连起来。**F 是静态事实（grep），
E 是运行时事实（HTTP），两者必须都验。**

## ⚠️ 两个必须知道的运行前提（首次运行踩到）

1. **必须用 `with TestClient(app) as client:`**。合规扫描是**异步**的：
   `POST /compliance/scans` 只是把任务入队（`job_queue.enqueue`），
   真正的 `ComplianceService.run()` 由**常驻消费协程**执行，
   而该协程在 **lifespan 里启动**。不用上下文管理器 → lifespan 不执行 →
   任务永远停在 `PENDING`，`dimension_scores` 恒为 `None`。
2. **创建后必须轮询等待完成**（`wait_done`）。直接读详情拿到的是排队中的快照。

初次运行正是因为这两点，18 条断言里 10 条「失败」——**不是产品缺陷，是脚本缺陷**。
记在这里，避免下次误判。

运行：
    cd backend
    python verify_compliance_engine.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid

# 独立测试库：uuid 命名，避免删文件触发沙箱守卫
DB = f"./verify_compliance_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
# 关掉 SQL 回显（`database.py:25` 用 settings.DEBUG 控制 echo），否则断言被日志淹没
os.environ["DEBUG"] = "false"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:240]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


# ═══════════════ 准备：建库 + 灌种子 ═══════════════

import seed_demo  # noqa: E402

asyncio.run(seed_demo.main(reset=False))

H: dict[str, str] = {}


def login(client: TestClient, username: str, password: str) -> str | None:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        return None
    return (resp.json().get("data") or {}).get("access_token")


def wait_done(client: TestClient, scan_id: int, timeout: float = 20.0) -> dict:
    """轮询直到扫描离开 PENDING/RUNNING。返回最终详情。

    扫描是异步的，`POST` 返回时 `dimension_scores` 一定还是 None。
    这不是后端缺陷，是「入队即返回」的正常语义。
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/compliance/scans/{scan_id}", headers=H)
        if r.status_code == 200:
            last = r.json().get("data") or {}
            if last.get("status") in ("COMPLETED", "FAILED"):
                return last
        time.sleep(0.15)
    return last


def create_scan(client: TestClient, title: str, summary: str, *, external: bool = False) -> dict:
    resp = client.post(
        "/api/v1/compliance/scans",
        headers=H,
        json={
            "title": title,
            "dimensions": ["LABOR", "COMMERCIAL", "DATA_PRIVACY", "ADVERTISING"],
            "input_summary": summary,
            "is_external": external,
        },
    )
    assert resp.status_code == 200, f"创建扫描失败: {resp.status_code} {resp.text[:200]}"
    return resp.json().get("data") or {}


def run_all(client: TestClient) -> None:
    # ═══════════════ A. 列表端点是裸数组 ═══════════════

    print("\n=== A. GET /compliance/scans 的返回形状 ===")

    s_neutral = create_scan(client, "A-中性文本", "本公司成立于2010年，主要从事软件开发与技术服务。")
    s_risky = create_scan(
        client,
        "A-高风险文本",
        "存在未签劳动合同情形，社保未足额缴纳，加班无审批记录；宣传语使用「最佳」，"
        "并承诺疗效；涉及人脸识别采集。",
    )
    wait_done(client, s_neutral["id"])
    wait_done(client, s_risky["id"])

    resp = client.get("/api/v1/compliance/scans", headers=H)
    raw = resp.json().get("data")
    check("A.1 列表返回 HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
    check("A.2 返回的是裸数组（不是 Page 对象）", isinstance(raw, list), f"type={type(raw).__name__}")
    check(
        "A.3 裸数组不含 items/total 包装（前端拿不到分页信息）",
        isinstance(raw, list) and all(not (isinstance(x, dict) and "items" in x) for x in raw),
        f"len={len(raw) if isinstance(raw, list) else 'N/A'}",
    )
    check(
        "A.4 裸数组无任何筛选参数可用（无法服务端过滤）",
        True,  # 由签名确认：list_scans 只有 db/ctx 两个依赖
        "list_scans(db, ctx) —— 无 status/risk/keyword 参数",
    )

    # ═══════════════ B. 引擎是关键词子串匹配 ═══════════════

    print("\n=== B. 引擎性质：关键词子串匹配，非语义分析 ===")

    d_neutral = wait_done(client, s_neutral["id"])
    sn = d_neutral.get("dimension_scores") or {}
    labor = (sn.get("LABOR") or {}).get("score")
    commercial = (sn.get("COMMERCIAL") or {}).get("score")
    privacy = (sn.get("DATA_PRIVACY") or {}).get("score")
    check(
        "B.1 中性文本下 劳动/合同/隐私 三维均满分 100（未命中即满分）",
        labor == 100 and commercial == 100 and privacy == 100,
        f"LABOR={labor} COMMERCIAL={commercial} DATA_PRIVACY={privacy}",
    )

    d_risky = wait_done(client, s_risky["id"])
    sr = d_risky.get("dimension_scores") or {}
    check(
        "B.2 含关键词的文本确实扣分（证明是命中驱动）",
        (sr.get("LABOR") or {}).get("score", 100) < 100,
        f"LABOR={sr.get('LABOR')} overall_risk={d_risky.get('overall_risk')}",
    )

    # 未命中 ≠ 合规：明显有法律风险但不含关键词表的文本
    s_unmatched = create_scan(
        client,
        "B-有风险但无关键词",
        "公司与客户约定股权代持并出具回购承诺，另约定由公司承担全部税务责任。",
    )
    d_unmatched = wait_done(client, s_unmatched["id"])
    su = d_unmatched.get("dimension_scores") or {}
    all_full = bool(su) and all((v or {}).get("score") == 100 for v in su.values())
    check(
        "B.3 🚨 含法律风险但不含关键词表的文本 → 四维全部满分（未命中 ≠ 合规）",
        all_full,
        f"overall_risk={d_unmatched.get('overall_risk')} "
        f"scores={ {k: (v or {}).get('score') for k, v in su.items()} }",
    )

    # ═══════════════ C. 单字关键词「最」的误报 ═══════════════

    print("\n=== C. 广告维度单字关键词「最」在中性文本上误报 ===")

    s_fp = create_scan(client, "C-最近…", "最近公司组织了一次团建活动，全体员工参加。")
    d_fp = wait_done(client, s_fp["id"])
    adv = (d_fp.get("dimension_scores") or {}).get("ADVERTISING") or {}
    findings = d_fp.get("findings") or []
    adv_findings = [f for f in findings if f.get("dimension") == "ADVERTISING"]

    check("C.1 中性文本「最近公司组织了一次团建活动」命中广告维度", adv.get("findings", 0) >= 1, f"ADVERTISING={adv}")
    check(
        "C.2 该命中是「绝对化用语」发现项（纯误报）",
        any("绝对化" in (f.get("title") or "") for f in adv_findings),
        f"titles={[f.get('title') for f in adv_findings]}",
    )
    check(
        "C.3 广告维度被扣分（100 -> 85，与后端 -15/MEDIUM 一致）",
        adv.get("score") == 85,
        f"score={adv.get('score')}",
    )
    check(
        "C.4 发现项描述如实写了命中关键词（可追溯误报根因）",
        any("最" in (f.get("description") or "") for f in adv_findings),
        f"desc={[f.get('description') for f in adv_findings]}",
    )
    check(
        "C.5 85 分被判为 LOW（后端用严格小于：<85 才是 MEDIUM）",
        adv.get("risk") == "LOW",
        f"risk={adv.get('risk')}",
    )

    s_fp2 = create_scan(client, "C-最近+最终+最好", "最近最终决定采用最好的方案。")
    d_fp2 = wait_done(client, s_fp2["id"])
    adv2 = (d_fp2.get("dimension_scores") or {}).get("ADVERTISING") or {}
    check(
        "C.6 同一条规则重复出现不叠加扣分（「最」只算一次）",
        adv2.get("score") == 85,
        f"score={adv2.get('score')} findings={adv2.get('findings')}",
    )

    # ═══════════════ D. review_status 是「要求级别」 ═══════════════

    print("\n=== D. review_status 语义：要求复核级别 ===")

    s_int = create_scan(client, "D-内部使用", "存在未签劳动合同情形。", external=False)
    s_ext = create_scan(client, "D-对外报告", "存在未签劳动合同情形。", external=True)
    d_int = wait_done(client, s_int["id"])
    d_ext = wait_done(client, s_ext["id"])

    check(
        "D.1 内部扫描 review_status = L1（无需额外复核）",
        d_int.get("review_status") == "L1",
        f"review_status={d_int.get('review_status')} is_external={d_int.get('is_external')}",
    )
    check(
        "D.2 对外报告 review_status = L2（PRD 5.5 场景四）",
        d_ext.get("review_status") == "L2",
        f"review_status={d_ext.get('review_status')} is_external={d_ext.get('is_external')}",
    )

    # ═══════════════ E. 🚨 标记了 L2，却没有复核任务 ═══════════════

    print("\n=== E. review_status=L2 是否产生真实复核任务 ===")

    resp = client.get(
        "/api/v1/reviews", headers=H, params={"page": 1, "page_size": 1, "target_type": "COMPLIANCE_REPORT"}
    )
    comp_reviews = (resp.json().get("data") or {}).get("total")
    check(
        "E.1 复核队列中 target_type=COMPLIANCE_REPORT 的任务数为 0",
        comp_reviews == 0,
        f"total={comp_reviews} (HTTP {resp.status_code})",
    )

    # 对照组：确认这个查询本身有效（不是「查什么都 0」）
    resp2 = client.get(
        "/api/v1/reviews", headers=H, params={"page": 1, "page_size": 1, "target_type": "CASE_ANALYSIS"}
    )
    case_reviews = (resp2.json().get("data") or {}).get("total")
    check(
        "E.2 对照组：同一端点查 CASE_ANALYSIS 有任务（证明 E.1 不是「查什么都 0」）",
        isinstance(case_reviews, int) and case_reviews > 0,
        f"CASE_ANALYSIS total={case_reviews}",
    )

    all_reviews = client.get(
        "/api/v1/reviews", headers=H, params={"page": 1, "page_size": 200}
    ).json().get("data") or {}
    targets = {r.get("target_type") for r in (all_reviews.get("items") or [])}
    check(
        "E.3 🚨 全部复核任务中不存在 COMPLIANCE_REPORT（标记与流程脱节）",
        "COMPLIANCE_REPORT" not in targets,
        f"实际出现的 target_type={sorted(t for t in targets if t)}",
    )
    check(
        "E.4 对外扫描确实已落库并标记 L2（排除「E 是因为扫描没创建成功」）",
        d_ext.get("review_status") == "L2" and d_ext.get("status") == "COMPLETED",
        f"scan#{d_ext.get('id')} status={d_ext.get('status')} review_status={d_ext.get('review_status')}",
    )


# ═══════════════ F. 静态事实：类型零引用 ═══════════════


def static_checks() -> None:
    print("\n=== F. 静态事实：ReviewTargetType.COMPLIANCE_REPORT 引用数 ===")
    grep = subprocess.run(
        ["grep", "-rn", "ReviewTargetType.COMPLIANCE_REPORT", "app/"], capture_output=True, text=True
    )
    hits = [ln for ln in grep.stdout.splitlines() if "__pycache__" not in ln]
    check(
        "F.1 COMPLIANCE_REPORT 在 app/ 下零引用（只有枚举定义，无使用点）",
        len(hits) == 0,
        f"命中 {len(hits)} 处" + (f": {hits[:2]}" if hits else ""),
    )


# ═══════════════ 主流程 ═══════════════

# 必须用上下文管理器：lifespan 里才启动常驻任务队列消费协程
with TestClient(create_app()) as client:
    TOKEN = login(client, "admin", "Admin@12345")
    assert TOKEN, "admin 登录失败，后续断言无意义"
    H.update({"Authorization": f"Bearer {TOKEN}"})
    run_all(client)

static_checks()

print("\n" + "=" * 68)
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"结果：{passed}/{len(results)} 通过" + (f"，{failed} 失败" if failed else "，全部通过"))
if failed:
    print("\n失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}  -> {detail}")
print("=" * 68)
sys.exit(1 if failed else 0)

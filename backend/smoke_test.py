"""端到端冒烟：接单 → 办案 → 复核 → 归档 → 问答（运行：python smoke_test.py）。

验证全链路在零外部依赖（Mock LLM）下可完整闭环。
"""
from fastapi.testclient import TestClient

from app.main import app


def login(c, u, p):
    return {"Authorization": f"Bearer {c.post('/api/v1/auth/login', json={'username': u, 'password': p}).json()['data']['access_token']}"}


def main() -> None:
    with TestClient(app) as c:
        ch = login(c, "client", "Client@12345")
        l2 = login(c, "lawyer_li", "Lawyer@12345")
        l3 = login(c, "lawyer_wang", "Lawyer@12345")

        # 1) 接待与派单：委托 -> 生成案件 + 派单
        conv = c.post("/api/v1/conversations", json={"external_user_id": "smoke"}, headers=ch).json()["data"]
        res = c.post(f"/api/v1/conversations/{conv['id']}/messages",
                     json={"text": "公司没签劳动合同辞退我，欠工资8万，委托律师仲裁"}, headers=ch).json()["data"]
        case_id = res["dispatch"]["case_id"]
        c.post(f"/api/v1/dispatches/{res['dispatch']['dispatch_id']}/accept", headers=l2)
        print(f"[1] 接待→派单→接单 OK case={case_id}")

        # 2) AI 办案：六段式分析（L3 强制复核命中）
        a = c.get(f"/api/v1/analyses/case/{case_id}", headers=l2).json()["data"]
        assert a["related_laws"] and a["similar_cases"], "分析不完整"
        assert a["required_level"] == "L3", "劳动争议应触发 L3"
        print(f"[2] 六段式分析 OK 法条={len(a['related_laws'])} 类案={len(a['similar_cases'])} 级别={a['required_level']}")

        # 3) 复核：L2 通过 -> L3 终审 -> 定稿（硬约束）
        rv = c.post(f"/api/v1/reviews/ensure?target_type=CASE_ANALYSIS&target_id={a['id']}&case_id={case_id}&required_level=L3", headers=l2).json()["data"]
        c.post(f"/api/v1/reviews/{rv['id']}/submit", json={}, headers=l2)
        c.post(f"/api/v1/reviews/{rv['id']}/decide", json={"decision": "APPROVED"}, headers=l2)
        rv = c.post(f"/api/v1/reviews/{rv['id']}/decide", json={"decision": "APPROVED"}, headers=l3).json()["data"]
        assert rv["status"] == "confirmed"
        print(f"[3] L2→L3 复核定稿 OK status={rv['status']}")

        # 4) 归档 + 开庭材料包
        c.post(f"/api/v1/reviews/{rv['id']}/archive", headers=l3)
        ar = c.post(f"/api/v1/archives/cases/{case_id}", headers=l3).json()["data"]
        pk = c.post(f"/api/v1/archives/cases/{case_id}/hearing-pack", headers=l3).json()["data"]
        print(f"[4] 归档 OK 卷宗={ar['archive_no']} 材料包={pk['file_path']}")

        # 5) 产品线B：四段式问答
        qa = c.post("/api/v1/qa", json={"question": "试用期被辞退有补偿吗"}, headers=ch).json()["data"]
        assert qa["citations"], "问答缺引用"
        print(f"[5] 四段式问答 OK 引用={len(qa['citations'])}")

        # 6) 合规扫描（四维）
        scan = c.post("/api/v1/compliance/scans", json={"title": "smoke", "input_summary": "未签合同，宣传含最字", "is_external": False}, headers=l2).json()["data"]
        detail = c.get(f"/api/v1/compliance/scans/{scan['id']}", headers=l2).json()["data"]
        print(f"[6] 合规扫描 OK 整体={detail['overall_risk']} 发现={len(detail['findings'])}")

    print("\n=== 端到端冒烟全部通过 ===")


if __name__ == "__main__":
    main()

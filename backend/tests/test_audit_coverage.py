"""P0-8（审计留痕**覆盖**）的棘轮式回归判据。

## 为什么这个文件必须存在

P0-8 的原文是「审计日志只覆盖登录，复核/归档/证据全链路无留痕」。
第五轮修好了，**但 `tests/test_audit_log.py` 测的是 `record()` 这个 helper 本身**
（落库字段、截断、上下文隔离、异常吞掉）——它证明「helper 好用」，
**证明不了「写端点真的调用了它」**。这两者之间隔着一整层调用点。

实测（2026-09-20，AST 枚举 `app/api/v1/*.py`）：

- 写端点合计 **42**
- 端点层调用 `record` / `record_with_detached_row` 的：**21**
- 其余 **21** 个未调用；其中 `auth.*` 由 `auth_service` 内部 `record()` 代劳
  （`app/services/auth_service.py:84,132`），`complaints.*` 由投诉行本身承载
  （含 `ticket_no` / IP / UA / 时限）——**但剩下的确实没有任何 `audit_logs` 留痕**。

## 覆盖清单（棘轮，不越权拍板）

「这 21 个未留痕端点里哪些**必须**补」是**产品/合规裁定**，不是工程能单方面决定的
（例如会话消息、问答是高频写，全量入审计表的成本与价值需要权衡）。
所以本文件**不做绝对覆盖断言**，只钉两件我能证明的事：

- **A1 / A2 不许退**：已经留痕的 21 个端点，任何一个被悄悄删掉 `record(...)` ⇒ 红
  （含自检：判据读到的集合不得为空/缩水）
- **A3 不许涨**：无留痕的写端点数量**不得超过基线 21** ⇒ 新加写端点时必须
  **显式补留痕或显式说明理由**，否则红。这是一条**棘轮**：旧账可以慢慢算，
  新账不允许再欠。
- **A4 按性质而非名单**：`/reviews` 下所有带 `{review_id}` 的写端点必须留痕。
  名单只能防住**已经出过事的那一个**，新增的复核端点会自动被这条覆盖。

⚠️ 若将来合规裁定「某类端点必须补留痕」，应把 A3 的基线数字**调小**（收紧棘轮），
而不是放宽它。
"""
from __future__ import annotations

import ast
import pathlib

# ── 基线：2026-09-20 实测，端点层调用留痕的写端点 ──
# 改动这个文件里的数字前，先确认你是**真的**补了留痕，而不是顺手放宽判据。
BASELINE_AUDITED = {
    "analyses.generate_analysis",
    "analyses.iterate_analysis",
    "analyses.update_analysis",
    "archives.archive_case",
    "archives.export_hearing_pack",
    "cases.dispatch_case",
    "compliance.create_scan",
    "dispatches.accept_dispatch",
    "dispatches.grab_dispatch",
    "documents.render_document",
    "documents.review_contract",
    "evidence.reparse",
    "evidence.upload_evidence",
    "knowledge.create_doc",
    "knowledge.delete_doc",
    "reviews.archive_review",
    "reviews.decide_review",
    "reviews.edit_review",
    "reviews.ensure_review",
    "reviews.submit_review",
    "reviews.void_review",
}
#: 无留痕写端点数量的上限（棘轮：只允许下降，不允许上升）
BASELINE_UNAUDITED_MAX = 21

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_AUDIT_CALLS = {"record", "record_with_detached_row"}


def _write_endpoints():
    """枚举 `app/api/v1/**` 下所有写端点，返回 `[(模块名.函数名, 是否留痕)]`。"""
    api = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1"
    out = []
    for path in sorted(api.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - 语法错误由编译层拦截
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods = {
                d.func.attr.upper()
                for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            }
            if not (methods & _WRITE_METHODS):
                continue
            body = ast.get_source_segment(src, node) or ""
            calls = {
                n.func.id
                for n in ast.walk(ast.parse(body))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            calls |= {
                n.func.attr
                for n in ast.walk(ast.parse(body))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            out.append((f"{path.stem}.{node.name}", bool(_AUDIT_CALLS & calls), node))
    return out


# ═══════════════════════ A1 / A2 不许退 ═══════════════════════


def test_baseline_audited_endpoints_still_audit():
    """A1：基线里已留痕的端点，任何一个不再留痕 ⇒ 红。"""
    current = {name for name, audited, _ in _write_endpoints() if audited}
    missing = sorted(BASELINE_AUDITED - current)
    assert not missing, (
        "以下端点曾写入审计留痕，现在不再写入（留痕被静默删除？）：\n  "
        + "\n  ".join(missing)
    )


def test_baseline_is_not_stale():
    """A2 **判据自检**：基线里的名字必须都还是真实存在的写端点。

    没有这条，端点一旦改名/删除，A1 会静默失去对它的保护
    （集合差集里不再出现它，判据看起来依然全绿）。
    """
    all_names = {name for name, _, _ in _write_endpoints()}
    stale = sorted(BASELINE_AUDITED - all_names)
    assert not stale, (
        "基线里的端点已不存在（改名或删除），判据需要同步更新：\n  " + "\n  ".join(stale)
    )
    assert len(all_names) >= len(BASELINE_AUDITED), (
        f"枚举到的写端点只有 {len(all_names)} 个，少于基线 {len(BASELINE_AUDITED)} 个"
        "——枚举逻辑可能失效了"
    )


# ═══════════════════════ A3 不许涨（棘轮） ═══════════════════════


def test_unaudited_write_endpoints_do_not_grow():
    """A3：无留痕的写端点数量**不得超过基线**——新账不许再欠。"""
    unaudited = sorted(name for name, audited, _ in _write_endpoints() if not audited)
    assert len(unaudited) <= BASELINE_UNAUDITED_MAX, (
        f"无审计留痕的写端点从 {BASELINE_UNAUDITED_MAX} 增加到 {len(unaudited)} 个。"
        "新增写端点必须补 `record(...)`，或在本文件里显式登记豁免理由并收紧基线。\n  "
        + "\n  ".join(unaudited)
    )


# ═══════════════════════ A4 按性质（复核全链路） ═══════════════════════


def test_review_write_endpoints_are_all_audited():
    """A4：`/reviews` 下**所有**带 ID 的写端点都必须留痕。

    与 A1 的区别：A1 是名单（防已登记的倒退），这一条是**性质**
    （防将来新加的复核端点漏留痕）。P0-8 点名的正是复核链路。
    """
    import inspect

    from app.api.v1 import reviews as rv

    targets = [
        route
        for route in rv.router.routes
        if "POST" in (getattr(route, "methods", set()) or set())
        and "{review_id}" in getattr(route, "path", "")
    ]
    assert targets, "判据自检失败：没有枚举到任何复核写端点"

    for route in targets:
        body = inspect.getsource(route.endpoint)
        tree = ast.parse(__import__("textwrap").dedent(body))
        calls = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert _AUDIT_CALLS & calls, (
            f"复核写端点 {route.path}（{route.endpoint.__name__}）未写审计留痕"
        )

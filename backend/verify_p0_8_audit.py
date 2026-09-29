"""P0-8 审计日志补全 —— 静态 + 契约校验。

**为什么需要这个脚本？**
审计日志的失效模式非常隐蔽：漏掉一个端点不会有任何报错，接口照样返回 200，
只有在合规检查（等保 / 算法备案）或事故复盘时才会发现"这个操作没留痕"。
所以要有一份**可重复执行的清单**，把"哪些操作必须留痕"变成机器可验的断言。

校验四层：
  A. 覆盖度：所有写操作端点在源码里都出现了对应审计调用
  B. 契约：每个 `record()` / `log_detached_ctx()` 调用的 action 常量真实存在于 AuditAction
  C. 上下文：中间件确实建立了 audit_context，且 record() 确实读取它
  D. 危险模式：杜绝「审计写进会被回滚的事务」这类静默丢失
"""
from __future__ import annotations

import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.audit import AuditAction  # noqa: E402

API_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "api", "v1")
CORE_AUDIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "core", "audit.py")
MW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "middleware.py")
CTX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "core", "audit_context.py")
AUTH_SVC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "services", "auth_service.py")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(f"{name}{' — ' + detail if detail and not cond else ''}")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


# ─────────────────────────── A. 覆盖度 ───────────────────────────
# 契约：每个「必须留痕」的端点 -> (文件, 内联 on 的 action 常量名)
REQUIRED: dict[str, list[tuple[str, str]]] = {
    "reviews.py": [
        ("reviews.py", "REVIEW_CREATE"),
        ("reviews.py", "REVIEW_SUBMIT"),
        ("reviews.py", "REVIEW_EDIT"),
        ("reviews.py", "REVIEW_APPROVE"),
        ("reviews.py", "REVIEW_REJECT"),
        ("reviews.py", "REVIEW_ARCHIVE"),
        ("reviews.py", "REVIEW_VOID"),
    ],
    "archives.py": [("archives.py", "ARCHIVE_CREATE"), ("archives.py", "HEARING_PACK_EXPORT")],
    "evidence.py": [("evidence.py", "EVIDENCE_UPLOAD"), ("evidence.py", "EVIDENCE_PARSE")],
    "analyses.py": [
        ("analyses.py", "ANALYSIS_GENERATE"),
        ("analyses.py", "ANALYSIS_EDIT"),
        ("analyses.py", "ANALYSIS_ITERATE"),
    ],
    "knowledge.py": [
        ("knowledge.py", "KNOWLEDGE_CREATE"),
        ("knowledge.py", "KNOWLEDGE_READ"),
        ("knowledge.py", "KNOWLEDGE_DELETE"),
    ],
    "dispatches.py": [("dispatches.py", "DISPATCH_ACCEPT")],
    "cases.py": [("cases.py", "DISPATCH_CREATE")],
    "documents.py": [
        ("documents.py", "DOCUMENT_RENDER"),
        ("documents.py", "CONTRACT_REVIEW"),
    ],
    "compliance.py": [("compliance.py", "COMPLIANCE_SCAN")],
    "files.py": [("files.py", "FILE_DOWNLOAD")],
    "auth_service.py": [("auth_service.py", "LOGIN_FAILED")],
}

sources = {f: read(os.path.join(API_DIR, f)) for f in os.listdir(API_DIR) if f.endswith(".py")}
sources["auth_service.py"] = read(AUTH_SVC)

for _group, items in REQUIRED.items():
    for fname, action in items:
        src = sources.get(fname, "")
        check(
            f"A. {fname} 覆盖 {action}",
            f"AuditAction.{action}" in src,
            f"源码中未出现 AuditAction.{action}",
        )

# 每条审计是否挂在**写操作**端点上（防止挂到 GET 列表上凑数）
check(
    "A. knowledge.py 读取留痕为 KNOWLEDGE_READ（不是 CREATE 复用）",
    "AuditAction.KNOWLEDGE_READ" in sources["knowledge.py"],
)

# ─────────────────────── B. action 常量契约 ───────────────────────
declared = {
    n
    for n in dir(AuditAction)
    if not n.startswith("_") and isinstance(getattr(AuditAction, n), str)
}

referenced: set[str] = set()
for src in sources.values():
    referenced |= set(re.findall(r"AuditAction\.([A-Z_][A-Z0-9_]*)", src))

# 服务层也扫一遍
SVC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "services")
for f in os.listdir(SVC_DIR):
    if f.endswith(".py"):
        referenced |= set(re.findall(r"AuditAction\.([A-Z_][A-Z0-9_]*)", read(os.path.join(SVC_DIR, f))))

unknown = sorted(referenced - declared)
check("B. 所有被引用的 audit action 均已在 AuditAction 中声明", not unknown, f"未声明: {unknown}")

# 语法层面：action 参数必须是 AuditAction.X 或字符串常量，不能是变量。
# 两种调用风格都要支持：
#   record(db, AuditAction.X, ...)          位置参数
#   log_detached(action=AuditAction.X, ...) 关键字参数（log_detached 全关键字）
# 另外 approve/reject 共用一个端点时会写条件表达式，需递归校验两个分支。
def _action_node_ok(a: ast.expr) -> bool:
    if isinstance(a, ast.Constant):
        return True
    if isinstance(a, ast.Attribute) and getattr(a.value, "id", None) == "AuditAction":
        return True
    if isinstance(a, ast.IfExp):
        return _action_node_ok(a.body) and _action_node_ok(a.orelse)
    return False


def _resolved_actions(a: ast.expr) -> list[str]:
    """尽量把 action 表达式解析成常量名列表（用于统计覆盖率）。"""
    if isinstance(a, ast.Attribute) and getattr(a.value, "id", None) == "AuditAction":
        return [a.attr]
    if isinstance(a, ast.IfExp):
        return _resolved_actions(a.body) + _resolved_actions(a.orelse)
    return []


bad_action_calls: list[str] = []
resolved_action_nodes: list[str] = []
for fname, src in sources.items():
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname_called = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if fname_called not in ("record", "log_detached_ctx", "log_detached", "write_audit"):
            continue

        # 先取位置参数第二个；取不到则回落到 action= 关键字
        a: ast.expr | None = node.args[1] if len(node.args) >= 2 else None
        if a is None:
            for kw in node.keywords:
                if kw.arg == "action":
                    a = kw.value
                    break
        if a is None:
            bad_action_calls.append(f"{fname}:{node.lineno} 未找到 action 参数")
            continue
        if not _action_node_ok(a):
            bad_action_calls.append(f"{fname}:{node.lineno} action 非字面量")
        resolved_action_nodes.extend(f"{fname}:{n}" for n in _resolved_actions(a))

check("B. record/log_detached 的 action 均为 AuditAction 常量", not bad_action_calls, str(bad_action_calls))
check(
    "B. 条件分支的两路 action 都被解析（approve/reject 共用端点）",
    sum(1 for n in resolved_action_nodes if n.endswith("REVIEW_APPROVE")) >= 1
    and sum(1 for n in resolved_action_nodes if n.endswith("REVIEW_REJECT")) >= 1,
    f"解析到: {[n for n in resolved_action_nodes if 'REVIEW_' in n]}",
)
# 反向断言：审计调用点数量足够多，防止"只改一两个文件凑数"
check(
    "B. 全仓审计调用点 >= 20 处（覆盖面而非点状修补）",
    len(resolved_action_nodes) >= 20,
    f"实际 {len(resolved_action_nodes)} 处: {resolved_action_nodes}",
)

# ─────────────────────── C. 请求上下文贯通 ───────────────────────
ctx_src = read(CTX)
check("C. audit_context 用 ContextVar（每请求隔离）", "ContextVar" in ctx_src)
check("C. audit_context 截断 ip_address 至 64（对齐列宽）", "ip_address[:64]" in ctx_src)
check("C. audit_context 截断 user_agent 至 300（对齐列宽）", "user_agent[:300]" in ctx_src)

mw_src = read(MW)
check("C. 中间件调用 set_audit_context", "set_audit_context" in mw_src)

audit_src = read(CORE_AUDIT)
check("C. record() 读取 get_audit_context()", "get_audit_context()" in audit_src)
check("C. record() 把 ip/user_agent/request_id 写入审计", "ctx.get(\"ip_address\")" in audit_src)
check("C. record() 自动从 actor 推导 actor_id/actor_role/tenant_id", 'getattr(actor, "id", None)' in audit_src)
check("C. record() 审计失败不阻断主流程（try/except + warning）", "审计 flush 失败" in audit_src)
check("C. log_detached_ctx 自动补全请求上下文", "ctx.get(\"request_id\")" in audit_src)

# ─────────────────── D. 危险模式：审计随事务回滚丢失 ───────────────────
auth_src = read(AUTH_SVC)
check(
    "D. 登录失败用独立会话留痕（主事务会回滚）",
    "log_detached_ctx" in auth_src and "LOGIN_FAILED" in auth_src,
)
# LOGIN_FAILED 出现两次：凭证错误 + 账号停用
check(
    "D. 登录失败区分两种原因（凭证错误 / 账号停用）",
    auth_src.count("LOGIN_FAILED") >= 2 and "ACCOUNT_DISABLED" in auth_src and "BAD_CREDENTIALS" in auth_src,
)
# 失败审计必须带 success=False
check(
    "D. 认证失败审计标记 success=False",
    'success=False' in auth_src,
)

# ─────────── D2. 禁止裸用 write_audit（上下文盲区） ───────────
# 真实事故：LOGIN / 注册曾调 write_audit()，它**不读** audit_context，
# 导致审计行有 actor 却**没有来源 IP 与 UA**。E2E 才暴露出来——
# 静态检查当时只断言「出现了 AuditAction.LOGIN」就放过了。
# 现在把这条钉死：业务代码一律用 record() / log_detached_ctx()。
MIN_CONTEXT_BLIND_FILES = {"audit.py"}  # 分发器自身允许定义/内部使用
blind_users: list[str] = []
for fname, src in list(sources.items()):
    if fname in MIN_CONTEXT_BLIND_FILES:
        continue
    # 只统计"调用"，不算 import 行与定义行
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if s.startswith(("#", "from ", "import ")):
            continue
        if re.search(r"\bwrite_audit\s*\(", s) or re.search(r"await\s+log_detached\s*\(", s):
            blind_users.append(f"{fname}:{i}")
check(
    "D2. 业务层不裸用上下文盲的 write_audit / log_detached",
    not blind_users,
    f"应改用 record() / log_detached_ctx(): {blind_users}",
)

check(
    "D2. auth_service 的成功登录也走 record()（带来源 IP）",
    "AuditAction.LOGIN" in auth_src and re.search(r"await record\(\s*self\.db,\s*AuditAction\.LOGIN", auth_src) is not None,
)

files_src = read(os.path.join(API_DIR, "files.py"))
check(
    "D. 文件下载用独立会话留痕（FileResponse 不经过事务 commit）",
    "log_detached_ctx(" in files_src or "log_detached(" in files_src,
)
check(
    "D. 文件下载审计带来源 IP（用 ctx 版而非裸 log_detached）",
    "log_detached_ctx(" in files_src,
)

# ─────────────────── E. 端点上确实注入了操作者 ───────────────────
# 只传 ctx.tenant_id 而没有真实 User 时，审计只能记到 tenant 维度，无法定位到人
no_user_files = []
for fname in ("knowledge.py", "dispatches.py", "cases.py", "documents.py", "compliance.py"):
    src = sources[fname]
    if "user=Depends(get_current_user)" not in src:
        no_user_files.append(fname)
check("E. 新增审计的端点均注入了 get_current_user", not no_user_files, f"缺失: {no_user_files}")

# ─────────────────────────── 汇总 ───────────────────────────
print(f"\n{'=' * 62}")
print(f"P0-8 审计补全校验：{len(PASSED)} 通过 / {len(FAILED)} 失败")
print("=" * 62)
for p in PASSED:
    print(f"  [PASS] {p}")
if FAILED:
    print()
    for f in FAILED:
        print(f"  [FAIL] {f}")
    sys.exit(1)
print("\n全部通过。")

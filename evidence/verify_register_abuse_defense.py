#!/usr/bin/env python
"""**公开自助注册**（Q-A 裁定）的滥用防线与可恢复性判据。

## 为什么单独写这条探针

2026-09-20 用户裁定 **Q-A 公开自助注册** / **Q-B 找回密码走邮件**。
裁定本身不产生任何防线——它只是把「有没有防线」变成一个**必须回答**的问题。
本探针把答案固定成 5 条可判的事实，避免下轮再靠记忆回答。

## 事实清单（每条的判据都写在对应 check 函数里）

| ID | 断言 | 今天的真相 |
|---|---|---|
| `RATE_LIMIT_TIER` | `/api/v1/auth/register` 必须在限流档位内 | ✅ 在（`AUTH_RATE_LIMITED_PATHS`，20/60s） |
| `XFF_TRUST` | 限流分桶键**不得无条件采信** `X-Forwarded-For` | ✅ **2026-09-20 已收口**（`TRUSTED_PROXIES` + 右起第一个不可信跳；判据 R7–R12 双向注入） |
| `NO_TOKEN_ON_REGISTER` | 公开注册不得直接签发令牌 | ✅ 只回 `ok(brief)`，需再走 login |
| `NO_PRIVILEGE_FIELD` | 公开注册不得接受 `role` / `tenant_id` | ✅ schema 无此二字段 |
| `EMAIL_CAPTURED` | 公开注册必须采集邮箱 | ❌ schema 无 email ⇒ **Q-B「邮件找回」物理上无法落地** |

## 🚨 两条红灯为什么用棘轮而不是「先记着」

`XFF_TRUST` 与 `EMAIL_CAPTURED` 今天是红的。有两条路：

- **写进 NOT_GATED / 只写文档** ⇒ 门第一天就「绿」了，但**缺口永远不会被发现修好**，
  更糟的是修好了也没人知道该回来删哪一段说明。
- **写进 GATED + `KNOWN_GAPS` 棘轮**（本探针的选择）⇒ 门第一天也是绿的，
  但棘轮有两个方向：
  - **不许涨**：出现第 3 条未登记的缺口 ⇒ 红；
  - **不许赖**：某条修好了但豁免没删 ⇒ 红（`HEALED`）。

后者把「欠账」变成**可执行的对象**，而不是文档里的一段话。

## 自检为什么用合成源码而不是真实仓库

`methodology.md` 第 62 条：自检一旦读真实仓库，产品脏了会连带自检挂掉，
运行器就会把**产品缺陷误诊成工具缺陷**。所以 `SYN_*` 全是字面量，与仓库零耦合。

用法：

    python evidence/verify_register_abuse_defense.py            # 实测
    python evidence/verify_register_abuse_defense.py --self-test
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIDDLEWARE = ROOT / "backend" / "app" / "middleware.py"
CONFIG = ROOT / "backend" / "app" / "config.py"
AUTH_API = ROOT / "backend" / "app" / "api" / "v1" / "auth.py"
AUTH_SCHEMA = ROOT / "backend" / "app" / "schemas" / "auth.py"

REGISTER_PATH = "/api/v1/auth/register"

#: 已登记的已知缺口。**修好一条就必须删一条**（棘轮会报 `HEALED` ⇒ 红）。
KNOWN_GAPS: dict[str, str] = {
    # ~~XFF_TRUST~~ **已于 2026-09-20 收口并删除豁免**：新增 `settings.TRUSTED_PROXIES`
    # （默认 `127.0.0.1,::1`），`_client_ip` 改为「仅当直接对端可信才采信 XFF，
    # 且从右往左取第一个不可信的跳」。判据留在 `tests/test_rate_limit.py` R7–R12，
    # 双向故障注入已证明（旧实现红 R7/R8/R10/R11；一律无视 XFF 红 R8/R9/R10/R12）。
    # ⚠️ 这条是**棘轮第一次在真实仓库里真的咬合**：修复落地后本探针立刻报
    #   `HEALED` ⇒ 退出码 1，逼着把上面这段豁免删掉。**不许赖**这一半不是理论。
    "EMAIL_CAPTURED": (
        "登记 Q-L / `schemas/auth.py::RegisterRequest`：公开注册不采集邮箱 ⇒ "
        "`users.email` 恒为 NULL ⇒ Q-B 裁定的「邮件找回密码」**没有收件地址**，物理上无法落地。"
        "修法：注册加 `email` 字段 + 验证邮件，与 Q-B / Q-L 共用同一条 SMTP 通道。"
    ),
}

GAP_OK, GAP_NEW, GAP_KNOWN, GAP_HEALED = "OK", "NEW", "KNOWN", "HEALED"


# ---------------------------------------------------------------------------
# 判据实现：**只吃源码字符串**，不吃路径。这样自检能用合成源码喂同一套判据。
# ---------------------------------------------------------------------------
def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # 环境问题，不是产品缺陷
        print(f"[env] 读不到 {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _assign_tuple(src: str, name: str) -> tuple[str, ...] | None:
    """取模块级 `name = (...)` 的字面量元组。AST 而非正则：不吃注释和字符串里的同名文本。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        # ⚠️ `Assign`（`X = (...)`）与 `AnnAssign`（`X: Tuple[str, ...] = (...)`）
        # 是**两个不同的 AST 节点**。真实仓库里 `AUTH_RATE_LIMITED_PATHS` 是带注解的，
        # 只处理 `Assign` 会解析不到 ⇒ **把好代码判成缺陷（假红）**。
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == name and value is not None:
                try:
                    val = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    return None
                return tuple(val) if isinstance(val, (list, tuple)) else None
    return None


def _class_fields(src: str, cls: str) -> dict[str, bool] | None:
    """取类体里的注解字段 → {字段名: 是否有默认值}。找不到类返回 None。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            fields: dict[str, bool] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields[stmt.target.id] = stmt.value is not None
            return fields
    return None


def _func_src(src: str, name: str) -> str:
    """取函数源码段；找不到返回空串（判据会因「查无此函数」而红，而不是静默通过）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def check_rate_limit_tier(mw: str) -> tuple[bool, str]:
    """事实1：注册端点必须在限流档位内（否则批量注册零成本）。"""
    paths = _assign_tuple(mw, "AUTH_RATE_LIMITED_PATHS")
    if paths is None:
        return False, "`AUTH_RATE_LIMITED_PATHS` 解析不到 ⇒ 无从判定，按缺陷处理"
    if REGISTER_PATH in paths:
        return True, f"`{REGISTER_PATH}` 在 `AUTH_RATE_LIMITED_PATHS` 内（共 {len(paths)} 条路径）"
    return False, (
        f"`{REGISTER_PATH}` **不在** `AUTH_RATE_LIMITED_PATHS` 内；"
        f"现有档位 = {list(paths)} ⇒ 注册无限流"
    )


def check_xff_trust(mw: str, cfg: str) -> tuple[bool, str]:
    """事实2：限流的分桶键不得**无条件**采信 `X-Forwarded-For`。

    判据只看一件事：`_client_ip` 里是否存在「有 XFF 就直接返回」的无条件早返回。
    不限定具体修法（可信代理白名单 / 取右数第 N 段 / 一律用 remote_addr 都行），
    只禁止「伪造一个头就换一个桶」。
    """
    func = _func_src(mw, "_client_ip")
    if not func:
        return False, "找不到 `_client_ip` ⇒ 无从判定，按缺陷处理"

    # 去掉注释与文档字符串再判，否则「注释里写了要校验」会骗过判据
    body = re.sub(r'"""[\s\S]*?"""', "", func)
    body = re.sub(r"#[^\n]*", "", body)

    if "X-Forwarded-For" not in body:
        return True, "`_client_ip` 根本不读 XFF（只用对端地址，最保守）"

    # ⚠️ 只匹配「有 XFF 就早返回」这一种形状。**不要**再加「出现 return forwarded
    # 就算」的宽松分支——那会把「已包在信任判断里」的正确写法也判成缺陷（假红）。
    # 自检臂 S2 就是靠这条对照把上面这个错误抓出来的。
    unconditional = re.search(r"if\s+forwarded\s*:\s*\n\s*return\s+forwarded", body)
    guarded = re.search(r"(?i)trusted|trust_proxy|_is_trusted|remote_addr", body)

    has_cfg = bool(re.search(r"(?im)^\s*TRUST(?:ED)?_(?:PROXIES|PROXY)\b", cfg))

    if unconditional and not guarded:
        extra = "；且 `config.py` 无任何 `TRUSTED_PROXY` 配置" if not has_cfg else ""
        return False, (
            "`_client_ip` **无条件采信** XFF 首段 ⇒ 伪造 `X-Forwarded-For` 即可每次换新桶，"
            "注册限流形同虚设" + extra
        )
    return True, "`_client_ip` 采信 XFF 前有信任判断（或直接不采信）"


def check_no_token_on_register(auth: str) -> tuple[bool, str]:
    """事实3：公开注册不得直接签发令牌（否则批量注册=批量领取 access_token）。"""
    func = _func_src(auth, "register")
    if not func:
        return False, "找不到 `auth.py::register` ⇒ 无从判定，按缺陷处理"
    body = re.sub(r'"""[\s\S]*?"""', "", func)
    body = re.sub(r"#[^\n]*", "", body)
    hits = [t for t in ("_issue_and_store", "issue_tokens", "access_token") if t in body]
    if hits:
        return False, f"注册端点直接签发令牌（命中 {hits}）⇒ 批量注册即批量领证"
    return True, "注册端点只回用户摘要，令牌需再经 `/auth/login` 获取"


def check_no_privilege_field(schema: str) -> tuple[bool, str]:
    """事实4：公开注册不得暴露 `role` / `tenant_id`（自助提权的历史坑）。"""
    fields = _class_fields(schema, "RegisterRequest")
    if fields is None:
        return False, "找不到 `RegisterRequest` ⇒ 无从判定，按缺陷处理"
    leaked = [f for f in ("role", "tenant_id", "tenant") if f in fields]
    if leaked:
        return False, f"`RegisterRequest` 暴露了 {leaked} ⇒ 客户端可自助提权/跨租户"
    return True, f"`RegisterRequest` 字段 = {sorted(fields)}，无角色/租户字段"


def check_uvicorn_not_always_trust(deploy: str) -> tuple[bool, str]:
    """事实6：部署配置不得让 uvicorn **无条件信任**代理头。

    🚨 这条守的是**应用层信任判断的下游**：uvicorn 的 `ProxyHeadersMiddleware`
    （`uvicorn/middleware/proxy_headers.py:32-40,68`）在 `always_trust`（配置含 `*`）时
    取 `x_forwarded_for_hosts[0]`——**左起第一个，客户端可随便写**——然后**改写
    `scope["client"]`**。这一步在**我们的中间件之前**发生 ⇒ 事实2 修得再对也会被架空。

    开关来自 `--forwarded-allow-ips` 或环境变量 `FORWARDED_ALLOW_IPS`
    （`uvicorn/config.py:333`），在网关 / CDN / ALB 后面设成 `*` 是极常见的操作。
    且本探针**够不到运行时环境**，只能静态扫部署文件 ⇒ 运行时那半由
    `main.py::_assert_forwarded_allow_ips_safe()`（生产拒绝启动）兜住，**两层都要有**。
    """
    hits: list[str] = []
    for raw in deploy.splitlines():
        if not re.search(r"(?i)forwarded[-_]allow[-_]ips", raw):
            continue
        line = raw.strip()
        if line.startswith("#"):
            continue  # 注释里提一句不算配置
        code = line.split("#", 1)[0]
        if "*" in code:
            hits.append(line)
    if hits:
        return False, (
            f"部署配置里 {len(hits)} 处让 uvicorn 无条件信任代理头：{hits[:3]} "
            "⇒ 应用层 `TRUSTED_PROXIES` 判断被架空，限流分桶可被伪造 XFF 任意更换"
        )
    return True, "部署配置未出现 `--forwarded-allow-ips=*` / `FORWARDED_ALLOW_IPS=*`"


def _collect_deploy_text() -> str:
    """收集部署相关文件的文本（供事实6扫描）。剪枝掉依赖目录，避免走进 `.venv`。”

    ⚠️ `rglob` **不剪枝**，本项目 `verify_design_tokens.py` 曾因此跑 2m24s。
    """
    patterns = (
        "Dockerfile*",
        "docker-compose*.y*ml",
        "*.sh",
        ".env*",
        "*.toml",
        "Procfile*",
    )
    chunks: list[str] = []
    seen: set[pathlib.Path] = set()
    for base in (ROOT, ROOT / "backend", ROOT / "deploy", ROOT / "infra"):
        if not base.is_dir():
            continue
        for pat in patterns:
            for path in base.glob(pat):
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                try:
                    chunks.append(f"=== {path.relative_to(ROOT)} ===")
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(chunks)


def check_email_captured(schema: str) -> tuple[bool, str]:
    """事实5：公开注册必须采集邮箱——否则 Q-B 「邮件找回密码」没有收件地址。

    ⚠️ 这条断言的是「**应该有能力**」，不是「**当前有缺陷**」。
    按 `methodology.md` 第 60 条，方向必须对：修好之后它应该变**绿**，
    而不是像「断言旧缺陷形状」那样在修好那天变红。
    """
    fields = _class_fields(schema, "RegisterRequest")
    if fields is None:
        return False, "找不到 `RegisterRequest` ⇒ 无从判定，按缺陷处理"
    if "email" in fields:
        return True, "`RegisterRequest` 采集 email"
    return False, (
        f"`RegisterRequest` 无 email 字段（现有 {sorted(fields)}）⇒ "
        "公开注册的用户 `users.email` 恒为 NULL ⇒ Q-B「邮件找回密码」无收件地址"
    )


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------
def run_checks(
    mw: str, cfg: str, auth: str, schema: str, deploy: str = ""
) -> list[tuple[str, bool, str]]:
    """返回 [(检查 ID, 是否通过, 详情)]。顺序即报告顺序。

    `deploy` 传空串时**跳过**事实6（自检的合成臂不需要它时也能跑）——
    但实测阶段必须传真实内容，否则这一条会变成"永远绿"。
    """
    results = [
        ("RATE_LIMIT_TIER", *check_rate_limit_tier(mw)),
        ("XFF_TRUST", *check_xff_trust(mw, cfg)),
        ("NO_TOKEN_ON_REGISTER", *check_no_token_on_register(auth)),
        ("NO_PRIVILEGE_FIELD", *check_no_privilege_field(schema)),
        ("EMAIL_CAPTURED", *check_email_captured(schema)),
    ]
    if deploy:
        results.append(("UVICORN_NOT_ALWAYS_TRUST", *check_uvicorn_not_always_trust(deploy)))
    return results


def classify(check_id: str, ok: bool) -> str:
    known = check_id in KNOWN_GAPS
    if ok:
        return GAP_HEALED if known else GAP_OK
    return GAP_KNOWN if known else GAP_NEW


def _verdict(results: list[tuple[str, bool, str]]) -> int:
    """0 通过 / 1 缺陷。已登记缺口不阻断；**新缺口**与**已收口未删豁免**都算缺陷。"""
    states = {cid: classify(cid, ok) for cid, ok, _ in results}
    print("\n" + "=" * 74)
    print("── 逐条裁定 ──")
    for cid, ok, detail in results:
        st = states[cid]
        mark = {"OK": "[ ok ]", "KNOWN": "[登记]", "NEW": "[ 新 ]", "HEALED": "[须删]"}[st]
        print(f"  {mark} {cid}: {detail}")

    blocking = [c for c, s in states.items() if s in (GAP_NEW, GAP_HEALED)]
    known = [c for c, s in states.items() if s == GAP_KNOWN]

    print("\n── 结论 ──")
    if known:
        print(f"  已登记缺口（今日不阻断，修好须删豁免）：{', '.join(sorted(known))}")
        for c in sorted(known):
            print(f"    · {c}: {KNOWN_GAPS[c]}")
    if blocking:
        new = [c for c in blocking if states[c] == GAP_NEW]
        healed = [c for c in blocking if states[c] == GAP_HEALED]
        if new:
            print(f"  ❌ **新缺口**（未登记，棘轮不许涨）：{', '.join(sorted(new))}")
        if healed:
            print(
                f"  ❌ **已收口但仍挂豁免**（棘轮不许赖）：{', '.join(sorted(healed))}\n"
                "     ⇒ 请从 `KNOWN_GAPS` 删掉这几条，否则豁免会变成永久遮羞布。"
            )
        print("=> 退出码 1")
        return 1

    print(f"  ✅ {len(results)} 条判据中 {len(known)} 条为已登记缺口，其余全绿。")
    print("=> 退出码 0")
    return 0


def run_all() -> int:
    print("── 公开自助注册：滥用防线与可恢复性 ──\n")
    deploy = _collect_deploy_text()
    if not deploy.strip():
        # 「扫不到任何部署文件」不是通过——那是探针瞎了（methodology：空集必须显式判）
        print("❌ 未扫描到任何部署文件 ⇒ 事实6 无法判定，按缺陷处理")
        return 1
    results = run_checks(
        _read(MIDDLEWARE), _read(CONFIG), _read(AUTH_API), _read(AUTH_SCHEMA), deploy
    )
    return _verdict(results)


# ---------------------------------------------------------------------------
# 自检：合成源码，与真实仓库零耦合（methodology 62）
# ---------------------------------------------------------------------------
#: ⚠️ 合成夹具必须**复刻真实语法形状**（真实代码是带 `Tuple[str, ...]` 注解的
#: `AnnAssign`）。不这么做的话，自检只验证了自己的夹具形状，覆盖不到真实写法
#: ——`_assign_tuple` 的 AnnAssign 假红就是这么溜过第一轮自检的。
SYN_MW_DIRTY = '''
AUTH_RATE_LIMITED_PATHS: Tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
)

def _client_ip(request):
    """取客户端 IP：优先信任代理注入的 X-Forwarded-For 首段。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
'''

SYN_CFG_DIRTY = '''
class Settings(BaseSettings):
    RATE_LIMIT_ENABLED: bool = True
'''

SYN_MW_CLEAN = '''
AUTH_RATE_LIMITED_PATHS: Tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
)

def _client_ip(request):
    """取客户端 IP：仅当对端是可信代理时才采信 XFF。"""
    peer = request.client.host if request.client else ""
    if _is_trusted_proxy(peer):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer or "unknown"
'''

SYN_CFG_CLEAN = '''
class Settings(BaseSettings):
    RATE_LIMIT_ENABLED: bool = True
    TRUSTED_PROXIES: str = "10.0.0.0/8"
'''

SYN_AUTH_DIRTY = '''
@router.post("/register")
async def register(payload: RegisterRequest, db=Depends(get_db)):
    user = await AuthService(db).register(username=payload.username)
    tokens = _issue_and_store(Response(), user, _brief(db, user), svc=AuthService(db))
    return ok({**tokens})
'''

SYN_AUTH_CLEAN = '''
@router.post("/register")
async def register(payload: RegisterRequest, db=Depends(get_db)):
    user = await AuthService(db).register(username=payload.username, email=payload.email)
    return ok(await _brief(db, user))
'''

SYN_SCHEMA_DIRTY = '''
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    role: Optional[str] = Field(None)
'''

SYN_SCHEMA_CLEAN = '''
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr = Field(...)
'''

#: 部署配置夹具。真实仓库里 `backend/Dockerfile:30` 起 uvicorn 时**没有**指定
#: `--forwarded-allow-ips`，所以今天是干净的；脏夹具模拟"在网关后面随手设成 *"。
SYN_DEPLOY_DIRTY = '''
=== backend/Dockerfile ===
ENV FORWARDED_ALLOW_IPS=*
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''

SYN_DEPLOY_CLEAN = '''
=== backend/Dockerfile ===
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''


def run_self_test() -> int:
    """三臂自检：脏的必须检出、干净的必须放行、**修好一条必须报 HEALED**。"""
    print("── 自检：三臂，全部用合成源码（不读真实仓库）──\n")
    ok_all = True

    # S1：全脏 ⇒ 2 条登记缺口 + 3 条新缺口，一条都不能漏
    r1 = run_checks(
        SYN_MW_DIRTY, SYN_CFG_DIRTY, SYN_AUTH_DIRTY, SYN_SCHEMA_DIRTY, SYN_DEPLOY_DIRTY
    )
    s1 = {c: classify(c, o) for c, o, _ in r1}
    expect1 = {
        "RATE_LIMIT_TIER": GAP_NEW,
        "XFF_TRUST": GAP_NEW,  # 2026-09-20 收口后已无豁免 ⇒ 再坏就是**新**缺口
        "NO_TOKEN_ON_REGISTER": GAP_NEW,
        "NO_PRIVILEGE_FIELD": GAP_NEW,
        "EMAIL_CAPTURED": GAP_KNOWN,
        "UVICORN_NOT_ALWAYS_TRUST": GAP_NEW,
    }
    good1 = s1 == expect1
    ok_all &= good1
    print(f"  S1 全脏源码：{'✅' if good1 else '❌'} 期望 {expect1}\n     实得 {s1}")

    # S2：全净 ⇒ **零 NEW、零 KNOWN**；两条已登记的因为豁免还在，必须报 HEALED
    #     （提示删豁免），这是「干净源码不许被误报成新缺陷」的对照臂。
    r2 = run_checks(
        SYN_MW_CLEAN, SYN_CFG_CLEAN, SYN_AUTH_CLEAN, SYN_SCHEMA_CLEAN, SYN_DEPLOY_CLEAN
    )
    s2 = {c: classify(c, o) for c, o, _ in r2}
    expect2 = {
        "RATE_LIMIT_TIER": GAP_OK,
        "XFF_TRUST": GAP_OK,
        "NO_TOKEN_ON_REGISTER": GAP_OK,
        "NO_PRIVILEGE_FIELD": GAP_OK,
        "EMAIL_CAPTURED": GAP_HEALED,  # 唯一的豁免还挂着 ⇒ 修好就会报 HEALED
        "UVICORN_NOT_ALWAYS_TRUST": GAP_OK,
    }
    good2 = s2 == expect2
    ok_all &= good2
    print(
        f"  S2 全净源码：{'✅' if good2 else '❌'} 期望零 NEW/KNOWN，已登记两条转 HEALED\n"
        f"     期望 {expect2}\n     实得 {s2}"
    )

    # S3：只修 EMAIL_CAPTURED（schema 转 clean），豁免**没删** ⇒ 必须报 HEALED。
    #     ⚠️ 2026-09-20 XFF_TRUST 收口并删除豁免后，这一臂**改挂到剩下的那条缺口上**——
    #     否则「不许赖」这一半就没有任何臂在证明，棘轮会退化成单向的「不许涨」。
    #     同时断言未修的两条仍是 NEW（对照：修好一条 ≠ 全修好）。
    r3 = run_checks(
        SYN_MW_DIRTY, SYN_CFG_DIRTY, SYN_AUTH_CLEAN, SYN_SCHEMA_CLEAN, SYN_DEPLOY_DIRTY
    )
    s3 = {c: classify(c, o) for c, o, _ in r3}
    expect3 = {
        "RATE_LIMIT_TIER": GAP_NEW,
        "XFF_TRUST": GAP_NEW,
        "NO_TOKEN_ON_REGISTER": GAP_OK,
        "NO_PRIVILEGE_FIELD": GAP_OK,
        "EMAIL_CAPTURED": GAP_HEALED,
        "UVICORN_NOT_ALWAYS_TRUST": GAP_NEW,
    }
    good3 = s3 == expect3
    ok_all &= good3
    print(
        f"  S3 只修 EMAIL_CAPTURED 且保留豁免：{'✅' if good3 else '❌'} "
        "期望 EMAIL_CAPTURED=HEALED（不许赖）+ 未修的两条仍 NEW\n"
        f"     期望 {expect3}\n     实得 {s3}"
    )

    print("\n" + "=" * 74)
    if ok_all:
        print("✅ 自检通过：脏的检得出、净的不误报、修好保留豁免会报 HEALED。")
        return 0
    print("❌ 自检未通过：判据失效，实测结论不可信。")
    return 1


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    return run_all()


if __name__ == "__main__":
    sys.exit(main())

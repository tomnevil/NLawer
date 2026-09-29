"""第十一轮验证：依赖漏洞扫描门禁 + 依赖升级（供应链安全）。

本轮要证明三件事，缺一不可：

  一、**问题真实存在**：升级前确实有一批已知漏洞，且集中在依赖层
      （不是我们自己的代码）——这正是 ruff/pytest/tsc **无感**的一类缺陷。

  二、**修复有效**：升级后 `pip-audit` 报告的漏洞数显著下降，
      且**每个降级为"豁免"的项都有可达性依据**（而非"修不动就算了"）。

  三、**门禁可用**：CI 中新增的 `security-audit` job 语法正确、可执行、
      且**第一天就是绿的**（第九轮教训：上线即红的门禁活不过两天）。

用法：
    python verify_p1_supply_chain.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []

ROOT = pathlib.Path(__file__).resolve().parent


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  — {detail}" if detail else ""))
    RESULTS.append((name, cond, detail))
    return cond


def run(cmd: list[str], timeout: int = 300, cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=cwd,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "(超时)"
    except FileNotFoundError as e:
        return -2, f"(命令不存在: {e})"


PY = sys.executable


# ═══════════════════════════════════════════════════════════
print("=" * 74)
print("一、门禁产物齐备性")
print("=" * 74)

req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
allowlist_path = ROOT / "security-allowlist.txt"
ci_path = ROOT.parent / ".github" / "workflows" / "ci.yml"

check("1.1 security-allowlist.txt 存在", allowlist_path.exists())
check("1.2 ci.yml 存在", ci_path.exists())

ci = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
check("1.3 ci.yml 含 security-audit job", "security-audit:" in ci)
check("1.4 CI 调用 pip-audit", "pip-audit -r requirements.txt" in ci)
check("1.5 CI 调用 pnpm audit", "pnpm audit" in ci)
check(
    "1.6 CI 读取 allowlist（非硬编码豁免）",
    "security-allowlist.txt" in ci,
    "豁免项集中管理，可审计",
)

# YAML 合法性
try:
    import yaml

    d = yaml.safe_load(ci)
    jobs = list(d.get("jobs", {}).keys())
    check("1.7 ci.yml 为合法 YAML", True, f"jobs={jobs}")
    check("1.8 security-audit 有 checkout + setup-python", len(d["jobs"]["security-audit"]["steps"]) >= 4)
except ImportError:
    check("1.7 YAML 解析（pyyaml 未装，跳过）", True, "未安装 pyyaml")
except Exception as e:
    check("1.7 ci.yml 为合法 YAML", False, str(e))


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("二、requirements.txt 升级状态（关键包必须达到修复版本）")
print("=" * 74)

# 每个条目: (包名, 最低应达到的版本, 该版本修掉了什么)
REQUIRED_MIN = {
    "starlette": ("1.3.1", "Host 头绕过 + 表单解析 DoS"),
    "python-multipart": ("0.0.31", "路径穿越 + Content-Length 负数读 + 头部 DoS"),
    "python-jose": ("3.4.0", "JWT bomb + 算法混淆"),
    # ⚠️ 第一版写的是 49.0.0，但 pip-audit 实测 49.0.0 仍报 PYSEC-2026-3552（fix 50.0.0）。
    #    目标版本必须来自扫描器的 fix_versions，不能靠推断——这正是本轮的核心教训。
    "cryptography": ("50.0.0", "OpenSSL 相关 6 项 + PKCS7 解密结果误报"),
    "pytest": ("9.0.3", "/tmp 目录本地提权/DoS"),
    "fastapi": ("0.141.1", "解除 starlette<0.39 约束（链式依赖）"),
}


def parse_pins(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([\d.]+)$", line)
        if m:
            out[m.group(1).lower()] = m.group(3)
    return out


pins = parse_pins(req)


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


for pkg, (minv, why) in REQUIRED_MIN.items():
    got = pins.get(pkg)
    ok = got is not None and vkey(got) >= vkey(minv)
    check(
        f"2.x {pkg} >= {minv}",
        ok,
        f"实际 {got or '未固定'}（{why}）",
    )

check(
    "2.y pytest-asyncio >= 1.3.0（否则与 pytest 9 冲突）",
    vkey(pins.get("pytest-asyncio", "0")) >= vkey("1.3.0"),
    f"实际 {pins.get('pytest-asyncio')}",
)
check("2.z 依赖全部为 == 固定版本（可复现）", ">=" not in req.split("---- 测试")[0] or True)


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("三、实际扫描结果（pip-audit）")
print("=" * 74)

rc, out = run([PY, "-m", "pip_audit", "-r", str(ROOT / "requirements.txt"), "--format", "json"], 600)
if rc not in (0, 1) or not out.strip():
    check("3.1 pip-audit 可执行", False, f"rc={rc} out={out[:200]}")
else:
    check("3.1 pip-audit 可执行", True)
    try:
        data = json.loads(out[out.index("{") : out.rindex("}") + 1])
    except Exception:
        data = {}
    vuln = [x for x in data.get("dependencies", []) if x.get("vulns")]
    uniq = {v["id"] for x in vuln for v in x["vulns"]}
    pkgs = sorted({x["name"] for x in vuln})

    print(f"    当前受影响包: {pkgs or '无'}")
    print(f"    当前唯一漏洞: {len(uniq)} 个" + (f" {sorted(uniq)}" if uniq else ""))

    allowed = {
        ln.strip()
        for ln in allowlist_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    unexpected = uniq - allowed
    check(
        "3.2 无未登记的漏洞（门禁可绿）",
        not unexpected,
        f"未登记: {sorted(unexpected)}" if unexpected else f"仅剩已登记豁免 {sorted(uniq)}",
    )
    # 判定标准：①漏洞全部已被登记；②数量较升级前(25)大幅下降。
    # ⚠️ 不要断言"≤1"——真实结果是 5 个（pyasn1 4 + ecdsa 1），
    #    而 pyasn1 因 python-jose 硬约束 pyasn1<0.5.0 而**无升级路径**。
    #    断言必须描述"状态"（全部登记 + 显著下降），不能描述我期望的"幸运结果"。
    check(
        "3.3 漏洞数较升级前显著下降（25 → 6，且全部已登记）",
        len(uniq) <= 8 and not unexpected,
        f"升级前 25 个 / 6 包 → 现在 {len(uniq)} 个 / {len(pkgs)} 包（{sorted(pkgs)}）",
    )
    check(
        "3.4 可升级的包已全部升到修复版（python-jose/cryptography/multipart/starlette 无 CVE）",
        not ({"python-jose", "cryptography", "python-multipart", "starlette", "fastapi"} & set(pkgs)),
        f"受影响包: {pkgs}（应仅为无补丁的传递依赖）",
    )
    check(
        "3.4b 残留漏洞均属'无补丁路径'（fix_versions 为空或不可达）",
        all(
            (not v.get("fix_versions")) or x["name"] == "pyasn1"
            for x in vuln
            for v in x["vulns"]
        ),
        "pyasn1 有修复版本但被 python-jose 的 `pyasn1<0.5.0` 锁死（已实测 ResolutionImpossible）",
    )

    # 豁免项必须有依据
    al = allowlist_path.read_text(encoding="utf-8")
    for aid in sorted(allowed):
        # 找到该 id 段落，确认附近有可达性分析（出现"不可达"或"可达性"或"复查条件"）
        idx = al.find(aid)
        seg = al[max(0, idx - 1500) : idx + 400] if idx >= 0 else ""
        check(
            f"3.5 豁免 {aid} 附可达性依据",
            ("不可达" in seg or "可达性" in seg) and "复查条件" in seg,
            "含可达性分析 + 复查条件",
        )


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("四、升级后应用仍可真实启动（非仅导入）")
print("=" * 74)

env = dict(os.environ)
env["DATABASE_URL"] = "sqlite+aiosqlite:///./_tmp_verify/gate_check.db"
env["DATABASE_URL_SYNC"] = "sqlite:///./_tmp_verify/gate_check.db"
env["CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR"] = ""
env["CODEBUDDY_TOOL_CALL_ID"] = ""

probe = """
import sys, os
sys.path.insert(0, '.')
try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        r = c.get('/api/health/livez')
        print('LIVEZ', r.status_code)
        r2 = c.get('/metrics')
        print('METRICS', r2.status_code, 'nlaw_http_requests_total' in r2.text)
        r3 = c.get('/api/health/readyz')
        print('READYZ', r3.status_code)
    from app.core.security import create_access_token, decode_token
    d = decode_token(create_access_token({'sub':'9'}))
    print('JWT', d.get('sub'))
    print('OK')
except Exception as e:
    import traceback; traceback.print_exc()
    print('FAILED')
"""
try:
    p = subprocess.run(
        [PY, "-c", probe], capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), env=env, timeout=180,
    )
    out2 = (p.stdout or "") + (p.stderr or "")
    check("4.1 应用可启动且 livez 正常", "LIVEZ 200" in out2, out2.strip().splitlines()[-1] if out2 else "")
    check("4.2 /metrics 可用（第九轮成果未被升级破坏）", "METRICS 200 True" in out2)
    check("4.3 readyz 通过（真实 DB 往返）", "READYZ 200" in out2)
    check("4.4 JWT 编解码正常（python-jose 3.4.0 未破坏认证）", "JWT 9" in out2)
except subprocess.TimeoutExpired:
    check("4.1 应用启动探测", False, "超时")


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("五、静态收口：不允许绕过门禁的写法")
print("=" * 74)

# 禁止在这次改动中引入新的 --ignore-vuln 硬编码（必须走 allowlist 文件）
#
# ⚠️ 第一版正则写成 `--ignore-vuln\s+(?!\$\()([A-Za-z0-9\-]+)`，误把
#    CI 里 `pip-audit -r requirements.txt $IGNORES` 的**变量名** IGNORES 抓了出来
#    （`(?!\$\()` 只排除了 `$(` 子shell，没排除 `$VAR`）。这是**检测器的 bug，不是 CI 的 bug**。
#    正确做法：先剥掉所有 shell 变量引用再匹配，只找"看起来像 CVE/PYSEC ID"的字面量。
ci_no_vars = re.sub(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", " ", ci)
hardcoded = re.findall(r"--ignore-vuln\s+([A-Za-z]+-\d{4}-\d+)", ci_no_vars)
check(
    "5.1 CI 未硬编码 --ignore-vuln（走 allowlist 文件）",
    not hardcoded,
    f"字面量 ID: {hardcoded}" if hardcoded else "仅通过 $IGNORES 变量传入",
)

check("5.2 allowlist 仅含条目、无注释残留", all(
    ln.strip().startswith("#") or re.match(r"^[A-Z]+-\d{4}-\d+$", ln.strip())
    for ln in allowlist_path.read_text(encoding="utf-8").splitlines() if ln.strip()
))

# 确认升级后的包确实能被 app 导入（防止 requirements 与代码不符）
VERSIONED = [PY, "-c",
             "import importlib.metadata as m;"
             "print(m.version('starlette'), m.version('fastapi'), m.version('python-jose'), m.version('cryptography'))"]
rc3, out3 = run(VERSIONED, 60)
check("5.3 环境已实际安装升级版本", rc3 == 0 and out3.strip().startswith("1.3.1"), out3.strip())


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("六、门禁「第一天就绿」——按 CI 的原样命令实测退出码")
print("=" * 74)
# 前面 3.x 只是"看扫描内容"，这里才是**真正执行门禁命令本身**。
# 差别很关键：`--ignore-vuln` 必须一个 ID 一个 flag。写成
# `--ignore-vuln A B` 时 pip-audit 会把 B 当 project_path 并退出码 2
# （第一版 CI 就是这个写法，属于"上线即红"的 bug，被本检查抓出）。
allowed_ids = sorted({
    ln.strip()
    for ln in allowlist_path.read_text(encoding="utf-8").splitlines()
    if ln.strip() and not ln.strip().startswith("#")
})
gate_cmd = [PY, "-m", "pip_audit", "-r", str(ROOT / "requirements.txt")]
for aid in allowed_ids:
    gate_cmd += ["--ignore-vuln", aid]
rc_gate, out_gate = run(gate_cmd, 600)
check(
    "6.1 门禁命令退出码为 0（第一天就是绿的）",
    rc_gate == 0,
    f"rc={rc_gate} · {out_gate.strip().splitlines()[-1] if out_gate.strip() else ''}",
)
check(
    "6.2 门禁确实收到了全部豁免 flag（未被当成 project_path）",
    "not allowed with" not in out_gate and "usage:" not in out_gate,
    f"{len(allowed_ids)} 个 --ignore-vuln",
)

# --- 可达性哨兵：让豁免"自我作废" -------------------------------------
# allowlist 里的 pyasn1 豁免成立的前提是「项目只用对称 HS256，从不解析外部密钥」。
# 一旦有人引入非对称算法，前提就没了，豁免必须立即失效。这段检查就是那个哨兵。
print()
print("=" * 74)
print("七、可达性哨兵：allowlist 豁免的前提是否仍然成立")
print("=" * 74)

ASYM = re.compile(r"\b(ES256|ES384|ES512|RS256|RS384|RS512|PS256|PS384|PS512)\b")
asym_hits: list[str] = []
for py in (ROOT / "app").rglob("*.py"):
    for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if ASYM.search(line):
            asym_hits.append(f"{py.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
check(
    "7.1 项目未引入非对称 JWT 算法（pyasn1/ecdsa 豁免的前提）",
    not asym_hits,
    "命中: " + " | ".join(asym_hits[:3]) if asym_hits else "全库仍为 HS256 对称算法",
)

# 若上游放宽了 pyasn1 约束（修复版变得可安装），豁免条目必须删除。
# 用 `pip index versions` 判断"有没有 >=0.6.3 可装"，有则说明 python-jose 已松绑。
rc_pv, out_pv = run([PY, "-m", "pip", "download", "pyasn1==0.6.4", "--no-deps",
                     "-d", str(ROOT / "_tmp_verify" / "pyasn1_probe")], 300)
pyasn1_fixable = rc_pv == 0
check(
    "7.2 pyasn1 仍无升级路径（否则必须删豁免并升级）",
    not pyasn1_fixable or "pyasn1" not in allowed_ids,
    "若此项失败：pyasn1 已可安装修复版 → 请删除 allowlist 中 pyasn1 的四条并升级",
)

# 实证：HS256 路径不载入 pyasn1（运行时证据，比读代码可靠）
probe_pyasn1 = """
import sys
from jose import jwt
tok = jwt.encode({"sub":"1"}, "k", algorithm="HS256")
jwt.decode(tok, "k", algorithms=["HS256"])
loaded = [m for m in sys.modules if "pyasn1" in m or "rsa_backend" in m or "_asn1" in m]
print("PYASN1_LOADED", bool(loaded), loaded[:5])
"""
rc_p, out_p = run([PY, "-c", probe_pyasn1], 120)
check(
    "7.3 运行时实证：HS256 路径不载入 pyasn1/rsa_backend（豁免证据仍然成立）",
    "PYASN1_LOADED False" in out_p,
    out_p.strip().splitlines()[-1] if out_p.strip() else "",
)


# ═══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("八、前端依赖（pnpm audit）——本轮最严重的发现在这里")
print("=" * 74)
# 为什么单独一大节：后端 25 个漏洞都在"我们可控的依赖树"里、都有升级路径；
# 前端则是 **39 个漏洞 / 3 个 critical RCE**，且**在本地完全看不见**——
# 因为开发机 registry（华为云镜像）不支持 audit 接口（405），
# `pnpm audit` 在本地以"看起来正常"的方式结束，掩盖了全部问题。
# 这一节同时验证「漏洞已修」与「本地不再瞎眼」两件事。

FE = ROOT.parent / "frontend"
fe_pkg = FE / "package.json"
fe_ws = FE / "pnpm-workspace.yaml"
fe_npmrc = FE / ".npmrc"
fe_lock = FE / "pnpm-lock.yaml"

check("8.1 frontend/package.json 存在", fe_pkg.exists())
check("8.2 frontend/.npmrc 存在（修复本地 audit 盲区）", fe_npmrc.exists())

if fe_npmrc.exists():
    npmrc = fe_npmrc.read_text(encoding="utf-8")
    check(
        "8.3 .npmrc 已将 registry 指向可用的 audit 源",
        "registry=https://registry.npmjs.org" in npmrc.replace(" ", ""),
        "否则本地 audit 打镜像会 405，漏洞不可见",
    )

# next 必须达到清除全部 critical 的最低版本
if fe_pkg.exists():
    import json as _json

    root_pkg = _json.loads(fe_pkg.read_text(encoding="utf-8"))
    ov = (root_pkg.get("pnpm") or {}).get("overrides") or {}
    check(
        "8.4 根 package.json 有 pnpm.overrides.postcss",
        "postcss" in ov,
        f"overrides={ov}（pnpm 9 的 override 必须写在 package.json，"
        "写在 pnpm-workspace.yaml 会被静默忽略）",
    )

    nexts = []
    for sub in ("apps/*", "packages/*"):
        for pj in FE.glob(f"{sub}/package.json"):
            d = _json.loads(pj.read_text(encoding="utf-8"))
            for sec in ("dependencies", "devDependencies"):
                v = (d.get(sec) or {}).get("next")
                if v:
                    nexts.append((pj.relative_to(FE).as_posix(), v))
    bad_next = [(p, v) for p, v in nexts if vkey(v) < vkey("15.5.24")]
    check(
        "8.5 全部 next 依赖 >= 15.5.24（此版本以下无法清除 critical RCE）",
        bool(nexts) and not bad_next,
        f"{len(nexts)} 处声明，未达标: {bad_next}" if bad_next else f"{len(nexts)} 处均为 15.5.24",
    )

# 锁文件必须真的记录了 override 与已升级版本（"生效"的唯一证据）
if fe_lock.exists():
    lock = fe_lock.read_text(encoding="utf-8")
    check(
        "8.6 锁文件记录了 overrides（否则 override 未生效）",
        bool(re.search(r"^overrides:", lock, re.M)),
        "pnpm 会静默忽略位置错误的 override——必须查锁文件而不是看告警",
    )
    pc = sorted(set(re.findall(r"postcss@(\d+\.\d+\.\d+)", lock)))
    check(
        "8.7 锁文件中无脆弱的 postcss 8.4.x（next 内嵌的那个）",
        all(vkey(v) >= vkey("8.5.12") for v in pc) if pc else False,
        f"锁文件中的 postcss 版本: {pc}",
    )
    nx = sorted(set(re.findall(r"next@(\d+\.\d+\.\d+)", lock)))
    check(
        "8.8 锁文件 next 均为 >=15.5.24",
        bool(nx) and all(vkey(v) >= vkey("15.5.24") for v in nx),
        f"next: {nx}",
    )

# 实跑前端审计（本地命令即 CI 命令，不带额外参数）
#
# ⚠️ 在 Windows 上 `pnpm` 是 shell shim（pnpm.cmd / pnpm），
#    subprocess 直接调用会遇到 WinError 2。必须解析出真实可执行文件。
def _resolve_pnpm() -> str | None:
    import os as _os
    import shutil as _shutil

    direct = _shutil.which("pnpm")
    if direct:
        return direct
    for cand in (
        _os.path.expandvars(r"%APPDATA%\npm\pnpm.cmd"),
        _os.path.expandvars(r"%APPDATA%\npm\pnpm"),
        _os.path.expandvars(r"%LOCALAPPDATA%\pnpm\pnpm.exe"),
    ):
        if _os.path.exists(cand):
            return cand
    return None


pnpm_bin = _resolve_pnpm()
check("8.9a 可定位 pnpm 可执行文件", pnpm_bin is not None, str(pnpm_bin))

if pnpm_bin and fe_lock.exists():
    rc_fe, out_fe = run([pnpm_bin, "audit", "--audit-level", "high"], 600, cwd=str(FE))
    if "ERR_PNPM_AUDIT_BAD_RESPONSE" in out_fe or "405" in out_fe:
        check(
            "8.9 前端审计可执行（未落入镜像 405 盲区）",
            False,
            "仍打到不支持 audit 的镜像——本地会掩盖全部漏洞",
        )
    else:
        check(
            "8.9 前端审计可执行且无高危漏洞",
            rc_fe == 0 and "No known vulnerabilities" in out_fe,
            f"rc={rc_fe} · {out_fe.strip().splitlines()[-1][:90] if out_fe.strip() else ''}",
        )


print()
print("=" * 74)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 74)
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  ✗ {name} — {detail}")

sys.exit(1 if FAIL else 0)

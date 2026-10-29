#!/usr/bin/env python
"""「浏览器 job」的编排器：把**真判据**要的那一整套环境，起 → 等 → 跑 → 收。

═══════════════════════════════════════════════════════════════════════════
为什么需要它（而不是把命令直接写进 `ci.yml`）
═══════════════════════════════════════════════════════════════════════════

`ci.yml` 里写一句 `python evidence/verify_dark_mode.py --app admin` 只是**一行**，
但它背后有 **5 个前提**，任何一个不成立，探针都会报 `exit 2`（环境问题）：

  1. **后端在 8001** —— 探针把 `http://127.0.0.1:8001` 写死在源码里；
  2. **库已灌种子** —— 它们都要用 `admin` / `Admin@12345` 登录，没种子一律卡在登录页；
  3. **admin 前端在 3002**，且是**生产构建产物**（`next start` 需要 `.next/BUILD_ID`）；
  4. **`apps/admin/.env.local` 已播种** —— `NEXT_PUBLIC_API_BASE` 是**构建期内联**的，
     缺了不会报错，只会烘出 SDK 兜底的 `:8000`（`verify_api_base_baked.py` 那个坑）；
  5. **顺序**：必须**先 ready 再跑**。抢跑会得到一堆「登录失败」，
     而它长得和**产品缺陷一模一样**（本项目反复栽在「把环境问题读成产品缺陷」上）。

把这 5 条塞进 YAML 的 `run: |` 里，就等于**把编排逻辑写进了一个没有类型、
没有自检、没有回滚、没法单测**的地方。本仓库已经因为「接线只存在于注释/人脑里」
栽过多次（`evidence/README.md` 坑 64 / 65 / 66）⇒ 编排收进本脚本，
`ci.yml` 只留**一行**调用。

═══════════════════════════════════════════════════════════════════════════
三条纪律（都是踩过的坑换来的）
═══════════════════════════════════════════════════════════════════════════

**① 轮询健康检查，不许 `sleep` 赌。**

    `sleep 30` 不是「等」，是「赌」：赌输了就是 flaky，赌赢了也只是白等 30s。
    这里一律用**真 HTTP 往返**——
      后端轮 `/api/health/readyz`（`app/main.py:337`，**它会做一次真 DB 往返**，
      连接被中间件静默断开也能发现；失败返回 503 而不是 200）；
      前端轮首页 200。
    超时才判失败，并把**两个服务的日志尾巴**一起打出来 —— 不然 CI 日志里
    只剩一句「登录失败」，看不出是服务没起还是真坏了。

    ⚠️ 循环里的 `time.sleep(POLL_INTERVAL_SEC)` **不是**「赌」：它只是**轮询间隔**。
       区别在于「每轮都重新问一次、并检查进程是否已经死了」，而不是「睡够就假定好了」。

**② 独立 `DATABASE_URL`，绝不碰开发库。**

    `seed_demo.py --reset` 会**清空业务表**。本仓库已登记过「跑全量 pytest 会清空
    开发库」（`#209`）⇒ 这里用 `tempfile.mkdtemp()` 下一个**全新** SQLite 文件，
    路径与仓库内任何 `.db` 都不重合，收尾时整个目录删掉。

**③ 探针清单**只有一份**。**

    从 `run_ci_probes.CI_JOB_PROBES` **导入**（不在这里重抄一遍），`ci.yml` 只传 job 名。
    「第二份手写清单一定会漂移」是 `README.md` 坑 64 的原话 —— 别再开第二份。
    本脚本自己的 `JOB_SPECS`（端口 / 给每条探针的额外参数）与那份清单的一致性
    由 `--self-test` 在**阶段 1**（`evidence` job）强制，不靠人眼。

═══════════════════════════════════════════════════════════════════════════
退出码（三值语义，与 `evidence/*.py` 一致）
═══════════════════════════════════════════════════════════════════════════

    0  全部探针通过
    1  至少一条**产品缺陷**（探针 rc=1）
    2  环境 / 仪器问题（服务起不来、构建产物缺失、探针 rc=2 或超时）

    ⚠️ **优先级：`1` 压过 `2`** ——「产品坏了」比「我没测全」更该被看见。
    ⚠️ 两者**都会让 CI 红**。这一点与 `run_ci_probes.py` 的阶段 3 **刻意不同**：
       阶段 3 的 `exit 2` 被翻译成 SKIP（因为那一档本来就是「有产物才判」的降级半边），
       而本 job 的存在意义**就是**把前提备齐 —— 前提备不齐时**跳过 = 白跑**，
       正是坑 65 的形状。所以这里**不翻译成跳过**。

用法：

    python evidence/run_browser_job.py --job browser-admin
    python evidence/run_browser_job.py --job browser-admin --reuse    # 复用已起的服务
    python evidence/run_browser_job.py --job browser-admin --keep     # 不拆服务，便于手查
    python evidence/run_browser_job.py --job browser-admin --verbose
    python evidence/run_browser_job.py --self-test                    # 只校验登记表一致性
"""
from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

sys.path.insert(0, str(HERE))

from run_ci_probes import CI_JOB_PROBES, JOB_ENTRYPOINTS  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
API_PORT = 8001
API_BASE = f"http://127.0.0.1:{API_PORT}"

# 四端端口。**出处**：`verify_dark_mode.APPS` / `verify_reduced_motion.APPS` /
# `apps/<app>/package.json` 的 `-p` 参数（三处一致）。这里**不 import 探针模块** ——
# 那会把 `cdp.py` → `websockets` 拖进本脚本的导入链，而 `--self-test` 要能在
# 阶段 1 轻量地跑。跨表一致性由下面的 `--self-test` 与 `run_ci_probes` 的判据 6 兜。
APP_PORTS: dict[str, int] = {"web": 3000, "lawyer": 3001, "admin": 3002, "im": 3003}

# 每个 job 的运行时规格。**探针清单不在这里**（那份在 `run_ci_probes.CI_JOB_PROBES`），
# 这里只放「怎么把环境摆出来」+「每条探针要什么额外参数」。
#
# 🚨 `apps` 是**列表**而不是单值（2026-09-25 Phase 3 起支持多端）：
#    单端 job 写 `("admin",)` —— 语义与原 `"app": "admin"` 完全一致；
#    四端 job 写 `("web", "lawyer", "admin", "im")` ⇒ 编排器会起**四个** `next start`
#    并把四端 origin 全塞进后端的 `CORS_ORIGINS`（漏一个 = 那一端「点了没反应」，
#    而控制台才有 CORS 报错 —— 又是一次「长得像产品缺陷的环境问题」）。
JOB_SPECS: dict[str, dict] = {
    "browser-admin": {
        "apps": ("admin",),
        # ⚠️ 必须**逐条**写明额外参数：`verify_admin_413_toast_e2e.py` **没有** `--app`
        #   （它只服务 admin 一端），统一加 `--app admin` 会让它 argparse 直接 exit 2。
        #   「哪些探针吃 `--app`」是**逐条事实**，不许用「大概都能加」这种规则去猜。
        # ✅ `verify_dark_mode.py`（2026-09-25 接回）：吃 `--app`，且它自己的 `APPS["admin"]`
        #   与本表的 `APP_PORTS["admin"]=3002` 一致（端口/账号/页面三处对齐）。
        #   ⇒ 它此前被移出本表是因为 **rc=1 = 产品缺陷 #40**，产品修好后接回。
        "probe_args": {
            "verify_reduced_motion.py": ("--app", "admin"),
            "verify_dark_mode.py": ("--app", "admin"),
            "verify_admin_413_toast_e2e.py": (),
        },
    },
    # ── Phase 3（2026-09-26 · 拍板 §11.4-③）：**四端全量** job ────────────────
    #   接线依据 = §11.6 的摸底（2026-09-25 四端生产构建 + 隔离库 + 真后端 · 14 条全真跑 · 823s）
    #   ＋ 2026-09-26 复跑确认**最后 2 条也转绿**（见 `evidence/four_app_rm_2026-09-26.txt`）：
    #     · `verify_breakpoints.py`   rc=0（57.6s）—— **#39**（im ≥1024 导航出口）闭合
    #     · `verify_adjacent_targets.py` rc=0（116.3s）—— **#72**（Pagination 间距/热区）闭合
    #   ⇒ **14 条全部 rc=0**，可一次接线（不是 §11.6 当时的「绿的 12 条」）。
    #
    #   ⚠️ **CI 成本**：四端生产构建（本机 5m41s，runner 更慢）+ 探针 ≈14 min ⇒ **单次 22–25 min**
    #      ⇒ `ci.yml` 里**只在 main / 手动触发**（`if: github.event_name != 'pull_request'`），
    #      与 `browser-admin`（单端 ~2 min）分工：那个守 PR，这个守主干。
    #
    #   🚨 **`probe_args` 全为空 —— 这是逐条核过的事实，不是「大概都不用加」**：
    #     · 12 条**根本没有 `--app`** ⇒ 加了会 argparse 直接 exit 2
    #       （`verify_adjacent_targets` / `verify_ws_msg_type_bypass` / `verify_breakpoints` /
    #        `verify_contract_review_gate_e2e` / `verify_task_center_e2e` / `verify_im_e2e` /
    #        `verify_im_safe_area` / `verify_im_tabs` / `verify_mobile_actionbar` /
    #        `verify_render_e2e`，以及 `verify_mobile_375`（它的参数叫 `--apps`，复数））；
    #     · 余下 3 条（`verify_mobile_safe_area` / `verify_runtime_health` / `verify_tap_targets`）
    #       的 `--app` 是**单端**参数且**默认「四端全跑」** ⇒ 本 job 要的正是四端全跑，
    #       **刻意不加**（加了反而退化成只测一端）。
    #     ⇒ 「哪些探针吃 `--app`」是**逐条事实**，不许用「大概都能加」这种规则去猜
    #       （同 `browser-admin` 里 `verify_admin_413_toast_e2e.py` 的注释）。
    "browser-all": {
        "apps": ("web", "lawyer", "admin", "im"),
        "probe_args": {
            "verify_ws_msg_type_bypass.py": (),
            "verify_im_safe_area.py": (),
            "verify_im_e2e.py": (),
            "verify_contract_review_gate_e2e.py": (),
            "verify_mobile_actionbar.py": (),
            "verify_im_tabs.py": (),
            "verify_mobile_safe_area.py": (),
            "verify_tap_targets.py": (),
            "verify_render_e2e.py": (),
            "verify_runtime_health.py": (),
            "verify_mobile_375.py": (),
            "verify_task_center_e2e.py": (),
            "verify_adjacent_targets.py": (),
            "verify_breakpoints.py": (),
        },
    },
}

POLL_INTERVAL_SEC = 1.0
READY_TIMEOUT_SEC = 180      # 后端：冷启 + 建表 + 灌种子后的首次请求
PAGE_TIMEOUT_SEC = 180       # 前端：`next start` 首次编译路由
SEED_TIMEOUT_SEC = 300
PROBE_TIMEOUT_SEC = 420      # 每条探针都要登录 + 多页渲染，给足
STOP_GRACE_SEC = 10

# 探针在**无种子库**上会退化成「登录失败」，那是最容易被误读成产品缺陷的一种失败
# ⇒ 收尾时若整体失败，把后端日志尾巴打出来（见 `_report`）。
LOG_TAIL_LINES = 30


# ---------------------------------------------------------------------------
# 环境构造
# ---------------------------------------------------------------------------
def _backend_env(db_path: pathlib.Path, apps: tuple[str, ...]) -> dict[str, str]:
    """后端 / 探针共用的环境变量。

    🚨 为什么**后端和探针必须用同一套**：探针会 `import app.*`（读 `settings`），
       两边 `DATABASE_URL` 不一致时，探针会按**另一张库**解释它看到的东西。
    🚨 `CORS_ORIGINS` 必须含**本 job 起的所有端**的 origin：浏览器页在
       `localhost:<port>`，而它请求的是 `127.0.0.1:8001` ⇒ **跨源**。
       漏掉任一端 ⇒ 那一端表现为「点了没反应」，控制台才有 CORS 报错
       —— 又是一次「长得像产品缺陷的环境问题」。
    """
    origins = ",".join(
        f"http://localhost:{APP_PORTS[a]},http://127.0.0.1:{APP_PORTS[a]}" for a in apps
    )
    env = dict(os.environ)
    env.update({
        # 绝对路径：`sqlite+aiosqlite:///` + `/tmp/...` = 四斜杠；Windows 下
        # `as_posix()` 给 `C:/...`，拼出三斜杠 —— 两种写法 SQLAlchemy 都认。
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
        "DATABASE_URL_SYNC": f"sqlite:///{db_path.as_posix()}",
        "ENVIRONMENT": "test",
        # 🚨 `DEBUG` 默认 **True**，而它**只**被 `app/database.py` 用来开 SQL echo
        #    （全仓唯一的用处）⇒ 不关掉的话：① `backend.log` 被 SQL 刷屏
        #    （失败取证时反而看不清关键行）；② **探针只要 `import app.database`
        #    就会把 SQL 打进 stdout**，污染探针输出（实测 2026-09-25：
        #    `verify_task_center_e2e.py` 的输出里混进 `sqlalchemy.engine.Engine` 行）。
        #    ⇒ 关掉**不改任何 API 行为**，只降噪。
        "DEBUG": "false",
        "SECRET_KEY": "ci-only-not-a-real-secret-0123456789abcdef",
        "MODERATION_ENABLED": "true",
        "MODERATION_BACKEND": "builtin",
        "API_PORT": str(API_PORT),
        "CORS_ORIGINS": origins,
        # 环回地址不该走代理。本仓库 `envprobe.py` 里为同一个坑单独记过一笔。
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        # 沙箱的批量删除守卫（本机跑时才会碰到；CI 上无副作用）。
        "CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR": "",
        "CODEBUDDY_TOOL_CALL_ID": "",
    })
    return env


def _popen_kwargs() -> dict:
    """让子进程**自成一个进程组**，收尾时才能连子孙一起收掉。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _tail(path: pathlib.Path, n: int = LOG_TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(读不到日志)"
    return "\n".join(lines[-n:]) or "(日志为空)"


# ---------------------------------------------------------------------------
# 服务生命周期
# ---------------------------------------------------------------------------
class Service:
    """一个被本脚本拉起来的子进程 + 它的日志文件。"""

    def __init__(self, label: str, cmd: list[str], cwd: pathlib.Path,
                 env: dict[str, str], log_path: pathlib.Path) -> None:
        self.label = label
        self.cmd = cmd
        self.log_path = log_path
        self._fh = log_path.open("w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(  # noqa: S603
            cmd, cwd=str(cwd), env=env, stdout=self._fh, stderr=subprocess.STDOUT,
            **_popen_kwargs(),
        )

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                if os.name == "nt":
                    # `next start` / `uvicorn` 都是单进程，但留 `-T` 兜子孙。
                    subprocess.run(  # noqa: S603
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True, check=False,
                    )
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.proc.wait(timeout=STOP_GRACE_SEC)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    self.proc.kill()
                    self.proc.wait(timeout=STOP_GRACE_SEC)
        self._fh.close()


def _wait_http(url: str, timeout: float, svc: Service, what: str) -> bool:
    """轮询直到 200 / 超时 / 进程死亡。**不许 `sleep` 赌**（见模块 docstring 纪律 ①）。"""
    deadline = time.monotonic() + timeout
    last = "尚未连上"
    while time.monotonic() < deadline:
        if not svc.alive():
            print(f"❌ {svc.label} 进程**已退出**（等 {what} 时）—— 日志尾巴：")
            print(_indent(_tail(svc.log_path)))
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
                last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            # 🚨 `/api/health/readyz` 在 DB 不可用时**故意**返回 503（不是 200）⇒
            #    这里必须当成「还没好，继续等」，而不是「服务坏了」。
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 连接类异常五花八门，统一当「还没好」
            last = type(exc).__name__
        time.sleep(POLL_INTERVAL_SEC)

    print(f"❌ 等 {what} 超时（{timeout:.0f}s，最后一次：{last}）—— {svc.label} 日志尾巴：")
    print(_indent(_tail(svc.log_path)))
    return False


def _indent(text: str, prefix: str = "      ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _seed(db_path: pathlib.Path, env: dict[str, str]) -> bool:
    """灌种子。**必须在起后端之前**：避免两个进程同时写同一个 SQLite 文件。"""
    print("→ 灌种子（seed_demo.py --reset，库：%s）" % db_path.name)
    try:
        cp = subprocess.run(  # noqa: S603
            [sys.executable, "seed_demo.py", "--reset"],
            cwd=str(BACKEND), env=env, capture_output=True, text=True,
            timeout=SEED_TIMEOUT_SEC, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(f"❌ 灌种子超时（{SEED_TIMEOUT_SEC}s）")
        return False
    if cp.returncode != 0:
        print(f"❌ 灌种子失败（rc={cp.returncode}）")
        for label, stream in (("stdout", cp.stdout), ("stderr", cp.stderr)):
            if stream and stream.strip():
                print(f"  ── {label} ──")
                print(_indent("\n".join(stream.strip().splitlines()[-15:])))
        return False
    tail = (cp.stdout or "").strip().splitlines()
    if tail:
        print(_indent("\n".join(tail[-3:])))
    return True


def _start_backend(env: dict[str, str], log_dir: pathlib.Path) -> Service:
    print(f"→ 起后端 uvicorn（127.0.0.1:{API_PORT}）")
    return Service(
        label="后端(uvicorn)",
        cmd=[sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(API_PORT), "--log-level", "warning"],
        cwd=BACKEND, env=env, log_path=log_dir / "backend.log",
    )


def _next_entry(app: str) -> pathlib.Path:
    """`next` 的**真 JS 入口**。

    🚨 为什么不调 `pnpm --filter app-admin start`：
       `pnpm` 在 Windows 上是 `pnpm.CMD`（批处理）⇒ 从 Python 起它要过 `cmd.exe`。
       本机沙箱**拦 `cmd.exe`**（"Invoking cmd.exe from Bash bypasses all command validation"），
       且批处理还会把参数里的非 ASCII 路径搞坏。
       直接 `node <next 的 JS 入口>` 是**跨平台同一条命令**，没有 shell、没有 shim。
    """
    entry = FRONTEND / "apps" / app / "node_modules" / "next" / "dist" / "bin" / "next"
    if not entry.exists():
        raise RuntimeError(
            f"找不到 next 入口：{entry}\n"
            f"  ⇒ 多半是 `frontend/` 还没 `pnpm install`。")
    return entry


def _start_frontend(app: str, node: str, log_dir: pathlib.Path) -> Service:
    port = APP_PORTS[app]
    app_dir = FRONTEND / "apps" / app
    build_id = app_dir / ".next" / "BUILD_ID"
    if not build_id.exists():
        raise RuntimeError(
            f"`apps/{app}/.next/BUILD_ID` 不存在 ⇒ 这不是**生产构建**产物，`next start` 起不来。\n"
            f"  ⇒ 先跑：`cd frontend && pnpm --filter app-{app} build`\n"
            f"  ⚠️ 本机若四端 dev server 正在跑，重建会毁掉它们的 `.next`（先停掉它们）。")
    print(f"→ 起前端 next start（{app} → http://localhost:{port}）")
    return Service(
        label=f"前端({app})",
        # 不传 `-H`：`next start` 默认绑 `0.0.0.0`。**刻意不绑 127.0.0.1** ——
        # 探针访问的是 `http://localhost:3002`，而某些系统上 `localhost` 先解析到 `::1`，
        # 只绑 IPv4 会得到一个「连不上」的假缺陷。
        cmd=[node, str(_next_entry(app)), "start", "-p", str(port)],
        # ⚠️ 日志**按端分文件**：四端 job 里若共用 `frontend.log`，
        #    四个 `Service` 会各自 `open("w")` 同一个路径 ⇒ 后开的截断先开的
        #    ⇒ 失败取证时只能看到最后一端（`--reuse`/`--keep` 时尤其误导）。
        cwd=app_dir, env=dict(os.environ), log_path=log_dir / f"frontend-{app}.log",
    )


# ---------------------------------------------------------------------------
# 跑探针
# ---------------------------------------------------------------------------
def _run_probe(name: str, extra: tuple[str, ...], env: dict[str, str]) -> tuple[int, float, str, str]:
    """跑一条探针。返回 `(rc, 秒, stdout, stderr)`。**不打印、不改判**（与运行器同构）。"""
    t0 = time.monotonic()
    try:
        cp = subprocess.run(  # noqa: S603
            [sys.executable, str(HERE / name), *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SEC, encoding="utf-8", errors="replace",
        )
        return cp.returncode, time.monotonic() - t0, cp.stdout or "", cp.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, time.monotonic() - t0, "", ""


_RC_MEANING = {
    0: ("✅", "通过"),
    1: ("❌", "**产品缺陷**"),
    2: ("⚠️", "**环境问题**（不是产品坏了）"),
    124: ("⏱", f"超时（>{PROBE_TIMEOUT_SEC}s）—— 当**仪器问题**，不当跳过"),
}


def _run_probes(job: str, env: dict[str, str], verbose: bool) -> int:
    spec = JOB_SPECS[job]
    probes = CI_JOB_PROBES[job]
    print(f"\n{'=' * 76}\n跑 {len(probes)} 条真判据（job = {job}）\n{'=' * 76}")

    results: list[tuple[str, int, float]] = []
    for name in probes:
        extra = spec["probe_args"][name]
        cmd = " ".join([f"python evidence/{name}", *extra])
        print(f"\n▸ {cmd}")
        rc, sec, out, err = _run_probe(name, extra, env)
        icon, meaning = _RC_MEANING.get(rc, ("❓", f"未知退出码 {rc}"))
        print(f"  {icon} rc={rc} · {meaning} · {sec:.1f}s")
        results.append((name, rc, sec))
        # 🚨 **stdout 与 stderr 都要打**：探针「启动期崩溃」（ImportError / 依赖缺失）
        #    的 traceback 全在 stderr 上，只打 stdout 会得到一片空白的失败记录
        #    （`README.md` 坑 48 的原话）。
        if verbose or rc != 0:
            for label, stream in (("stdout", out), ("stderr", err)):
                lines = stream.strip().splitlines()
                if lines:
                    print(f"  ── {label} 尾部 ──")
                    print(_indent("\n".join(lines[-12:])))
                    print("  ────────────")

    defects = [n for n, rc, _ in results if rc == 1]
    envbad = [n for n, rc, _ in results if rc not in (0, 1)]

    print(f"\n{'─' * 76}")
    print(f"汇总：{len(results) - len(defects) - len(envbad)}/{len(results)} 通过")
    if defects:
        print(f"  ❌ 产品缺陷（{len(defects)}）：" + "、".join(defects))
    if envbad:
        print(f"  ⚠️ 环境/仪器（{len(envbad)}）：" + "、".join(envbad))
    if defects or envbad:
        print("  ⚠️ 「产品坏了」与「我没测成」必须分开读 —— 上面每条都标了是哪一类。")
    # ⚠️ **优先级：1 压过 2**。产品缺陷比环境问题更该被看见。
    return 1 if defects else (2 if envbad else 0)


# ---------------------------------------------------------------------------
# 自检（纯函数：只校验登记表，不碰服务 ⇒ 可在阶段 1 跑）
# ---------------------------------------------------------------------------
def _check_live_db_honors_env() -> list[str]:
    """纯函数：**造数必须落到后端真正在读的那张库**。

    🚨 为什么这条自检必须存在（2026-09-25 实测踩到）：
       编排器用 `DATABASE_URL` 把后端指到**隔离临时库**；但探针若**自己猜库路径**
       （硬编码 `backend/demo_8001.db`），造的数据就进了**另一张库** ⇒ 后端看不到。
       症状**不是**「我写错库了」，而是「环境问题（rc=2）」或**假绿**
       —— 又是一次「长得像产品缺陷的环境问题」。
       实测：`verify_task_center_e2e.py` 因此 rc=2（W4 `clicked=no-span`）；
       同族的 `verify_ws_msg_type_bypass.py` 更险 —— 它会把「产品有洞」误报成「环境问题」。
       ⇒ 在这里用一张**临时库**验证 `envprobe.resolve_live_db()` 真的读 `DATABASE_URL`。
    """
    try:
        from envprobe import resolve_live_db
    except Exception as exc:  # noqa: BLE001
        return [f"`envprobe.resolve_live_db` 导入失败：{type(exc).__name__}: {exc}"]

    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="nlawer-livedb-selftest-"))
    tmp_db = tmp_dir / "probe.db"
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "DATABASE_URL_SYNC")}
    try:
        con = sqlite3.connect(str(tmp_db))
        con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, tenant_id TEXT)")
        con.execute("INSERT INTO jobs (tenant_id) VALUES ('ent_acme')")
        con.commit()
        con.close()

        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_db.as_posix()}"
        os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{tmp_db.as_posix()}"
        got = resolve_live_db()
        if got is None or pathlib.Path(got).resolve() != tmp_db.resolve():
            return ["`envprobe.resolve_live_db()` **没有**按 `DATABASE_URL` 解析隔离库："
                    f"期望 `{tmp_db}`，实测 `{got}`"
                    " ⇒ 探针会把造数写进**另一张库**（症状是环境问题或假绿）。"]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return []


def run_self_test() -> int:
    problems: list[str] = []

    # 🚨 **隔离库必须真的被探针看见**：编排器设了 `DATABASE_URL`，探针不认就等于没设。
    #    这条不查登记表，查的是「编排器与探针之间那条隐含契约」。
    problems += _check_live_db_honors_env()

    jobs = set(JOB_SPECS)
    listed = set(CI_JOB_PROBES)
    for job in sorted(jobs - listed):
        problems.append(f"`JOB_SPECS` 有 `{job}`，但 `run_ci_probes.CI_JOB_PROBES` 里没有它"
                        " ⇒ 编排器会跑一个**没有任何探针**的 job（空转）")
    # ⚠️ 反向**不是**要求相等：`CI_JOB_PROBES` 里可以有不归编排器管的 job
    #    （如 `frontend` —— 它的两步直接写在 `ci.yml` 里，2026-09-24 拍板 ①）。
    #    但那种 job **必须**在 `JOB_ENTRYPOINTS` 里表态过，否则没人知道它怎么跑
    #    ⇒ 「名册说有人跑」变成**假接线**（坑 65/66 的形状）。
    for job in sorted(listed - jobs):
        if job not in JOB_ENTRYPOINTS:
            problems.append(
                f"`CI_JOB_PROBES` 有 `{job}`，但它**既不在** `JOB_SPECS`（本编排器不跑它）、"
                f"**又不在** `run_ci_probes.JOB_ENTRYPOINTS`（没人声明怎么跑它）⇒ **假接线**")

    for job, spec in JOB_SPECS.items():
        probes = set(CI_JOB_PROBES.get(job, ()))
        args = set(spec.get("probe_args", {}))
        for name in sorted(probes - args):
            problems.append(f"[{job}] 探针 `{name}` 没写 `probe_args` ⇒ 不知道该怎么调它")
        for name in sorted(args - probes):
            problems.append(f"[{job}] `probe_args` 里有 `{name}`，但清单里没有这条探针")
        for name in sorted(probes):
            if not (HERE / name).exists():
                problems.append(f"[{job}] 探针文件不存在：evidence/{name}")
        apps = spec.get("apps")
        if not isinstance(apps, tuple) or not apps:
            problems.append(f"[{job}] `apps` 必须是非空 tuple（现在：{apps!r}）——"
                            "单端也要写 `(\"admin\",)`")
            continue
        if len(set(apps)) != len(apps):
            problems.append(f"[{job}] `apps` 里有重复：{apps}")
        for a in apps:
            if a not in APP_PORTS:
                problems.append(f"[{job}] 未知前端 `{a}`（已知：{sorted(APP_PORTS)}）")

    # 🚨 端口冲突只在**同一个 job 内**才是问题：不同 job 从不同时跑
    #    （`ci.yml` 里是两个独立 job，本机也不会同时跑两个）。
    #    所以这里按 job 分组查，而不是全局查 —— 全局查会把「两个 job 各起一个 admin」
    #    误报成冲突，而那是完全正常的。
    for job, spec in JOB_SPECS.items():
        seen: dict[int, str] = {}
        for a in spec.get("apps") or ():
            port = APP_PORTS.get(a, -1)
            if port in seen:
                problems.append(f"[{job}] 端口 {port} 上同时起了 `{seen[port]}` 与 `{a}`")
            seen[port] = a

    if problems:
        print("❌ 编排器自检失败（`--self-test`，纯函数、不碰服务）—— 各臂分开报：")
        for p in problems:
            print(f"  · {p}")
        print("   ⇒ 这类不一致**只会在真跑的时候才暴露**，且会伪装成产品缺陷 ⇒ 必须在这里拦住。")
        return 1

    # 🚨 **每条臂都要自己报出来**：本项目反复踩过「接线了但看不出跑没跑」
    #    —— 一条**静默通过**的臂等于没有臂（下次它坏掉时，没人知道它曾经跑过）。
    #    所以这里仿 `run_ci_probes.py --self-test` 的格式，逐臂打「期望 / 实测」。
    print("✅ 编排器自检通过（`--self-test`，纯函数、不碰服务）—— 各臂分开报：")
    print("  [L1] 隔离库契约（编排器设 `DATABASE_URL` ⇒ 探针 `resolve_live_db()` 认它）")
    print("        期望：解析结果 == 注入的临时库   实测：✓")
    print("  [J1] 登记表一致性（`JOB_SPECS` / `CI_JOB_PROBES` / `JOB_ENTRYPOINTS` /")
    print("        `probe_args` / `apps` 形态 / **同 job 内**端口冲突）")
    print("        期望：零不一致   实测：✓")
    total = sum(len(v) for v in CI_JOB_PROBES.values())
    print(f"  本编排器负责 {len(JOB_SPECS)} 个 job（其余 job 的入口见 `run_ci_probes.JOB_ENTRYPOINTS`）：")
    for job, spec in sorted(JOB_SPECS.items()):
        probes = CI_JOB_PROBES[job]
        where = " · ".join(f"{a}@{APP_PORTS[a]}" for a in spec["apps"])
        print(f"  · [{job}] 前端 {where} · 后端 @{API_PORT} · {len(probes)} 条真判据")
        for name in probes:
            extra = " ".join(spec["probe_args"][name])
            print(f"      - evidence/{name} {extra}".rstrip())
    print(f"  · `CI_JOB_PROBES` 合计 {len(CI_JOB_PROBES)} 个 job / {total} 条探针"
          "（清单来自 `run_ci_probes.CI_JOB_PROBES`，**不在这里重抄**）")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _check_node() -> str:
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        raise RuntimeError(
            "找不到 `node`。CI 上由 `actions/setup-node` 提供；本机请确认它在 PATH 上，"
            "或用 `NODE_BIN=<路径>` 显式指定。")
    return node


def _build_hint(app: str) -> str:
    return f"cd frontend && pnpm --filter app-{app} build"


def main(argv: list[str] | None = None) -> int:
    # 🚨 **行缓冲**：本脚本是**分钟级**的（构建产物 + 起两个服务 + 真判据），
    #    而 Python 在 stdout **不是 TTY**（CI、`> file`）时默认**块缓冲** ⇒
    #    日志会攒到 8 KB 才吐 ⇒ 出问题时「日志里什么都没有」，看的人只能干等。
    #    实测踩到过：本机 `> /tmp/x.log` 后 45s 内文件仍是**空的**。
    #    `reconfigure` 在 Python 3.7+ 可用；被重定向到不支持的对象时忽略即可。
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    ap = argparse.ArgumentParser(description="浏览器 job 编排器（起→等→跑→收）")
    ap.add_argument("--job", default="browser-admin", help=f"要跑的 job（{sorted(JOB_SPECS)}）")
    ap.add_argument("--self-test", action="store_true", help="只校验登记表一致性（纯函数）")
    ap.add_argument("--reuse", action="store_true",
                    help="**不**起服务，直接对已运行的服务跑探针（本机验证用）")
    ap.add_argument("--keep", action="store_true", help="跑完**不**拆服务（便于手查）")
    ap.add_argument("--verbose", action="store_true", help="每条探针都打完整输出（不只失败时）")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if args.job not in JOB_SPECS:
        print(f"❌ 未知 job：{args.job}（已登记：{sorted(JOB_SPECS)}）")
        print("   ⚠️ job 名必须与 `run_ci_probes.CI_JOB_PROBES` 的键、以及 `ci.yml` 里"
              " `--job` 的取值三者一致。")
        return 2

    spec = JOB_SPECS[args.job]
    apps: tuple[str, ...] = spec["apps"]

    # 登记表自检先跑：不一致时**根本不该开工**（否则失败会伪装成产品缺陷）。
    if run_self_test() != 0:
        return 2

    if args.reuse:
        print("→ `--reuse`：不起服务，直接对**已运行**的服务跑探针")
        print(f"   ⚠️ 前提：后端在 {API_PORT}、"
              + "、".join(f"{a} 在 {APP_PORTS[a]}" for a in apps)
              + "、且**库已灌种子**。任一不成立 ⇒ 探针会报环境问题（不是产品缺陷）。")
        env = _backend_env(pathlib.Path(":memory:"), apps)  # 仅用于给探针一套 settings
        return _run_probes(args.job, env, args.verbose)

    tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="nlawer-browser-job-"))
    log_dir = tmp_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_root / "browser_job.db"
    env = _backend_env(db_path, apps)

    print("═" * 76)
    print(f"浏览器 job 编排：{args.job}"
          f"（前端 {'、'.join(f'{a}@{APP_PORTS[a]}' for a in apps)} · 后端 @{API_PORT}）")
    print(f"  隔离库：{db_path}")
    print(f"  日志：{log_dir}")
    print("═" * 76)

    services: list[Service] = []
    rc = 2
    try:
        # 🚨 **先查产物，再灌种子 / 起后端**：多端 job 里「只构建过其中一两端」极常见
        #    （本机 2026-09-25 就是只有 admin 有产物）。提前失败 ⇒ 省掉一次
        #    「灌种子 + 起后端」的浪费，而且错误信息**一次列全所有缺的端**，
        #    不是起一个报一个。
        missing = [a for a in apps
                   if not (FRONTEND / "apps" / a / ".next" / "BUILD_ID").exists()]
        if missing:
            print("❌ 以下端**没有生产构建产物**（`.next/BUILD_ID` 不存在）⇒ `next start` 起不来：")
            for a in missing:
                print(f"   · {a} —— 先跑：{_build_hint(a)}")
            print("   ⚠️ 本机若这些端的 dev server 正在跑，重建会毁掉它们的 `.next`（先停掉它们）。")
            return 2

        if not _seed(db_path, env):
            return 2

        backend = _start_backend(env, log_dir)
        services.append(backend)
        if not _wait_http(f"{API_BASE}/api/health/readyz", READY_TIMEOUT_SEC, backend,
                          "后端 readyz（真 DB 往返）"):
            return 2
        print("  ✓ 后端就绪（readyz 200）")

        node = _check_node()
        # 🚨 多端 job 里这是**多个** `next start`。**逐个起、逐个等就绪** ——
        #    一次性全起再统一等，会让「到底哪一端没起来」在日志里分不清
        #    （四个端口的超时信息会混在一起）。
        for a in apps:
            frontend = _start_frontend(a, node, log_dir)
            services.append(frontend)
            if not _wait_http(f"http://localhost:{APP_PORTS[a]}/", PAGE_TIMEOUT_SEC, frontend,
                              f"{a} 首页"):
                return 2
            print(f"  ✓ {a} 就绪（http://localhost:{APP_PORTS[a]}/ 200）")

        rc = _run_probes(args.job, env, args.verbose)
    except RuntimeError as exc:
        print(f"❌ 环境准备失败：{exc}")
        rc = 2
    finally:
        if not args.keep:
            for svc in reversed(services):
                svc.stop()
        # 🚨 失败时**必须**把服务日志尾巴打出来：探针的输出只覆盖「它自己看到的」，
        #    而「服务根本没起来 / 种子没灌进去 / CORS 挡了」这些**全在服务日志里**。
        #    CI 上那段 artifact 要人去下载，日志里直接有才是**默认能被看到**的。
        if rc != 0 and services:
            print("\n── 服务日志尾巴（**取证**：环境问题的痕迹都在这两处）──")
            for svc in services:
                print(f"  ▸ {svc.label}（{svc.log_path.name}）")
                print(_indent(_tail(svc.log_path), "      "))
        if args.keep or rc != 0:
            # 失败时**保留**临时目录：日志是唯一的取证材料，清掉就什么都没了。
            why = "`--keep`：服务**仍在运行**" if args.keep else "本次未通过 ⇒ 服务已停、日志**保留**"
            print(f"\n→ {why}：")
            for svc in services:
                pid = svc.proc.pid if args.keep else "(已停)"
                print(f"   · {svc.label}  pid={pid}  日志={svc.log_path}")
            print(f"   · 隔离库：{db_path}")
        else:
            print("\n→ 服务已收尾（全部通过 ⇒ 连临时目录一起清掉）")
            shutil.rmtree(tmp_root, ignore_errors=True)

    if rc == 1:
        print("\n❌ 浏览器 job 失败：**产品缺陷**（判据断言不成立）。")
    elif rc == 2:
        print("\n⚠️ 浏览器 job 未通过：**环境/仪器问题**。")
        print("   别把它读成产品缺陷 —— 但也**别翻译成「跳过」**："
              "本 job 的存在意义就是把前提备齐，备不齐时跳过 = 白跑（README 坑 65）。")
        print("   ⇒ 常见三条：① 生产构建没跑（无 `.next/BUILD_ID`）："
              + "、".join(f"`{_build_hint(a)}`" for a in apps)
              + "；② `apps/<app>/.env.local` 没播种；③ 端口被占。")
    else:
        print(f"\n✅ 浏览器 job 通过：{len(CI_JOB_PROBES[args.job])} 条真判据全绿。")
    return rc


if __name__ == "__main__":
    sys.exit(main())

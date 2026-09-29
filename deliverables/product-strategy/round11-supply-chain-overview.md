# 第十一轮：依赖漏洞扫描门禁（供应链安全）

**日期**：2026-09-14
**类型**：安全加固 / 工程化（P1）
**参与成员**：方向明（产品舵手，编排与汇编）
**关联轮次**：承接第十轮「列表 COUNT 有界化」，为第九轮 CI/CD 门禁补上最后一块拼图

---

## 📌 TL;DR（执行摘要）

- **核心目标**：让「依赖被投毒 / 带已知漏洞」这类问题**无法悄悄流进主干**。前九轮修的全是我们自己写的代码，而 `ruff` / `pytest` / `tsc` 对**依赖本身**完全无感——这是最后一类"改坏了不会被发现"。
- **关键发现（远超预期）**：后端 25 个漏洞、**前端 39 个漏洞（含 3 个 critical RCE）**。前端的问题**在本地完全看不见**——开发机 registry（华为云镜像）不支持 audit 接口（405），本地门禁是"假的"。
- **关键决策**：① 引入 `pip-audit` + `pnpm audit` 双门禁（4 个 CI job）；② 依赖升级到「无已知漏洞的最低版本」；③ 上游无补丁的漏洞登记 `security-allowlist.txt` 并附**可达性分析**（而非让 CI 长期报红）。
- **下一步**：后端 219 测试全绿、双门禁本地实跑均绿 → 进入产品功能缺口（通知系统只写不读 / 前端注册支付 / IM 真实打通）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 双端依赖漏洞扫描门禁 + 依赖升级到修复版 + 显式豁免清单（附可达性分析） |
| 优先级 | **P1**（供应链安全） |
| 预期影响 | 后端 25 → 5（全部为无补丁路径）；前端 **39 → 0**（含消除 3 个 critical RCE） |
| 资源需求 | 4 个 CI job（原 3 个）；无新增外部服务依赖 |
| 风险等级 | **中**（含 FastAPI 0.115→0.141、starlette 0.38→1.3、Next 14→15 三次实质升级，均已实测验证） |

---

## 1. 为什么这一轮必须做：门禁的盲区

前九轮的所有缺陷都有一类共同点——**能用我们自己的工具发现**：

| 缺陷类型 | 发现手段 | 前九轮是否覆盖 |
|---------|---------|--------------|
| 语法/类型错误 | `ruff` / `tsc` / `compileall` | ✅ |
| 逻辑与并发错误 | `pytest` + 真实 DB / 并发 | ✅ |
| 鉴权与越权 | 5 层验证 + IDOR 扫描 | ✅ |
| **依赖被投毒 / 带已知 CVE** | **只能靠扫描器** | ❌ **本轮补上** |

> ⚠️ 这是本轮的核心判断：`ruff`/`pytest`/`tsc` 检查的是"**我们写的代码**对不对"，
> 它们**不会**告诉我们"我们依赖的代码是否已被攻破"。
> 一个被投毒的依赖能让所有测试通过、所有静态检查通过、然后在生产环境开门。

---

## 2. 实测：问题比预期严重得多

### 2.1 后端（pip-audit）

修复前：**25 个唯一漏洞 / 6 个包**（47 条原始记录，含重复）

| 包 | 漏洞数 | 是否可达（本项目的真实暴露） | 判定 |
|----|-------|--------------------------|------|
| `python-multipart` 0.0.9 | 7 | **✅ 可达**（`evidence.py` 接收 `UploadFile`） | 必须修 |
| `starlette` 0.38.6 | 7 | **✅ 可达**（Host 头校验绕过影响本项目中间件鉴权） | 必须修 |
| `cryptography` 43.0.1 | 6 | 间接（TLS / 证书链） | 必须修 |
| `python-jose` 3.3.0 | 3 | ❌ 不可达（已用 `algorithms=[...]` 白名单、从不调用 `jwe`） | 仍升级 |
| `pytest` 8.3.3 | 1 | 仅开发期 | 升级 |
| `ecdsa` 0.19.2 | 1 | ❌ 不可达（从不 import，仅是 python-jose 的传递依赖） | **无补丁，登记豁免** |

**关键方法论：严重度 ≠ 数量，必须做可达性分析。**
本轮逐条读代码确认，得出结论：**认证栈的 4 类高危 CVE 在本项目其实都不可达**——因为
`app/core/security.py` 用了 `algorithms=[settings.ALGORITHM]`（显式白名单，仅 HS256），
这正是官方推荐的规避方式；且项目**从不调用 `jwe`**。真正的暴露面是
`python-multipart` 的 DoS（`evidence.py` 确实接收上传）。

> 📌 这个结论直接影响优先级排序：如果只看 CVE 数量，会把精力全砸在"认证栈 3 个高危"上；
> 而实际上**最该修的是看起来数量最多的 `python-multipart`**。

### 2.2 前端（pnpm audit）——本轮最严重的发现

修复前：**39 个漏洞 / 3 个 critical / 14 个 high**

| 包 | 漏洞数 | 代表性问题 |
|----|-------|-----------|
| `next` 14.2.5 | 33 | **2 个 critical 未认证 RCE** + 中间件鉴权绕过 + 多项 SSRF/DoS |
| `postcss` 8.4.31（**next 内嵌**） | 6 | 任意文件读取 / 路径穿越 / XSS |

**⚠️ 最危险的不是漏洞本身，而是"本地看不见"**：

```
$ pnpm audit --audit-level high      # 本地（华为云镜像）
ERR_PNPM_AUDIT_BAD_RESPONSE ... responded with 405: Request method 'POST' is not supported
```

镜像**不支持 audit 接口**。接入本轮修复前，本地跑这条命令**不会暴露任何漏洞**，
而同一份代码在 CI（官方 registry）上会报出 39 个。
**本地与 CI 结论不一致的门禁，比没有门禁更危险**——因为它会让人误以为"已经检查过了"。

---

## 3. 修复方案

### 3.1 后端依赖升级（链式约束，不能只升一个）

`fastapi==0.115.0` **硬约束 `starlette<0.39.0`**，而 starlette 的修复全在 0.40+。
因此这是一个**链式升级**，不是随意改版本号：

| 包 | 升级 | 修复内容 |
|----|------|---------|
| `fastapi` | 0.115.0 → **0.141.1** | 解除 starlette 上界（链式约束的解锁钥匙） |
| `starlette` | 0.38.6 → **1.3.1** | Host 头校验绕过（可绕过按路径的鉴权）+ 表单解析 DoS |
| `python-multipart` | 0.0.9 → **0.0.31** | 路径穿越 + Content-Length 负数读 + 头部 DoS（7 项） |
| `python-jose` | 3.3.0 → **3.4.0** | JWT bomb DoS + 算法混淆 |
| `cryptography` | 43.0.1 → **50.0.1** | OpenSSL 6 项 + PKCS7 解密结果误报 |
| `pytest` | 8.3.3 → **9.0.3** | `/tmp` 目录本地提权/DoS |
| `pytest-asyncio` | 0.24.0 → **1.3.0** | 必须 ≥1.3.0（否则与 pytest 9 冲突） |
| `pip-audit` | 新增 **2.10.1** | 扫描工具本身 |

**目标版本必须来自扫描器的 `fix_versions`，不能靠推断**——本轮第一版把 `cryptography`
定在 49.0.0（凭感觉猜的"最低修复版"），实测 `pip-audit` 仍报 `PYSEC-2026-3552`（fix 50.0.0）。

### 3.2 前端升级：Next 14 → 15（无 14.x 可解）

经计算，**没有任何 14.x 版本能清除那两个 critical RCE**：
需要 `next >= 15.5.24` 才能覆盖全部 33 条 Next 告警。

破坏面评估（升级前逐项核查）：
- ✅ **无 `middleware.ts`** —— Next 15 最高风险的破坏性变更（middleware 语义）不受影响
- ✅ 全部 App Router + React 18.3.1（与 Next 15 兼容）
- ⚠️ **仅 1 处**需要改：`apps/lawyer/app/cases/[id]/page.tsx` 的同步 `params`

修复方式：该组件是 `"use client"`，改用 `useParams()` 读取路由参数，
**彻底移除对 `params` prop 的依赖**，未来版本语义再变也不受影响。

**`postcss` 需要额外 override**：`next@15.5.24` 仍然**精确锁定 `postcss@8.4.31`**，
仅升 next 清不掉它（实测残留 4 个漏洞）。必须在解析层强制改写为 8.5.28。

### 3.3 豁免清单：让门禁"第一天就是绿的"

**设计要点**：门禁必须第一天就绿，且不能变成"误报机器"。
做法是把上游无补丁的漏洞显式登记在 `security-allowlist.txt`，每条附**可达性分析 + 复查条件**。

| 豁免条目 | 理由 |
|---------|------|
| `PYSEC-2026-1325`（ecdsa Minerva 时序攻击） | ① 不可达（从不 import ecdsa；仅 HS256；从不调 jwe）② **上游明确声明不修**，`fix_versions` 为空 |
| `PYSEC-2026-2263 / -3455 / -3456 / -3457`（pyasn1 ASN.1 解码 DoS ×4） | ① **依赖闭包锁死**：`python-jose 3.4.0` 声明 `pyasn1<0.5.0`，修复版是 0.6.3/0.6.4，**在保留 python-jose 的前提下无升级路径**（实测 `ResolutionImpossible`）② 不可达（附**运行时实证**，见下） |

**pyasn1 的不可达是"运行时实证"的，不是读代码猜的**：

```
HS256 encode+decode 前后比对 sys.modules：
  pyasn1                → 未载入
  jose.backends.rsa_backend → 未载入
  jose.backends._asn1       → 未载入
```

**可达性哨兵**（让豁免"自我作废"）：`verify_p1_supply_chain.py` 第七节会在
① 项目引入任何非对称算法（RS256/ES256/…）、或 ② pyasn1 修复版变得可安装时，
**主动报失败**——防止豁免静默腐烂。

### 3.4 修复本地盲区

新增 `frontend/.npmrc`，把 registry 指向官方源，使**本地与 CI 结论一致**。

> 实测纠错：先写的是 `audit-registry=...`，**该设置项 pnpm 9.12 不支持**（仍打镜像）；
> 改为 `registry=...` 后生效。这类"写了以为生效、实际没生效"的配置，
> 是本轮反复踩到的同一个坑（override 位置也是）。

---

## 4. 验证（5 层，全部实跑）

### 4.1 后端

| 层级 | 手段 | 结果 |
|------|------|------|
| 编译 | `compileall app/` | 0 错误 |
| 静态 | `ruff check .` | **All checks passed**（顺手修掉第十轮遗留 2 处） |
| 单测 | `pytest -q` | **219 passed**（升级后栈，EXIT=0） |
| 真实 DB | 门禁脚本第二节 readyz（真实 DB 往返） | 通过 |
| 端到端 | `TestClient` → livez / metrics / readyz / JWT | 全部通过 |

### 4.2 门禁本身（这是本轮的核心交付物）

| 检查 | 结果 |
|------|------|
| `pip-audit -r requirements.txt` + 5 个 `--ignore-vuln` | **`No known vulnerabilities found, 10 ignored`，退出码 0** |
| `pnpm audit --audit-level high`（本地，无额外参数） | **`No known vulnerabilities found`，退出码 0** |
| CI YAML 合法性 | 4 jobs：`['backend','frontend','security-audit','docker-build']` |

### 4.3 验证脚本

`backend/verify_p1_supply_chain.py` —— **48 项检查全通过**，八节：
① 门禁产物齐备性 ② requirements 升级状态 ③ 实际扫描结果 ④ 升级后应用可真实启动
⑤ 静态收口（禁止绕过门禁的写法）⑥ 门禁命令实跑退出码 ⑦ 可达性哨兵 ⑧ 前端依赖

---

## 5. 本轮的方法论教训（比代码更值得记住）

1. **"修复版本"必须来自扫描器的 `fix_versions`，不能推断。**
   我凭感觉把 cryptography 定在 49.0.0，实测仍有 1 个漏洞（fix 50.0.0）——
   并且这个错误**顺带暴露了另一个被我忽略的包 pyasn1**。

2. **升级会带来新约束，必须记录。**
   升 `python-jose` 到 3.4.0 换来的是 `pyasn1<0.5.0` 的硬约束，
   反而锁死了一个本可修复的包。这类"升级的副作用"必须写进文件，否则下一个人会以为是漏升。

3. **配置"写了"不等于"生效"，判定标准是产物而非告警。**
   - `overrides` 写在 `pnpm-workspace.yaml`：**静默忽略**（无报错），锁文件里没有 `overrides:` 段
   - 写在 `package.json` 的 `pnpm.overrides`：**生效**，尽管终端会打印一条
     "The pnpm field in package.json is no longer read" 的**误导性告警**
   - ✅ 唯一可靠判据：`grep -A2 '^overrides:' pnpm-lock.yaml`

4. **"本地绿"必须与"CI 绿"同义。**
   华为云镜像的 405 让本地 audit 静默失效，掩盖了 39 个漏洞（含 3 个 critical）。
   门禁如果只在 CI 生效，就会在本地养成"我检查过了"的错觉。

5. **⚠️ 绝不要在 pip install 中途杀进程。**
   本轮我把一个卡住的 `pip install` 杀掉，结果 pip 已卸载完旧包、尚未装新包，
   把 venv 的 `site-packages` 留成**完全空的**（连 pip 自身都没了）。
   修复：`python -m ensurepip --upgrade --default-pip` 恢复 pip，再整体重装。
   **pip 不是事务性的**——中途中断会让环境处于"什么都没有"的状态。

6. **跳过"看起来不必要"的验证，代价很大。**
   我曾以为 Next 15 升级"肯定有大量破坏性变更"，实际逐项核查后只有 1 处
   （且无 `middleware.ts`）。**先测量再动手，比凭印象排斥大版本升级更省时间。**

---

## ✅ 行动清单

| # | 行动 | 负责方 | 状态 |
|---|------|--------|------|
| 1 | 后端依赖升级到修复版（8 个包，含链式约束） | 后端 | ✅ 完成 |
| 2 | 前端 Next 14→15 + postcss override | 前端 | ✅ 完成 |
| 3 | 建立 `security-allowlist.txt`（含可达性分析 + 复查条件） | 安全 | ✅ 完成 |
| 4 | CI 新增 `security-audit` job（pip-audit + pnpm audit） | 工程 | ✅ 完成 |
| 5 | 修复本地 audit 盲区（`frontend/.npmrc`） | 工程 | ✅ 完成 |
| 6 | 编写 `verify_p1_supply_chain.py`（48 项）+ 可达性哨兵 | 质量 | ✅ 完成 |
| 7 | 后端全量回归 219 通过 | 质量 | ✅ 完成 |
| 8 | **持续**：每次依赖变更后复核 allowlist 是否可删 | 全员 | 🔁 长期 |

---

## ⚠️ 待确认 / 假设 / Non-goals

**待确认**：
- 前端 `next 15.5.24` 的部分告警修复版本要求 `>=15.5.24`，需关注是否需随上游继续跟进
- 若团队网络无法直连官方 registry，需为 audit 提供等价方案（**但不可退回"本地看不到漏洞"**）

**假设**：
- 假设项目持续使用对称 HS256 JWT（这是 `pyasn1` / `ecdsa` 豁免成立的前提，已有哨兵守护）
- 假设 `python-jose` 在可见未来仍硬约束 `pyasn1<0.5.0`

**Non-goals（本轮明确不做）**：
- ❌ 不引入 SBOM（软件物料清单）生成与签名——需先有供应链治理流程
- ❌ 不做依赖哈希锁定（`--require-hashes`）——会显著抬高依赖维护成本，当前阶段收益不足
- ❌ 不替换 `python-jose`（改用 `PyJWT`）——为避免一次性引入认证重构风险，
  当前已用白名单+不调 jwe 规避，风险可接受
- ❌ 不升级 Tailwind 3→4 —— `painful` 全量样式回归，与本轮安全主题无关

---

## 📚 数据来源 & 成员产出索引

- **方向明（产品舵手）**：本轮编排、依赖可达性分析、门禁设计与全部验证
- 第九轮成果：`ci.yml` 3 个 job（本轮扩展为 4 个）、`metrics.py` 可观测性（本轮验证未被升级破坏）
- 第十轮成果：`COUNT` 有界化 219 测试基线（本轮沿用为回归基准）

**关键证据文件**：
| 文件 | 内容 |
|------|------|
| `backend/requirements.txt` | 升级后的依赖声明（每项附"修了什么"） |
| `backend/security-allowlist.txt` | 5 条豁免，附可达性分析与复查条件 |
| `backend/verify_p1_supply_chain.py` | 48 项验证（含可达性哨兵） |
| `.github/workflows/ci.yml` | 4 个 job（新增 security-audit） |
| `frontend/.npmrc` | 修复本地 audit 盲区 |
| `frontend/pnpm-workspace.yaml` | override 位置的实测说明（防再次踩坑） |
| `frontend/package.json` | `pnpm.overrides.postcss` |
| `frontend/apps/lawyer/app/cases/[id]/page.tsx` | Next 15 `useParams()` 适配 |

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。

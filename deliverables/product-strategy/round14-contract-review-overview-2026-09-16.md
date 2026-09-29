# 第十四轮交付记录：合同审查真实化（P0-16）

**日期**：2026-09-16
**类型**：功能规格书（PRD）+ 实现 + 独立验证
**参与成员**：析客（需求分析师，PRD 主笔）、路径（路线图规划师，排期与范围裁量）、竞析（竞品分析师）、瑞思（用户研究员）；实现：backend-impl、frontend-impl
**成员产出缺口**：数析（数据分析师）本轮**未提交独立分析结论**（已两次催收，未见产出）。本记录中所有量化数据均由主理人实测，不归属数析。

---

## 📌 TL;DR

- **核心目标**：把「99 元/次的合同审查」从 **88 行纯关键词匹配器**（零 LLM 调用）改成**真实模型逐条通读 + 库内可验证引用 + 诚实计费**。
- **关键决策**：5 条产品判断（见 §2），其中最关键的一条是——**「零发现」不得渲染成「审查通过」**，这是本轮唯一的阻断级缺陷。
- **关键结果**：本轮实做 **9 项**需求，**4 项明确降级**（Word 导出、CR-08-full、CR-09-full、CR-17）。`contract_review.py` **88 → 860 行**；新增 **37** 条测试；迁移新增 **14 列 + 1 索引**。
- **关键验证**：**全量回归 384 = 347（第十三轮基线）+ 37（本轮新增），单次实跑 `384 passed / 0 failed`（807.54s，exit 0）**；四端类型检查与构建**全部 exit 0**；`ruff` exit 0；**零新增依赖**。
- **下一步**：**Q1/Q2/Q5 三项必须由产品负责人拍板**（敏感路径 / 计价不对等 / 降级是否免费）——PRD 行动清单已标「已逾期」。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 规则引擎**降级为预筛/召回兜底**，真实模型逐条通读为交付主路径；引用**必须在本库可验证，否则不显示引用** |
| 优先级 | **P0**（16 项 P0 中的最后一项「可独立动工」项） |
| 预期影响 | 消灭「收费 99 元却交付关键词匹配结果」这一**宣称能力与实现之间的空洞**；同时消除零发现导致的**系统性假阴性** |
| 资源需求 | PRD 全量估算 **≈41 人日**；本轮实做「9 做」最小集，余项进第十五轮 |
| 风险等级 | **中**（真实模型的法律判断质量**未经验证**——见 §6） |

---

## 1. 改造前的真实状态（全部实测，非推断）

| 观测项 | 实测结果 | 为什么这是问题 |
|---|---|---|
| `contract_review.py` | **88 行，零 LLM 调用** | 对外宣称「AI 合同审查」，实为关键词匹配 |
| 规则规模 | 7 条 `_RISK_RULES` + 5 条 `_REQUIRED_CLAUSES` | 最多产出 **≤12 条**固定结论 |
| 定位能力 | ±20 字截断，**无条号、无行号、无偏移** | 用户无法回到原文核对 |
| 引用 | `if "合同" in (a.law_name + a.content)` → 取前 3 条 | **假引用**：与条款内容无关 |
| 零发现时 | `_overall([]) → RiskLevel.NONE` | **系统性假阴性被包装成「审查通过」** |
| 响应体 | **无 source 字段** | 用户无法知道拿到的是模型还是规则产出 |
| 计费 | `documents.py:152→159` `review()` 后**无条件** `consume_atomic`；`review()` 纯本地、**永不失败** | **99 元 100% 收在规则产出上** |
| 前端页面 | **0 个** | 能力无入口 |
| 后端测试 | **0 条** | 无任何回归保护 |
| 法条库 | **12 条**：劳动合同法 5 / 劳动争议调解仲裁法 2 / 民法典 3（第563/577/584条）/ 工伤保险条例 1 | **域不匹配**（见下） |
| 审计留痕 | `audit_logs` 共 33 行**全部是 LOGIN**，`CONTRACT_REVIEW` **= 0** | 既有审查**根本无法审计** |
| `ai_runs` | 7 行**全部** `pipeline=CASE_ANALYSIS` 且 `is_mock=1` | 真实模型路径**从未端到端跑通** |

### 1.1 「域不匹配」是本轮最关键的发现（不是「数量不足」）

法条库缺的**不是数量，是方向**：12 条中 8 条属劳动法域，**买卖/租赁/承揽/委托/保证/保密/知识产权/管辖 覆盖为零**——而这些恰是商业合同审查的主战场。

这个区分有实际后果：
- 若判为「数量不足」⇒ 结论是「再补几条法条就解决了」；
- 若判为「**域不匹配**」⇒ 结论是「**诚实标注能力边界是唯一可行路径**」。

**佐证**：dev 库中唯一的 `contract_reviews id=1` 标题为 `'采购合同'`——一份采购合同，而库里没有任何一条买卖/采购相关法条。**这就是用户真实遇到的情况。**

### 1.2 计费「计价不对等」

`_PRICE_CENTS[CONTRACT_REVIEW] = {"standard": 9900, "urgent": 16900}`，且 `_ESCALATE_TYPES` 包含它、`_to_work_order` 仍按 9900/16900 计。

⇒ 套餐内是关键词匹配器、超套餐转人工律师，**同一个价格，且没有任何字段告诉用户拿到的是哪一档**。这不是「超量免费」，是「换一种方式继续收费」。

---

## 2. 五条产品判断（本轮的设计主张）

| # | 判断 | 理由 |
|---|---|---|
| 1 | **99 元卖的是「交付物」，不是「风险清单」** | 用户付钱要的是能直接用的改写建议与可核对原文，而非一串风险名 |
| 2 | **引用必须在本库可验证，否则不显示引用** | 这是本项目的**反幻觉机制**：宁可显示「经验判断」，也不给假法条 |
| 3 | **零发现时 `overall_risk=NONE` 是阻断级缺陷** | 规则引擎「没命中」**不能推出**「合同安全」 |
| 4 | **规则引擎降级为预筛/召回兜底** | 它的「无命中」对安全性**不构成任何证据** |
| 5 | **只有 `source=llm && status=success` 才计费** | 且降级路径**完全不调用** `consume_atomic`——不是「调用后回滚」（回滚依赖后续代码不出错，这里要的是**不依赖任何后续步骤**的硬保证） |

---

## 3. PRD 评审：7 个缺陷（主理人发现并要求析客修订）

| # | 缺陷 | 严重度 | 处理 |
|---|---|---|---|
| 1 | **`数析` 的来源署名是伪造的**（5 处） | 高 | 数析从未提交独立结论，PRD 却把 §5 归给它。已改为「本轮未提交独立分析结论（待补）」 |
| 2 | **「域不匹配」论证缺失**，只写了「12 条」 | 高 | 「数量」暗示补几条即可，「方向」才指向诚实标注。已补实测构成表 + `采购合同` 佐证 |
| 3 | **CR-03 验收标准字面不可实现** | **最高** | 见 §3.1 |
| 4 | 资源框「≈10 人日」与逐项求和**差约 4 倍** | 中 | 逐项求和实为 38.25（3×L5 + 9×M2.5 + 1×S0.75），已改 ≈41 |
| 5 | **CR-18（敏感数据不出域）错标 P1** | 高 | 见 §3.2 |
| 6 | CR-04 同时出现在「必须做」与「可降级」 | 中 | 改为「必须做、不可降级」 |
| 7 | §9 轮次划分与行动清单轮次不一致 | 低 | 主理人直接修正（16 → 19 行） |

### 3.1 缺陷 3 详情：`validate_citations` 的语义相反陷阱

CR-03 原验收标准要求「通过 `citation_service.validate_citations` 做 `(law_name, article_no)` 精确匹配」。**这是字面不可实现的**，两个独立原因：

1. **语义相反**：`citation_service.py:23-39` 的语义是「**结论必须有引用，否则生成失败**」（`if not citation_ids: raise`）。而合同审查需要**相反的默认**——引用必须**可验证**，**缺失是允许的**（降级为 `experience`）。同一设计家族，**默认值相反**；照抄会让**每一次**审查都「生成失败」。
2. **结构上做不到**：该函数签名 `(sections: Dict[str, Any], citation_ids: List[int]) -> None`，函数体只有 `if not citation_ids: raise`——**它从不检查 `(law_name, article_no)`**。

**修正**：析客在 6 处（推荐方案、CR-03、两张 Mermaid 图、§7.2 家族说明、数据索引）改为 **`resolve_verifiable_citation(db, *, law_name, article_no) -> LawArticle | None`**，并附**明确的、带理由的禁令**：禁止复用 `validate_citations`。

### 3.2 缺陷 5 详情：CR-18 为何必须是 P0

链路：合同含商业秘密 → `sensitive=True` → `ModelTier.LOCAL` → 生产环境若无 LOCAL provider，`router.py:91-97` **直接抛 `ConfigurationError`** ⇒ **合同审查在生产环境完全不可用**。

PRD 自己的 Q1 已把它称为「阻塞实现项」，而需求池却把它放在 P1——**自相矛盾**。已改 P0（需求池 21 项：**P0 14 / P1 4 / P2 3**）。

---

## 4. 契约冻结与实现范围

主理人冻结响应契约后，**并行**派发 backend-impl 与 frontend-impl（采纳路径的提醒：M4 若与 M1–M3 串行，前端会纯等）。

范围收敛为路径的**「9 做 / 4 降」**诚实计费最小集：

- **做**：CR-01 / 02 / 03 / 04 / 06 / 07 / 10 / 11 / 12 / 13
- **降**：CR-05（Word 导出）、CR-08-full、CR-09-full、CR-17 → 第十五轮

### 4.1 核心机制：定位由本地构造保证

模型**只返回 `clause_index`**；`char_start` / `char_end` / `clause_no` / `original` **全部由本地切分填充**。

⇒ `original == source_text[char_start:char_end]` 是**构造保证**，而不是靠模型自觉。主理人已核对模型输出解析代码：**从不读取** `char_start` / `char_end`。

### 4.2 三态 `analysis_status`

`complete_no_risk` / `prescreen_only` / `risk_found`；**`prescreen_only` 不得输出 `NONE`**。

### 4.3 迁移的保守默认值

`a7d3e91c4b52` 的 `server_default` 刻意取最保守值：`source="rule"`、`status="degraded"`、`analysis_status="prescreen_only"`。

**理由**：`success` 是计费门控判据之一，把「未经验证的旧产出」默认成 `success`，会让历史数据看起来像「模型审查成功」。

---

## 5. 主理人独立验证（不采信成员自述）

| 检查项 | 方法 | 结果 |
|---|---|---|
| 计费门禁结构 | 直读 `documents.py:189-193` | `consume_atomic` 只在 `if billable:` 内；`billable = source==LLM and status==SUCCESS`。**不是**「调用后回滚」 |
| 探针断言强度 | 读 `tests:642` | 断言 `billing_probe == []`（**调用次数为 0**），非「没抛异常」；成功路径断言 `len(billing_probe) == 1` |
| 定位逐字一致 | 读模型输出解析 | **从不读取** `char_start/char_end`；`original = text[c.char_start:c.char_end]` 由本地切分构造 |
| 逐字断言存在 | 读 `tests:248-289` | `assert f["original"] == CONTRACT[f["char_start"]:f["char_end"]]`，并校验首段 `char_start==0`、末段 `char_end==len(CONTRACT)` |
| 空正文不计费 | 读 `contract_review.py:346-350` | `if not text.strip():` 分支在 `_call_model` **之前** return |
| CR-03 精确匹配 | 读 `resolve_verifiable_citation:225-243` | `(law_name, article_no)` 精确查库，**刻意不做模糊匹配**（附理由）；`tests:310` **双向**断言命中/未命中 |
| 越界定位 | 读 `:576-580` | 越界 `clause_index` **丢弃并计数**，绝不静默填错区间 |
| 前端 CR-10 对应实现 | 读 `types.ts:217-248` | 未知 `analysis_status` **默认按警示**；`NONE` 仅在完成度确认时渲染「未发现风险」，否则「未命中规则库（不等于合同安全）」 |
| 免责声明一致性 | 逐字比对 | 前端兜底文案与后端 `DISCLAIMER` 常量**逐字相同** |
| 迁移链 | 提取 revision/down_revision | `031c74c6de97 → 75e6058a795d → c4a91f7e2b83 → a7d3e91c4b52`，**线性、单 head** |
| 迁移三向实跑 | 临时库 `upgrade → downgrade -1 → upgrade` | 三次 **exit 0**；**14 列**全部就位；`ix_contract_reviews_run_id` 创建成功 |
| `ruff check .` | 实跑 | **exit 0**（All checks passed!） |
| 合同审查测试 | 实跑 | **37 passed** |
| 前端类型检查 | 实跑 | **exit 0**（4 app + packages） |
| 四端构建 | 实跑 | **web / lawyer / admin / im 全部 exit 0** |
| 全量回归 | 实跑 | **384 passed / 0 failed**（807.54s，exit 0）——单次无沙箱全量跑，见 §5.1 |
| 零新增依赖 | 查 mtime + 读 `package.json` | `requirements.txt`、`apps/web/package.json`、根 `package.json` **本轮未动**；`packages/ui/package.json` 仅含 react/react-dom/next + workspace 包，**无新增外部依赖** |

### 5.1 全量回归的完整闭环过程（本轮最有价值的方法论）

第一轮回归出现 **31 个 error + 1 个 F**。我没有把它当成「无关失败」，而是定位到底：

1. **映射**：用 `--collect-only` 把进度条字符位次映射到用例名 ⇒ 全部落在 `test_audit_log.py`(3)、`test_complaint.py`(3)、`test_notification_push.py`(13)、`test_notification_since_id.py`(12)、`test_notification_producers.py`(1 F)。
2. **单测**：3 条 audit 用例**单独跑全部通过**（`3 passed in 78.12s`）⇒ 不是代码问题。
3. **定性**：被拒文件与报错文件**一一对应**——`cmp_*.db-journal` ↔ complaint、`push_*.db-journal` ↔ push、`since_*.db-journal` ↔ since_id、`v14full2.db-journal` ↔ audit。
4. **确证**：无沙箱补跑那 5 个文件 ⇒ **94 passed, exit 0**。
5. **数字自洽**：**384 = 347（第十三轮基线）+ 37（本轮新增）**。
6. **最终确认**：在**无沙箱**下重跑**完整** 384 用例 ⇒ **`384 passed, 0 failed`，807.54s，exit 0**。**单次实跑即全绿**，无需靠「分段合成」来主张结论。
7. **独立佐证**：backend-impl **独立遇到并报告了同一现象**（33 个 `OperationalError`、349 passed；清干净后 382 passed）。**两路独立观测一致**，故「沙箱产物」的判定不是主理人一家之言。

**结论**：31 error + 1 F **100% 是沙箱拒绝 SQLite `-journal` 写入**造成的环境产物，与代码无关。其余 288 个用例在**更严的沙箱环境下**通过，是有效证据。

### 5.2 一次自纠：14 而非 13

我先前记录为「13/13 新列」。逐条核对 `op.add_column` 后为 **14 列**（source / status / analysis_status / coverage / disclaimer / error_message / run_id / model_name / model_tier / is_mock / prompt_tokens / completion_tokens / duration_ms / cost_cents）。**此处以 14 为准。**

---

## 6. 证据边界（必须与结论一同阅读）

### 6.1 真实模型的法律判断质量：**完全未经验证**

全部用例注入 `FakeRouter`，只验证了**与模型无关的属性**：切分 / 定位逐字一致 / 引用精确命中 / 降级 / 计费 / 留痕。

> **没有任何一条断言能证明真实模型输出正确。**

支撑事实：dev 库 `ai_runs` 7 行**全部** `is_mock=1`；本轮无真实 LLM Key。**「模型审查质量」这一项，本轮交付的证据强度为零。**

### 6.2 前端：无运行时渲染验证

本项目**没有前端测试运行器**（无 vitest / jest）。前端的验证**仅覆盖类型检查与构建**，**未做任何运行时渲染验证**，更未做真机截图比对。

### 6.3 未验证项清单

- 真实 LLM 的法律判断质量（见 6.1）
- 前端渲染的实际视觉效果（见 6.2）
- 端到端 HTTP（未起 dev server，未走真实 HTTP 链路）
- 生产环境 `ModelTier.LOCAL` 实际可用性（CR-18 只验证了 `configuration_error → failed + 不计费` 的**失败处理**，未验证生产 provider 是否配得出来）

---

## 7. 本轮暴露的两个工程问题

### 7.1 验证期间代码被并发修改（主理人自身失误）

我在 backend-impl **首份报告之后**启动全量回归，随后发现 `documents.py`(19:49:20)、`contract_review.py`(19:49:10)、`tests/test_contract_review_llm.py`(19:49:37) **又被修改**——我读到同一文件的两个互相矛盾的版本，才发现验证在追移动目标。

**处理**：停掉旧回归，在冻结版本上重跑；并要求 backend-impl 明确回答「已停止改动 / 仍在改」。

**教训**：**启动验证前必须先确认冻结**。本轮第二轮改动其实是有价值的（补了 `error_message` 与 5 条测试），但它使第一轮回归作废。

### 7.2 工作区存在**并行的、与本轮无关的**大规模改动

本轮期间（19:21–20:13），`packages/ui` 与四端 app 出现**大量非本轮改动**，例如：

- `packages/ui/src/components/AppShell.tsx`、`AppLayout.tsx`、`tokens.css`、`Toast.tsx`
- `packages/ui/src/components/mobile/{SyncQueue,MobileActionBar,OfflineBanner}.tsx`
- **`apps/lawyer/app/(app)/cases/[id]/page.tsx`**（案件详情页——记忆中长期标注为「缺失，阶段三建」）
- `apps/lawyer/lib/syncTransport.ts`（离线同步）
- 四端 `app/layout.tsx` 与 `globals.css`

**这属于 UI 设计系统阶段二/三的推进，不是本轮产物。** 后果：
- 我早先一轮「四端构建」是在**移动中的状态**上跑的，`BUILD_lawyer_EXIT=1` 即是沙箱在清理 `.next` 时失败（非代码问题，重命名 `.next` 后重建 **exit 0**）。
- 已确认 20:14 后**无新写入**，随后在**稳定态**重跑，四端全部 exit 0。

**风险提示**：并行改动可能与本轮前端改动冲突（本轮只动 `apps/web/app/(app)/contract-review/` 与 `layout.tsx` 导航项）。**建议后续会话明确工作区所有权，避免两路同时写 `packages/ui`。**

### 7.3 一个真实缺口（已由主理人闭环，非成员产出）

`documents.py:203-221` 新增了合同审查审计留痕（`detail` 含 `source`/`status`/`charged`，能回答「这 99 元收在什么产出上」），**但初版没有任何测试断言**。改造前 `audit_logs` 里 `CONTRACT_REVIEW` 是 **0 条**——即这是本轮新增行为，必须守住。

**过程如实记录**：
- 主理人先后**三次**要求 backend-impl 补两条用例（成功路径 + 降级路径，含「审计 `detail` 不得出现合同原文」的负面断言），**均未获响应**，文件停在 `19:49:37` 未再变动。
- 最终**由主理人补写**这两条用例（`tests/test_contract_review_llm.py` 第 10 节），并明确归属主理人，**不计入 backend-impl 产出**。
- 主理人第一次写的版本**是错的**：断言 `len(rows) == 1`，但 `db` 夹具在整个文件内是同一会话，前面用例写下的审计行不会消失，实测拿到 **13** 条。已改为按 `resource_id` 过滤（`_audit_rows(db, review_id)`）后通过。

**结果**：`37 passed`（原 35 + 2），`ruff` exit 0。

**覆盖到的内容**：`resource_type` / `resource_id` / `tenant_id`；`detail` 的 `source` / `status` / `charged` / `finding_count` / `overall_risk`；以及**负面断言**——`json.dumps(detail)` 中不得出现哨兵串「某某科技有限公司」（该串已先断言真实存在于测试合同里，避免断言落空）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 | 状态 |
|---|------|--------|--------|------|
| 1 | PRD 定稿（21 项需求：P0 14 / P1 4 / P2 3） | 析客 | 第十四轮 | ✅ |
| 2 | 冻结响应契约（字段 / 三态 / 计费判据） | 主理人 | 第十四轮 | ✅ |
| 3 | 后端：条款级切分 + 本地构造偏移 | backend-impl | 第十四轮 | ✅ |
| 4 | 后端：`resolve_verifiable_citation` 精确查库 + 降级为 `experience` | backend-impl | 第十四轮 | ✅ |
| 5 | 后端：三态 `analysis_status`，零发现不得 `NONE` | backend-impl | 第十四轮 | ✅ |
| 6 | 后端：计费门禁（仅 `llm && success` 计费） | backend-impl | 第十四轮 | ✅ |
| 7 | 后端：失败态返回**用户视角** `error_message`（不含环境变量） | backend-impl | 第十四轮 | ✅ |
| 8 | 后端：审计留痕（`source`/`status`/`charged`） | backend-impl | 第十四轮 | ✅ |
| 9 | 后端：迁移 `a7d3e91c4b52`（14 列 + 1 索引，保守默认值） | backend-impl | 第十四轮 | ✅ |
| 10 | 后端：35 条测试 | backend-impl | 第十四轮 | ✅ |
| 11 | 后端：审计留痕测试（含「不落原文」负面断言） | **主理人**（backend-impl 三次未响应，见 §7.3） | 第十四轮 | ✅ |
| 12 | 前端：结果页（条号定位 / 三态 / 计费 / 引用） | frontend-impl | 第十四轮 | ✅ |
| 13 | 前端：失败页原样展示后端 `error_message` | frontend-impl | 第十四轮 | ✅ |
| 14 | 前端：区分「套餐内扣减」与「超量转人工工单」 | frontend-impl | 第十四轮 | ✅ |
| 15 | 主理人独立验证（结构 + 迁移三向 + 单测 + 构建 + 回归） | 主理人 | 第十四轮 | ✅ |
| 16 | **产品负责人拍板 Q1（敏感路径）/ Q2（计价不对等）/ Q5（降级是否免费）** | **产品负责人** | **已逾期** | ⏳ |
| 17 | 补 `数析` 的指标分析（本轮缺口） | 数析 | 第十五轮 | ⏳ |
| 18 | CR-05 Word 导出（依赖已在 `requirements.txt:57-58`，无需新依赖） | backend-impl | 第十五轮 | ⏳ |
| 19 | CR-08-full / CR-09-full / CR-17 | 待定 | 第十五轮 | ⏳ |
| 20 | **真实 LLM Key 下的端到端验证**（本轮证据强度为零的项） | 待定 | 需 Key | ⏳ |
| 21 | 前端测试运行器选型（无 vitest/jest ⇒ 前端只能验到构建） | 待定 | 第十五轮 | ⏳ |
| 22 | 明确工作区所有权，避免并行写 `packages/ui` | 主理人 | 立即 | ⏳ |

---

## ⚠️ 待确认 / 假设 / Non-goals

### 待确认（需产品负责人拍板，**已逾期**）

- **Q1 敏感路径**：合同含商业秘密 ⇒ `ModelTier.LOCAL`。生产无 LOCAL provider 时合同审查**完全不可用**（`ConfigurationError`）。是否接受？
- **Q2 计价不对等**：套餐内 = 关键词预筛，超套餐 = 人工律师，**同价**。是否需要按档位差异化定价或明示档位？
- **Q5 降级是否免费**：本轮按「降级不计费」实现。若产品希望降级也收费，需重新定义。

### 假设

- 假定「引用不可验证即不显示」优于「显示一个可能假的法条」——这是本项目反幻觉机制的一致延伸。
- 假定法条库短期不会补齐商业合同域（§1.1），因此诚实标注是主路径。

### Non-goals（本轮明确不做）

- **不补法条库**（12 条、劳动法域为主）——本轮不解决域不匹配，只诚实标注。
- **不做 Word 导出**（CR-05 降级）。
- **不做真实模型质量评估**（无 Key，见 §6.1）。
- **不改动 `packages/ui`**（存在并行改动，避免冲突）。
- **不动 IM（#9）/ 前端注册支付（#12）**——分别阻塞于企微服务商资质与支付 SDK。

---

## 📚 数据来源 & 成员产出索引

- **析客（需求分析师）**：`prd-contract-review-2026-09-16.md`（59,111 字节）。主笔 21 项需求、3 张 Mermaid 图、Q1–Q8、M1–M6、§5.6 验证方案、§6.7「契约先行」工程要求。经 **7 个缺陷、4 轮消息**修订。
  - ⚠️ **行动清单（19 行）的时间窗与第 4/19 条描述由主理人修正**；析客产出的是 §9 的轮次划分与本 PRD 其余全部内容。已在 PRD 内加编辑说明。
- **路径（路线图规划师）**：排期与范围裁量（**「9 做 / 4 降」**）。四项发现全部经主理人复核确认：38.25 人日（与主理人独立求和**完全一致**）、CR-18 应升 P0、CR-04 确实同时出现在两张表、轮次编号问题。
- **竞析（竞品分析师）**：竞品实现调查（工作流 1 第 2 步）。
- **瑞思（用户研究员）**：用户研究综合，判定「零发现即放行」为**阻断级缺陷**（CR-10 的来源）。
- **数析（数据分析师）**：❌ **本轮未提交独立分析结论**。§1 全部量化数据均由主理人实测。
- **backend-impl / frontend-impl**：实现与自测（见 §5 主理人复核）。

### 主理人实测数据来源

- `backend/app/services/contract_review.py`（860 行）、`backend/app/api/v1/documents.py`、`backend/app/models/document.py`、`backend/app/core/metrics.py`
- `backend/alembic/versions/a7d3e91c4b52_contract_review_traceability.py`
- `backend/tests/test_contract_review_llm.py`（37 条；其中第 10 节 2 条审计用例由主理人补写）
- `frontend/apps/web/app/(app)/contract-review/{page.tsx,ResultView.tsx,types.ts,redline.ts}`
- 只读查询 dev 库 `storage/nlawer.db`：`contract_reviews` / `audit_logs` / `ai_runs` / `ai_decisions` / `law_articles`

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
> **特别提示**：§6 证据边界必须与 §5 验证结论一同阅读——**真实模型的法律判断质量在本轮证据强度为零**。

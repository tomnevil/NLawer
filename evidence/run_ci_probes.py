#!/usr/bin/env python
"""CI 运行器：把 `evidence/` 里的探针接进门禁。

## 为什么不直接在 ci.yml 里写 5 行 `python evidence/verify_x.py`

1. **退出码 2 必须翻译成「跳过」而不是「失败」**——`evidence/*.py` 的约定是
   `0` 通过 / `1` 产品缺陷 / `2` 环境问题。环境问题不是产品坏了，不该让 CI 红；
   但也不能翻译成「静默通过」，否则门禁空转。
2. **「全部跳过」必须算失败**：一条永远跳过的门禁 = 没有门禁。
   这是本运行器最重要的一条判据（自检臂 S1 覆盖）。
3. **新探针必须被分类**：仓库里出现既不在 `GATED` 也不在 `NOT_GATED` 的
   `verify_*.py` ⇒ **失败**。否则「新建了探针」和「探针进了门禁」会被当成一回事
   ——正是本项目反复踩的那个坑（「写了」≠「有判据」）。
4. **探针自检与实测分开跑**：自检挂了 = **工具坏了**，不是产品坏了。
   混在一起报会让人去产品里找根本不存在的问题。
5. **带 `--self-test` 的脚本必须表态**：`evidence/` 下**任何** `.py` 只要有
   `--self-test`，就必须落进下面五档之一 —— 否则它的自检**从不执行**，
   而「一条会腐烂的判据」与「没有判据」在效果上无法区分。
   ⚠️ 判据 0c 原先是 `glob("verify_*.py")`，**太窄**：`report_spec_coverage.py`
   就带着自检溜在外面（2026-09-23 修）。**按名字守的守卫，名字外的东西全逃** ——
   与判据 0 → 0b 是同一个形状的洞。

## 分档标准（写死在这里，不要靠记忆）

| 档 | 判据 | 在 CI 里 |
|---|---|---|
| `GATED` | 只读源码，或自带临时 SQLite；**不需要浏览器、不需要 dev server、不需要生产构建产物** | 跑；`1` ⇒ 失败 |
| `NOT_GATED` | 需要真浏览器（CDP）/ 运行中的前后端 / `.next` 产物 | **`ci.yml` 里没有任何 job 跑它**；理由逐条写在下面。✅ **2026-09-26 起为 0**（`browser-all` job 落地，14 条全部接进 CI） |
| `CI_ELSEWHERE` | **已在 CI 里，但由别的 job 跑**（如 `frontend` job 的生产构建产物） | 跑；但它**不能**进 `GATED` —— `evidence` job 里没有那些前提 |
| `SELFTESTABLE` / `SELFTEST_ONLY` | 自检是纯函数 / 测不了但工具可自检 | 阶段 1 跑；`1` ⇒ 失败 |
| `SELFTEST_REPO_BOUND` | 自检**读真实仓库**（不是合成夹具） | 阶段 1 **照跑**；`1` ⇒ 失败，但话术是「**登记表落后**」而不是「工具坏了」 |
| `SELFTEST_ENV_BOUND` | 自检**依赖环境**（真浏览器 / 服务 / 已删的产物目录） | 不跑 —— 它给不出结论（**不是「不重要」**） |
| `SELFTEST_ELSEWHERE` | 由**别处**跑（如 `ci.yml` 的 `Gate self-test` 步）/ 只能手工跑 | 不在阶段 1 —— 每条须写明**在哪跑** |

⚠️ 上面「在 CI 里跑」这句话**由两条判据共同保证**，缺一条就会变成假接线：
  · **判据 5**：`SELFTEST_JOB_PROBES` / `CI_JOB_PROBES` 两张「job → 探针清单」表，
    与对应的 `*_ELSEWHERE`（「探针 → 在哪跑」）**必须互相兜住**，
    且声称必须写成机器可读的 `@job:<name>`（或显式 `@manual`）。
  · **判据 6**：`JOB_ENTRYPOINTS` 里那几条命令，**逐字**出现在 `ci.yml` 的对应 job 块里。
    —— 判据 5 证明不了这件事：**移走一步 YAML，两张表照样自洽**（坑 65 的形状）。
  🚨 两张「job → 探针清单」表**刻意不合并**：`SELFTEST_JOB_PROBES` 是「自检由谁跑」、
    `CI_JOB_PROBES` 是「**真判据**由谁跑」。合成一张 ⇒ 「自检跑了」会被读成
    「真判据也跑了」—— 那正是坑 65（**跳过被当成通过**）的根因。

⚠️ 把 CDP 探针塞进 CI 的诱惑很大（"反正 runner 上有 Chrome"），但那需要同时
起 4 个 Next dev server + 后端，是 10 分钟级的 flaky 源。**宁可少守一条，
也不要一条天天红的门禁**——长期红的门禁两天内必被注释掉（第九轮的教训）。

✅ **2026-09-26：这条路走通了，但按上面那句话的形状走通的** ——
不是「塞进每个 PR」，而是**新开一个只在主干 / 手动触发的 `browser-all` job**
（`if: github.event_name != 'pull_request'`，单次 ≈22–25 min）：
  · **PR 的反馈回路没有被拖慢**（本 job 不参与 PR）；
  · 那 14 条**在主干上真的会跑**（不再有「从不执行的门禁」）；
  · ⚠️ 代价是**「有个 job 会跑它」≠「每个 PR 都会跑它」** —— 对 PR 而言它们依然不跑
    ⇒ PR 上的源码级地板（如 `verify_appshell.py` / `verify_im_mobile_nav.py`）**不可省**。

用法：

    python evidence/run_ci_probes.py            # 跑全部 GATED 探针
    python evidence/run_ci_probes.py --list     # 只打印分档清单
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

TIMEOUT_SEC = 300

# `--list` 页脚那份报表的超时。它**只扫源码、不跑探针**（实测 ~1.8s），
# 给 120s 是防呆：**超时只让页脚少印一块，绝不让 `--list` 变红**。
FOOTER_TIMEOUT_SEC = 120

# ---------------------------------------------------------------------------
# ① 门禁内：这些**必须**是绿的（允许出现已登记的已知缺口，靠探针自己的棘轮管）
# ---------------------------------------------------------------------------
GATED: tuple[tuple[str, str], ...] = (
    ("verify_component_wiring.py",
     "组件接线（含棘轮：已知缺口不阻断、新掉线必红、已收口必须删豁免）"),
    ("verify_conversation_channel_500.py",
     "conversations.channel 的枚举残留风险（模型层 / raw SQL 仍可落脏值 ⇒ 整租户 500）"),
    ("verify_enum_domain_drift.py",
     "枚举域漂移：写入侧必须拒绝越界值"),
    ("verify_migration_enum_defaults.py",
     "迁移的枚举默认值与越界归一化"),
    ("verify_token_storage.py",
     "前端令牌不得落 Web Storage（**前端零测试框架**，这是它唯一的门）"),
    ("verify_register_abuse_defense.py",
     "公开自助注册的滥用防线（Q-A 派生；棘轮管 XFF 信任 / 注册采集邮箱两条欠账）"),
    ("verify_route_coverage.py",
     "路由 × 端点层覆盖棘轮（基线 **22**：零覆盖条数**只能降不能涨**；"
     "55→49→39→29→22→16→11，B 类已归零）"),
    ("verify_spacing_scale.py",
     "§4.2 间距刻度：任意值间距必须是令牌引用、规范点名禁止的 5/13/18px 不得出现（只读源码）"),
    ("verify_font_stack.py",
     "§3.1 三套字体栈：族名/顺序/通用族收尾必须合规范；衬线使用点普查（只读源码；"
     "渲染层 F3 实测「衬线≠无衬线」需 `--render`，CI 不跑）"),
    ("verify_endpoint_authz.py",
     "写端点 × **端点层授权证据**（Q-R 那一类形状）：43 条写路由分 "
     "`GATE`（`require_permissions`/`require_roles`）/ `OWNER`（`*_or_404` 或到人引用）/ "
     "`TENANT`（只查 tenant_id，横向没堵，**只报**）/ `NONE`（连租户都没查，**棘轮管**）四档。"
     "两条判据：① `BASELINE_UNGUARDED=2`（`auth/login` + `auth/refresh` 天然豁免，"
     "发生在拿到身份之前）；② 🚨 **门控清单不得减少** —— 只管 NONE 抓不到「把已有门控摘掉」"
     "这种回归（掉进 TENANT/OWNER 就不红了），而 Q-R 恰恰是那个形状，故逐条钉住 10 条基线路由。"
     "🚨 **仪器三处失真全靠自检逮到**：`defmap` 扫整个 `app/` ⇒ 服务层同名函数抢先命中、读到"
     "服务层源码（4 条假红）；只认 `require_permissions` ⇒ `require_roles` 的 3 条假红；"
     "门控挂在**签名默认值**里（`ctx=Depends(require_roles(...))`）⇒ 装饰器+函数体都扫不到。"
     "**两臂故障注入均已转红**：摘 `billing/quota/adjust` 门控 ⇒ 点名该路由；新增无授权写端点 ⇒ 棘轮红"),
    ("verify_request_text_limits.py",
     "请求体**自由文本**字段的长度上限（Q-F 同范式的「旧账登记棘轮」）："
     "缺陷类是写端点的自由文本入参没有 `max_length` ⇒ 提示词成本/存储被单次请求放大，"
     "而全库 `grep max_length tests/` **零命中** ⇒ 此前零判据。"
     "口径**收窄过一版**：首版扫全部 schema 得 170 个 str 字段，绝大多数是**响应模型**与"
     "**枚举/代码型**字段 ⇒ **总数不是信号，分类才是**；现只取端点签名里当入参用的模型、"
     "排除 `*Out`/`*Response` 与代码型字段。判据两条：**A 守卫**（≥8 模型 / ≥10 字段，"
     "防解析器静默退化成「0 欠账」）+ **B 棘轮**（OPEN ⊆ `KNOWN_OPEN_TEXT_FIELDS`，"
     "清单外的**新**字段红并点名；实测 18 条旧账全部登记）。"
     "🚨 自检 Q3/Q5 当场抓到分类漏洞：`type` / `kind` 这类**不带前缀**的代码名匹配不到 `_type` 后缀"
     "⇒ 补了 `CODE_EXACT` 整名集合。🚨 计数**必须去重**：`ComplianceScanCreate` 在两个 schema 文件"
     "各定义一次 ⇒ 不去重报 21、去重后 18。"
     "**故障注入两臂**：新增无上限自由文本 ⇒ 点名转红；同一字段补 `max_length` ⇒ **回到绿**"
     "（后一臂证明它不是「只会红」的判据）"),
    ("verify_service_tenant_param.py",
     "「服务层不收 `tenant_id`」模式横扫棘轮（基线 20，**只防新增**："
     "存量 20 条里有不少在端点层已收口，判红会淹没有效信号；"
     "派单 §3.28 与文书 §3.29 是这个模式查出的两个 P0）"),
    ("verify_provenance_tristate.py",
     "§5 责任边界三态系统**五条使用规则**（三态组件早已全建、产品页 8 处徽章，"
     "但 §5 一条判据都没有 ⇒「已实现 ≠ 有判据」）。判 F1 三重编码 / F2 规范点名三页的图例 / "
     "F3 三态单一真源（结构式识别手抄联合，**不做关键词匹配**）/ F4 徽章可点击留痕；"
     "棘轮三条欠账：#45 文书页缺图例 · #46 手抄三态联合 · #47 徽章不可点击"),
    ("verify_radius_scale.py",
     "§4.3「圆角 / 阴影 / 边框」的圆角部分（焦点环那条归 `verify_focus_ring.py`，**不重叠**）："
     "`--r1..--r4` 阶梯 + 明文禁令「废弃 16px 及以上圆角」。"
     "🚨 **判据按「解析后的 px 值」判，不按类名判** —— preset 把存量档位 `2xl/3xl` "
     "映射到 `var(--r4)`=12px，按类名判会对合法代码产生**稳定假红**。"
     "当前 **0 欠账**（迁移已完成）：0 处 ≥16px、0 处废弃值、preset 11 档全指向令牌"),
    ("verify_component_refactor.py",
     "§6.3「现有组件改造要点」六条 bullet（Sidebar / Header / Card / KpiCard / Button / "
     "LoginShell）里**已实现且无人认领**的那部分：J1 侧栏底色 · J2 激活项 3px 金色指示条 · "
     "J3 顶栏 56px · J4 AppShell 无 `dark:` · J5 Card hover 无位移 · J6 Card 1px 边框 + `--s1` · "
     "J7 KpiCard 28px 等宽 · J8 单位小字 · J9 Button 无渐变 · J10 登录页网格纹理 + 分段切换。"
     "**分工**：Card 圆角归 §4.3 门禁 / Button 变体尺寸归 `verify_buttons.py` / "
     "侧栏 240px 归 `verify_breakpoints.py` / KpiCard `color` 归 `verify_grade_badges.py` —— 不重叠。"
     "🚨 **J4/J5/J9 必须在「剥掉注释」的源码上判**：项目用 `dark:` 这个字面量来声明它的缺席"
     "（`AppShell.tsx:373` 的 JSDoc「本组件不含任何 dark: 变体」）⇒ 扫全文是**稳定假红**。"
     "当前 **0 欠账**；三条功能缺口（迷你趋势线 / Button `loading` 态 / 手机号短信端点）**只报不判**"),
    ("verify_motion_tokens.py",
     "§4.4「动效」的**时长/缓动令牌表**（reduced-motion **行为**归 `verify_reduced_motion.py`，"
     "**不重叠**）：§4.4 给 **5 行**表（#75 增「加载指示（旋转）」）+ 「必须响应 `prefers-reduced-motion`」。"
     "**为什么此前零判据**：`verify_reduced_motion.py` 的 `LIVE = {\"--dur-fast\": \"120ms\", …}` "
     "是**夹具里硬编码的期望值**，**没有任何门禁断言 `tokens.css` 的 `--dur-*`/`--ease-*` == §4.4 表**"
     "（那三个数字恰好是对的，但「恰好对」不是判据）。"
     "判 **A0** 守卫（表行数 / 令牌数 / preset 缓动表）/ **A1** 🎯 **按「场景名 ↔ 令牌注释」精确配对**"
     "逐条判时长（**不按顺序** —— 令牌注释逐字抄了规范场景名，这是可验证的对应关系）/ "
     "**A2** 缓动（令牌化的必须指向 `var(--ease-*)`；`cubic-bezier` 字面量必须有等价令牌，"
     "**归一化后比较**）/ **A3** 机制（`.flow-typing::after` 必须接在 `var(--dur-pulse)` 上）。"
     "**R 组只报**：R1 🚨 **打字光标时长规范 1.2s vs 实现 1.6s**（**合并令牌**，改它会连带改骨架屏 ⇒ 需拍板）· "
     "R2 `--dur-pulse` 的消费者（直接 2 + 间接 4）· R3 规范**没有骨架屏那一行** · "
     "R4 `ease-out` 被重定义为 `cubic-bezier(0,0,0.2,1)`（≠ CSS 关键字标准值，项目有意为之）· "
     "R5 裸 ms/s 时长 · R6 未令牌化的 CSS 原生关键字缓动。"
     "🚨 **判据过宽被自检逮到**：第一版对**所有** `ease-*` 都要求 preset 有同名键 ⇒ 对 `ease-in-out`"
     "（项目里走**原生关键字**）**假红**；且 `token_cb` 误从只含 `--dur-*` 的表建 ⇒ **缓动令牌根本没进表**。"
     "两条都修掉后 23/23。🚨 **`REQUIRED_FILES` 守卫当场咬合**：`SPEC_REL` 是 `.md`、不在被遍历的两棵树里，"
     "而 R3 要读它 ⇒ 必须补进 `EXTRA_RELS`，否则 R3 会**拿空串下结论**。"
     "**产品侧**：**0 欠账**（A1 **4 行**精确配对全部一致、A2/A3 通过）+ 6 条只报 + **R7 存档项**。"
     "**2026-09-26（#75）**：§4.4 增第 5 行「加载指示（旋转）」⇒ 新令牌 `--dur-spin` / `--spin-iter`；"
     "**A4 扩到内置旋转类**（**先收敛 33 处→0，再纳入** —— 顺序反了就是「判据比出处更宽」）；"
     "**R7 由「只报不判」改「存档项」**（判据已并入 A4，保留它只为看得出「33→0 有没有被回退」）；"
     "**A1 配对 3 → 4 行**（新令牌的行注释逐字等于场景名，否则会**静默不被判**）；自检 **25 → 27**。"
     "**注入反证**：`--dur-fast: 120ms→160ms` ⇒ A1 红；`var(--dur-pulse)→1.6s` ⇒ A3 红；"
     "两文件「备份→注入→还原→`cmp` 逐字节一致」，复跑与注入前**字节一致**"),
    ("verify_page_concepts.py",
     "§7「页面概念图」8 个**页面级**「核心改动」。**为什么此前零判据**：`## 07 页面概念图` "
     "**没有编号子标题**，8 个小节全是**表格首列** ⇒ 基于标题的扫描根本不知道它们存在，"
     "§7.1–§7.7 从未进入任何门禁视野（`verify_runtime_health.py` 只查运行时错误，不查页面结构）。"
     "判 **A0** 守卫（§7 表必须解析出 8 行 + 核心改动非空，否则 **exit 2**——不在一张空表上「全部通过」）/ "
     "**A1** 8 个页面归属文件存在（🚨 **文件缺失是产品缺陷 exit 1**，与「规范缺失 ⇒ exit 2」语义不同，"
     "**不混进同一个守卫**）/ **A2** §7.5「六段式」⇒ `SECTIONS` 长度 == **从规范现读**的中文段数 + `n` 从 1 连续 / "
     "**A3** §7.3「引用溯源**常驻**面板」⇒ `CitationPanel` 同时带 `hidden` 与 `lg:` 可见类，"
     "**且** `--citation-panel-w` == 规范写明的「常驻面板 380px」，**且** preset 把 `citation` 映射到该令牌 / "
     "**A4** §7.2 渲染 `KpiCard` / **A5** §7.6 存在 `aria-label` 含「案件/委托」的 `aside`。"
     "🚨 **② 契约阻塞项绝不建判据**：§7.4 环比 / 流失标注 / 质量趋势被 "
     "`CaseOut`/`ReviewOut`/`DispatchOut` **全无时间字段**阻塞"
     "（`admin-gap-analysis.md:499` 已根因定位**「不可做」**，后端已冻结）⇒ 建判据 = 每天一条无法修的假红；"
     "③ §7.3 三栏化 / 会话历史 未实现且无人跟踪 ⇒ 与 ② 一并进 `KNOWN_GAPS` 并带登记号。"
     "🚨 **A2 的排版假设**：`SECTION_N_RE` 首版**锚行首**，真实文件是多行对象所以「恰好」绿 —— "
     "等于把「Prettier 不会内联短对象」偷偷当成判据前提，自检 Q3d 用**紧凑单行**夹具逮到；"
     "改为不锚行首 + `(?<![\\w$.])` 挡 `icon:` 里的子串 `n:`。"
     "**注入反证 12/12**（全部在**内存**注入、不碰产品文件）：控制组绿 · A0 缺行 / 清空改动 ⇒ exit 2 · "
     "A1 文件不存在 ⇒ exit 1 · A2 删一段 · A3a 令牌 360≠380 · A3b 去 `hidden lg:flex` · "
     "A3c 去 preset 映射 · A3d **注释掉常驻类 ⇒ 仍须红** · A4 去 `KpiCard` · A5 去案件语义 · "
     "对照「**同一份注入文本**：不剥注释 ⇒ **假绿**，剥注释 ⇒ 红」（证明 `strip_comments` 接在主流程上）。"
     "**自检反证 5/5**（把自检该抓的弄坏 ⇒ 必须红）。产品侧 **0 欠账** + 2 条只报"
     "（§7.1 规范「2×2」vs 实现 6 个演示账号 · §7.6 `aside` 计数）"),
    ("verify_im_mobile_nav.py",
     "`im-mobile-nav-spec.md` §6.1 / §6.2 / §6.3 的**静态地板**。"
     "🚨 **为什么需要它**：这三节的判据**不是不存在**，而是**全在渲染级探针里** —— "
     "`verify_im_safe_area.py`（§6.2 安全区双算）与 `verify_im_tabs.py`（§6.1 桌面隐藏）"
     "都要 CDP + im dev server，在 `run_ci_probes.py` 里是 **`NOT_GATED`** "
     "⇒ **CI 里一条守 §6.1/§6.2 的判据都没有**；§6.3（高度与滚动）则**哪里都没判**"
     "（`verify_im_safe_area.py` 的 docstring 里出现过 `h-dvh`，但那是**描述**根容器、不是断言 —— "
     "拿「关键词命中」当判据正是坑 14）。"
     "判 **A0** 守卫（规范读不出「N 项 Tab」或 §4 映射表 ⇒ **exit 2**）/ "
     "**A1** §8.4+§4：`TABS` 恰好 N 项、名字与**顺序** == 规范现读、`href` == §4 表现读、"
     "`id` == 一级路径段（§4 的约束；错则高亮**静默失效**）/ "
     "**A2** §6.1：`TabBar.tsx` 必须仍带 `lg:hidden`（去掉后**三端**桌面端都会多出一条底栏）/ "
     "**A3** §6.2：根容器 `style` **不得**含 `paddingBottom`（`TabBar` 内部已加过一次 ⇒ 刘海屏多 34px），"
     "内容区**必须**同时有 `pb-[var(--actionbar-bottom)]` 与 `lg:pb-[var(--safe-bottom)]` / "
     "**A4** §6.3：根容器**必须**有 `h-dvh` 与 `overflow-hidden`，全文件**不得**有 `min-h-dvh` / "
     "**A5** §4：四个落地页必须存在（Tab 指向 404 是本项目 #15 的原缺陷形态）。"
     "🚨 **必须锚到根容器**：`layout.tsx` 的**会话恢复占位**分支里**也有一个 `h-dvh`** ⇒ "
     "全文件搜 `h-dvh` 会在根容器被改坏时**假绿**（Q17 就是这条臂）。"
     "🚨 **`strip_comments()` 必须在判据函数内部**：`layout.tsx` 的注释里**明写了** "
     "`paddingBottom` / `--safe-bottom` / 「不能改成 `min-h-dvh`」—— 不剥就是**假红**；"
     "第一版把它放在 `main()` 里 ⇒ 自检传的是**原始**文本，Q18 当场**假红**、Q19/Q20 只是**碰巧**绿"
     "（于是「剥注释真的接在主流程上」**没有任何臂在守**）。"
     "**注入反证 16/16**（**内存**注入、不碰产品文件；每条都**断言锚点命中数** —— "
     "实测 `TABS` 在文件里出现 **2** 次，锚点不唯一会让整条臂**静默空转**）：控制组绿 · "
     "A3 根容器加回 `paddingBottom` · 内容区丢两个令牌 · A4 `h-dvh→min-h-dvh` / 丢 `overflow-hidden` / "
     "**只留占位分支的 `h-dvh`** · A1 少一项 / 顺序对调 / href 不符 / id 不符 / `TABS` 整体改名 · "
     "A2 丢 `lg:hidden` · 对照「注释里写 `paddingBottom: var(--safe-bottom)`」与「注释里写 `min-h-dvh`」"
     "**均须绿**。**自检 22 臂**（全合成夹具）。"
     "⚠️ 与渲染级探针是**互补不是重复**：静态地板拦「**改错了**」，渲染探针拦「**量出来不对**」"
     "（几何量只有真浏览器有值）。产品侧 **0 欠账**"),
    ("verify_appshell.py",
     "`design-spec.md` §4.1「AppShell（桌面形态）」的**静态地板**。"
     "🚨 **为什么需要它**：§4.1 的唯一覆盖者是 `verify_breakpoints.py` —— 它在 `run_ci_probes.py` 里是 "
     "**`NOT_GATED`**（需要 CDP 测量 `env()`/媒体查询），**而且没有免浏览器开关**"
     "（只有 `--self-test` / `--shot` / `--dump` / `--route`）⇒ **CI 里一条守 §4.1 的判据都没有**。"
     "判 **A0** 守卫（从 §4.1 **现读**侧栏 px / 内容最大宽三档 / 图标条禁令 px / 三端清单；读不出 ⇒ **exit 2**）/ "
     "**A1** `tokens.css` 的 `--sidebar-w` 与 `--content-{workbench,wide,reading}` == 现读值 / "
     "**A2** preset 必须把 `sidebar` 映射到 `var(--sidebar-w)`（`width` 或 `spacing` 段都接受）"
     "**且** `AppShell.tsx` 真用了 `w-sidebar`（否则令牌是死的 ⇒ 改令牌等于没改）/ "
     "**A3a** 常驻侧栏 className 同时带 `hidden` 与 `lg:flex` / "
     "**A3b** `{drawerOpen && ( … )}` 的**配对括号子树**内、且在第一个 `z-drawer` **之前**必须有 `lg:hidden` / "
     "**A4** 图标条禁令：全文件（**剥注释**）不得出现 `w-{px/4}` 与 `w-[{px}px]`。"
     "🚨 **A3b 的第一版是不可证伪的**：写成「`{drawerOpen &&` **之后**任意处有 `lg:hidden`」，"
     "而顶栏那个「打开导航」汉堡按钮（`lg:hidden`）**也在锚点之后** ⇒ 删掉抽屉容器的守卫它照样绿。"
     "**注入臂 I5 实测 rc=0（期望 1）** 才暴露 —— 这是「一条只会绿的判据」的**搜索范围**版本"
     "（谓词没问题，**它在哪段文本里找**决定了它能否被证伪）。"
     "🚨 **锚点唯一性守卫**：`AppShell.tsx` 有**两个** `<aside>`（桌面常驻 `z-sidebar` / 移动抽屉 `z-drawer`），"
     "**都带 `w-sidebar`** ⇒ 只按 `w-sidebar` 定位会命中 **2** 处（第一版实测当场咬合）。"
     "语义必须分清：**0 处 = 产品缺陷（rc 1）**、**>1 处 = 我没法定位（exit 2）** —— "
     "把 0 处判成 exit 2 会把「侧栏被删了」误报成环境问题（假阴性）。"
     "🚨 **配对括号的「过冲」**：删掉**一个** `)` 不会让配对失败，只会让子树**向外扩张**到下一层"
     "（第一版 Q12d 就这么假绿：扫描器一路吃到函数末尾的 `);`，于是子树里又混进汉堡按钮的 `lg:hidden`）"
     "⇒ 加了**过冲哨兵**（子树里出现主区的 `z-topbar` ⇒ exit 2），夹具也改成**去掉全部闭括号**。"
     "**注入反证 8/8**（**内存**注入、不碰产品文件；每条都**断言锚点命中 1 次**）：控制组绿 · "
     "`--sidebar-w` 240→200 · `--content-wide` 1600→1440 · preset 脱开令牌 · 侧栏丢 `lg:flex` · "
     "**抽屉容器丢 `lg:hidden`（汉堡那处顶不上）** · 侧栏加 `w-16` · `lg:hidden` 从容器挪到内层 `aside`。"
     "**自检 35 臂**（全合成夹具，含 Q14 剥注释对照 / Q15a·Q15b「期望值从规范现读」的一对 / "
     "Q12c–Q12g 抽屉子树的五种坏法）。"
     "⚠️ 与渲染级探针是**互补不是重复**：静态地板拦「**改错了**」，`verify_breakpoints.py` B2b 拦"
     "「**量出来不对**」（几何量只有真浏览器有值）。⚠️「顶部 56px」本门禁**不判**"
     "（`verify_component_refactor.py` J3 已在 CI 里判，归属 §6.3）—— 只**现读并打印**，"
     "避免两处要同步的期望值。产品侧 **0 欠账**"),
    ("verify_page_padding.py",
     "`design-spec.md` §4.2「间距（4pt 网格）」里**页边距**那条要求的静态地板。"
     "🚨 **为什么需要它 —— 报表的「小节级」粒度掩盖了「要求级」缺口**："
     "`verify_spacing_scale.py` 让 §4.2 被记为「**CI 内已覆盖**」，但 §4.2 其实有**三条**要求，"
     "只有一条有判据：**刻度 / 禁令**（5·13·18px）✅ 有；"
     "**桌面页面内边距 24px / 移动端 16px** 🚨 **谁都没判**（本门禁补）；"
     "**栅格 12 列 / gutter 24px / 断点 640·1024·1280·1600** 🚨 谁都没判（R4，且「12 列」实测未实现）。"
     "⇒ 这是失真 ③「**提到** vs **判**」的**粗粒度版本**："
     "「**小节**被判过」≠「**小节里每条要求**都被判过」。"
     "判 **A0** 守卫（从 §4.2 **现读**「桌面页面内边距 Npx；移动端 Mpx」；读不出 ⇒ **exit 2**）/ "
     "**A1** `tokens.css` 的 `--page-pad` == 现读的移动端值 / "
     "**A2** `AppShell.tsx` 的**内容区**（`padded &&` 之后那个 className）必须**同时**给出"
     "移动端水平内边距（无断点前缀）与桌面水平内边距（`lg:`），且**解析后的 px** == 现读值 —— "
     "⚠️ 只判「令牌存在」是**空判**（本项目先例：`screens.wide` 是死配置、§4.1 门禁 A2 明写"
     "「令牌存在但没人用 ⇒ 改令牌等于没改」）。"
     "🚨 **刻度不硬编码**：`px-4` 的 16px 是**读 preset 的 `spacing` 段 + Tailwind 默认 `n×4`** 算出来的"
     "（Q16：给 preset 加 `4: \"20px\"` 后同一个 `px-4` **必须**红）。"
     "🚨 **锚点唯一性守卫**（同 §4.1 门禁的教训）：`padded &&` 实测 1 处 ⇒ **0 处 = 产品缺陷（rc 1）**、"
     "**>1 处 / 取不到字面量 = 我没法定位（exit 2）**；而「内容区没有 `px-*` 类」是 **rc 1**（页边距没实现）"
     "—— 三条语义分开，别用一句「取不到」糊过去。"
     "**R 组只报**：R1 `--page-pad` 的**消费者数 = 0** ⇒ 🚨 **死令牌**（改它**不会改渲染**，"
     "页边距实际由内容区的 `px-*` 类决定）⇒ 「让它被用 / 删掉它」是**设计系统改动**，待裁定 · "
     "R2 `tokens.css` **没有桌面档**页边距令牌（规范要 24px，而 `--page-pad` 的注释里只有"
     "「桌面 24px」**这句话**）· R3 ⚠️ **第 8.2 节**的断点表重复了同一要求，但本门禁"
     "**不因此声称覆盖第 8.2 节**（那节还有断点数字矛盾、12 栅格未实现）· R4 §4.2 的「12 列栅格」"
     "实测 `grid-cols-12` **0 处** ⇒ **未实现**（功能缺口，不是覆盖率缺口）。"
     "⚠️ R2/R3 文案**刻意不写 `§` 符号**（避免假覆盖，`README.md` 坑 38）。"
     "**注入反证 6/6**（**内存**注入、不碰产品文件；每条都**断言锚点命中 1 次**）：控制组绿 · "
     "`--page-pad` 16→20 · `px-4`→`px-2` · `lg:px-6`→`lg:px-4` · 丢掉全部水平内边距 · "
     "**改用令牌 `px-[var(--page-pad)]`（同一个值）⇒ 必须绿**（不逼死令牌化）。"
     "**自检 22 臂**（全合成夹具；含 Q14 剥注释对照 —— ⚠️ 光在文件里放个含 `px-2` 的注释**测不出东西**，"
     "必须把**带引号**的坏值放在**锚点与真字面量之间**才有判别力 / Q17a·Q17b「期望值从规范现读」的一对 / "
     "Q18 死令牌**不得**红）。⚠️ 与 `verify_spacing_scale.py` **不重叠**（它判刻度与禁令）——"
     "一节两门禁是本项目既有形态（§4.3 也是圆角 / 焦点环两条）。产品侧 **0 欠账** + 4 条只报"),
    ("verify_shadow_tokens.py",
     "`design-spec.md` §4.3「圆角 / 阴影 / 边框」里**阴影那半**的静态地板。"
     "🚨 **这是「小节级覆盖掩盖要求级缺口」的第二个实例**（第一个是 §4.2 的页边距，见 "
     "`verify_page_padding.py`）：§4.3 **不在**「CI 内零判据」名单里（它被记为「已覆盖」，因为有圆角门禁），"
     "但它的**六条要求里两条零判据** —— **阴影三档 `--s1/--s2/--s3` 的值** 🚨 谁都没判 · "
     "**令牌 → `boxShadow.s*` 的链** 🚨 谁都没判（本门禁补这两条）。"
     "⚠️ **`verify_radius_scale.py` 的 R6 早就看见了这个洞，但它选了「只报」**"
     "（原话：「只报：`--s1..--s3` 与 `boxShadow.s*` 是否成链」）⇒ 本门禁把**链**升格为**判**，"
     "理由同 §4.1 门禁 A2：「令牌存在但没人用 ⇒ **改令牌等于没改**」。"
     "判 **A0** 守卫（从 §4.3 **现读**阴影表，要求**连续** `s1..sN` 且 **N ≥ 3**；"
     "读不出 / 不连续 / 少于 3 档 ⇒ **exit 2**）/ "
     "**A1** `tokens.css` 的每个 `--sN` **归一化后** == 现读值 / "
     "**A2** `preset` 的 `boxShadow.sN` 必须**指向** `var(--sN)`（写成硬编码字面量 ⇒ rc 1）。"
     "🚨 **比较必须归一化，否则稳定假红**：规范写 `` `rgba(16,24,40,.06)` ``、"
     "令牌写 `rgba(16, 24, 40, 0.06)` —— 差别在**逗号后空格** / **`.06` vs `0.06`** / **`.10` vs `0.1`**。"
     "（同族先例：`verify_contrast.py` 把规范那一列当精确值 ⇒ **5 条假红**。）"
     "⇒ 归一化 = 压空白 + 去逗号旁空白 + 小数补前导零 + 去尾随零。"
     "**R 组只报**：R1 「优先用 1px 边框而非阴影」是**取向声明**（规范原话「阴影轻飘，边框稳定」）"
     "**不可判** ⇒ 只报使用点（实测 `shadow-s2` **7** 处 · `shadow-s3` **9** 处）· "
     "R2 `preset` 里三个**存量未令牌化**硬编码阴影 `brand-sm`/`brand-md`/`brand-glow` · "
     "R3 **归属**：链归本门禁判，`verify_radius_scale.py` 的 R6 是**只报**的历史形态（不重复判），"
     "圆角那半仍归它 · R4 §4.3 的**边框**半（Card 1px 边框）归 `verify_component_refactor.py` **J6**。"
     "**注入反证 6/6**（**内存**注入、不碰产品文件；每条都**断言锚点命中 1 次**）：控制组绿 · "
     "`--s1` 值改 · `--s3` 值改 · "
     "🚨 **令牌改写成规范那种紧凑写法（同一个值）⇒ 必须绿**（证明归一化真的生效）· "
     "preset `s2` 脱开令牌 · preset 删 `s3` 键。"
     "**自检 19 臂**（全合成夹具；**Q9/Q9b/Q9c 是一组三臂的归一化对照** —— "
     "紧凑写法与补零写法**都必须绿**，而 `.06`→`.07` **必须红**（证明归一化没把真差异吃掉）/ "
     "Q15 剥注释对照 / Q16a·Q16b「期望值从规范现读」的一对 / "
     "**Q17 规范新增 `--s4` ⇒ 门禁跟着走**（不被写死的 3 卡住））。产品侧 **0 欠账** + 4 条只报"),
    ("verify_spec_refs.py",
     "**引用一致性**：规范引用不许写行号，`§x.y「锚点」` 的锚点必须**真在该节**。"
     "🚨 起因：`design-spec.md:NNN` 式引用**会腐烂** —— 往规范中间插内容 ⇒ 其后行号整体 +N。"
     "实测（2026-09-26）：全仓 100 处，**活文件里 39 处已失效**（那一行现在是空行或表格分隔线），"
     "累积漂移 **+52**（§8.3）乃至 **+72**（§8.5）⇒ 根治成「节号 + 锚点文本」。"
     "判 **G1**（不得出现 `design-spec.md:NNN`）/ **G2**（同行既点名又写裸 `:NNN`）/ "
     "**G3**（锚点在该节号**所属文档的别的节**里 ⇒ 引错了节，消息直接给出实际所在节）。"
     "**G3s / G4 / G5 三档只报**：G3s 锚点在同号文档里找不到（可能是**未登记文档**的引用，"
     "或引文不够精确）· G4 `§x.y` 的号不在任何已登记文档里（**多数是后端文档的编号体系**，"
     "511 处 / 78 个号 ⇒ **按号聚合**打印，出现次数少的才可疑）· G5 同行既无文档名也无别的"
     "文件名的裸 `:NNN`（**天生有歧义**：`` `billing.py` 的 `:72` `` 与表格体里孤零零的 "
     "`` `:195` `` 光看一行分不出来）。"
     "🚨 **判红为什么要求「同号文档」**：`§5.1「✅ 定稿」` 指的是某份**未登记草稿**自己的 §5.1，"
     "而 `5.1` 这个号恰好被 `im-mobile-nav-spec` 占着 ⇒ 不加这个前置会**稳定假红**"
     "（实测 41 条判红里 **16 条**是这一类）。**宁可少判，不可错判。**"
     "🚨 **归一化里两个自己踩过的坑（自检 Q18/Q19 钉住）**：① 剥 HTML 标签必须写成 "
     "`</?[A-Za-z][^<>]*>` —— 写成 `<[^>]+>` 时 §8.2 表里的裸 `< 640px` 会一路吃到下一个 `>`，"
     "**整张表被删光** ⇒ 判据把正文吃掉比它要抓的缺陷更危险；② 锚点只许命中**正文**，"
     "不许命中**别人的引用**（`§8.1「桌面视为放大适配」` 曾在 §8.2 里被判「找到」，"
     "而 §8.2 那句本身就是引用 §8.1 的引用 ⇒ **自证成立**）。"
     "**自检 19 臂**（全合成夹具；Q13/Q19 是判红的两条正向臂，Q14/Q16/Q17/Q18 是四条归一化对照）。"
     "**注入反证 7/7**（`--inject`：**内存**注入、不碰产品文件，但用的是**真实规范** ⇒ 与自检互补 —— "
     "自检证明「判定函数会红」，注入证明「**在这个仓库上**会红」；少了后者，『判据写错而夹具同样写错』"
     "这种**同向错误**永远看不出来。⚠️ 注入臂的锚点由 `INJECT_ANCHORS` **现读校验**，"
     "规范改措辞后前提没了 ⇒ **exit 2**，不许静默变绿。未进 CI —— 手工跑）"
      "产品侧 **0 判红** + 8 条 G3s（6 条模板化锚点 + 2 条未登记文档引用）"),
     ("verify_methodology_numbering.py",
      "**编号不变量**：`.workbuddy-ai/memory/methodology.md` 的编号不许再乱。"
      "🚨 起因：并行会话各写各的 ⇒ 三种乱并存（标题式编号与扁平**重号** · 标题式条目排在扁平 "
      "`188.`–`204.` **之后** ⇒ 读到 204 又跳回 184 · 一条 `### #183` **插在扁平列表中间**）。"
      "整理后定为两套：扁平 `1.`–`204.`（**冻结**）+ 标题式 `## 205.`–（今后一律 `## NNN.` 追加到末尾）。"
      "判 **M1**（扁平 `1..N` 连续、无缺口、无重号、**且按文件顺序递增**）/ "
      "**M2**（标题式严格递增、无重、无缺）/ **M3**（标题式最小号 == 扁平最大号 + 1 ⇒ 接得上且不重叠）/ "
      "**M4**（扁平区不许残留插入式标题 `### #NNN`）/ **M5**（首行**自述**条数与区间 == 实测）/ "
      "**M7**（活文件里 `#旧号` 且同行命中该条目关键词 ⇒ 疑似旧编号未换算）。"
      "**M6 / M7s 只报**：M6 其它标题里的区间过时（首行区间归 M5，不重复算）· "
      "M7s **歧义号段**（205–209 由旧 `#183`–`#187` 换算，而**扁平里各有同号的 183–187**，"
      "一个裸 `#187` 光看号码分不出指哪个 ⇒ 与 `verify_spec_refs.py` 的 **G5 同族**：天生歧义的不做成棘轮）。"
      "🚨 **M7 第一版造出 34 条假红**：关键词抽取把标题末尾的「（原 `#187`）」抽成了关键词 `#187` "
      "⇒ **任何**含 `#187` 的行都「命中自己」⇒ 恒红。与 G3 同族洞：**命中了声明，不是引用**。"
      "⇒ ① 含 `#` 的段不得当关键词；② 同行含「原」⇒ 视为**换算声明**，降为只报。"
      "**自检 13 臂**（Q11–Q13 是 M7 的三条臂）/ **注入反证 6/6**（`--inject`，真实文件上做、不碰磁盘；"
      "**I5 用真实映射表验 M7** —— 合成夹具的措辞可能与真实条目不同；注入臂前提由 `build_old_map` "
      "**现读校验**，措辞变了 ⇒ **exit 2**，不许静默变绿。未进 CI —— 手工跑）。"
      "产品侧 **0 判红** + 12 条 M7s（人工已逐条核过语义：均指扁平 184/186/187）"),
)

# ---------------------------------------------------------------------------
# ② 探针自检：与实测分开。空集也要显式写出来，别让人以为"忘了加"。
# ---------------------------------------------------------------------------
SELFTESTABLE: frozenset[str] = frozenset({
    "verify_component_wiring.py",
    "verify_enum_domain_drift.py",
    "verify_register_abuse_defense.py",
    "verify_token_storage.py",
    # §3.1 字体栈：自检是纯函数（用 SPEC_SNIPPET / TOKENS_SNIPPET / 合成 fixture），
    # **不启浏览器、不读真实仓库文件** ⇒ 满足下面三条收录标准。
    "verify_font_stack.py",
    # §5 三态：自检 18 臂全用**合成 fixture**（`SYN_SPEC` / `SYN_COLORS_OK` / `SYN_BADGE_OK`），
    # 不读真实仓库、不启浏览器 ⇒ 同上。**其中 Q14–Q16 锁棘轮的三条分支**
    # （棘轮内不阻断 / 棘轮外阻断 / 豁免过期也阻断）。
    "verify_provenance_tristate.py",
    # §4.3 圆角：自检 17 臂全用**合成 fixture**（`SYN_SPEC` / `SYN_TOKENS` / `SYN_PRESET`），
    # 不读真实仓库、不启浏览器 ⇒ 同上。**Q4 是关键的假阳性对照**：
    # 存量档位 `2xl/3xl` 映射到 `var(--r4)`=12px ⇒ **不得红**（锁住「按解析值判、不按类名判」）。
    "verify_radius_scale.py",
    # §6.3 组件改造：自检 25 臂全用**合成 fixture**（`SYN_SPEC` / `SYN_SHELL` / `SYN_CARD` /
    # `SYN_KPI` / `SYN_BUTTON` / `SYN_LOGIN`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q18–Q21 是关键的假阳性对照**：注释里写 `dark:` / `hover:-translate-y-0.5` /
    # `bg-gradient-to-r` ⇒ **不得红**；而「注释 + 真实违规同存」仍必须红
    # （锁住「剥注释」没被写成「整行丢弃」）。
    "verify_component_refactor.py",
    # §4.4 动效令牌：自检 23 臂全用**合成 fixture**（`SYN_SPEC` / `SYN_TOKENS` / `SYN_PRESET` /
    # `SYN_STYLES`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q5c 是第一版「判据过宽」的回归臂**：`ease-*` 关键字**缺失 preset 键**时
    # **不得红**（项目里 `ease-in-out` 走 CSS 原生关键字）；Q7 锁「`cubic-bezier` 归一化后相等不得红」。
    "verify_motion_tokens.py",
    # 端点层授权：自检 7 臂全用**合成源码**（`SYN_GATE_DECORATOR` / `SYN_GATE_ROUTER_LEVEL` /
    # `SYN_GATE_ROLES` / `SYN_OWNER` / `SYN_NONE` / `SYN_TENANT_ONLY`），
    # 不读真实仓库、不启服务 ⇒ 同上。**Q3b 是关键的假阳性对照**：
    # `require_roles(...)` 挂在签名默认值里 ⇒ **必须判 GATE**（首扫漏掉它，3 条假红）；
    # **Q5 防假绿**：只查 `tenant_id` 不算 OWNER（Q-S 修掉的形状）⇒ 判 TENANT。
    "verify_endpoint_authz.py",
    # 请求体文本上限：自检 8 臂全用**合成 schema**（`SYN_SCHEMA` 写进临时目录再跑真 `scan()`），
    # 不读真实仓库、不启服务 ⇒ 同上。**Q2/Q3/Q5 是关键假阳性对照**：
    # 有 `max_length` 的 `description`、代码型的 `type` / `kind` ⇒ **均不得算 OPEN**。
    "verify_request_text_limits.py",
    # §7 页面概念图：自检 23 臂全用**合成 fixture**（`SYN_SPEC` / `_syn_sections()` /
    # `SYN_QA` / `SYN_TOKENS` / `SYN_PRESET`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q3d 是「判据不得依赖排版」的回归臂**：紧凑单行的 `SECTIONS` 也必须认
    # （首版锚行首 ⇒ 对合法内联格式**假红**）；**Q2c 是 A1 的注入臂** ——
    # 真仓库 8 个文件都在，不给它注入探针的话 A1 **永远只会绿**。
    "verify_page_concepts.py",
    # im 移动端导航（§6.1/§6.2/§6.3）：自检 22 臂全用**合成 fixture**
    # （`SYN_SPEC` / `SYN_LAYOUT` / `SYN_TABBAR`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q18–Q20 是关键的假阳性对照**：注释里写 `min-h-dvh` / `paddingBottom: var(--safe-bottom)`
    # ⇒ **不得红**（锁住 `strip_comments` 真的接在 `evaluate()` **内部**）。
    # **Q21 是 A5 的注入臂** —— 真仓库四个落地页都在，不给它注入的话 A5 **永远只会绿**。
    "verify_im_mobile_nav.py",
    # §4.1 AppShell：自检 35 臂全用**合成 fixture**（`SYN_SPEC` / `SYN_TOKENS` / `SYN_PRESET` /
    # `SYN_SHELL`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q12c–Q12g 是 A3b「搜索范围」的回归臂**：`lg:hidden` 挪到内层 `aside` ⇒ 红 /
    # 闭括号**全去掉** ⇒ exit 2 / 只删一个 `)`（配对**过冲**，靠 `z-topbar` 哨兵拦下）⇒ exit 2 /
    # 抽屉子树整个没了 ⇒ **rc 1 而不是 exit 2**（防「把产品缺陷盖成环境问题」）/
    # 抽屉被提到条件渲染之外 ⇒ rc 1。
    # **Q15a·Q15b 是「期望值从规范现读」的一对**：同一个 `w-14`，禁令 64px 时**不得**红、
    # 规范改成 56px 后**必须**红 —— 单臂证明不了「值来自规范」。
    "verify_appshell.py",
    # §4.2 页边距：自检 22 臂全用**合成夹具**（`SYN_SPEC` / `SYN_TOKENS` / `SYN_PRESET` /
    # `SYN_SHELL`），不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q14 是关键的假阳性对照**：锚点后紧邻一段**带引号**的坏值注释 ⇒ **不得红**
    # （锁住 `strip_comments` 真的接在 `evaluate()` 内部）。
    # **Q16 是「刻度不硬编码」的回归臂**：给 preset 加 `4: "20px"` ⇒ 同一个 `px-4` **必须**红。
    # **Q13/Q13b/Q13c 是锚点守卫的三条分支**（0 处 / 2 处 / 取不到字面量 ⇒ 均 exit 2），
    # **Q11/Q12 是产品缺陷分支**（没有 `px-*` / 没有 `lg:` ⇒ rc 1）——
    # ⚠️ 两组**必须**分开：混起来会把「页边距没实现」误报成环境问题。
    "verify_page_padding.py",
    # §4.3 阴影：自检 19 臂全用**合成夹具**（`SYN_SPEC` / `SYN_TOKENS` / `SYN_PRESET`），
    # 不读真实仓库、不启浏览器 ⇒ 同上。
    # **Q9/Q9b/Q9c 是一组三臂的归一化对照**（紧凑写法与补零写法都必须绿、真差异必须红）——
    # 这一组是**假红防线**：规范与令牌写的是同一个值，只是格式不同。
    # **Q15 是剥注释对照**；**Q17 证明门禁跟着规范走**（新增 `--s4` 不被写死的 3 卡住）。
    "verify_shadow_tokens.py",
    # 引用一致性（`§x.y「锚点」`）：自检 19 臂全用**合成夹具**（`SPEC_FIXTURE`，一份 4 节的小文档），
    # 不读真实仓库、不启浏览器 ⇒ 同上。
    # 🚨 **Q18/Q19 钉的是判据自己的两个归一化陷阱**（裸 `<` 把正文当标签吃掉 / 锚点命中了
    # **别人的引用** ⇒ 自证成立）—— 它们各自都让门禁**误报**过，且**只在真实仓库上才现形**
    # （合成夹具里不写那一行就永远测不出来）⇒ 必须固化成合成臂，否则同一个洞会再踩一次。
    "verify_spec_refs.py",
    # 编号不变量（`methodology.md`）：同样是**纯静态** —— 读一个 `.md` + 扫文本，不启浏览器。
    # 🚨 M7 是**唯一跨文件**的判据（扫活文件里的歧义号引用）⇒ 传 `live_docs` dict 而不是传 root，
    # 这样自检/注入都能喂合成文档，**不用往磁盘上写文件**。
    "verify_methodology_numbering.py",
})

# ---------------------------------------------------------------------------
# ②b 「只跑自检」的探针：它们**测不了**（要浏览器 / dev server / 生产产物），
#     但「探针自己坏没坏」是可测的 —— 一条会腐烂的判据等于没有判据。
#
# 🚨 收录标准（**三条全部满足**才准进，缺一条都会在 CI 上造出假红）：
#   ① 自检函数**不启动浏览器** —— `cdp.find_chrome()` 只认 Windows 路径，
#      在 `ubuntu-latest` 上会 `RuntimeError` ⇒ 被误判成「工具坏了」。
#      （实测 `breakpoints` / `dark_mode` / `font_scale` / `mobile_375` /
#        `reduced_motion` 的自检都要浏览器 ⇒ **不收**）
#   ② 自检函数**不读真实仓库文件** —— 否则产品一脏，自检就挂，
#      运行器会把**产品缺陷误诊成工具缺陷**（methodology 62）。
#      （实测 `api_base_baked` 的自检扫真实 `FRONTEND` ⇒ **不收**）
#   ③ 不需要网络 / dev server。
# ---------------------------------------------------------------------------
SELFTEST_ONLY: tuple[str, ...] = (
    "verify_buttons.py",
    "verify_contrast.py",
    "verify_focus_ring.py",
    "verify_grade_badges.py",
    # ⚠️ 2026-09-22 补齐：下面 5 个**一直有 `--self-test`，但两个清单都没登记**
    #    ⇒ 它们的自检**从不执行**。这与判据 0b 同形状：**登记表不完整 + 没有守卫**。
    #    代价已经出现过一次：`verify_design_tokens.py` 的 `rglob` 挂死（160s）
    #    **本该被阶段 1 逮到**（「工具坏了」），却因为没登记而一路溜到实测阶段。
    #    ⚠️ 只收**自检快（≤2s）、不启浏览器**的；慢的/要环境的见 `SELFTEST_ENV_BOUND`。
    "verify_adjacent_targets.py",   # 实测 1.0s
    "verify_design_tokens.py",      # 实测 0.8s
    "verify_font_scale.py",         # 实测 1.1s
    "verify_spacing_scale.py",      # 实测 0.7s
    "verify_typography.py",         # 实测 1.6s
)

# 有 `--self-test`，但**阶段 1 不跑它**：自检**依赖环境**（会真起浏览器 / 真连服务），
# 或**成本过高**。⚠️ 这**不是**「不重要」，是「**阶段 1 给不出结论**」——
# 判据 0c 只要求它**被明确表态过**，每条都带 2026-09-22 实测依据。
SELFTEST_ENV_BOUND: dict[str, str] = {
    "verify_api_base_baked.py":
        "自检要读归档的生产产物 `frontend/_prev_build/im-prod-build-*`，"
        "而该目录**已不存在** ⇒ 自检 exit 2。"
        "⚠️ 2026-09-23：**探针本体已接进 CI**（`frontend` job，见 `CI_ELSEWHERE`），"
        "只是**它的自检**仍跑不出结论（要那份已删的归档坏产物）。",
    "verify_contract_review_gate_e2e.py":
        "自检会真起浏览器（C1–C3：登录 + 表单渲染 + 灌 25000 字，实测 ~20s）⇒ CI 里无 Chromium。",
    "verify_admin_413_toast_e2e.py":
        "自检会真起浏览器（T1–T3：登录 admin + 投诉页渲染 + 打开处理弹窗，实测 ~15s）⇒ CI 里无 Chromium。",
    "verify_task_center_e2e.py":
        "自检会真起浏览器（W1/W2/L1/A1：三端登录 + 抽屉/任务页渲染，实测 ~40s）⇒ CI 里无 Chromium。",
    "verify_im_e2e.py":
        "自检会真起浏览器（I1–I2：登录 im + 工作台渲染，实测 ~12s）⇒ CI 里无 Chromium。",
    "verify_runtime_health.py":
        "🚨 自检会**真起 Chromium 跑 4 个端**：服务活着时实测 **151.6s / exit 0**；"
        "服务不在时它自己 L287–292 就 `return 2`（「不启动浏览器，产出的干净是假的」）。"
        "⇒ **CI 里必然 exit 2**；本机则要白等 151s。两种环境都给不出「工具好不好」的结论。",
}

# ---------------------------------------------------------------------------
# ②c **自检会读真实仓库**的脚本：**必须跑**（否则仪器静默腐烂），
#     但它的红**不能**报成「工具坏了」—— 上面 ② 的收录标准明确把「读真实仓库」
#     排除掉，理由是**误归因**（methodology 62：产品一脏，自检挂掉会被读成工具坏）。
#
#     ⚠️ 但「排除掉」**不等于**「可以不跑」：`report_spec_coverage.py` 的自检里
#     Q6/Q13/Q14 正是**「仪器与仓库是否同步」**这条要求本身（新增规范文档必须登记），
#     不跑它 = 这条要求没有判据。⇒ 正确解法是**跑，但把话术改对**：
#     这一档的红先读成「**登记表落后**」（新文档 / 新探针没表态），
#     既不是产品缺陷、也不是工具坏了。**排除一个自检的唯一正当理由是「跑不出结论」**，
#     而不是「跑出来不好归因」。
# ---------------------------------------------------------------------------
SELFTEST_REPO_BOUND: dict[str, str] = {
    "report_spec_coverage.py":
        "自检的 Q6（`design-spec.md` 收全 §7.1–7.8）/ Q13（三份矩阵内文档都被判「纳入」）/"
        "Q14（**扫盘无未分类文档**）读**真实** `deliverables/ui-design/*.md` 与全仓扫盘结果 ⇒ "
        "**这一档的红首先要读成「登记表落后」**（典型：新增了一份带编号章节的规范文档，"
        "但没在报表的 `SPEC_FILES` / `EXCLUDED_DOCS` 里表态）。"
        "⚠️ 实测（2026-09-23）：待裁定 ④ 落地前 Q14 就是红的（3 份未分类文档）——"
        "它当时**真的逮到了**一件该修的事，这正是它必须跑的理由。",
    "run_browser_job.py":
        "**编排器**（Phase 2）：自检校验「`JOB_SPECS` ↔ `run_ci_probes.CI_JOB_PROBES`」"
        "以及**每条探针文件真的存在** ⇒ 会读**真实仓库**（`evidence/*.py` 的存在性）⇒ 归这一档。"
        "**它的红要读成「登记表落后」**（典型：探针改了名，或 `probe_args` 里写了个不存在的名字），"
        "**不是**「编排器坏了」。"
        "🚨 为什么这条自检必须存在：`probe_args` 与探针清单的**不一致只会在真跑的时候暴露**，"
        "而且会**伪装成产品缺陷**（探针 argparse 报错 ⇒ rc=2 ⇒ 被读成「环境问题」或「登录失败」）"
        "⇒ 必须在**零成本**的阶段 1 拦住。"
        "⚠️ 它**不**起浏览器、不连服务、不读 `ci.yml`（后者是判据 6 的活）⇒ 阶段 1 跑得起。",
}

# ---------------------------------------------------------------------------
# ②d 有 `--self-test`，但**不由运行器跑**：由别处跑，或只能手工跑。
#     ⚠️ 与 `SELFTEST_ENV_BOUND` 的区别：那一档是「**想跑但跑不出结论**」，
#     这一档是「**本来就该在别处跑**」—— 每条都要能指出**在哪跑**。
#     判据 0c 只要求「被明确表态过」，但这张表**不许当垃圾桶**：
#     写不出「在哪跑」的，就该进上面三档之一。
# ---------------------------------------------------------------------------
SELFTEST_ELSEWHERE: dict[str, str] = {
    "run_ci_probes.py":
        "@job:evidence —— **运行器自身**：自检由 `ci.yml` 的 `Gate self-test` 步"
        "（`python evidence/run_ci_probes.py --self-test`）执行，"
        "**不在阶段 1 里自调**（自己跑自己 = 递归）。"
        "⚠️ `@job:evidence` 是**唯一**允许「不在任何探针清单里」的 job 名"
        "（它跑的是运行器本体，不是一个探针）—— 判据 5 只要求它在 `JOB_ENTRYPOINTS` 表过态。",
    "visual_baseline.py":
        "@manual —— 像素级视觉基线回归：自检要**真浏览器 + 已有基线**"
        "（且与 `--capture` 互斥）⇒ **只能手工跑**（同 `NON_GATE_SCRIPTS` 里的表态）。"
        "🚨 **2026-09-24 用户拍板 ⑤：明确不接进 CI，且这是永久边界** —— "
        "像素基线对**渲染环境**敏感（字体栅格化 / 抗锯齿 / 子像素 / GPU），"
        "Linux runner 与本机 Windows 的截图**必然**不同 ⇒ 接进 CI = **永久假红**。"
        "对照：同族判据里的**几何量 / `env()` / 媒体查询**跨平台一致 ⇒ 可接；**像素不行**。"
        "⚠️ `@manual` 是「**没有 job 会跑它**」的**显式表态** —— 留空会被读成「忘了写」。",
    # ── 2026-09-24 用户拍板 ③：新增 `browser-selftest` job（runner **预装 Chrome**）──
    #   这 4 条的 `--self-test` 走 `data:text/html` **合成页** ⇒ 不需要 dev server /
    #   后端 / 种子库，**只需要一个浏览器** ⇒ 唯一一批能在「零服务」job 里跑的自检。
    #   ⚠️ 它们此前在 `SELFTEST_ENV_BOUND`，理由写的是「CI 里无 Chromium」——
    #     该理由的**前提已不成立**：GitHub `ubuntu-latest`（Ubuntu 24.04）**预装 Chrome 152**。
    #   ⚠️ 探针**本体**仍留在 `NOT_GATED`（真判据要 dev server，属 Phase 2/3）。
    "verify_breakpoints.py":
        "@job:browser-selftest —— 该 job 里跑 `--self-test`（实测 6.6s）。",
    "verify_dark_mode.py":
        "@job:browser-selftest —— 该 job 里跑 `--self-test`（实测 11.9s）。",
    "verify_mobile_375.py":
        "@job:browser-selftest —— 该 job 里跑 `--self-test`（实测 5.4s）。",
    "verify_reduced_motion.py":
        "@job:browser-selftest —— 该 job 里跑 `--self-test`（实测 14.3s）。",
}

# ---------------------------------------------------------------------------
# ③c **「由别的 job 跑」的自检：把「job → 探针清单」做成机器可读的**。
#
# 🚨 为什么要这张表（README **坑 64**）：`ci.yml` 里如果**再手写一遍**探针名，
#   那就是**第二份清单** ⇒ 一定会漂移（新增一条 CDP 自检 ⇒ YAML 忘了加 ⇒ 它从不执行，
#   而名册上却写着「由 `browser-selftest` job 跑」—— **假接线**）。
#   ⇒ `ci.yml` 只写 `--selftest-for-job browser-selftest`，**清单只有这一份**。
#
# ⚠️ 与 `SELFTEST_ELSEWHERE` 的分工：那一档回答「**在哪跑**」（给人读的散文），
#   这一档回答「**跑哪些**」（给机器读的清单）。**判据 5 强制两者互相兜住**。
# ---------------------------------------------------------------------------
SELFTEST_JOB_PROBES: dict[str, tuple[str, ...]] = {
    # runner **预装 Chrome** ⇒ 零安装成本；这 4 条只走 `data:text/html` 合成页（不碰服务）。
    "browser-selftest": (
        "verify_breakpoints.py",
        "verify_dark_mode.py",
        "verify_mobile_375.py",
        "verify_reduced_motion.py",
    ),
}

# ---------------------------------------------------------------------------
# ③c″ **「由别的 job 跑」的真判据**：与 `SELFTEST_JOB_PROBES` 同构，但跑的是**探针本体**。
#
# 🚨 为什么不与 ③c 合成一张表（**这是坑 65 的根因，别合**）：
#   `SELFTEST_JOB_PROBES` 回答「哪些探针的 **`--self-test`** 由哪个 job 跑」；
#   这一档回答「哪些探针的 **真判据** 由哪个 job 跑」。两者**不是同一批**：
#     · `verify_dark_mode.py` **两边都出现**（自检在 `browser-selftest`、
#       真判据在 `browser-admin`）—— 合成一张表后，「自检跑了」会被读成
#       「真判据也跑了」，而那正是坑 65 的形状（**跳过被当成通过**）。
#     · `verify_admin_413_toast_e2e.py` **只**在后者（它没有能在合成页上跑的自检）。
#   ⇒ 两张表 + **同一条判据 5** 强制各自与 ELSEWHERE 表互相兜住。
#
# ⚠️ 与 `CI_ELSEWHERE` 的分工：那一档回答「**在哪跑**」（给人读的散文，带 `@job:` 标记），
#   这一档回答「**跑哪些**」（给机器读的清单）。
# ---------------------------------------------------------------------------
CI_JOB_PROBES: dict[str, tuple[str, ...]] = {
    "frontend": (
        "verify_api_base_baked.py",
        "verify_typography.py",
    ),
    # Phase 2（2026-09-24）：单端（admin）真判据。**清单是唯一事实来源** ——
    # `evidence/run_browser_job.py` 从**这里 import**，`ci.yml` 只写 job 名。
    #
    # 🚨 **条数的来历：3 → 2 → 3**（每次都是**据实测**改的，不是拍脑袋）：
    #   · `verify_reduced_motion.py` ✅ rc=0 —— 真页面、真后端、真种子库（实测 ~55s）
    #   · `verify_admin_413_toast_e2e.py` ✅ rc=0（实测 24.4s）——
    #     🚨 它**第一次跑是红的**，根因是**探针自己的顺序错**：先 `goto('/complaints')`
    #     再 `seed_complaint()` ⇒ 列表在导航那一刻就渲染完了，页面不轮询 ⇒ 新工单不在 DOM 里
    #     ⇒ 找不到「处理」钮。**在本机旧库上永远看不出来**（库里早有别的 PENDING 工单撑着）
    #     —— 这正是「本机绿、CI 红」的经典成因，也是 Phase 2 用**隔离库**的价值。
    #   · `verify_dark_mode.py` —— 2026-09-24 首次真跑 **rc=1**：D0 在 admin 的
    #     `/`、`/audit`、`/complaints` 三页 `@dark` 上**全部命中**（observer 序列
    #     `['dark','light','dark']`）= **产品缺陷 #40**（`ThemeProvider` 挂载时摘掉 `.dark`）。
    #     ⇒ 当时**据实测收窄到 2 条**（本仓库纪律：门禁第一天必须绿；CDP 探针**没有棘轮**）。
    #     ✅ **2026-09-25：`packages/ui/src/theme/ThemeProvider.tsx` 已修**（首帧不回写 class，
    #        只有用户显式切换才回写）⇒ 重新接回，**这就是第 3 条**。
    "browser-admin": (
        "verify_reduced_motion.py",
        "verify_dark_mode.py",
        "verify_admin_413_toast_e2e.py",
    ),
    # Phase 3（2026-09-26 · 拍板 §11.4-③）：**四端全量**真判据。**14 条全部 rc=0**。
    #
    # 🚨 **条数为什么是 14 而不是 §11.6 写的「绿的 12 条」**：
    #   §11.6 的摸底（2026-09-25）留下 2 条 rc=1，但**两条都不是「改一行就好」**——
    #   各自卡在一条**待拍板的设计裁定**上（§11.4-① 与待办 #39）。用户 2026-09-26
    #   「全部按建议」拍板后，两条都已**从产品侧修完**：
    #     · **#39** im ≥1024 无导航出口 ⇒ `verify_breakpoints.py`（57.6s）**rc=0**
    #     · **#72** Pagination 4px 间距 vs 48px 热区 ⇒ `verify_adjacent_targets.py`（116.3s）**rc=0**
    #   ⇒ 两条**同日复跑确认转绿**（存证 `evidence/four_app_rm_2026-09-26.txt`）。
    #   ⇒ 接线口径由「12 条」更新为 **14 条**（与 §11.7 把 `task_center_e2e` 从 rc=2 修成 rc=0 同一逻辑：
    #      **先据实测收窄，再据实测接回**）。
    #
    # ⚠️ **顺序 = §11.6 摸底报告的顺序**（大致按耗时从短到长），便于与存证逐条对照。
    # ⚠️ 与 `browser-admin` **零重叠**：那 3 条（`reduced_motion` / `dark_mode` /
    #   `admin_413_toast_e2e`）**不在**本清单里 —— 它们是 `--app admin` 的单端判据（PR 级），
    #   本 job 是四端全量（主干级）。两个 job 各自独立、不重复跑同一条。
    "browser-all": (
        "verify_ws_msg_type_bypass.py",
        "verify_im_safe_area.py",
        "verify_im_e2e.py",
        "verify_contract_review_gate_e2e.py",
        "verify_mobile_actionbar.py",
        "verify_im_tabs.py",
        "verify_mobile_safe_area.py",
        "verify_tap_targets.py",
        "verify_render_e2e.py",
        "verify_runtime_health.py",
        "verify_mobile_375.py",
        "verify_task_center_e2e.py",
        "verify_adjacent_targets.py",
        "verify_breakpoints.py",
    ),
}

# ---------------------------------------------------------------------------
# ③d **「怎么把它跑起来」**：每个 job 在 `ci.yml` 里**必须出现**的命令片段。
#
# 🚨 为什么需要它（这是判据 6 的靶子）：判据 5 只证明**两张表自洽** ——
#   它证明不了「`ci.yml` 里真有那个 job、真有那一步」。
#   于是**移走一步 YAML** 就能让名册说谎：名册写着「由 `browser-admin` 跑」，
#   而 `ci.yml` 里那个 job 早被删了 ⇒ **没有任何判据会红**（与坑 65 同族）。
#   ⇒ 把「job → 命令」也做成机器可读的，由**判据 6** 去 `ci.yml` 里逐个核。
#
# ⚠️ 片段必须**逐字**取自 `ci.yml`（含 `python` / `python3` 的差异 —— 本仓库
#   `frontend` job 用 `python3`、其余用 `python`，这不是笔误，是现状）。
# ⚠️ 键集合必须**覆盖** `CI_JOB_PROBES ∪ SELFTEST_JOB_PROBES`（判据 6 强制），
#   且允许**多出**「跑探针但不列清单」的 job（如 `evidence` 自己）。
# ---------------------------------------------------------------------------
JOB_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "evidence": ("python evidence/run_ci_probes.py --self-test",),
    "frontend": (
        "python3 ../evidence/verify_api_base_baked.py",
        "python3 ../evidence/verify_typography.py --source-only",
    ),
    "browser-selftest": (
        "python evidence/run_ci_probes.py --selftest-for-job browser-selftest",
    ),
    "browser-admin": (
        "python evidence/run_browser_job.py --job browser-admin",
    ),
    "browser-all": (
        "python evidence/run_browser_job.py --job browser-all",
    ),
}

# `ci.yml` 的路径。**做成模块级常量**是为了让自检臂能把它指向一份**合成 YAML**
# （判据 6 读的就是这个常量，臂改它即可注入故障，不必碰真文件）。
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

# 「由运行器**自己所在的那个 job** 跑」的 job 名。
# 用在 `@job:evidence` 上：它跑的是运行器本体（`--self-test`），
# **不属于**任何探针清单 ⇒ 判据 5 对它只要求「在 `JOB_ENTRYPOINTS` 里表过态」。
SELF_RUN_JOB = "evidence"


def _ci_job_blocks(text: str) -> dict[str, str]:
    """把 `ci.yml` 的 `jobs:` 段切成 `{job 名: 该 job 的原文}`。

    ⚠️ 只扫 `jobs:` 段 —— `on:` 段下的 `pull_request:` / `push:` 也是「2 空格 + 冒号」，
       不切段会把它们当成 job。**判据 6 的靶子是 job，不是任意 2 空格键。**
    ⚠️ 这是**文本**切分，不是 YAML 解析：仓库里没有 PyYAML 依赖，为一条判据引一个
       解析器不划算；而这里要的只是「某个片段是否出现在某个 job 的块里」，
       文本包含判断足够，且**误报方向是「漏报」而非「假红」**（切错只会让块变小）。
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "jobs:":
            start = i + 1
            break
    if start is None:
        return {}
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i] and not lines[i][0].isspace():
            end = i
            break

    blocks: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in lines[start:end]:
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            if cur is not None:
                blocks[cur] = "\n".join(buf)
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf)
    return blocks


# ---------------------------------------------------------------------------
# 判据 7 的靶子：`SystemExit(<字符串>)` / `sys.exit(<字符串>)`
#
# 🚨 Python 语义：字符串参数会被打到 stderr，然后**以状态码 1 退出**。
#    本仓库的 `evidence/*.py` 约定 `0` 通过 / `1` 产品缺陷 / `2` **环境问题** ⇒
#    `raise SystemExit("❌ 环境：… → exit 2")` 会**静默**变成「产品缺陷」。
#    它**看起来完全正确**（消息里就写着 exit 2），所以只能靠静态判据守。
#
# 🚨 **用 AST，不用正则**（第一版用正则，被自己的自检臂当场否掉）：
#    正则版在**注释里写了 `SystemExit("msg")` 做说明**时**把自己扫红了**
#    —— 于是 H0/S3 两条「期望 exit 0」的阴性对照臂同时转红。
#    这正是坑 46 第三形态（**按文本守的守卫会被文本本身骗**）：
#    「说明这个坏写法」的注释与「真的用了这个坏写法」在**文本上无法区分**，
#    在 **AST 上可以**。⇒ 只认**真的调用节点**，注释/字符串里的一律不算。
#
# ⚠️ 只判**字符串字面量 / f-string** 两种参数：
#    · `SystemExit(2)` / `sys.exit(main())`（`main()` 返回 int）**都是合法**的 ⇒ 不得红；
#    · 其余形态（变量、表达式）**刻意不判** —— 判不准就不要判（宁可漏，不可假红）。
# ---------------------------------------------------------------------------
def judge_exit_codes(sources: dict[str, str]) -> list[str]:
    """扫 `{文件名: 源码}`，返回「用了字符串退出码」的位置。**纯函数**（自检直接喂合成源码）。"""
    problems: list[str] = []
    for name, src in sorted(sources.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            # 语法错的文件归 `compileall` / ruff 管，这里不重复报（免得噪声盖住真信号）。
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            is_sys_exit = (
                isinstance(func, ast.Attribute) and func.attr == "exit"
                and isinstance(func.value, ast.Name) and func.value.id == "sys"
            )
            is_system_exit = isinstance(func, ast.Name) and func.id == "SystemExit"
            if not (is_sys_exit or is_system_exit):
                continue
            arg = node.args[0]
            is_str = (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) \
                or isinstance(arg, ast.JoinedStr)
            if is_str:
                text = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
                problems.append(f"{name}:{node.lineno} —— {text}")
    return problems


def _evidence_sources() -> dict[str, str]:
    """`evidence/*.py` 的 `{文件名: 源码}`。

    **做成函数**（而不是在判据里直接 `glob`）是为了让自检臂能替换它 ——
    否则判据 7 的**接线**（它有没有真的被 `run_all()` 调用）就没法证明，
    而「一条接不上的判据」与「没有判据」在效果上无法区分（坑 65）。
    """
    out: dict[str, str] = {}
    for p in sorted(HERE.glob("*.py")):
        try:
            out[p.name] = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 文件系统异常
            continue
    return out

# ---------------------------------------------------------------------------
# ③b **已在 CI 里、但不在 `evidence` job** 的探针：由别的 job 跑。
#     每条**必须写明「在哪个 job / 哪一步跑」** —— 写不出的不许进这一档。
#
#     🚨 为什么要单开一档：`NOT_GATED` 的语义是「**没接进 CI**」。
#     2026-09-23 把生产构建接进 `frontend` job 后，`verify_api_base_baked.py`
#     就**不再属于** `NOT_GATED` 了 —— 留在那里会让「仍有 N 个从不执行」的计数**虚高**，
#     也会让下一个人以为它还没接。
#     ⚠️ 但它**不能**进 `GATED`：`GATED` 是 **`evidence` job** 跑的那一档，
#     而那个 job 里**没有前端产物**（不装 pnpm、不 build）⇒ 进去只会永远 `exit 2`（跳过）。
#     ⇒ **「在 CI 里跑」与「在 evidence job 里跑」是两件事**，分档必须按后者。
# ---------------------------------------------------------------------------
CI_ELSEWHERE: dict[str, str] = {
    "verify_api_base_baked.py":
        "@job:frontend —— `ci.yml` 的 **`frontend` job**：`Seed build-time env` → "
        "`Build (production)` → 紧跟一步 `python3 ../evidence/verify_api_base_baked.py`。"
        "⚠️ 它读的是 `apps/<app>/.env.local`（由该 job **播种**）与 `.next/static/chunks`。"
        "⚠️ 本机**不能**跑 `pnpm build` 来验证这条接线（四端 dev server 正在跑，"
        "重建会毁掉它们的 `.next`）⇒ **首次 CI 运行即为它的端到端验证**。",
    "verify_typography.py":
        "@job:frontend —— `ci.yml` 的 **`frontend` job**：`Install dependencies`"
        "（pnpm ⇒ 有 `node_modules`）"
        "→ `Build (production)`（⇒ 有 `apps/*/.next`）→ 紧跟一步 "
        "`python3 ../evidence/verify_typography.py --source-only`（**断言 exit 0**）。"
        "🚨 **为什么必须挪到这里（2026-09-24 用户拍板 ①，实测坐实）**：T2/T2b/T2c "
        "**同时**依赖两个前提 —— ① `frontend/node_modules/tailwind-merge`（真 merge）；"
        "② 四端 `.next` 产物 CSS（`leadings`，T2 的行高比全靠它，`judge_line_height` 逐对跳过）。"
        "**实测四组合（判对数 / exit）**：本机 6/0 · **只补 node_modules 0/2** · "
        "**只补 `.next` 8/1（假阳）** · CI 现状 0/2。"
        "⇒ `evidence` job 里**两个都缺**：T2 逐对跳过（`judged=0`）⇒ `total==0` ⇒ "
        "**也是 exit 2** ⇒ 阶段 3 记 SKIP ⇒ **棘轮从未被评估**（README 坑 65）。"
        "⇒ **只补 `node_modules` 不够**（判对数仍 0）—— 必须是**既有 node_modules 又有 `.next`** 的 job。"
        "⚠️ 棘轮语义**未削弱**：冻结值本就是 `rc_max=0 / count_max=0`，"
        "而 `exit 0` ⇔ 0 缺陷 ⇒ 「断言 exit 0」与棘轮**等价且更强**（exit 2 也会红）。"
        "⚠️ 本机**不能**跑 `pnpm build` 验证这条接线（四端 dev server 正在跑，重建会毁掉它们的 `.next`）"
        "⇒ **首次 CI 运行即为它的端到端验证**（同 `verify_api_base_baked.py`）。"
        "历史明细 `evidence/cn_fix_execution_2026-09-24.txt` + `_t2c_*.log`。",
    # ── Phase 2（2026-09-24）：`browser-admin` job 的真判据 ──────────────────
    #   前提由 `evidence/run_browser_job.py` **备齐**（隔离 SQLite + 种子 + 后端 8001
    #   + admin 生产构建产物 @3002 + 轮询就绪），`ci.yml` 只写一行调用。
    #   ⚠️ 与 `SELFTEST_ELSEWHERE` 里同名的条目**不冲突**：那里跑的是 `--self-test`
    #      （合成页、零服务），这里跑的是**真判据**（真页面 + 真后端 + 真种子库）。
    #      这正是坑 65 的教训：**「自检跑了」≠「判据跑了」**，两件事必须分开登记。
    "verify_reduced_motion.py":
        "@job:browser-admin —— `ci.yml` 的 **`browser-admin` job**（仅 main / 手动触发）："
        "`pnpm --filter app-admin build` → `python evidence/run_browser_job.py --job browser-admin`"
        "（编排器起后端 8001 + admin 3002 后跑 `--app admin`，覆盖 `/`、`/audit`）。"
        "✅ **实测 rc=0（2026-09-24，54.4s）**。"
        "🚨 它**必须**有真页面才判得动：R1a/R1b/R1c/R1d 是「页面上不得有还在跑的东西」，"
        "合成页上恒为空 ⇒ **空真**。⚠️ 同族的 `--self-test` 在 `browser-selftest` job。"
        "**2026-09-26（#75）**：**R1c（内置旋转类在 reduce 下仍跑）由「只报不判」升为判红**"
        "（#75 已拍板「归零静止」）；同时 R2 覆盖与 `PROJECT_ANIMS` 补上 `--dur-spin` / "
        "`--spin-iter` / `spin-soft`（**新令牌不登记 ⇒ 运行时无人断言，且不报错**）；"
        "`pending`（只报不判）通道整体移除；自检 10 → 13 浏览器臂。",
    "verify_admin_413_toast_e2e.py":
        "@job:browser-admin —— `ci.yml` 的 **`browser-admin` job**（仅 main / 手动触发）："
        "真点一次「确认受理」触发后端 413，断言 `ContentTooLargeGate` 的全局 toast 出现。"
        "✅ **实测 rc=0（2026-09-24，24.4s）**。"
        "🚨 它**没有能在合成页上跑的自检** ⇒ 它此前**完全不在任何 job 里**"
        "（`--self-test` 只跑到 T1–T3，仍要服务）—— Phase 2 是它**第一次**进 CI。"
        "⚠️ 它依赖**已灌种子**的库（投诉工单 + `admin` 账号）⇒ 编排器必须先 seed 再起服务。"
        "🚨 **Phase 2 首次真跑时它是红的，根因在探针自己**：先 `goto('/complaints')` 再"
        "`seed_complaint()` ⇒ 新工单不在 DOM 里 ⇒ 找不到「处理」钮。"
        "**本机旧库上永远看不出来**（早有别的 PENDING 工单撑着）⇒ 隔离库才暴露。已修。",
    "verify_dark_mode.py":
        "@job:browser-admin —— `ci.yml` 的 **`browser-admin` job**（仅 main / 手动触发）："
        "`--app admin` 覆盖 `/`、`/audit`、`/complaints` 三页，D0 断言深色 class **不得被摘除**。"
        "✅ **实测 rc=0（2026-09-25）** —— 修复见 `packages/ui/src/theme/ThemeProvider.tsx`（缺陷 **#40**）。"
        "🚨 **它的接线史就是本档存在的意义**：2026-09-24 首次真跑 **rc=1**（observer 序列 "
        "`['dark','light','dark']`）⇒ 当时**据实测收窄掉**、留在 `NOT_GATED` 写明阻塞项；"
        "产品修好后（09-25）才搬到这里。⇒ **「暂时不接」必须写成有阻塞项的条目**，"
        "否则它就会变成「忘了接」。"
        "⚠️ 同族的 `--self-test` 在 `browser-selftest` job（合成页、零服务）——"
        "**「自检跑了」≠「判据跑了」**（坑 65），两件事分开登记。",
    # ── Phase 3（2026-09-26 · 拍板 §11.4-③）：`browser-all` job 的 **14 条真判据** ──
    #   前提与 `browser-admin` 同一套，由 `evidence/run_browser_job.py` **备齐**，
    #   区别只是**四端全起**：隔离 SQLite + `seed_demo.py --reset` + 后端 8001
    #   + **四端生产构建产物**（web 3000 / lawyer 3001 / admin 3002 / im 3003）+ 逐端轮询就绪。
    #   `ci.yml` 只写一行调用；探针清单的唯一事实来源是 `CI_JOB_PROBES["browser-all"]`。
    #   ✅ **14 条全部实测 rc=0**：12 条见 §11.6 摸底（2026-09-25 · 四端生产构建 + 隔离库 · 823s）；
    #      余 2 条（`verify_adjacent_targets` / `verify_breakpoints`）在 #72 / #39 **产品修复后**
    #      于 2026-09-26 复跑确认转绿（存证 `evidence/four_app_rm_2026-09-26.txt`）。
    #   ⚠️ 成本：四端构建（本机 5m41s，runner 更慢）+ 探针 ≈14 min ⇒ **单次 22–25 min**
    #      ⇒ 只在 **main / 手动**触发（与 `browser-admin` 分工：那个守 PR，这个守主干）。
    #   🚨 **这 14 条是 `NOT_GATED` 全部搬空的结果** —— 搬走它们之后那一档**清空**，
    #      因为「`NOT_GATED` 的语义 = `ci.yml` 里没有任何 job 跑它」已不再成立。
    "verify_ws_msg_type_bypass.py":
        "@job:browser-all —— `ci.yml` 的 **`browser-all` job**（仅 main / 手动触发）："
        "WebSocket 帧 `type` 白名单绕过取证，需**运行中的后端**（真握手）+ im 端。"
        "✅ rc=0（11.1s）。🚨 它**读错库会把「产品有洞」误报成「环境问题」**"
        "（§11.7 的根因①），已改走 `envprobe.resolve_live_db()`。",
    "verify_im_safe_area.py":
        "@job:browser-all —— **`browser-all` job**：im 安全区（`--safe-bottom` 等）实测，"
        "CDP + im 端。✅ rc=0（17.9s）。",
    "verify_im_e2e.py":
        "@job:browser-all —— **`browser-all` job**：im 登录 + 工作台渲染，"
        "CDP + 后端 + im 端。✅ rc=0（20.7s）。",
    "verify_contract_review_gate_e2e.py":
        "@job:browser-all —— **`browser-all` job**：合同审查**决策门**（真点一次「开始审查」），"
        "CDP + 后端 + web 端 + 已灌种子的库。✅ rc=0（31.1s）。"
        "🚨 它**不是**预防性门禁 —— 它**真的逮到过**一个缺陷（413 被当成审查失败 ⇒ 决策门永不出现）。",
    "verify_mobile_actionbar.py":
        "@job:browser-all —— **`browser-all` job**：律师端常驻底部操作条（规范 08 节硬要求），"
        "CDP + lawyer 端。✅ rc=0（36.2s）。",
    "verify_im_tabs.py":
        "@job:browser-all —— **`browser-all` job**：im `TabBar` 形态 + 阶段 B 断言，"
        "CDP + im 端。✅ rc=0（41.1s）。",
    "verify_mobile_safe_area.py":
        "@job:browser-all —— **`browser-all` job**：四端安全区（`--app` 默认四端全跑 ⇒ 本 job 不传），"
        "CDP + 四端。✅ rc=0（63.4s）。",
    "verify_tap_targets.py":
        "@job:browser-all —— **`browser-all` job**：48px 触控热区（`--app` 默认四端全跑 ⇒ 不传），"
        "CDP + 四端。✅ rc=0（64.8s）。",
    "verify_render_e2e.py":
        "@job:browser-all —— **`browser-all` job**：21 项真实渲染断言，"
        "CDP + 后端 + 四端。✅ rc=0（76.2s）。",
    "verify_runtime_health.py":
        "@job:browser-all —— **`browser-all` job**：26 页 × 6 账号运行时健康"
        "（`--app` 默认四端全跑 ⇒ 不传），CDP + 后端 + 四端。✅ rc=0（120.7s）。"
        "⚠️ 它与 `verify_mobile_375.py` 是本 job **最贵的两条**，接线时单独权衡过。",
    "verify_mobile_375.py":
        "@job:browser-all —— **`browser-all` job**：375 基准门禁（26 页 × 3 档），"
        "CDP + 四端。✅ rc=0（141.4s）—— 本 job 单条最贵。",
    "verify_task_center_e2e.py":
        "@job:browser-all —— **`browser-all` job**：三端任务中心抽屉/任务页，"
        "CDP + 后端 + web/lawyer/admin。✅ rc=0（61.9s）。"
        "🚨 **首次真跑是 rc=2**，根因**全在探针自己**（① 猜库路径 ⇒ 隔离库静默失效；"
        "② `seed_demo.py --reset` 根本不建 `jobs`）⇒ §11.7 已修（两层根因），"
        "是「本机绿、CI 红」的又一例。",
    "verify_adjacent_targets.py":
        "@job:browser-all —— **`browser-all` job**：§8.3「相邻可点元素间距 ≥ 8px」渲染级，"
        "CDP（`elementFromPoint` 测真实几何）+ 四端。✅ rc=0（116.3s）。"
        "🚨 **首次真跑 rc=1**（`Pagination` 的 4px 间距，4 对）⇒ 那**不是「改一行就好」**，"
        "而是**待拍板的设计冲突**（§11.4-①：32px 盒 + 8px 间距与 48px 热区不相容）"
        "⇒ 经 **#72** 拍板「盒 32→40px + 间距 4→8px」修产品后转绿。",
    "verify_breakpoints.py":
        "@job:browser-all —— **`browser-all` job**：§8.2 四档断点 + §8.4 导航模式，"
        "CDP + 四端（含 im `/chat` `/cases` `/me` 的 expanded 档）。✅ rc=0（57.6s）。"
        "🚨 **首次真跑 rc=1**（B2a：im 在 1024/1440 **没有导航出口**）⇒ 正是待办 **#39**"
        "—— 此前只写在任务里，**那次让它有了硬性的 CI 后果**；#39 修完后转绿。",
}

# ---------------------------------------------------------------------------
# ③ 门禁外：逐条写清理由 + 「要接进来还差什么」。
#    空口说"暂时不接"没有价值，得说清楚缺的是哪个前提。
# ---------------------------------------------------------------------------
# 🚨 **`NOT_GATED` 不等于「不需要跑」，只等于「没接进 CI」。**
#
# 2026-09-22 实测：**21 个探针在 CI 里从不执行** —— `.github/workflows/ci.yml` 的
# `evidence` job 只跑 `GATED`，`frontend` job 只跑 `typecheck`，没有任何 job 碰它们。
# ⇒ 这是「**有判据 ≠ 会被跑**」：一条从不执行的门禁与没有门禁**在效果上无法区分**，
#   欠账只会在无人察觉的情况下增长。
#
# ✅ **2026-09-22 已收口 7 个**（用户拍板 **① 棘轮**）：其中 7 个探针**有免浏览器模式**，
#    已移入 `GATED_STATIC`，以「**冻结条数、只拦增长**」的方式**接进了 CI**（见下）。
#    ⇒ 本档从 21 降到 **14**，这 14 个是**真的需要浏览器 / dev server / 生产产物**的。
#
# ⚠️ **盘点方式本身踩过一次坑**：第一遍是 `grep --source-only` 数出「6 个」，
#    漏了 `verify_contrast.py` —— 它的**同能力开关叫 `--tokens-only`**。
#    ⇒ **按「能力」（是否需要浏览器）盘，不要按「开关名」盘**；否则统计的是
#      命名习惯而不是能力。（同 `README.md` 坑 13/31：工具返回 0 先怀疑工具。）
# ✅ **2026-09-26：本档已清空（14 → 0）** —— 拍板 §11.4-③ 落地，14 条**全部接进 CI**。
#
# 这一档的语义是「**`ci.yml` 里没有任何 job 跑它**」。Phase 3 新增 `browser-all` job
# （四端生产构建 + 隔离库 + 真后端 ⇒ 这些探针要的**环境前提**齐了）后，这个语义
# **对它们不再成立** ⇒ 按本档一贯的纪律（2026-09-23 `verify_api_base_baked.py`、
# 2026-09-24 `verify_admin_413_toast_e2e.py`、2026-09-25 `verify_dark_mode.py` 三次先例）
# **必须挪走** —— 否则计数虚高、下一个人以为它还没接。
#
# ⚠️ **留空不是「没有欠账」**，而是「这一档的欠账已清零」。空字典是**结论**，不是占位符。
#    若将来又出现「需要浏览器但 CI 里没有 job 跑它」的探针，**照样登记到这里**。
#
# 📌 **本档两次搬迁留下的两条纪律（比条目本身值钱，故留档）**：
#   1. **「暂时不接」必须写成带阻塞项的条目，不能删掉/留空** ——
#      `verify_dark_mode.py` 2026-09-24 首次真跑 rc=1（产品缺陷 **#40**）时**按纪律不接线**
#      （CDP 探针没有棘轮，见 §11.4-②），但**写成了条目并注明阻塞项**；
#      产品修好后立刻搬进 `CI_ELSEWHERE`。删掉它 = 变成「忘了接」。
#   2. **「在 CI 里跑」与「在 `evidence` job 里跑」是两件事** ——
#      这 14 条都**不能**进 `GATED`：`evidence` job 里**没有前端产物**（不装 pnpm、不 build）
#      ⇒ 进去只会永远 `exit 2`（跳过）。分档必须按**「在哪个 job 里跑」**，不是「跑不跑」。
#
# ⚠️ 另一条**方法论**（2026-09-22 盘点时踩过）：**按「能力」（是否需要浏览器）盘，
#    不要按「开关名」盘** —— 第一遍用 `grep --source-only` 数出「6 个」，
#    漏了 `verify_contrast.py`（它的同能力开关叫 `--tokens-only`）⇒ 统计的是命名习惯。
#    （同 `README.md` 坑 13/31：工具返回 0 先怀疑工具。）
NOT_GATED: dict[str, str] = {}

# ── 判据 0b：**每个** `.py` 都必须被分类（不只是 `verify_*`）────────────────
#
# 🚨 **为什么需要**：判据 0 只 `glob("verify_*.py")` —— 那是**按名字**守，
#    于是「一个**真门禁**、但起名不叫 `verify_*`」会**完全逃过守卫**。
#    这正是 `README.md` 坑 46 的同一个形状：**按名字盘 ≠ 按能力盘**。
#
#    实测（2026-09-22）：`evidence/` 有 **59** 个 `.py`，其中 **20** 个不是 `verify_*`
#    —— 它们**从来没人分类过**，也不在任何清单里。其中 `visual_baseline.py`
#    还是一个**真能力**（像素级视觉基线回归，`README.md` 有专节），
#    却**不在任何清单里** ⇒ 谁也不知道它该不该跑。
#
#    ⇒ 逐个显式登记（库 / 工具 / 一次性诊断），并**让未登记的 `.py` 直接失败**：
#      新脚本要么进 `GATED`/`NOT_GATED`（它是门禁），要么进这里（它不是）。
#      ⚠️ 这条守卫**不是**说这 20 个都该跑 —— 是要求**每一个都被明确表态过**。
NON_GATE_SCRIPTS: dict[str, str] = {
    "run_ci_probes.py": "运行器本身",
    "cdp.py": "**库**：CDP 极简客户端（不依赖 playwright，直连本机 Chromium）—— 被 `mobile_preview` / `im_login_test` / `visual_baseline` 复用",
    "envprobe.py": "**库**：环境探测（HTTP + 种子规模指纹）—— 被 `verify_render_e2e` / `visual_baseline` 复用（`NO_PROXY` 那个坑要两处一致，故抽成单点）",
    "report_spec_coverage.py": "**报表**：规范覆盖率矩阵。**故意不叫 `verify_*`** —— 它不是门禁，不该被卷进判据 0",
    "visual_baseline.py": "**工具**：像素级视觉基线回归（真浏览器 + 必须同一数据集 **且同一服务模式** `dev`/`prod` ⇒ **只能手工跑**，进不了 CI）",
    "mobile_preview.py": "**工具**：手机视口截图（真浏览器）",
    "apply_enum_normalization.py": "**一次性**：给没有 alembic 版本表的开发库应用 `b1f7c2a94e30` 的归一逻辑",
    "authz_probe.py": "**一次性**：证据接口越权读取诊断（只读）",
    "im_login_test.py": "**一次性**：IM 登录实际打到的 URL 取证",
    "probe_admin_role_gate.py": "**一次性**：四端登录角色门禁取证",
    "probe_app_login.py": "**一次性**：登录页演示账号实测取证",
    "probe_audit_effect.py": "**一次性**：`/audit` 页「审计记录总量」波动取证",
    "probe_cdp.py": "**一次性**：CDP 可行性取证",
    "probe_cn_render_delta.py": "**一次性**：#42 修法 C 的**渲染级**取证（`cn()` 吞字号到底改多少 px）—— **只测量、恒 exit 0**；不碰产品代码、不依赖数据库。结论见 `cn_render_delta_2026-09-24.txt`",
    "probe_dom.py": "**一次性**：可靠选择器取证（只读）",
    "probe_logged_in.py": "**一次性**：可靠选择器取证（只读）",
    "probe_login.py": "**一次性**：登录流程取证",
    "probe_mobile_nav.py": "**一次性**：手机宽度下 admin 菜单形态取证",
    "probe_mono_coverage.py": "**一次性**：§1 原则 4「全站等宽」静态可判性实测（**只测量、恒 exit 0**）—— 结论见 `mono_static_feasibility_ruling_2026-09-24.txt`：**静态不可判**，应并入「浏览器 job」",
    "probe_pulltorefresh_in_sheet.py": "**一次性**：`PullToRefresh` 能否装进 `BottomSheet` 的决策取证",
    "probe_sheet_scroll_contract.py": "**一次性**：`BottomSheet` 的 `scrollOwner=content` 槽位该用 `grid` 还是 `flex` 的决策取证（**§9 第 10 项定稿依据**；五臂，只测量 ⇒ 退出码仅 0/2）",
    "probe_ptr_scroll_target.py": "**判据**：`PullToRefresh` 的 `scrollTarget`（外部滚动元素）契约 —— 三臂（① 默认模式不变 ② 文档滚动下「守卫瞎」的复现 ③ `scrollTarget=window` 修好后）+ 七条**源码级地板**（⚠️ 只挡回退、**不能证真**）。**与 `probe_sheet_scroll_contract.py` 同族**：测合成页 CSS 形态，不启 dev server ⇒ 退出码仅 0/2。真机渲染验证仍走 `browser-all` job",
    "probe_switcher.py": "**一次性**：可靠选择器取证（只读）",
    "probe_tapghost_desktop.py": "**一次性**：拍板前的桌面端取证",
    "run_browser_job.py": "**编排器**（Phase 2）：起后端 + 灌种子 + 起单端前端 → 跑 `CI_JOB_PROBES` 的真判据 → 收尾。"
                          "**故意不叫 `verify_*`** —— 它不判任何东西，它是**把判据跑起来的手**；"
                          "判据本体在 `verify_dark_mode` / `verify_reduced_motion` / `verify_admin_413_toast_e2e`。"
                          "⚠️ 它**不在** `GATED`（要 pnpm 构建产物 + 真浏览器 + 端口），由 `ci.yml` 的 "
                          "`browser-admin` job 直接调（见 `CI_ELSEWHERE` 的 `@job:browser-admin`）。",
}


def _run_probe(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float, str, str]:
    """核心：跑一个探针，返回 `(rc, 秒, stdout, stderr)`。**不打印、不改判**。"""
    t0 = time.monotonic()
    try:
        cp = subprocess.run(  # noqa: S603
            [sys.executable, str(HERE / name), *extra],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
        return cp.returncode, time.monotonic() - t0, cp.stdout or "", cp.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, time.monotonic() - t0, "", ""


def _report_failure(rc: int, out: str, err: str, *, verbose: bool = True) -> int:
    """把沙箱守卫的假红改判成 `2`；`verbose` 时打印**两个通道**的尾巴。

    ⚠️ `verbose=False` 是给**棘轮**用的：阶段 3 里这 7 个探针的 rc **本来就是 1**
    （已登记欠账），若照打「失败尾部」，一个**通过的**阶段会满屏「失败」⇒ 误导。
    棘轮只在**真的判红**时才由调用方补打尾巴。
    """
    if rc == 0:
        return rc
    if rc == 124:
        if verbose:
            print(f"      ⏱ 超过 {TIMEOUT_SEC}s —— **超时当失败**，不当跳过（挂住的探针是问题）")
        return rc

    if verbose:
        # 失败时把尾巴打出来，省得再去翻日志。
        # 🚨 **stderr 也必须打**：探针「启动期崩溃」（ImportError / 库被别的进程锁住 /
        #    权限）的 traceback **全在 stderr 上**，只打 stdout 会得到**一片空白**的失败
        #    记录 —— 2026-09-22 实测：两个探针 1s 失败、日志里一个字都没有，只能靠
        #    「单独重跑又绿了」反推（见 README 坑 48）。
        printed = False
        for label, stream in (("stdout", out), ("stderr", err)):
            lines = stream.strip().splitlines()
            if not lines:
                continue
            printed = True
            tail = "\n".join(lines[-6:])
            print(f"      ── {label} 尾部 ──\n{_indent(tail)}\n      ────────────")
        if not printed:
            print("      ⚠️ 探针**没有任何输出**就退出了 —— 先怀疑启动期崩溃/被信号杀掉，"
                  "不要当成「断言失败」")

    # 🚨 **沙箱的批量删除守卫**会从 `os.unlink()` **内部**抛 `SystemExit(1)`
    #    （`sitecustomize.py::_exit_bulk_guard_control`），于是探针的退出码看起来
    #    就是「1 = 产品缺陷」—— 这是**假红**，必须改判成 2（环境问题）。
    #    实测（2026-09-22）：`verify_conversation_channel_500.py` 启动期
    #    `unlink()` 临时库、`verify_migration_enum_defaults.py` `unlink()` 旧库，
    #    两个都 1s 退出、rc=1、stdout 全空 —— 单独重跑又全绿。
    #    根因：沙箱把 `PYTHONPATH` 指向 shim 目录 ⇒ `site.py` 自动 import
    #    `sitecustomize` ⇒ 全局接管 `os.unlink/rmdir`、`shutil.rmtree`、
    #    `pathlib.Path.unlink`；本回合累计删除超过阈值（默认 50）后，删除一律
    #    `SystemExit(1)`。**CI 里没有这个守卫**（`ci.yml` 已显式置空相关变量）。
    if "[safe-delete]" in err:
        print("      ⚠️ 探针被**沙箱批量删除守卫**打断 ⇒ 改判 **环境问题（exit 2）**，"
              "不是产品缺陷。")
        print("         重跑本套件请加前缀：`CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR=` "
              "（置空即可跳过守卫；删除**仍走回收站**）")
        return 2
    return rc


def run_one(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float]:
    """跑一个探针，返回 `(rc, 秒)`。⚠️ **这个签名被 `run_self_test` 的桩依赖，别改。**"""
    rc, dt, out, err = _run_probe(name, extra)
    return _report_failure(rc, out, err), dt


def run_ratchet_one(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float, str]:
    """跑一个探针并**返回 stdout**（棘轮要数条数）。

    `verbose=False`：这 7 个的 rc **本来就是 1**（已登记欠账），照打「失败尾部」会
    让一个**通过的**阶段满屏「失败」⇒ 误导。判红时由调用方补打（见阶段 3）。
    """
    rc, dt, out, err = _run_probe(name, extra)
    return _report_failure(rc, out, err, verbose=False), dt, out


def _indent(s: str) -> str:
    return "\n".join("      " + ln for ln in s.splitlines())


def run_all() -> int:
    """跑完整门禁。抽成函数是为了让**运行器自己**也能被自检（见 `run_self_test`）。"""
    # ---- 判据 0：**每个** verify_*.py 都必须被分类 ----
    # 三档：`GATED`（直接跑）/ `GATED_STATIC`（用免浏览器开关跑 + 棘轮）/ `NOT_GATED`（真跑不了）
    all_probes = {p.name for p in HERE.glob("verify_*.py")}
    classified = ({n for n, _ in GATED} | set(GATED_STATIC) | set(NOT_GATED)
                  | set(CI_ELSEWHERE))
    unclassified = sorted(all_probes - classified)
    stale = sorted(classified - all_probes)
    if unclassified or stale:
        print("❌ 探针清单与仓库不一致 —— 新增/改名/删除的探针必须同步到这里：")
        for n in unclassified:
            print(f"  · 仓库里有但没分类：{n}")
        for n in stale:
            print(f"  · 清单里有但仓库没有：{n}")
        return 1

    # ---- 判据 0b：**每个** `.py` 都必须被分类（不只 `verify_*`）----
    # 判据 0 是**按名字**守的，所以「一个真门禁但不叫 verify_*」会静默逃过。
    # 这条把范围扩到 evidence/ 下的**全部** .py：要么是门禁（GATED/NOT_GATED），
    # 要么在这里被明确表态为「不是门禁」。**新增脚本必须二选一**。
    all_py = {p.name for p in HERE.glob("*.py")}
    known_py = all_probes | set(NON_GATE_SCRIPTS)
    unknown_py = sorted(all_py - known_py)
    stale_non_gate = sorted(set(NON_GATE_SCRIPTS) - all_py)
    if unknown_py or stale_non_gate:
        print("❌ evidence/ 里有**未分类**的 .py —— 判据 0 只按 `verify_*.py` 守，")
        print("   所以任何**不叫 `verify_` 的真门禁**都会静默逃过（见 README 坑 46）。")
        print("   新增脚本必须表态：是门禁 ⇒ 进 `GATED`/`GATED_STATIC`/`NOT_GATED`/`CI_ELSEWHERE`；"
              "不是 ⇒ 进 `NON_GATE_SCRIPTS`：")
        for n in unknown_py:
            print(f"  · 仓库里有但没分类：{n}")
        for n in stale_non_gate:
            print(f"  · NON_GATE_SCRIPTS 里有但仓库没有：{n}")
        return 1

    # ---- 判据 0c：**有 `--self-test` 的脚本必须登记**（否则它的自检从不执行）----
    # 与判据 0b **同形状**：登记表不完整 + 没有守卫 ⇒ 静默逃过。
    # 实测（2026-09-22）：**28 个探针有 `--self-test`，只有 17 个登记** ⇒ **11 个从不执行**。
    # 代价已经出现过：`verify_design_tokens.py` 的 `rglob` 挂死本该被阶段 1 逮到。
    #
    # 🚨 2026-09-23 修：扫描范围原先是 `glob("verify_*.py")` —— **太窄**。
    #    `report_spec_coverage.py` 有 `--self-test` 却**不在扫描范围内** ⇒
    #    它既不会被判「没登记」，也不会被执行 ⇒ 同一形状的洞**又开了一次**
    #    （与判据 0 → 0b 的修法一模一样：**按名字守的守卫，名字外的东西全逃**）。
    #    ⇒ 改成 `glob("*.py")`：evidence/ 下**任何**脚本只要有 `--self-test`，
    #    就必须在**四档之一**表态（或进 `SELFTEST_ELSEWHERE` 并指出在哪跑）。
    has_st: set[str] = set()
    for p in sorted(HERE.glob("*.py")):
        try:
            if '"--self-test"' in p.read_text(encoding="utf-8", errors="ignore"):
                has_st.add(p.name)
        except OSError:  # pragma: no cover - 文件系统异常
            continue
    st_registered = (set(SELFTESTABLE) | set(SELFTEST_ONLY)
                     | set(SELFTEST_ENV_BOUND) | set(SELFTEST_REPO_BOUND)
                     | set(SELFTEST_ELSEWHERE))
    st_missing = sorted(has_st - st_registered)
    st_stale = sorted(st_registered - has_st)
    if st_missing or st_stale:
        print("❌ `--self-test` 的登记与仓库不一致 —— 有自检的脚本必须**四选一**：")
        print("   `SELFTESTABLE`（自检是纯函数，阶段 1 跑）/")
        print("   `SELFTEST_ONLY`（测不了但工具可自检，阶段 1 跑）/")
        print("   `SELFTEST_ENV_BOUND`（自检依赖环境，阶段 1 跑不出结论）/")
        print("   `SELFTEST_REPO_BOUND`（自检读真实仓库，**跑**但红要读成分诊欠账）——")
        print("   或进 `SELFTEST_ELSEWHERE`（由别处跑 / 只能手工跑，须写明在哪跑）。")
        print("   否则它的自检**从不执行**：")
        for n in st_missing:
            print(f"  · 有 `--self-test` 但没登记：{n}")
        for n in st_stale:
            print(f"  · 登记了但源码里没有 `--self-test`：{n}")
        return 1

    # ---- 判据 5：两张「job 清单」各自与它的 ELSEWHERE 表**互相兜住**（README 坑 64）----
    # 见 `SELFTEST_JOB_PROBES` / `CI_JOB_PROBES` 上方的长注释。
    # 「写明在哪跑」**不等于**「真的有人在跑」—— 两个方向都要守。
    #
    # 🚨 **声称口径必须是机器可读的标记 `@job:<name>`，不能是「理由里出现 job 名」**：
    #    第一版用 `any(j in why for j in SELFTEST_JOB_PROBES)`，被自检臂 **G2 当场否掉**
    #    —— 把整个 job 的登记删掉后，`SELFTEST_JOB_PROBES` 变空 ⇒ `any(...)` 恒 False
    #    ⇒ **声称跟着一起隐形**，`_unbacked` 为空 ⇒ 判据 5 反而放行。
    #    ⇒ 标记是**独立于被检查对象**的，删掉 job 登记它照样在 ⇒ G2 能红（README 坑 66）。
    #
    # ⚠️ **2026-09-24 泛化**：原先只查「自检」那一族。Phase 2 起 `CI_JOB_PROBES`
    #    也要同一条守卫 —— 否则「真判据由某 job 跑」会**重开同一个洞**：
    #    名册写着有人跑、`ci.yml` 里其实没有。两族共用下面这段（**不写第二份逻辑**，
    #    理由同坑 64：第二份手写清单一定会漂移）。
    def _judge_job_claims(table: dict[str, tuple[str, ...]], elsewhere: dict[str, str],
                          table_name: str, elsewhere_name: str) -> list[str]:
        """两族共用的判据 5 内核。返回问题列表（空 = 通过）。"""
        probs: list[str] = []
        claims: dict[str, str] = {}
        for n, why in elsewhere.items():
            m = re.search(r"@job:([A-Za-z0-9_.-]+)", why)
            if m:
                claims[n] = m.group(1)
            elif "@manual" not in why:
                probs.append(
                    f"[{elsewhere_name}] `{n}` **没有任何机器可读的声称** —— "
                    "既没有 `@job:<name>` 也没有 `@manual` ⇒ 这一行**无法被核**（坑 66 的形状）")
        listed = {n for v in table.values() for n in v}
        for n in sorted(listed - set(elsewhere)):
            probs.append(f"列在 `{table_name}` 的某个 job 里，但**没登记**进 `{elsewhere_name}`：{n}")
        for n, j in sorted(claims.items()):
            if j == SELF_RUN_JOB:
                # 运行器本体（不是探针）：只要求 `JOB_ENTRYPOINTS` 里表过态。
                if j not in JOB_ENTRYPOINTS:
                    probs.append(f"[{elsewhere_name}] `{n}` 声称由 `{j}` 跑，"
                                 f"但 `JOB_ENTRYPOINTS` 里没有该 job")
            elif j not in table:
                probs.append(f"[{elsewhere_name}] `{n}` 声称由 job `{j}` 跑，"
                             f"但该 job **不在** `{table_name}` 里")
            elif n not in table[j]:
                probs.append(f"[{elsewhere_name}] `{n}` 声称由 `{j}` 跑，"
                             f"但**不在该 job 的清单里**")
        return probs

    _claims_problems = (
        _judge_job_claims(SELFTEST_JOB_PROBES, SELFTEST_ELSEWHERE,
                          "SELFTEST_JOB_PROBES", "SELFTEST_ELSEWHERE")
        + _judge_job_claims(CI_JOB_PROBES, CI_ELSEWHERE,
                            "CI_JOB_PROBES", "CI_ELSEWHERE")
    )
    if _claims_problems:
        print("❌ 「job → 探针清单」与「探针 → 在哪跑」两张表不一致：")
        for p in _claims_problems:
            print(f"  · {p}")
        print("   ⇒ 两边必须同时改：「**写明在哪跑**」不等于「**真的有人在跑**」。")
        print("   ⚠️ 声称必须写成机器可读的 `@job:<name>`（或显式 `@manual`）——"
              "「理由里恰好出现了 job 名」**不算**声称（README 坑 66）。")
        return 1

    # ---- 判据 6：`ci.yml` 里**真的**有那些 job、那些命令（判据 5 证明不了这件事）----
    # 🚨 判据 5 只证明**两张表自洽**。把 `ci.yml` 里的一步移走 ⇒ 两张表照样自洽、
    #    名册照样写着「由 `browser-admin` 跑」⇒ **没有任何判据会红**（与坑 65 同族）。
    #    ⇒ 把「job → 命令片段」也做成机器可读的（`JOB_ENTRYPOINTS`），**逐字**去 YAML 里核。
    #    ⚠️ 这是**文本**核对，不是 YAML 解析：仓库没有 PyYAML 依赖，为一条判据引一个解析器
    #       不划算；且误报方向是「漏报」（切错只会让 job 块变小 ⇒ 更容易报缺），不是假红。
    _ci_problems: list[str] = []
    for j in sorted((set(CI_JOB_PROBES) | set(SELFTEST_JOB_PROBES)) - set(JOB_ENTRYPOINTS)):
        _ci_problems.append(f"job `{j}` 在清单里，但 `JOB_ENTRYPOINTS` 里**没写它怎么被调用**")
    try:
        _ci_text = CI_YML.read_text(encoding="utf-8")
    except OSError as exc:
        _ci_text = ""
        _ci_problems.append(f"读不到 `ci.yml`（{CI_YML}）：{exc}")
    _ci_blocks = _ci_job_blocks(_ci_text) if _ci_text else {}
    for j, frags in sorted(JOB_ENTRYPOINTS.items()):
        if j not in _ci_blocks:
            _ci_problems.append(f"`ci.yml` 里**没有**名为 `{j}` 的 job（`JOB_ENTRYPOINTS` 声称它在）")
            continue
        for frag in frags:
            # 🚨 **不能裸用 `frag in block`** —— 实测被自检臂 **H2 当场否掉**：
            #    `--job browser-admin` 是 `--job browser-adminX` 的**前缀** ⇒
            #    把命令改一个字符，子串匹配**照样命中** ⇒ 判据 6 放行。
            #    （与坑 66 同族：**判据的判法本身**也会造假绿。）
            #    ⇒ 两侧都加**词边界**：片段前后不得紧邻 `[A-Za-z0-9_.-]`。
            #    这样 `...browser-adminX` / `xpython ...` 都不会被当成命中。
            if not re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(frag) + r"(?![A-Za-z0-9_.-])",
                             _ci_blocks[j]):
                _ci_problems.append(f"`ci.yml` 的 `{j}` job 里**找不到这一步**：`{frag}`")
    if _ci_problems:
        print("❌ `ci.yml` 与登记表不一致（**判据 5 证明不了这件事**）：")
        for p in _ci_problems:
            print(f"  · {p}")
        print("   ⇒ 「名册说有人跑」与「YAML 里真的有那一步」是**两件事**："
              "移走一步 YAML 不会让判据 5 变红 —— 那正是坑 65 的形状。")
        return 1

    # ---- 判据 7：**退出码语义**不得被 `SystemExit(<非整数>)` 静默破坏 ----
    # 🚨 Python 语义：`sys.exit("msg")` / `raise SystemExit("msg")` 把字符串打到 stderr 后
    #    **以状态码 1 退出** —— **只有**传入 int 才是那个 int。
    #    而 `evidence/*.py` 的约定是 `0` 通过 / `1` **产品缺陷** / `2` **环境问题** ⇒
    #    「环境问题」被**静默降级**成「产品缺陷」。
    #    最坏的地方是它**看起来完全正确**：消息里写着「→ exit 2」，读的人就信了。
    #    实测（2026-09-24 · Phase 2 首次真跑）：`verify_admin_413_toast_e2e.py` 因为
    #    CDP 输入 race 走环境分支 ⇒ **实际退出 1** ⇒ 浏览器 job 把它报成「产品缺陷」。
    #    当时全仓共 **14 处 / 5 个文件**，其中 `verify_register_abuse_defense.py` 已在
    #    `GATED` 里天天跑（只是没人触发过那条分支）。
    #    ⇒ 这正是 methodology 反复警告的「**把「我没测成」报成「产品坏了」**」。
    _exit_problems = judge_exit_codes(_evidence_sources())
    if _exit_problems:
        print("❌ 有脚本用 `SystemExit(<字符串>)` / `sys.exit(<字符串>)`"
              " —— 退出码会是 **1**，不是 2：")
        for p in _exit_problems:
            print(f"  · {p}")
        print("   ⇒ 字符串参数会被打到 stderr，然后**以状态码 1 退出**（只有 int 才是那个 int）。")
        print("      本仓库约定 `2` = 环境问题 ⇒ 这会把「我没测成」**静默报成「产品坏了」**。")
        print("      修法：`print(msg, file=sys.stderr)` + `raise SystemExit(2)`。")
        return 1

    # ---- 判据 1：探针自检（工具坏了 ≠ 产品坏了）----
    # 🚨 **必须遵守 exit 三语义**：`2` 是「**环境**不支持自检」，**不是**「工具坏了」。
    #    2026-09-22 实测踩到：`verify_runtime_health.py --self-test` 在**服务活着**时
    #    真起 Chromium 跑 4 个端（**151.6s**、exit 0）；而它 L287–292 写得很清楚 ——
    #    服务未就绪就 `return 2`。**CI 里四个 dev server 不存在** ⇒ 它会 exit 2
    #    ⇒ 旧实现 `ok = rc == 0` 会把它当「工具坏了」⇒ **CI 直接变红**。
    #    ⚠️ 这条坑的本质：**本机「服务活着」会让证据结果与 CI 不可比**。
    print("── 阶段 1：探针自检（合成夹具，与产品无关）──")
    broken: list[str] = []
    st_skipped: list[str] = []

    def _selftest_one(n: str, tag: str) -> None:
        rc, dt = run_one(n, ("--self-test",))
        if rc == 0:
            mark, verdict = "✓", tag
        elif rc == 2:
            mark, verdict = "⏭", "SKIP(环境)"
            st_skipped.append(n)
        else:
            mark, verdict = "✗", "**工具坏了**"
            broken.append(n)
        print(f"  {mark} {n:<40} exit={rc} ({dt:.1f}s)  {verdict}")

    for n in sorted(SELFTESTABLE):
        _selftest_one(n, "门禁内")
    print("  —— 以下探针**测不了**（要浏览器/服务/产物），只验「工具没坏」——")
    for n in sorted(SELFTEST_ONLY):
        _selftest_one(n, "仅自检")

    # ---- 判据 1c：自检**读真实仓库**的脚本 ----
    # 🚨 这一档**必须跑**，但红**必须换话术**：它的红最常见的成因是
    #    「仓库里多了没登记的东西」（新规范文档 / 新探针），那是**分诊欠账**。
    #    若照抄上面那句「工具坏了」，修的人会去改仪器 —— 方向完全错。
    #    ⚠️ 这与「零判据」是同一个道理：**判据给了错的方向，比没有判据更坏**。
    repo_bound_bad: list[str] = []
    if SELFTEST_REPO_BOUND:
        print("  —— 以下自检**读真实仓库**：红先读成「**登记表落后**」，不是工具坏了 ——")
        for n in sorted(SELFTEST_REPO_BOUND):
            rc, dt = run_one(n, ("--self-test",))
            if rc == 0:
                print(f"  ✓ {n:<40} exit=0 ({dt:.1f}s)  仪器与仓库一致")
            elif rc == 2:
                st_skipped.append(n)
                print(f"  ⏭ {n:<40} exit=2 ({dt:.1f}s)  SKIP(环境)")
            else:
                repo_bound_bad.append(n)
                print(f"  ✗ {n:<40} exit={rc} ({dt:.1f}s)  **登记表落后**")

    if st_skipped:
        print(f"\n⏭ {len(st_skipped)} 个自检因**环境问题（exit 2）**跳过 —— "
              "这**不是**通过，是「本次无法判断工具好不好」：")
        for n in st_skipped:
            print(f"  · {n}")
        print("   若某个探针**长期**跳过，它等于没有自检。")
    if broken:
        print(f"\n❌ {len(broken)} 个探针**自检不过** ⇒ 工具坏了，先修探针（exit 1）")
        print("   注意：自检挂了 = **工具**坏了，不是产品坏了。别去产品里找。")
        return 1
    if repo_bound_bad:
        print(f"\n❌ {len(repo_bound_bad)} 个「读仓库」自检不一致 ⇒ **登记表落后**（exit 1）")
        print("   先查这两件事（都不是产品缺陷）：")
        print("   ① 是不是**新增了规范文档**却没在 `report_spec_coverage.py` 里表态")
        print("      （`SPEC_FILES` 纳入 / `EXCLUDED_DOCS` 显式排除）；")
        print("   ② 是不是**新增/改名的探针**没在运行器里分类（判据 0/0b）。")
        print("   ⚠️ **别去产品代码里找，也别改仪器** —— 仪器是对的，欠的是分诊。")
        return 1

    # ---- 判据 2：实测 ----
    print("\n── 阶段 2：实测（exit 1 = 产品缺陷，exit 2 = 环境问题）──")
    failed: list[str] = []
    skipped: list[str] = []
    passed: list[str] = []
    for n, _why in GATED:
        rc, dt = run_one(n)
        if rc == 0:
            passed.append(n)
            mark, tag = "✓", "PASS"
        elif rc == 2:
            skipped.append(n)
            mark, tag = "⏭", "SKIP(环境)"
        else:
            failed.append(n)
            mark, tag = "✗", "FAIL"
        print(f"  {mark} {n:<40} exit={rc:<3} {tag} ({dt:.1f}s)")

    # ---- 判据 4：静态半边**棘轮**（2026-09-22 用户拍板 ①：冻结条数、只拦增长）----
    # 这 7 个用免浏览器开关跑。**今天全绿**（基线就是当前实测值），且**欠账涨了就红**。
    # ⚠️ 结果**单独计数**，不混进阶段 2 的 passed/skipped —— 否则会破坏判据 3 的语义
    #    （「全部跳过 ⇒ 失败」必须只看阶段 2）。
    print("\n── 阶段 3：静态半边棘轮（免浏览器；**只拦增长**）──")
    r_failed: list[str] = []
    r_skipped: list[str] = []
    r_passed = 0
    tighten: list[str] = []
    for n, g in sorted(STATIC_GATES.items()):
        rc, dt, out = run_ratchet_one(n, (g.flag,))
        probs, notes = judge_ratchet(g, rc, out)
        cnt = count_from(out, g.mode)
        shown = "取不到" if cnt is None else str(cnt)
        if rc == 2:
            r_skipped.append(n)
            mark, tag = "⏭", "SKIP(环境)"
        elif probs:
            r_failed.append(n)
            mark, tag = "✗", "FAIL(棘轮)"
        else:
            r_passed += 1
            mark, tag = "✓", f"PASS（rc={rc} · 条数 {shown} ≤ {g.count_max}）"
        print(f"  {mark} {n:<40} exit={rc:<3} {tag} ({dt:.1f}s)")
        for p in probs:
            print(f"      ❌ {p}")
        if probs:
            # **只在判红时**补打探针输出的尾巴 —— 一个「通过的」阶段里
            # 满屏「失败尾部」会让人以为阶段 3 挂了。
            tail = "\n".join(out.strip().splitlines()[-8:])
            if tail:
                print(f"      ── 探针输出尾部 ──\n{_indent(tail)}\n      ────────────")
        for nt in notes:
            print(f"      ℹ️ {nt}")
            if "收紧基线" in nt:
                tighten.append(n)

    # ---- 判据 4b：阶段 3 的 SKIP 必须显式豁免（否则这一档棘轮本次**没有评估**）----
    # 见 `STATIC_SKIP_EXEMPT` 上方的长注释与 `README.md` 坑 65。
    unexplained_skip = [n for n in r_skipped if n not in STATIC_SKIP_EXEMPT]
    if unexplained_skip:
        print("\n❌ 阶段 3 有探针**被环境跳过** ⇒ 这一档棘轮本次**没有评估**：")
        for n in unexplained_skip:
            print(f"  · {n}")
        print("   「跳过」不是「通过」—— 一条从不执行的棘轮与没有棘轮**无法区分**（README 坑 65）。")
        print("   两条出路：① **修环境**，让它真的能跑（推荐）；")
        print("             ② 确实跑不出结论 ⇒ 加进 `STATIC_SKIP_EXEMPT` 并**写明理由**。")
        return 1

    print("\n" + "=" * 76)
    print(f"[阶段 2] 通过 {len(passed)} · 失败 {len(failed)} · 跳过 {len(skipped)} · 共 {len(GATED)}")
    print(f"[阶段 3] 通过 {r_passed} · 失败 {len(r_failed)} · 跳过 {len(r_skipped)} · "
          f"共 {len(STATIC_GATES)}（棘轮：只拦增长）")

    if skipped:
        print("\n⚠️ 下列探针因**环境问题**被跳过，本次**没有**给出结论：")
        for n in skipped:
            print(f"  · {n}")
        print("   跳过不是通过。若它长期跳过，等于这条判据不存在。")

    if failed:
        print("\n❌ 门禁失败（产品缺陷）：")
        for n in failed:
            print(f"  · {n}")
        return 1

    # ---- 判据 3：**全部跳过 = 门禁空转** ----
    if skipped and not passed:
        print("\n❌ **所有门禁内探针都被跳过** ⇒ 这条门禁本次什么也没守住。")
        print("   要么修环境，要么把探针挪进 NOT_GATED 并写明理由。")
        return 1

    if tighten:
        print("\nℹ️ 下列探针的欠账**比冻结值更少** ⇒ **可以把基线收紧**（这不是失败）：")
        for n in tighten:
            print(f"  · {n}")

    if r_failed:
        print("\n❌ 棘轮失败（欠账**增长**了，或探针输出格式变了导致**取不到条数**）：")
        for n in r_failed:
            print(f"  · {n}")
        print("   ⇒ 「条数涨了」= 新欠账，必须修或走拍板；")
        print("     「取不到条数」= **仪器坏了**（输出格式变了）⇒ 先修仪器，别急着改产品。")
        return 1

    print("\n✅ evidence 门禁通过（含静态半边棘轮）。")
    return 0


# ---------------------------------------------------------------------------
# 运行器自检：**合成故障**，不碰仓库、不跑真探针。
#
# 为什么需要：判据 3（「全部跳过 ⇒ 失败」）在正常环境里**永远不会被触发**
# —— 它只在 CI 环境坏掉的那一天才生效，而那天没人会去检查它是不是真的会红。
# 一条从未被触发过的判据 = 一条没人知道存不存在的判据。
# ---------------------------------------------------------------------------
# 字段：`(键, 描述, 自检 rc, 实测 rc, 期望 exit, 「读仓库」自检 rc)`
#   ⚠️ 第 6 个字段**只作用于 `SELFTEST_REPO_BOUND` 里的脚本**（`None` = 与第 3 个字段同值）。
#   为什么要它：S4 的 rc=1 会**同时**命中阶段 1 的 `broken` 和阶段 1c 的 `repo_bound_bad`
#   ⇒ 删掉整个阶段 1c，S4 **照样绿**（`broken` 已经让它 exit 1）⇒ 它对阶段 1c 的接线
#   **没有判别力**。同 W 系列的理由：**只证明「判定函数会红」不等于「接线通」**。
SELFTEST_CASES: tuple[tuple[str, str, int, int, int, int | None], ...] = (
    ("S1", "探针全返回 2（环境）⇒ 运行器**必须失败**（门禁空转）", 0, 2, 1, None),
    ("S2", "有探针返回 1（产品缺陷）⇒ 运行器必须失败", 0, 1, 1, None),
    ("S3", "自检 0 + 实测全 0 ⇒ 运行器必须通过", 0, 0, 0, None),
    ("S4", "探针**自检**失败（工具坏了）⇒ 运行器必须失败，且不进实测", 1, 0, 1, None),
    # S5：阶段 1（合成夹具）**全绿**，只有阶段 1c（读仓库）失败 ⇒ 仍必须 exit 1。
    # ⇒ 这是**唯一**能证明「阶段 1c 真的接在 `run_all()` 上」的臂。
    ("S5", "**只有**「读仓库」自检失败 ⇒ 必须 exit 1（证明阶段 1c 接线通）", 0, 0, 1, 1),
)


def run_self_test() -> int:
    global run_one, run_ratchet_one, CI_YML, _evidence_sources
    real_one, real_ratchet = run_one, run_ratchet_one
    bad: list[str] = []
    print("运行器自检（合成故障，不碰仓库、不跑真探针）—— 各臂分开报：")
    try:
        for key, desc, selftest_rc, probe_rc, expect, repo_rc in SELFTEST_CASES:
            def fake(name: str, extra: tuple[str, ...] = (),
                     _s: int = selftest_rc, _p: int = probe_rc,
                     _rb: int | None = repo_rc) -> tuple[int, float]:
                if not extra:
                    return _p, 0.0
                # 「读仓库」那一档可以单独给 rc —— 否则无法把阶段 1c 与阶段 1 分开证伪
                if _rb is not None and name in SELFTEST_REPO_BOUND:
                    return _rb, 0.0
                return _s, 0.0

            def fake_ratchet(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float, str]:
                # 阶段 3 的桩：**必须给出「棘轮通过」的形状**（rc=0 + 可解析的条数），
                # 否则 S1/S3 会被阶段 3 搅乱。棘轮**自己的**判别力由 R 系列单独自检。
                return 0, 0.0, "缺陷 0 条\n"

            run_one = fake
            run_ratchet_one = fake_ratchet
            # 静音：自检只关心**退出码**，run_all 自己的报表会把四条臂搅在一起看不清
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
            ok = got == expect
            print(f"  [{key}] {desc}")
            print(f"        期望 exit={expect}  实测 exit={got}  {'✓' if ok else '✗'}")
            if not ok:
                bad.append(f"[{key}] 期望 exit={expect} 实测 exit={got}")

        # ---- R 系列：直接给 `judge_ratchet` 喂合成输入，证明**棘轮会红** ----
        # 棘轮在正常环境里**永远绿**（基线 = 当前实测值）⇒ 必须单独证明它**有判别力**。
        for key, desc, fixture, rc, out, expect_problem in RATCHET_SELFTEST:
            g = STATIC_GATES[fixture]          # 借真登记项当夹具（口径/上限都要对得上）
            probs, _notes = judge_ratchet(g, rc, out)
            got = bool(probs)
            ok = got == expect_problem
            print(f"  [{key}] {desc}")
            print(f"        期望「有问题」={expect_problem}  实测={got}  {'✓' if ok else '✗'}")
            if not ok:
                bad.append(f"[{key}] 期望有问题={expect_problem} 实测={got}")

        # ---- W 系列：证明**阶段 3 的接线**真的能让 `run_all()` 判红 ----
        # R 系列只证明 `judge_ratchet()` **会**返回问题；**接线断了**它也照样全绿。
        # 所以这里端到端跑一遍 `run_all()`：所有探针绿 + 棘轮条数暴涨 ⇒ **必须 exit 1**。
        def fake_ok(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float]:
            return 0, 0.0

        def fake_ratchet_grow(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float, str]:
            # 两种口径**都要**触发：`declared` 靠「缺陷 999 条」，`cross` 靠 999 行 `✗`
            return 0, 0.0, "缺陷 999 条\n" + "✗ x\n" * 999

        run_one = fake_ok
        run_ratchet_one = fake_ratchet_grow
        with contextlib.redirect_stdout(io.StringIO()):
            got = run_all()
        ok = got == 1
        print("  [W1] 阶段 2 全绿 + 棘轮条数暴涨 ⇒ `run_all()` **必须** exit=1（证明接线通）")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[W1] 期望 exit=1 实测 exit={got}")

        # ---- W2：证明**判据 4b**（阶段 3 的 SKIP 必须判红）真的接在 `run_all()` 上 ----
        # 为什么必须单独一臂：W1 只证明「棘轮**条数涨**会红」。
        # 判据 4b 走的是**另一条分支**（`rc == 2` 在 `judge_ratchet` **之前**就把探针
        # 划进 SKIP）⇒ 删掉 4b，W1 照样绿 ⇒ W1 对它**没有判别力**。
        # 这正是 `README.md` 坑 65 的缺陷形态：跳过被当成通过。
        def fake_ratchet_skip(name: str, extra: tuple[str, ...] = ()) -> tuple[int, float, str]:
            return 2, 0.0, ""          # rc=2 = 环境问题 ⇒ 阶段 3 记 SKIP

        run_one = fake_ok
        run_ratchet_one = fake_ratchet_skip
        with contextlib.redirect_stdout(io.StringIO()):
            got = run_all()
        ok = got == 1
        print("  [W2] 阶段 3 探针**被环境跳过** ⇒ `run_all()` **必须** exit=1"
              "（判据 4b：跳过 ≠ 通过）")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[W2] 期望 exit=1 实测 exit={got}")

        # ---- G 系列：**判据 5**（两张表必须互相兜住，README 坑 64）----
        # 就地增删（不换对象）⇒ `run_all()` 读到的就是改过的表。
        run_one = fake_ok
        run_ratchet_one = fake_ratchet          # 棘轮正常 ⇒ 结果只由判据 5 决定

        # G1：job 清单里放了**没登记**进 `SELFTEST_ELSEWHERE` 的探针 ⇒ 必须红
        SELFTEST_JOB_PROBES["__selftest_job__"] = ("__ghost_probe__.py",)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            del SELFTEST_JOB_PROBES["__selftest_job__"]
        ok = got == 1
        print("  [G1] job 清单里有**未登记**进 `SELFTEST_ELSEWHERE` 的探针 ⇒ 必须 exit=1")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[G1] 期望 exit=1 实测 exit={got}")

        # G2：`SELFTEST_ELSEWHERE` 声称「由某个 job 跑」，但 job 清单里没有它 ⇒ 必须红
        #     （这是「假接线」：名册说有人跑，实际没人跑）
        _saved_job = SELFTEST_JOB_PROBES.pop("browser-selftest")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            SELFTEST_JOB_PROBES["browser-selftest"] = _saved_job
        ok = got == 1
        print("  [G2] 声称由 `browser-selftest` 跑，但**该 job 的登记整个没了** ⇒ 必须 exit=1")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[G2] 期望 exit=1 实测 exit={got}")

        # G3：声称由**已登记** job 跑，但**不在该 job 的清单里** ⇒ 必须红
        #     （G2 是「job 整个没了」，G3 是「job 在、清单漏了这一条」—— 两种都要守）
        _saved_list = SELFTEST_JOB_PROBES["browser-selftest"]
        SELFTEST_JOB_PROBES["browser-selftest"] = tuple(
            x for x in _saved_list if x != "verify_dark_mode.py")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            SELFTEST_JOB_PROBES["browser-selftest"] = _saved_list
        ok = got == 1
        print("  [G3] 声称由已登记 job 跑、但**清单里漏了这一条** ⇒ 必须 exit=1")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[G3] 期望 exit=1 实测 exit={got}")

        # G4：`CI_ELSEWHERE` 的条目**没有机器可读标记** ⇒ 必须红
        #     （判据 5 泛化后**新增的分支**：原先「没有 `@job:`」的条目会**静默跳过**
        #      —— 那正是坑 66 的形状：**声称无法被核 = 没有声称**。）
        #     臂里刻意保留散文里的 job 名，证明「理由里恰好出现了 job 名」不算声称。
        _saved_dm = CI_ELSEWHERE["verify_reduced_motion.py"]
        CI_ELSEWHERE["verify_reduced_motion.py"] = (
            "由 `browser-admin` job 跑（**故意不写 @job: 标记**）。")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            CI_ELSEWHERE["verify_reduced_motion.py"] = _saved_dm
        ok = got == 1
        print("  [G4] `CI_ELSEWHERE` 条目**只在散文里提 job 名、没有 `@job:` 标记** ⇒ 必须 exit=1")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[G4] 期望 exit=1 实测 exit={got}")

        # G5：`CI_JOB_PROBES`（**真判据**那一族）里漏一条 ⇒ 必须红
        #     G3 只覆盖了「自检」那一族 ⇒ 泛化后必须**两族各有一臂**，
        #     否则「真判据的清单漏一条」这条分支**从来没被触发过**。
        _saved_ci_list = CI_JOB_PROBES["browser-admin"]
        CI_JOB_PROBES["browser-admin"] = tuple(
            x for x in _saved_ci_list if x != "verify_reduced_motion.py")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            CI_JOB_PROBES["browser-admin"] = _saved_ci_list
        ok = got == 1
        print("  [G5] `CI_JOB_PROBES` 的**真判据清单漏了一条**（声称还在）⇒ 必须 exit=1")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[G5] 期望 exit=1 实测 exit={got}")

        # ---- H 系列：**判据 6**（`ci.yml` 里真的**有**那些 job、**有**那些命令）----
        # 🚨 为什么必须单独一臂：判据 5 只证明**两张表自洽**。
        #    把 `ci.yml` 里的一步移走 ⇒ 两张表**照样自洽** ⇒ 判据 5 全绿
        #    ⇒ 名册写着「由 `browser-admin` 跑」而实际没有 ⇒ **坑 65 的形状**。
        # ⚠️ 用**合成 YAML**（不碰真文件）：真 `ci.yml` 是判据 6 的靶子，
        #    改它会把前面所有臂一起污染（本仓库已经栽过一次「模拟环境时把被测对象也换了」）。
        _syn_ok = (
            "name: CI\n"
            "on:\n"
            "  workflow_dispatch:\n"
            "jobs:\n"
            "  evidence:\n"
            "    steps:\n"
            "      - run: python evidence/run_ci_probes.py --self-test\n"
            "  frontend:\n"
            "    steps:\n"
            "      - run: python3 ../evidence/verify_api_base_baked.py\n"
            "      - run: python3 ../evidence/verify_typography.py --source-only\n"
            "  browser-selftest:\n"
            "    steps:\n"
            "      - run: python evidence/run_ci_probes.py --selftest-for-job browser-selftest\n"
            "  browser-admin:\n"
            "    steps:\n"
            "      - run: python evidence/run_browser_job.py --job browser-admin\n"
            "  browser-all:\n"
            "    steps:\n"
            "      - run: python evidence/run_browser_job.py --job browser-all\n"
        )
        _ci_tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="ci-probe-selftest-"))
        _tmp_ci = _ci_tmp_dir / "ci.yml"
        _real_ci = CI_YML
        try:
            for key, text, desc, expect in (
                ("H0", _syn_ok,
                 "合成 `ci.yml` **完整** ⇒ 必须 exit=0"
                 "（**阴性对照**：证明判据 6 不是「只会红」的判据）", 0),
                ("H1", _syn_ok.replace(
                    "  browser-admin:\n"
                    "    steps:\n"
                    "      - run: python evidence/run_browser_job.py --job browser-admin\n", ""),
                 "合成 `ci.yml` 里**整个 `browser-admin` job 没了** ⇒ 必须 exit=1", 1),
                ("H2", _syn_ok.replace("--job browser-admin", "--job browser-adminX"),
                 "合成 `ci.yml` 里那一步的命令**尾部多了一个字符** ⇒ 必须 exit=1"
                 "（**H2 是逮到过真实漏洞的那一臂**：裸子串匹配时它**照样绿**）", 1),
                ("H3", _syn_ok.replace("python evidence/run_browser_job.py",
                                       "xpython evidence/run_browser_job.py"),
                 "合成 `ci.yml` 里那一步的命令**头部多了一个字符** ⇒ 必须 exit=1"
                 "（证明**左侧**词边界也在起作用）", 1),
            ):
                _tmp_ci.write_text(text, encoding="utf-8")
                CI_YML = _tmp_ci
                with contextlib.redirect_stdout(io.StringIO()):
                    got = run_all()
                ok = got == expect
                print(f"  [{key}] {desc}")
                print(f"        期望 exit={expect}  实测 exit={got}  {'✓' if ok else '✗'}")
                if not ok:
                    bad.append(f"[{key}] 期望 exit={expect} 实测 exit={got}")
        finally:
            CI_YML = _real_ci
            shutil.rmtree(_ci_tmp_dir, ignore_errors=True)

        # ---- J 系列：**判据 7**（`SystemExit(<非整数>)` ⇒ 实际退出 **1**，不是 2）----
        # 🚨 这一条守的是**最容易骗过人的一类**：代码里写着「→ exit 2」、
        #    消息也打在 stderr 上，**读起来完全正确**，只有退出码是错的。
        #    J0/J2/J3 直接喂合成源码（纯函数）；J1 端到端证明**接线**
        #    —— 只证明「函数会返回问题」是不够的：函数没被 `run_all()` 调用时它也照样全绿。
        for key, src, expect_problem, desc in (
            ("J0", "raise SystemExit(2)\n", False,
             "**正确写法**（int 参数）⇒ **不得**报问题（**阴性对照**）"),
            ("J2", 'sys.exit("boom")\n', True,
             "`sys.exit(<字符串>)` ⇒ 必须报问题（退出码会是 1）"),
            ("J3", 'raise SystemExit(f"boom {x}")\n', True,
             "`raise SystemExit(<f-string>)` ⇒ 必须报问题"),
            ("J4", "sys.exit(main())\n", False,
             "`sys.exit(main())`（**本仓库每个脚本都这么写**）⇒ **不得**报问题"
             "（否则判据 7 一上线就红一片，属**假红**）"),
        ):
            got = bool(judge_exit_codes({"synthetic.py": src}))
            ok = got == expect_problem
            print(f"  [{key}] {desc}")
            print(f"        期望「有问题」={expect_problem}  实测={got}  {'✓' if ok else '✗'}")
            if not ok:
                bad.append(f"[{key}] 期望有问题={expect_problem} 实测={got}")

        def _fake_sources() -> dict[str, str]:
            return {"ghost.py": 'raise SystemExit("boom")\n'}

        _real_sources = _evidence_sources
        _evidence_sources = _fake_sources
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                got = run_all()
        finally:
            _evidence_sources = _real_sources
        ok = got == 1
        print("  [J1] 合成脏源码（`SystemExit(<字符串>)`）⇒ `run_all()` **必须** exit=1"
              "（证明判据 7 的**接线**通，而不只是「函数会返回问题」）")
        print(f"        期望 exit=1  实测 exit={got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[J1] 期望 exit=1 实测 exit={got}")
    finally:
        run_one = real_one
        run_ratchet_one = real_ratchet
    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **运行器坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print(f"\n自检通过：判据 0/1/2/3 各触发过一次（含「全部跳过 ⇒ 失败」）；"
          f"阶段 1c（读仓库自检）**接线** 1 臂（S5：只有它红 ⇒ 必须 exit 1）；"
          f"棘轮**判定** {len(RATCHET_SELFTEST)} 臂（含「**取不到条数必须红**」）；"
          f"棘轮**接线** 2 臂（W1 条数暴涨 / **W2 阶段 3 被环境跳过 ⇒ 判据 4b**）；"
          f"登记表**互相兜住** 5 臂（G1 孤儿清单 / **G2 job 登记被删** / **G3 清单漏一条** / "
          f"**G4 无 `@job:` 标记** / **G5 真判据清单漏一条**）—— 判据 5；"
          f"`ci.yml` **真的接上了** 4 臂（H0 阴性对照 / **H1 job 整个没了** / "
          f"**H2 尾部多一个字符** / **H3 头部多一个字符**）—— 判据 6；"
          f"**退出码语义** 5 臂（J0/J4 阴性对照 / J2 `sys.exit(字符串)` / "
          f"J3 `SystemExit(f-string)` / **J1 接线**）—— 判据 7。")
    return 0


# ── 免浏览器模式：**显式登记 + 守卫**（刻意不写「自动探测器」）──────────────
#
# 🚨 **为什么不自动探测**：这个「能力」**没有可靠的机器信号**。实测 7 个探针里，
#    `help` 文案写了「不开浏览器」的只有 **3 个**（focus_ring / typography / contrast）；
#    另外 **4 个连 `help` 都没有**：
#      · `verify_buttons`      `add_argument("--source-only", action="store_true")`  ← 无 help
#      · `verify_font_scale`   同上，无 help
#      · `verify_design_tokens` help="只跑源码级检查"   ← 没提浏览器
#      · `verify_grade_badges`  help="只扫源码层"       ← 没提浏览器
#    ⇒ 任何「按 help 文案自动探测」的实现都会**漏报 4/7**，
#      而**漏报比没有更危险**（`README.md` 坑 46）：它看起来权威，于是没人再手工盘一遍。
#    ⇒ 所以**显式登记**，并用一条**守卫**防它腐烂。
#
# ⚠️ 守卫**能**逮到：文件改名 / 开关改名（那时登记就是**假信息**）。
# ⚠️ 守卫**逮不到**：「条数过期」—— 所以每条旁**必须带测量日期**，
#    过期了就重跑 `--static-audit`（它会现场重跑并报「与冻结值不一致」）。
#
# ── 2026-09-22 用户拍板 **① 棘轮**：这 7 个已**接进 CI**（`run_all()` 阶段 3）──────
#    做法与项目既有棘轮（`GATE_BASELINE` / `BASELINE_UNGUARDED_READ`）同构：
#    **冻结当前条数、只拦增长** ⇒ 今天全绿，且欠账**再也无法悄悄变大**。
#    ⇒ 它们已从 `NOT_GATED` **移出**（那一档现在是 14 个「真需要浏览器/产物」的）。
#
# 🚨 **棘轮最大的风险 = 假绿**：若「取条数」的正则**突然匹配不上**，
#    天真实现会数出 **0 条** ⇒ **永久绿**。所以 `count_from()` 用 `None` 区分
#    「**取不到**」与「**真的是 0**」，`judge_ratchet()` 把 `None` **判红**。
#    ⇒ 「取不到条数」**不是通过**，是**无法判定**。


class StaticGate(NamedTuple):
    """一个「免浏览器半边」的棘轮登记项。"""

    flag: str       # 免浏览器开关（必须真的出现在探针源码里）
    rc_max: int     # 冻结的退出码：只许 ≤
    count_max: int  # 冻结的缺陷条数：只许 ≤
    mode: str       # 计数口径："declared"（探针自报 `缺陷 N 条`）| "cross"（数行首 `✗`）
    note: str       # 出处 + **测量日期**（守卫逮不到过期，靠日期人工复核）


STATIC_GATES: dict[str, StaticGate] = {
    # 探针: (开关, rc 上限, 条数上限, 计数口径, 测量说明) —— 全部 **2026-09-22 实测**
    "verify_buttons.py": StaticGate(
        "--source-only", 1, 10, "declared",
        "**10 条**（`<button>` 硬编码 `h-8`/`h-9` 绕过 `Button` ⇒ 移动端不足 §6.1 的 48px；"
        "带 `min-h-tap` 补偿的**已正确排除**）。欠账 #32。"),
    "verify_design_tokens.py": StaticGate(
        "--source-only", 0, 0, "cross",
        "**0 条（绿）**。⚠️ 修 `rglob` 剪枝前要 **160s** ⇒ 曾被当挂死；"
        "改 `os.walk` + `dirnames[:]` 后 1.3s，输出**逐字节不变**（对照组文件集 116==116）。"),
    "verify_focus_ring.py": StaticGate(
        "--source-only", 1, 3, "declared",
        "**3 条**（F3 环宽 `ring-2`=2px ≠ 规范 3px ×48 处、F4 环色 5 种不统一、"
        "F5 令牌 `shadow-focus` **0 调用点**）。欠账 #31。"),
    "verify_font_scale.py": StaticGate(
        "--source-only", 1, 6, "declared",
        "**6 条**（聚 1 条根因）+ 18 条 T4 提示（**只报不判**，供拍板）。"),
    "verify_grade_badges.py": StaticGate(
        "--source-only", 1, 5, "cross",
        "**5 条**（4 个等级映射不符 §2.4，其中 `brand`/`ink` 两个变体**组件层根本不存在**；"
        "+ G2 六份 `GRADE_TONE` 重复来源）。⚠️ 该探针**不自报** `缺陷 N 条` ⇒ 只能数 `✗`。欠账 #33。"),
    "verify_contrast.py": StaticGate(
        "--tokens-only", 0, 0, "cross",
        "**0 条（绿）**。唯一发现是规范表 5 条对比度**近似值**，"
        "探针自己标「不是产品缺陷」（属规范侧改数）。"),
}

# ---------------------------------------------------------------------------
# 🚨 判据 4b（2026-09-24 用户拍板 ⑥）：**阶段 3 出现 SKIP ⇒ 判红**
#
# 为什么需要（`README.md` **坑 65**）：`r_skipped` 此前**不参与任何失败判定**
#   （全文件只出现 3 次：初始化 / 追加 / 汇总打印）⇒ 一个探针可以**永久 SKIP 而门禁照绿**。
#   实测代价：`verify_typography.py` 因为 CI 里拿不到 `frontend/node_modules` **和**
#   `.next` 产物 ⇒ 整份脚本 `exit 2` ⇒ 阶段 3 记 SKIP ⇒
#   **T2/T2b/T2c 在 CI 里一条都没跑**（冻结 0/0 的棘轮**从未被评估**），而门禁一直显示绿。
#
# 🚨 与**坑 50** 的关系：坑 50 要求「棘轮『取不到条数』必须判红」—— 那是**脚本内部**的防线。
#   而 `rc == 2` 在**运行器层面、判定之前**就被划进 SKIP ⇒ **连那条防线都不会被触发**。
#   ⇒ 本条是坑 50 在**运行器层面**的补丁。
#
# ⚠️ **豁免必须显式登记**：允许跳过就等于允许「这条判据不存在」，所以每一条例外
#   都要写出**为什么它在 CI 里跑不出结论**。空表 = **一条都不许跳**。
# ⚠️ **顺序纪律**：先让探针真的能跑（Phase 0），再上这条守卫；反过来 CI 立刻红。
# ---------------------------------------------------------------------------
STATIC_SKIP_EXEMPT: dict[str, str] = {}

# 供判据 0 使用：`GATED_STATIC` 的键 = 「已接入 CI、但要用免浏览器开关跑」的那一档。
GATED_STATIC: tuple[str, ...] = tuple(STATIC_GATES)

_DECLARED_RE = re.compile(r"缺陷 (\d+) 条")
_CROSS_RE = re.compile(r"(?m)^\s*✗")


def count_from(stdout: str, mode: str) -> int | None:
    """从探针 stdout 取「缺陷条数」。🚨 **取不到返回 `None`，不是 0。**

    为什么必须分开：探针改了输出格式 ⇒ 正则失配 ⇒ 天真实现数出 0 ⇒ 棘轮**永久绿**。
    `None` 一路传到 `judge_ratchet()`，在那里**判红**。
    """
    if mode == "declared":
        m = _DECLARED_RE.search(stdout)
        return int(m.group(1)) if m else None
    if mode == "cross":
        return len(_CROSS_RE.findall(stdout))
    raise ValueError(f"未知计数口径：{mode!r}")


def judge_ratchet(g: StaticGate, rc: int, stdout: str) -> tuple[list[str], list[str]]:
    """**纯函数**：判断一个静态半边是否「只拦增长」。返回 `(问题, 提示)`。

    问题非空 ⇒ **判红**；提示只是「基线可以收紧了」，**不影响判定**。
    抽成纯函数是为了让 `--self-test` 能**直接**给它喂合成输入（见 `RATCHET_SELFTEST`）。
    """
    problems: list[str] = []
    notes: list[str] = []

    if rc == 2:
        notes.append("环境问题（exit 2）⇒ 本次**不给结论**（跳过不是通过）")
        return problems, notes
    if rc not in (0, 1):
        problems.append(f"退出码 {rc} 既不是 0/1 也不是 2 ⇒ 探针**本身可能坏了**")
        return problems, notes

    if rc > g.rc_max:
        problems.append(f"退出码涨了：{rc} > 冻结 {g.rc_max}")
    elif rc < g.rc_max:
        notes.append(f"退出码降了：{rc} < 冻结 {g.rc_max} ⇒ **可以收紧基线**")

    cnt = count_from(stdout, g.mode)
    if cnt is None:
        problems.append("**取不到条数**（探针没打印 `缺陷 N 条`）⇒ 无法判定，"
                        "**不等于通过**；多半是探针改了输出格式 ⇒ 人工确认后更新口径/基线")
    elif cnt > g.count_max:
        problems.append(f"缺陷条数涨了：{cnt} > 冻结 {g.count_max}")
    elif cnt < g.count_max:
        notes.append(f"缺陷条数降了：{cnt} < 冻结 {g.count_max} ⇒ **可以收紧基线**")
    return problems, notes


# ---------------------------------------------------------------------------
# 棘轮的**自检**：给 `judge_ratchet` 喂**合成输入**，证明它**会红**。
#
# 🚨 为什么必须有：棘轮在正常环境里**永远绿**（基线就是当前实测值）。
#    一条只会绿的防线不算防线 —— 必须证明「条数涨了」「**取不到条数**」「rc 涨了」
#    **真的会红**，否则它只是装饰。
#    每项 = (键, 说明, 夹具探针, rc, 合成 stdout, 期望「有问题」)
#    ⚠️ 夹具必须挑**对应上限**的登记项：R5 要 rc 上限 = 0 的（`design_tokens`），
#       R8/R9 要 `cross` 口径的（`grade_badges`）—— 否则臂本身测不到想测的分支。
# ---------------------------------------------------------------------------
RATCHET_SELFTEST: tuple[tuple[str, str, str, int, str, bool], ...] = (
    ("R1", "条数 == 冻结 ⇒ 通过（不报问题）", "verify_buttons.py", 1, "缺陷 10 条\n", False),
    ("R2", "条数 > 冻结 ⇒ **必须红**（棘轮的本职）", "verify_buttons.py", 1, "缺陷 11 条\n", True),
    ("R3", "条数 < 冻结 ⇒ 不红，只提示「可收紧」", "verify_buttons.py", 1, "缺陷 9 条\n", False),
    ("R4", "**取不到条数** ⇒ **必须红**（这就是防假绿的那一臂）", "verify_buttons.py", 1,
     "输出格式整个换掉了，一个数字都没有\n", True),
    ("R5", "rc 涨了（0→1）⇒ **必须红**", "verify_design_tokens.py", 1, "", True),
    ("R6", "rc=2（环境问题）⇒ **不红**，但也不给结论", "verify_buttons.py", 2, "", False),
    ("R7", "rc=124（超时）⇒ **必须红**（超时当失败）", "verify_buttons.py", 124, "", True),
    ("R8", "`cross` 口径：`✗` 行数涨了 ⇒ **必须红**", "verify_grade_badges.py", 1,
     "✗ a\n✗ b\n✗ c\n✗ d\n✗ e\n✗ f\n", True),
    ("R9", "`cross` 口径：`✗` 行数未涨 ⇒ 通过", "verify_grade_badges.py", 1,
     "✗ a\n✗ b\n✗ c\n✗ d\n✗ e\n", False),
)


def static_gate_guard() -> list[str]:
    """守卫：登记项必须**自洽**，且登记的开关必须**真的出现在探针源码里**。

    ⚠️ 逮不到「条数过期」—— 那是 `--static-audit` 现场重跑（并报 rc 漂移）的活。
    """
    problems: list[str] = []
    for name, g in sorted(STATIC_GATES.items()):
        p = HERE / name
        if not p.exists():
            problems.append(f"{name} 不存在（但登记里有它）")
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:  # pragma: no cover - 文件系统异常
            problems.append(f"{name} 读不到：{e}")
            continue
        if f'"{g.flag}"' not in src:
            problems.append(f"{name} 的源码里找不到开关 {g.flag}")
        if g.mode not in ("declared", "cross"):
            problems.append(f"{name} 的计数口径 {g.mode!r} 未知")
        if g.rc_max not in (0, 1):
            problems.append(f"{name} 的 rc 上限 {g.rc_max} 既不是 0 也不是 1")
        # 自洽：冻结 rc=0（=「全绿」）却允许有缺陷条数 ⇒ 登记本身自相矛盾
        if g.rc_max == 0 and g.count_max != 0:
            problems.append(f"{name} 冻结 rc=0 却允许 {g.count_max} 条缺陷 ⇒ 基线自相矛盾")
        # 自洽：口径是 declared 但源码里根本没印过 `缺陷 N 条` ⇒ 棘轮一定取不到数
        if g.mode == "declared" and "缺陷 " not in src:
            problems.append(f"{name} 登记口径是 `declared`，但源码里没有 `缺陷 N 条` 的输出")
    return problems


def run_static_audit() -> int:
    """现场重跑静态半边 —— **只报告，不判定，永远 exit 0**。

    🚨 **为什么它仍然 exit 0**：**判定**已经由 `run_all()` 的**阶段 3（棘轮）**负责了。
    本模式只负责两件人看的事：① 把**细节**打出来；② 报「**登记过期**」
    （实测 rc/条数与冻结值不一致）。
    ⇒ 两者共用 `STATIC_GATES` **同一个登记表** ⇒ 不会漂移（这是刻意合并的）。
    """
    print("【静态半边审计 —— **只报告不判定**；判定在 `run_all()` 阶段 3 的棘轮】\n")
    for prob in static_gate_guard():
        print(f"  ⚠️ 登记不自洽：{prob}")
    reds = 0
    stale = 0
    for name, g in sorted(STATIC_GATES.items()):
        rc, dt, out = run_ratchet_one(name, (g.flag,))
        cnt = count_from(out, g.mode)
        if rc == 1:
            reds += 1
        mark = {0: "✓ 绿", 1: "✗ 红", 2: "? 环境", 124: "⏱ 超时"}.get(rc, f"? rc={rc}")
        shown = "取不到" if cnt is None else str(cnt)
        drift = ""
        if rc != g.rc_max or cnt != g.count_max:
            stale += 1
            drift = (f"   ⚠️ 与冻结值不一致（rc {g.rc_max}→{rc}、"
                     f"条数 {g.count_max}→{shown}）⇒ 若为**下降**就收紧基线；若为**上升**则阶段 3 已判红")
        print(f"  {mark:<7} {name:<26} {g.flag:<15} {dt:5.1f}s  "
              f"条数={shown:<5} {g.note}{drift}")
    print(f"\n  合计 {len(STATIC_GATES)} 个，**{reds} 红**；"
          f"其中 **{stale} 个与冻结值不一致**（需人工复核基线）。")
    print("  ⇒ 这 7 个**已接入 CI**（阶段 3 棘轮，只拦增长）；"
          "本条只提供细节与「基线该收紧了」的信号。")
    return 0


def ci_gap_footer() -> None:
    """把「**CI 内零判据**」的结论印在 `--list` 的**页脚** —— **只报告不判定**。

    ## 它回答的是哪一个问题（和棘轮不是同一件事）

    `run_all()` 阶段 3 的棘轮回答的是「**已登记的欠账会不会悄悄变大**」；
    本条回答的是「**这条判据在 CI 里到底会不会跑**」。
    两者都不是「有没有判据」，所以**不能互相替代** —— 一条判据**存在**、
    但只写在 `NOT_GATED` 探针里 ⇒ 它在**任何 job 里都不执行**。

    ## 为什么印在页脚，而不是新做一条会变红的门禁

    `--list` 是 `ci.yml` 的 evidence job **本来就会跑**的那一步
    （`Print gating roster`）⇒ 印在这里 = **缺口每次 CI 自曝**，
    而且**不新增步骤、不可能让 CI 变红**（另一条路要拍板，且第一天就有 5 个真缺口
    ⇒ 一上线即常红；「长期红的门禁两天内必被注释掉」是本项目的第九轮教训）。
    与 `--static-audit` 同一模式：**报告给判定让路**。

    ## 取不到时怎么办

    **不打印数字，只打印「取不到 + 原因」。** 报表自己也守同一条
    （`_ci_gate_names()` 取不到分档时不出 v4 数字）：把「取不到」静默当 0，
    会得到一份「CI 内全覆盖」的**假绿**报告 —— 那比不打印更糟。
    """
    print("\n【报表口径：`report_spec_coverage.py --ci-gap-only`"
          "（**只报告不判定**，不影响本命令退出码）】")
    try:
        cp = subprocess.run(
            [sys.executable, str(HERE / "report_spec_coverage.py"), "--ci-gap-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=FOOTER_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"     ⏱ 取不到（>{FOOTER_TIMEOUT_SEC}s 超时）⇒ 本次不打印该维度")
        return
    except OSError as exc:  # pragma: no cover - 文件系统/权限异常
        print(f"     ⚠️ 取不到（{exc!r}）⇒ 本次不打印该维度")
        return
    body = (cp.stdout or "").strip("\n")
    if cp.returncode != 0 or not body:
        # ⚠️ **空输出也算「取不到」**：报表至少会印一行标题 ⇒ 空 = 它自己坏了。
        # 把空输出当成「零缺口」正是「取不到静默当 0」那种假绿。
        why = f"rc={cp.returncode}" if cp.returncode != 0 else "rc=0 但输出为空"
        print(f"     ⚠️ 取不到（{why}）⇒ 本次不打印该维度")
        tail = (cp.stderr or "").strip().splitlines()
        if tail:
            print(f"        它自己的报错（末行）：{tail[-1]}")
        return
    print(body)


def run_selftest_for_job(job: str) -> int:
    """跑某个 job 负责的那批自检（清单来自 `SELFTEST_JOB_PROBES`，**YAML 不写第二份**）。

    为什么单开一个入口：`ci.yml` 里手写探针名 = **第二份清单** ⇒ 一定漂移（README 坑 64）。
    这里只暴露 **job 名**，清单留在运行器里，与 `SELFTEST_ELSEWHERE` 由**判据 5** 强制一致。

    退出码沿用三语义：`0` 全过 / `1` 有自检失败（**仪器**坏了，不是产品缺陷）/
    `2` job 名没登记（**用错了入口**，属环境/配置问题）。
    """
    names = SELFTEST_JOB_PROBES.get(job)
    if names is None:
        print(f"❌ 没有登记过 job `{job}` 的自检清单。")
        print(f"   已登记：{', '.join(sorted(SELFTEST_JOB_PROBES)) or '（无）'}")
        print("   ⇒ 新增 job 时必须在 `SELFTEST_JOB_PROBES` 里表态（否则它的自检从不执行）。")
        return 2
    print(f"── 自检 · job=`{job}`（{len(names)} 条；清单来自 `SELFTEST_JOB_PROBES`）──")
    bad: list[str] = []
    for n in names:
        rc, dt, out, err = _run_probe(n, ("--self-test",))
        mark = "✓" if rc == 0 else ("⏭" if rc == 2 else "✗")
        print(f"  {mark} {n:<40} exit={rc:<3} ({dt:.1f}s)")
        if rc != 0:
            bad.append(n)
            _report_failure(rc, out, err)
    if bad:
        print(f"\n❌ {len(bad)} 条自检未通过：{', '.join(bad)}")
        print("   「自检失败」= **仪器坏了**（或环境缺浏览器），**不是产品缺陷** —— 先修仪器。")
        return 1
    print(f"\n✅ {len(names)} 条自检全部通过（job=`{job}`）。")
    print("   这一档守的是「**测量机制没坏**」（能在 Linux 上起浏览器、能测 `env()`/媒体查询），"
          "不是产品 —— 它是 Phase 2/3 接真判据的**前置**。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="evidence 探针的 CI 运行器")
    ap.add_argument("--list", action="store_true", help="只打印分档清单，不跑")
    ap.add_argument("--self-test", action="store_true", help="只跑运行器自身的合成自检，不跑真探针")
    ap.add_argument("--selftest-for-job", metavar="JOB", default=None,
                    help="只跑某个 job 负责的自检（清单来自 `SELFTEST_JOB_PROBES`；"
                         "**不要在 YAML 里手写探针名** —— 见 README 坑 64）")
    ap.add_argument("--static-audit", action="store_true",
                    help="重跑静态半边并打印细节（**只报告不判定，永远 exit 0**；"
                         "判定在 `run_all()` 阶段 3 的棘轮）")
    args = ap.parse_args()

    if args.selftest_for_job:
        return run_selftest_for_job(args.selftest_for_job)

    if args.list:
        print(f"【门禁内 GATED（直接跑，{len(GATED)} 个）】")
        for n, why in GATED:
            print(f"  ✓ {n:<40} {why}")
        print(f"\n【门禁内 · 静态半边棘轮 GATED_STATIC（{len(GATED_STATIC)} 个）】")
        print("   用免浏览器开关跑；**冻结条数、只拦增长**（2026-09-22 用户拍板 ①）。")
        for n, g in sorted(STATIC_GATES.items()):
            print(f"  ⇅ {n:<40} {g.flag} · 冻结 rc≤{g.rc_max} / 条数≤{g.count_max}"
                  f"（{g.mode}）")
            print(f"      {g.note}")
        print(f"\n【门禁外 NOT_GATED（{len(NOT_GATED)} 个；含「要接进来还差什么」）】")
        for n, why in NOT_GATED.items():
            print(f"  · {n:<40} {why}")
        print(f"\n【已在 CI、但**不在** `evidence` job —— CI_ELSEWHERE（{len(CI_ELSEWHERE)} 个）】")
        print("   ⚠️ 这一档**不是**「没接进 CI」：它们由**别的 job** 跑，所以**不能**进 `GATED`")
        print("      —— `GATED` 是 `evidence` job 跑的那一档，而那里**没有前端产物**。")
        for n, why in CI_ELSEWHERE.items():
            print(f"  ✓ {n:<40} {why}")
        # 2026-09-24（Phase 2）：把「**真判据**由哪个 job 跑」也印出来 ——
        # 这一档与上面的 `SELFTEST_JOB_PROBES`（自检）**必须分开看**：
        # 「自检跑了」证明不了「真判据跑了」，而那正是坑 65 的形状。
        print(f"\n【「真判据」由哪个 job 跑 —— CI_JOB_PROBES（{len(CI_JOB_PROBES)} 个 job）】")
        print("   ⚠️ 与下面【探针自检登记】里的 `SELFTEST_JOB_PROBES` **不是同一批**：")
        print("      这里列的是**探针本体**（真页面 / 真后端 / 真种子库），那里列的是 `--self-test`。")
        for j, names in CI_JOB_PROBES.items():
            print(f"  ▶ {j}（{len(names)} 条）")
            for frag in JOB_ENTRYPOINTS.get(j, ()):
                print(f"      入口：{frag}")
            for n in names:
                print(f"      · {n}")
        print(f"  ⚠️ 判据 6 逐字核对上表与 `ci.yml`（{CI_YML.name}）—— "
              "移走一步 YAML 会让它红，而**判据 5 不会**。")
        print(f"\n✅ 上面 {len(GATED_STATIC)} 个**已接进 CI**（阶段 3 棘轮：欠账再也无法悄悄变大）；"
              f"另有 {len(CI_ELSEWHERE)} 个由**别的 job** 跑。")
        if NOT_GATED:
            print(f"🚨 **仍有 {len(NOT_GATED)} 个探针在 CI 里从不执行**"
                  "（`ci.yml` 里**没有任何 job** 碰它们）。")
            print(f"   这 {len(NOT_GATED)} 个缺的是**环境前提**（浏览器 / dev server / 生产产物），"
                  "不是「不重要」—— 一条从不执行的门禁与没有门禁在效果上无法区分。")
            print("   明细：`evidence/ci_coverage_audit_2026-09-22.txt`")
        else:
            # ✅ 2026-09-26：`browser-all` job 落地后本档**清空**。
            #    ⚠️ 打印「0 个」而不是整段省掉 —— 「这一档现在是空的」本身是**结论**，
            #    下一个人需要知道它是**被清空的**，而不是**从来没写过**。
            print("✅ **`NOT_GATED` 已清空（0 个）** —— 仓库里每一条门禁都至少有一个 job 会跑它。"
                  "（2026-09-26 · 拍板 §11.4-③，`browser-all` job 落地。）")
        # 页脚：把上面这段「从不执行」**量化到具体是哪些规范小节**。
        # **只报告不判定**（详见 `ci_gap_footer()` 的 docstring）；
        # 刻意排在 `static_gate_guard()` 之前 —— 守卫报警是本步**唯一的**判定输出，
        # 让它紧贴 `return 0`，不与报表正文混在一起。
        ci_gap_footer()
        problems = static_gate_guard()
        if problems:
            print("\n⚠️ **登记守卫报警**（`STATIC_GATES` 已与源码不符）：")
            for prob in problems:
                print(f"     · {prob}")
        print(f"\n【非门禁脚本：{len(NON_GATE_SCRIPTS)} 个已表态（判据 0b 守卫）】")
        print("   库 / 工具 / 一次性诊断 —— **不是门禁，不该跑进 CI**。")
        print("   ⚠️ 判据 0 只按 `verify_*.py` 守 ⇒ 新增的 `.py` **必须在这里表态**，")
        print("      否则它可能是「一个不叫 verify_ 的真门禁」，会静默逃过（README 坑 46）。")

        # ── 探针自检登记（判据 0c 守卫）────────────────────────────────
        # 为什么必须印出来：`SELFTEST_ENV_BOUND` 里的 6 个**自检从不执行**，
        # 这是**有意识的**取舍 —— 但「有意识」只存在于代码注释里时，
        # 半年后没人知道它是「有意跳过」还是「忘了登记」。⇒ 让它在 `--list` 里可见。
        st_total = (len(SELFTESTABLE) + len(SELFTEST_ONLY) + len(SELFTEST_ENV_BOUND)
                    + len(SELFTEST_REPO_BOUND) + len(SELFTEST_ELSEWHERE))
        print(f"\n【探针自检登记：{st_total} 个带 `--self-test` 的脚本全部表态（判据 0c 守卫）】")
        print(f"  ✓ SELFTESTABLE（{len(SELFTESTABLE)}）—— 自检是纯函数，**阶段 1 跑**")
        print(f"  ✓ SELFTEST_ONLY（{len(SELFTEST_ONLY)}）—— 测不了（浏览器/服务），"
              "但工具可自检，**阶段 1 跑**")
        print(f"  ⏭ SELFTEST_ENV_BOUND（{len(SELFTEST_ENV_BOUND)}）—— 自检**依赖环境**，"
              "阶段 1 给不出结论（**这不是「不重要」**）")
        for n, why in SELFTEST_ENV_BOUND.items():
            print(f"      · {n:<34} {why}")
        print(f"  ✓ SELFTEST_REPO_BOUND（{len(SELFTEST_REPO_BOUND)}）—— 自检**读真实仓库**："
              "**照跑**，但红要读成「登记表落后」而不是「工具坏了」")
        for n, why in SELFTEST_REPO_BOUND.items():
            print(f"      · {n:<34} {why}")
        print(f"  ↷ SELFTEST_ELSEWHERE（{len(SELFTEST_ELSEWHERE)}）—— **不由运行器跑**"
              "（由别处跑 / 只能手工跑，每条都写明在哪跑）")
        for n, why in SELFTEST_ELSEWHERE.items():
            print(f"      · {n:<34} {why}")
        print("  ⚠️ 判据 0c 扫 `glob(\"*.py\")`（2026-09-23 从 `verify_*.py` **扩宽**）——")
        print("     新增的、带 `--self-test` 的脚本必须在上面五档之一表态，否则它**从不执行**。")
        return 0

    if args.self_test:
        return run_self_test()

    if args.static_audit:
        return run_static_audit()

    return run_all()


if __name__ == "__main__":
    sys.exit(main())

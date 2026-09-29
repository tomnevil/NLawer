#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_cn_render_delta.py —— #42 修法 C 的**渲染级**取证：`cn()` 吞字号到底改多少。

════════════════════════════════════════════════════════════════════════
这个脚本**不是门禁**：**只测量、不判定、恒 exit 0**（已登记 `NON_GATE_SCRIPTS`）。

它回答一个问题（修法 C 的「渲染基线」里**不依赖数据库的那一半**）：

    `cn()` 吞掉自定义字号类之后，元素**实际**渲染成几 px？
    修好之后又会渲染成几 px？差多少？

════════════════════════════════════════════════════════════════════════
为什么不能在源码层推：

  `verify_typography.py` 的 T2b 只能证明「**类被吞了**」。
  「**吞了之后渲染成几 px**」取决于**继承上下文** ——
  若最近的祖先已经设了字号（比如祖先自己带 `text-body-sm` = 13px），
  元素就继承 13px；若一路到 `body`，则是 **14px**（实测产物 CSS）。
  ⇒ **同一个 token，不同位置的渲染结果不同** ⇒ 必须**实测**，静态推不出来。

════════════════════════════════════════════════════════════════════════
方法（**不碰产品代码、不依赖数据库**）：

  1. 真 Chromium + CDP（复用 `evidence/cdp.py`）。
  2. 打开**免登录**页面（默认 `/login`；`/components-preview` 是组件预览页，也免登录）
     ⇒ 只要该页加载了**应用自己的全局 CSS**，量出来的就是真实生效的样式。
  3. 在该页 `document.body` 下临时插入两族 `<span>`，各读 `getComputedStyle().fontSize`：
       · **A「今天」** = `class="text-ink-900"`
         —— 这正是**默认 twMerge 的输出**（字号被吞、只剩颜色）⇒ 量的是**回退值**
       · **B「修法后」** = `class="text-<档> text-ink-900"`
         —— 修法 A 之后浏览器会收到的类 ⇒ 量的是**声明值**
  4. 逐个档打印 A / B / 差值；并打印 `body` 的计算字号（= 最外层回退值）。
  5. 用完即删插入的节点（`host.remove()`），**不改页面任何持久状态**。

⚠️ **这是「探针夹具」而不是「真实站点」**：它量的是**每个档在 body 上下文里的回退值**，
不是那 87 处各自位置上的值。要量后者需要**登录 + 真实页面 + 稳定数据集**，
那正是「浏览器 job」（#59）要建的地基 —— 本脚本刻意避开它，
以便在**并行会话正在改开发库**的时候也能拿到证据（见裁定书第 6 节的边界说明）。

════════════════════════════════════════════════════════════════════════
用法（**必须从仓库根、用后端 venv 的 python 跑**）：

    cd <仓库根>
    backend/.venv/Scripts/python.exe evidence/probe_cn_render_delta.py
    backend/.venv/Scripts/python.exe evidence/probe_cn_render_delta.py --url http://localhost:3001/login
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cdp import Browser  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# 与 `frontend/tailwind.preset.ts` 的**语义字号档**保持一致（10 个 = 17 档减去
# twMerge 默认就认识的 7 个存量档 `xs/sm/base/lg/xl/2xl/3xl`）。
# ⚠️ 2026-09-24：`verify_typography.py` 里原先的同名清单 `CUSTOM_SIZES` **已删除**
#    （判据改为**从 preset 派生**并实跑生存探针，见其 T2c）。本文件是**一次性探针**，
#    保留清单以便复现当时证据；**新增语义档时这里需手工跟上**（判据侧已不需要）。
TOKENS = ["caption", "label", "body-sm", "body", "body-lg",
          "h4", "h3", "h2", "h1", "display"]

# ⚠️ 修法 A 的目标语义：`text-<档>` 应归 **font-size** 组，`text-<颜色>` 归 **text-color** 组。
#    所以「今天」那一族只写颜色类 = 默认 twMerge 的输出。
JS = """
(() => {
  const tokens = %s;
  const host = document.createElement("div");
  host.setAttribute("data-cn-probe", "1");
  document.body.appendChild(host);
  const read = (el) => {
    const cs = getComputedStyle(el);
    return { fs: cs.fontSize, lh: cs.lineHeight };
  };
  const mk = (cls, text) => {
    const s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    host.appendChild(s);
    return s;
  };
  const rows = [];
  for (const t of tokens) {
    const a = read(mk("text-ink-900", "Ag"));
    const b = read(mk("text-" + t + " text-ink-900", "Ag"));
    rows.push({ token: t, today: a, fixed: b });
  }
  const body = read(document.body);
  host.remove();
  return JSON.stringify({ body, rows, href: location.href });
})()
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description="`cn()` 吞字号：渲染级差值实测（只测量）")
    ap.add_argument("--url", default="http://localhost:3000/login",
                    help="免登录页面（默认 web 端 /login）")
    ap.add_argument("--mobile", action="store_true", help="用手机视口（默认桌面）")
    args = ap.parse_args()

    print("=" * 84)
    print("probe_cn_render_delta.py —— `cn()` 吞字号：**渲染级**差值实测（只测量）")
    print("=" * 84)
    print(f"页面：{args.url}")
    print()

    w, h = (390, 844) if args.mobile else (1440, 900)
    async with Browser(headless=True, width=w, height=h, device_scale_factor=1) as b:
        await b.goto(args.url, wait=2.5)
        raw = await b.cdp.evaluate(JS % json.dumps(TOKENS))

    data = json.loads(raw)
    print(f"实际加载：{data['href']}")
    print(f"body 计算字号：{data['body']['fs']}（行高 {data['body']['lh']}）")
    print()
    print(f"  {'档':<10} {'今天（类被吞）':>16} {'修法后（类生效）':>18} {'字号差':>10} {'行高':>22}")
    print("  " + "-" * 80)
    for r in data["rows"]:
        t, a, f = r["token"], r["today"], r["fixed"]
        print(f"  {t:<10} {a['fs']:>16} {f['fs']:>18} "
              f"{'—' if a['fs'] == f['fs'] else '**变了**':>10} "
              f"{a['lh'] + ' → ' + f['lh']:>22}")
    print()
    print("── 读数须知 ──")
    print("  · 左列是**回退值**（继承上下文决定），右列是**声明值**（设计令牌决定）。")
    print("  · 两者不等 ⇒ 该档被 `cn()` 吞掉的**每一个位置**都会发生这个字号变化。")
    print("  · ⚠️ 真实站点上左列可能**不是** body 的值：祖先若已设字号，回退值就是祖先的值")
    print("    ⇒ 本表给的是**上界/下界式的参考**，不是那 87 处的逐点实测（见脚本头注释）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

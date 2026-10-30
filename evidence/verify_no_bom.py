#!/usr/bin/env python
"""UTF-8 BOM 门禁：扫描仓库内所有 `*.py` 的首字节，禁止 UTF-8 BOM（U+FEFF）。

## 为什么要这条判据

`U+FEFF` 作为**文件首字节**的 UTF-8 编码（`ef bb bf`）会被 Python 的 `ast.parse` /
`open(..., encoding="utf-8")` 当成**非法字符**（或静默改变语义），进而让**依赖源码解析的
门禁**（本项目 7 个 BOM 文件曾导致多条 `verify_*.py` 判据**静默失明或误报产品缺陷**）。
BOM 在 Python 源码里**没有合法用途**（Python 3 默认 UTF-8，PEP 263 走 `# -*- coding: ... -*-`）。
⇒ 根治：任何 `*.py` 的首字节都不得是 BOM —— 一次接进 CI，今后有人误加 BOM 立刻红。

## 判据

- 扫描仓库内所有 `.py`（排除 `.git` / `node_modules` / `.venv` / `.next` / `dist` / `__pycache__`）。
- 每个文件读**首 3 字节**；若 == `b"\xef\xbb\xbf"` ⇒ 判红（逐文件点名，附相对路径）。
- **只查首字节**：文件**中间**出现的 `U+FEFF`（合法零宽不换行空格）不判 —— 那不是 BOM。
- 退出码：`0` 通过 / `1` 有 BOM（产品缺陷）/ `2` 环境（连仓库根都走不到 ⇒ 不判）。

用法：

    python evidence/verify_no_bom.py
    python evidence/verify_no_bom.py --self-test    # 合成字节串自检（判定函数对不对）
    python evidence/verify_no_bom.py --inject       # 真实仓库字节上的注入反证（它在这个仓库上会红）
    python evidence/verify_no_bom.py --report-only  # 只打印，不判红

## 自检纪律（与本仓其它门禁同）

`--self-test` 用**合成字节串**（带 BOM / 不带 / 中间夹 U+FEFF / 空 / 多字节），证明 `has_bom`
判定函数对不对。`--inject` 把**真实仓库里某个 .py** 的字节前插 BOM，在**系统 temp** 落一个带
BOM 的副本并扫描那个 temp 目录，证明「**在这个仓库上**会红」，再删掉副本（**不碰产品文件**）。
少了后者，「判据写错了、而夹具恰好也错了」这类**同向错误**永远看不出来。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

BOM = b"\xef\xbb\xbf"
SKIP_DIR = {".git", "node_modules", ".venv", ".next", "dist", "__pycache__"}

RX_PY = re.compile(r"\.py$")


def has_bom(data: bytes) -> bool:
    """首 3 字节是 UTF-8 BOM ⇒ True。中间出现的 U+FEFF 不算（那不是 BOM）。"""
    return data[:3] == BOM


def iter_py_files(root: pathlib.Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not d.startswith(".next")]
        for fn in filenames:
            if RX_PY.search(fn):
                yield pathlib.Path(dirpath) / fn


def scan_root(root: pathlib.Path) -> list[str]:
    """返回所有带 BOM 的 .py 的相对路径（相对于 root）。"""
    bad: list[str] = []
    for p in iter_py_files(root):
        try:
            with open(p, "rb") as fh:
                head = fh.read(3)
        except OSError:
            continue
        if has_bom(head):
            bad.append(p.relative_to(root).as_posix())
    return bad


# ------------------------------------------------------------------ 自检

#: (臂名, 说明, 字节内容, 期望 has_bom)。全部用**合成字节串**，不读真实仓库、不启浏览器。
SELFTEST: tuple[tuple[str, str, bytes, bool], ...] = (
    ("Q1", "首字节是 BOM ⇒ 判红", BOM + b"x = 1\n", True),
    ("Q2", "干净对照：无 BOM ⇒ 不判", b"x = 1\n", False),
    ("Q3", "首字节是普通多字节 UTF-8（é 不在首位）⇒ 不判",
     "x = 'caf\u00e9'\n".encode("utf-8"), False),
    ("Q4", "中间夹 U+FEFF（合法零宽）⇒ 不判（只查首字节）",
     "a\uFEFFb\n".encode("utf-8"), False),
    ("Q5", "空文件 ⇒ 不判", b"", False),
    ("Q6", "首字节是 BOM、内容是多字节中文 ⇒ 仍判红",
     BOM + "\u4e2d\u6587 = 1\n".encode("utf-8"), True),
)


def run_self_test() -> int:
    print("自检（合成字节串，与产品无关）—— 各臂分开报：")
    bad: list[str] = []
    for key, desc, data, expect in SELFTEST:
        got = has_bom(data)
        ok = got == expect
        print(f"  [{'✓' if ok else '✗'}] {key} {desc} —— 期望 {expect} 实测 {got}")
        if not ok:
            bad.append(f"[{key}] 期望 {expect} 实测 {got}")
    # 前提锁：has_bom 对 BOM 必须 True（否则「解析不到」会被读成「没有 BOM」= 假绿）
    if not has_bom(BOM + b"x"):
        bad.append("[Q0] 对 BOM 字节竟返回 False ⇒ 判定函数失效")
    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print("\n自检通过。")
    return 0


# ------------------------------------------------------------------ 注入反证

#: 用本文件自身当真实样本（它肯定无 BOM）—— 证明「在本仓库字节上会红」。
INJECT_TARGET = "evidence/verify_no_bom.py"


def run_injection() -> int:
    src = ROOT / INJECT_TARGET
    if not src.exists():
        print("[env] 注入样本文件不存在 ⇒ 不判", file=sys.stderr)
        return 2
    raw = src.read_bytes()
    if has_bom(raw):
        print("[env] 样本自身竟有 BOM ⇒ 环境异常", file=sys.stderr)
        return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bom_inject_"))
    try:
        # 在**系统 temp** 落一个带 BOM 的副本（**不碰产品文件**），扫描那个目录 ⇒ 必须抓到 1 个。
        bommed = tmp / "sample.py"
        bommed.write_bytes(BOM + raw)
        (tmp / "clean.py").write_bytes(b"x = 1\n")
        found = scan_root(tmp)
        ok = found == ["sample.py"]
        print(f"  注入：temp 目录放 1 个带 BOM 副本 + 1 个干净 ⇒ 期望抓到 ['sample.py'] 实测 {found}")
        # 反向对照：同内容去掉 BOM 的副本 ⇒ 不被抓（证明不会误伤无 BOM 文件）。
        (tmp / "sample_clean.py").write_bytes(raw)
        found2 = scan_root(tmp)
        ok2 = "sample_clean.py" not in found2
        print(f"  反向对照：同内容去 BOM 副本 ⇒ 期望不被抓 实测 {found2}")
        if ok and ok2:
            print("\n注入反证通过（证明在本仓库字节上会红，且不会误伤无 BOM 文件）。")
            return 0
        print("\n注入反证失败 ⇒ **判定在这个仓库上没咬合**（exit 2）", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description="UTF-8 BOM 门禁：扫描仓库内所有 *.py 首字节，禁止 BOM")
    ap.add_argument("--self-test", action="store_true", help="合成字节串自检（证明判定函数对）")
    ap.add_argument("--inject", action="store_true", help="真实仓库字节上的注入反证（证明它在这个仓库上会红）")
    ap.add_argument("--report-only", action="store_true", help="只打印，不判红")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if args.inject:
        return run_injection()

    if not ROOT.exists():
        print("[env] 仓库根走不到 ⇒ 不判产品", file=sys.stderr)
        return 2
    bad = scan_root(ROOT)
    print(f"扫描仓库内 *.py（排除 {sorted(SKIP_DIR)}）：发现 **{len(bad)}** 个带 UTF-8 BOM 的文件")
    if bad:
        if not args.report_only:
            for rel in bad:
                print(f"  ✗ {rel}  —— 首字节为 UTF-8 BOM（U+FEFF），会让 ast.parse / open(encoding='utf-8') 失效")
            print("\n提示：用编辑器把文件存成「UTF-8 无 BOM」即可；Python 3 默认 UTF-8，BOM 无合法用途。")
            return 1
    print("✅ 无 UTF-8 BOM：所有 .py 首字节干净。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

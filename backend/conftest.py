"""pytest 全局配置：将 backend 目录加入 sys.path，使 `app` 包可被导入。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

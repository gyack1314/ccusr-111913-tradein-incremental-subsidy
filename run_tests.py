#!/usr/bin/env python3
"""隔离模式（python -I）测试入口：运行 tests/ 下全部测试。

用法：python3 -I run_tests.py
显式插入项目根目录，避免 -I 下找不到 audit/support 包，也不依赖 CWD。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

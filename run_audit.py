#!/usr/bin/env python3
"""命令行启动器：以隔离模式（python -I）运行 audit 应用。

用法：python3 -I run_audit.py analyze
独立脚本而非模块入口，确保 -I 下仍能定位项目根目录，且不从 CWD 隐式导入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit.cli import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])

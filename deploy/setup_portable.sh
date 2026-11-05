#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"
python_bin="${PYTHON_BIN:-python3.12}"
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3, 12), "此安装脚本使用 Python 3.12；其他版本请参照迁移说明"'
"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -c requirements-portable-py312.txt -e '.[conversion,training,preview,annotation,test]'
.venv/bin/python -m pip check
printf '%s\n' '环境安装完成。未启动 GUI、后台服务、上传或训练。请阅读 迁移运行说明.md。'

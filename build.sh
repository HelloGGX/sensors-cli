#!/usr/bin/env bash
# 构建 sensors 单文件可执行文件(PyInstaller,目标机器无需安装 Python)
# 用法: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"

# macOS 沙盒会阻止写入默认缓存目录 ~/Library/Application Support/pyinstaller,
# 重定向到项目内 build/ 下(已在 .gitignore 中)
export PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-cache"

echo "==> PyInstaller 构建 dist/sensors ..."
uv run pyinstaller \
  --onefile \
  --name sensors \
  --collect-all sensors \
  --collect-all pydantic \
  --collect-all pydantic_core \
  --clean \
  --noconfirm \
  sensors/cli.py

echo
echo "==> 构建完成"
ls -lh dist/sensors
file dist/sensors

echo
echo "==> 冒烟测试"
./dist/sensors --help >/dev/null && echo "OK: --help 正常"

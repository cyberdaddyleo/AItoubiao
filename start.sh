#!/usr/bin/env bash
# 本机一键启动（macOS / Linux）。需要 Python 3.10 及以上。
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "没有找到 python3，请先安装 Python 3.10 及以上版本"; exit 1
fi

if [ ! -d .venv ]; then
  echo "首次运行：创建虚拟环境并安装依赖…"
  "$PY" -m venv .venv
  # 国内网络慢可以设置：export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

if [ "$1" = "--demo" ]; then
  echo "写入演示数据…"
  .venv/bin/python -m app.seed_demo
fi

echo "启动中：浏览器打开 http://localhost:${BID_PORT:-8600}"
echo "首次登录账号 admin，密码见 data/初始管理员密码.txt"
exec .venv/bin/python -m app.main

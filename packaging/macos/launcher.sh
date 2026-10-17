#!/bin/bash
# AI投标系统 · macOS 启动器（兼容系统自带 bash 3.2）
#
# 双击应用时：
#   1. 第一次运行（或升级后）把内置运行环境复制到「~/Library/Application Support/AI投标系统」；
#   2. 在后台启动服务（只监听本机），等它就绪后用一次性令牌自动登录并打开浏览器；
#   3. 如果已经在运行，弹出「打开系统 / 打开数据文件夹 / 停止运行」。
# 关闭浏览器不会停止后台，定时采集照常进行。

export PATH="${BID_TEST_PATH:+$BID_TEST_PATH:}/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="en_US.UTF-8"

APP_TITLE="AI投标系统"
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
RES="$CONTENTS/Resources"
BUNDLED="$RES/runtime"
VERSION="$(cat "$RES/VERSION" 2>/dev/null || echo dev)"
SUPPORT="$HOME/Library/Application Support/AI投标系统"
RUNTIME="$SUPPORT/runtime"
DATA="$SUPPORT/data"
LOGDIR="$SUPPORT/logs"
LOG="$LOGDIR/server.log"
PIDFILE="$SUPPORT/server.pid"
PORTFILE="$SUPPORT/server.port"

mkdir -p "$DATA" "$LOGDIR"

# ---------------------------------------------------------------- 对话框
clean_text() {
  # AppleScript 字符串里不能有双引号和反斜杠
  printf '%s' "$1" | tr -d '"\\' | tr '\n' ' ' | cut -c1-600
}

notify() {
  osascript -e "display notification \"$(clean_text "$1")\" with title \"$APP_TITLE\"" >/dev/null 2>&1
}

# ask "消息" "按钮1" "按钮2" ... ；最后一个按钮为默认按钮；输出用户点的按钮
ask() {
  local msg list b last
  msg="$(clean_text "$1")"
  shift
  list=""
  for b in "$@"; do
    list="$list, \"$b\""
    last="$b"
  done
  list="{${list#, }}"
  osascript -e "activate" -e "button returned of (display dialog \"$msg\" with title \"$APP_TITLE\" buttons $list default button \"$last\" with icon note)" 2>/dev/null
}

fail() {
  osascript -e "activate" -e "display alert \"$APP_TITLE 启动失败\" message \"$(clean_text "$1")\" as critical" >/dev/null 2>&1
}

# ---------------------------------------------------------------- 服务状态
server_pid() { cat "$PIDFILE" 2>/dev/null; }

running_port() {
  local pid port
  pid="$(server_pid)"
  port="$(cat "$PORTFILE" 2>/dev/null)"
  [ -n "$pid" ] && [ -n "$port" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  curl -fsS -m 2 "http://127.0.0.1:$port/api/health" 2>/dev/null | grep -q '"desktop":true' || return 1
  echo "$port"
}

server_version() {
  curl -fsS -m 2 "http://127.0.0.1:$1/api/health" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p'
}

stop_server() {
  local pid i
  pid="$(server_pid)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    i=0
    while [ $i -lt 25 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.2
      i=$((i + 1))
    done
    kill -9 "$pid" 2>/dev/null
  fi
  rm -f "$PIDFILE" "$PORTFILE"
}

open_ui() {
  local token
  token="$(cat "$DATA/desktop.token" 2>/dev/null)"
  open "http://127.0.0.1:$1/api/auth/desktop?token=$token"
}

pick_port() {
  local p=8600
  while [ $p -le 8630 ]; do
    if ! nc -z 127.0.0.1 $p >/dev/null 2>&1; then
      echo $p
      return 0
    fi
    p=$((p + 1))
  done
  return 1
}

# ---------------------------------------------------------------- 运行环境
install_runtime() {
  if [ -f "$RUNTIME/.version" ] && [ "$(cat "$RUNTIME/.version")" = "$VERSION" ]; then
    return 0
  fi
  notify "正在准备运行环境（只需一次，约 10~30 秒）…"
  rm -rf "$RUNTIME.new"
  # --noqtn：复制时不带「从网上下载」的隔离标记
  if ! ditto --noqtn "$BUNDLED" "$RUNTIME.new" >>"$LOG" 2>&1; then
    fail "复制运行环境失败，请确认磁盘空间充足（需要约 300MB）。日志：$LOG"
    exit 1
  fi
  xattr -dr com.apple.quarantine "$RUNTIME.new" >/dev/null 2>&1
  rm -rf "$RUNTIME"
  mv "$RUNTIME.new" "$RUNTIME"
  echo "$VERSION" >"$RUNTIME/.version"
}

run_python() {
  (
    cd "$RUNTIME/system" || exit 1
    BID_DESKTOP=1 BID_DATA_DIR="$DATA" PYTHONPATH="$RUNTIME/site" PYTHONNOUSERSITE=1 PYTHONUTF8=1 \
      "$RUNTIME/python/bin/python3" "$@"
  )
}

start_server() {
  local port="$1" i pid
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 启动 v$VERSION 端口 $port" >>"$LOG"
  (
    cd "$RUNTIME/system" || exit 1
    BID_DESKTOP=1 BID_PORT="$port" BID_DATA_DIR="$DATA" PYTHONPATH="$RUNTIME/site" PYTHONNOUSERSITE=1 PYTHONUTF8=1 \
      nohup "$RUNTIME/python/bin/python3" -m app.main >>"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
  )
  echo "$port" >"$PORTFILE"
  pid="$(server_pid)"
  i=0
  while [ $i -lt 120 ]; do
    if curl -fsS -m 1 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

# ---------------------------------------------------------------- 主流程
PORT="$(running_port)"
if [ -n "$PORT" ]; then
  if [ "$(server_version "$PORT")" != "$VERSION" ]; then
    # 应用升级了：停掉旧版本，下面重新启动新版本
    stop_server
  else
    CHOICE="$(ask "AI投标系统正在运行（关闭浏览器不会停止，定时采集照常进行）。" "停止运行" "打开数据文件夹" "打开系统")"
    case "$CHOICE" in
      "停止运行")
        stop_server
        notify "已停止运行。再次双击即可重新打开。"
        ;;
      "打开数据文件夹")
        open "$SUPPORT"
        ;;
      "打开系统")
        open_ui "$PORT"
        ;;
    esac
    exit 0
  fi
fi

# 有残留进程但不响应，先清理
if [ -n "$(server_pid)" ]; then
  stop_server
fi

install_runtime

if [ ! -f "$SUPPORT/.initialized" ]; then
  CHOICE="$(ask "欢迎使用 AI投标系统！第一次使用，要先载入一套演示数据看看完整效果吗？（虚构的示例公司、资料库、真实公告样本和一个进行中的投标项目，正式使用前可以在「主体与订阅」里删除）" "空白开始" "载入演示数据")"
  if [ "$CHOICE" = "载入演示数据" ]; then
    notify "正在载入演示数据…"
    run_python -m app.seed_demo >>"$LOG" 2>&1
  fi
  touch "$SUPPORT/.initialized"
fi

PORT="$(pick_port)"
if [ -z "$PORT" ]; then
  fail "本机 8600~8630 端口都被占用，无法启动。"
  exit 1
fi

notify "正在启动…"
if start_server "$PORT"; then
  open_ui "$PORT"
  notify "已启动。关闭浏览器不会停止后台；再次双击图标可以停止。"
else
  TAIL="$(tail -n 8 "$LOG" 2>/dev/null)"
  stop_server
  fail "后台服务没有正常启动。日志在「$LOG」。最后几行：$TAIL"
  open -R "$LOG" 2>/dev/null
  exit 1
fi
exit 0

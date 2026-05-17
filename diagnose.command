#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/usr/bin/python3"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

echo "Claude Desktop 中文补丁诊断"
echo "目录: $DIR"
echo
echo "这个脚本只检查当前 /Applications/Claude.app 和本机 Claude 配置。"
echo "它不会安装补丁，不会替换 Claude.app，也不会修改 API、网关或模型配置。"
echo

USER_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  USER_HOME="/Users/$SUDO_USER"
fi

"$PYTHON" "$DIR/patch_claude_zh_cn.py" --diagnose --app /Applications/Claude.app --user-home "$USER_HOME" "$@"
STATUS=$?

echo
echo "诊断日志已生成："
echo "$DIR/Logs/latest.json"
echo
echo "如果要发给我，请打开这个文件夹，把 latest.json 拖到对话里："
echo "$DIR/Logs"
echo
echo "按回车退出。"
read -r _
exit "$STATUS"

#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/usr/bin/python3"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

echo "Claude Desktop 官方原版安装准备"
echo "目录: $DIR"
echo
echo "这个脚本会先退出 Claude，并解除当前 /Applications/Claude.app 的锁定、权限和扩展属性。"
echo "它不会删除或移动 Claude.app，也不会修改 API、网关或模型配置。"
echo "完成后，可以从官方 DMG 把 Claude.app 拖到 Applications 并选择替换。"
echo

if [ "$(id -u)" -ne 0 ]; then
  echo "需要管理员权限来准备 /Applications/Claude.app。"
  echo "请按提示输入这台 Mac 的登录密码。"
  echo
  sudo "$PYTHON" "$DIR/patch_claude_zh_cn.py" --user-home "$HOME" --prepare-official-update "$@"
  STATUS=$?
  echo
  echo "按回车退出。"
  read -r _
  exit "$STATUS"
fi

USER_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  USER_HOME="/Users/$SUDO_USER"
fi

"$PYTHON" "$DIR/patch_claude_zh_cn.py" --user-home "$USER_HOME" --prepare-official-update "$@"

echo
echo "完成。现在可以从官方 DMG 覆盖安装 Claude.app。按回车退出。"
read -r _

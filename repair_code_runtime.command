#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "修复 Claude Code 运行时配置..."
echo "这会退出 Claude，终止旧 Code 子进程，并把当前第三方推理配置同步到 ~/.claude/settings.json。"
echo "不会修改 /Applications/Claude.app，也不会替换应用。"
echo

/usr/bin/python3 patch_claude_zh_cn.py --repair-code-runtime --app /Applications/Claude.app --user-home "$HOME"

echo
echo "修复日志：$SCRIPT_DIR/Logs/latest.json"
echo "完成后请重新打开 Claude，再进入 Code 模式测试。"
echo
read -r -p "按回车关闭窗口..."

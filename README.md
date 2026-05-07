# Claude Desktop 中文补丁

这是一个用于 macOS 版 Claude Desktop 的本地中文补丁。它会把中文语言包、原生菜单翻译和部分硬编码界面文本写入本机的 `Claude.app`，并重新签名应用，让 Claude Desktop 可以以中文界面运行。

本项目只修改本机应用资源文件，不包含 Claude Desktop 本体，也不提供账号、API 或网关服务。

## 适用环境

- macOS
- 已安装 Claude Desktop，路径为 `/Applications/Claude.app`
- 系统自带 Python 3，通常为 `/usr/bin/python3`
- 当前用户有管理员密码，可以授权替换 `/Applications/Claude.app`

## 主要功能

- 安装 Claude Desktop 前端中文语言包。
- 安装 macOS 原生菜单中文资源。
- 翻译部分前端硬编码文本，包括设置页、菜单项、筛选项、模型选择项等。
- 自动写入用户配置，将 Claude 语言设置为 `zh-CN`。
- 自动写入应用语言偏好，优先使用中文。
- 安装前备份原始 Claude.app。
- 修改后对 Claude.app 及内部组件做一致的本机 ad-hoc 重签名。
- 清除 `com.apple.quarantine` 隔离属性，减少“应用损坏”类提示。
- 兼容第三方网关：保留 Claude Code 默认模型 `opus[1m]`，并在前端模型识别层兼容第三方网关返回的模型列表。

## 安装方法

1. 退出 Claude Desktop。
2. 下载或复制本项目文件夹到本机。
3. 双击 `install.command`。
4. 按提示输入 Mac 登录密码。
5. 等待脚本完成，Claude 会自动重新打开。

如果双击无法运行，可以在终端执行：

```bash
cd /path/to/Claude-desk-cn
chmod +x install.command
./install.command
```

也可以直接运行 Python 脚本：

```bash
cd /path/to/Claude-desk-cn
sudo /usr/bin/python3 patch_claude_zh_cn.py --user-home "$HOME" --launch
```

## 复制到其他电脑使用

复制整个项目文件夹即可。至少需要保留：

- `install.command`
- `patch_claude_zh_cn.py`
- `resources/`
- `README.md`

目标电脑需要先安装官方 Claude Desktop 到 `/Applications/Claude.app`。复制后双击 `install.command` 即可安装。

## 文件说明

- `install.command`：Mac 双击安装入口。
- `patch_claude_zh_cn.py`：执行补丁、备份、重签名和验证的主脚本。
- `docs/implementation.md`：当前功能、实现原理和维护逻辑说明。
- `resources/frontend-zh-CN.json`：前端界面中文翻译。
- `resources/desktop-zh-CN.json`：桌面壳层中文翻译。
- `resources/Localizable.strings`：macOS 原生菜单中文资源。
- `resources/statsig-zh-CN.json`：Statsig 文案兜底资源。
- `resources/manifest.json`：语言包信息。

## 脚本执行流程

安装时脚本会执行以下操作：

1. 检查 `/Applications/Claude.app` 是否存在。
2. 检查 Claude.app 是否保留虚拟化权限。
3. 退出正在运行的 Claude。
4. 将 Claude.app 复制到临时目录。
5. 给前端语言白名单加入 `zh-CN`。
6. 合并 `en-US.json` 与本项目中文语言包。
7. 安装原生菜单语言资源。
8. 替换部分硬编码英文文案。
9. 为第三方网关补充 `opus[1m]` 默认模型识别兼容，不把默认值改写成第三方模型名。
10. 重新签名临时 Claude.app。
11. 备份原始 Claude.app。
12. 将补丁后的 Claude.app 安装回 `/Applications/Claude.app`。
13. 写入用户语言配置并启动 Claude。

## 验证命令

只验证补丁流程，不替换当前 Claude.app：

```bash
sudo /usr/bin/python3 patch_claude_zh_cn.py --user-home "$HOME" --dry-run
```

验证资源文件：

```bash
python3 -m json.tool resources/frontend-zh-CN.json >/dev/null
python3 -m json.tool resources/desktop-zh-CN.json >/dev/null
python3 -m json.tool resources/statsig-zh-CN.json >/dev/null
python3 -m json.tool resources/manifest.json >/dev/null
plutil -lint resources/Localizable.strings
```

## 恢复原版

脚本安装前会在 `/Applications` 下生成备份，名称类似：

```text
Claude.backup-before-zh-CN-20260507-120000.app
```

脚本会自动只保留最新一个中文补丁备份，旧备份会移入当前用户废纸篓，避免 `/Applications` 里堆积多个 Claude 副本。

恢复步骤：

1. 退出 Claude Desktop。
2. 删除或移走当前 `/Applications/Claude.app`。
3. 将备份 app 改名为 `Claude.app`。
4. 放回 `/Applications/Claude.app`。

## 常见问题

### Claude 更新后中文消失

Claude Desktop 更新会覆盖应用资源。重新运行 `install.command` 即可。

### macOS 提示应用损坏或无法验证

先重新运行 `install.command`。脚本会重新签名应用并清除隔离属性。不要只手动执行单条 `codesign --deep`，因为 Claude.app 内部还有 helper app、framework、`.node` 原生模块和动态库，签名顺序不一致可能导致启动失败。

### 仍有少量英文

说明该位置是新增文案、硬编码文案或远端动态内容。可以把截图发出来，根据英文原文补充到 `resources/frontend-zh-CN.json` 或硬编码替换表中。

### 默认模型提示无法识别

如果使用第三方网关，同时 Claude Code 设置里保留 `opus[1m]`，本项目会在前端模型识别层把 `opus[1m]` 视为有效默认模型，即使网关模型列表只返回 Kimi、DeepSeek 或其他模型名。界面和本地设置仍保留 `opus[1m]`，不会写成第三方模型名。

模型菜单会固定保留 `Opus 4.7 1M` 作为第一项，并继承现有模型的强度/思考能力元数据。即使你手动切到 `Kimi-k2.6` 或其他第三方模型，Opus 也会继续留在下拉列表里，方便随时切回伪装入口。

默认对话和 Claude Code 使用不同的模型列表逻辑。本项目会同时补丁这两条路径，避免默认对话回落到 `Sonnet 4.6`。

注意：这只解决 Claude Desktop 前端识别问题。实际请求仍会携带 `opus[1m]`，第三方网关需要自行把它路由到真实模型，否则网关或上游服务仍可能拒绝请求。

## 注意事项

- 本项目是非官方本地补丁。
- 安装会替换 `/Applications/Claude.app`，但会先备份原应用。
- Claude Desktop 版本变化可能导致补丁点变化；如安装失败，请先恢复官方 Claude.app，再更新本项目后重试。
- 本项目不内置任何 API Key。请自行配置你的 Claude Desktop 或第三方 API 网关。

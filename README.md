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
- 固定 Cowork 和 Code 模型菜单：`Opus 4.71M` 是伪装入口，`Kimi-k2.6` 是真实入口。
- Cowork 和 Code 均显示五档强度：`低 / 中 / 高 / 超高 / 最大`，默认模型为 `Opus 4.71M`，默认强度为 `最大`。
- Claude Code 新建会话默认权限模式为 `绕过权限`，并隔离官方旧缓存里的 `接受编辑`。
- 生成安装诊断日志，升级后补丁点失效会中止安装，避免替换成半残 Claude.app。

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

## 诊断模式

如果升级 Claude 或复制到其他电脑后出现 `Legacy Model`、只能看到模型不能看到强度、只能看到 `· 高`、Kimi 不能选择、默认权限又变成 `接受编辑` 等问题，先运行只读诊断：

```bash
cd /path/to/Claude-desk-cn
/usr/bin/python3 patch_claude_zh_cn.py --diagnose --app /Applications/Claude.app
```

诊断不会修改 Claude.app，会写入：

```text
Logs/latest.json
Logs/patch-report-YYYYMMDD-HHMMSS.json
```

`Logs/` 就在本项目目录里，和 `install.command` 同级。其他电脑出问题时，直接把这个文件夹发出来即可。日志只记录 Claude 版本、bundle 文件名、补丁点命中状态、已知缺失文案检查、JS 语法检查和签名状态，不记录 API Key、token 或对话内容。安装流程也会写同样的日志；如果 Cowork/Code 必要补丁点或已知未汉化文案检查没命中，脚本会中止安装并保留原 Claude.app。

## 已实现能力与注意事项速览

- `Opus 4.71M` 是伪装入口，实际 model id 固定为 `opus[1m]`，用于保留 Claude Code 依赖 Opus 名称开启的能力。
- `Kimi-k2.6` 是真实入口，优先使用网关返回的真实 Kimi id，找不到时兜底为 `kimi-for-coding`。
- Cowork 和 Code 都必须固定显示两个模型入口，并显示五档强度：`低 / 中 / 高 / 超高 / 最大`，默认是 `Opus 4.71M · 最大`。
- Code 新建会话默认权限模式是 `绕过权限`。如果其他电脑又显示 `接受编辑`，先看 `Logs/latest.json` 里的 `code.permission_default_bypass`。
- 新版页面若又出现已记录过的英文残留，先看 `Logs/latest.json` 里的 `i18n.known_missing_strings`，它会列出缺失或仍等于英文原文的 i18n key。
- 复制到其他电脑时，推荐复制本项目并在目标电脑重新运行 `install.command`，不要直接复制已经补丁过的 `/Applications/Claude.app`。
- Claude Desktop 每次更新后都要重新运行补丁；如果新版 bundle 结构变化，安装会因 invariant 失败而中止，不会覆盖成半残 app。
- 已适配 Claude Desktop `1.6608.2` 与 `1.7196.0` 的共享模型选择器和第三方模型校验开关；后续版本如再次变动，优先看 `Logs/latest.json` 的失败项。
- `api.kimi.com` 健康横幅补丁只隐藏旧健康检查误报，不保证第三方网关真实请求一定成功；真实请求仍由网关配置、网络和上游模型决定。
- 出现异常时先运行 `--diagnose`，把项目根目录里的 `Logs/` 发回来，比截图更容易定位是哪一个补丁点失效。

## 复制到其他电脑使用

复制整个项目文件夹即可。至少需要保留：

- `install.command`
- `prepare_official_update.command`
- `patch_claude_zh_cn.py`
- `resources/`
- `README.md`

目标电脑需要先安装官方 Claude Desktop 到 `/Applications/Claude.app`。复制后双击 `install.command` 即可安装。

## 更新官方原版 Claude

不要直接从官方 DMG 把 `Claude.app` 拖到 Applications 覆盖当前汉化版。macOS Finder 在覆盖已补丁和重签名过的 app bundle 时，可能因为运行中占用、锁定标记、扩展属性或内部文件权限直接报“必须跳过某些项目”。

正确流程：

1. 退出 Claude。
2. 双击本项目里的 `prepare_official_update.command`。
3. 脚本会解除当前 `/Applications/Claude.app` 的锁定、权限和扩展属性，但不会删除或移动它。
4. 打开官方 DMG，把新的 `Claude.app` 拖到 Applications，并选择替换。
5. 如果还需要汉化，再双击 `install.command`。

API、网关、大模型等配置保存在用户目录的 `~/Library/Application Support/Claude*` 下，不在 `/Applications/Claude.app` 里。这个准备脚本不会修改这些配置。

也可以在终端执行：

```bash
cd /path/to/Claude-desk-cn
sudo /usr/bin/python3 patch_claude_zh_cn.py --user-home "$HOME" --prepare-official-update
```

## 文件说明

- `install.command`：Mac 双击安装入口。
- `prepare_official_update.command`：安装官方原版前的准备入口，会解除当前汉化版的覆盖阻碍，但不删除、不移动 app。
- `patch_claude_zh_cn.py`：执行补丁、备份、重签名和验证的主脚本。
- `docs/implementation.md`：当前功能、实现原理和维护逻辑说明。
- `Logs/`：安装和诊断日志目录，运行脚本后自动生成，不提交到 Git。
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

安装时会清理 Claude 的前端缓存目录，避免 Electron 继续加载旧 bundle，导致菜单文案或模型按钮没有立即更新。

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

Code 页面模型菜单固定为两项：`Opus 4.71M` 和 `Kimi-k2.6`。`Opus 4.71M` 是伪装入口，实际 model id 始终是 `opus[1m]`；`Kimi-k2.6` 是真实模型入口，会优先使用网关返回的真实 Kimi model id，未返回时使用 `kimi-for-coding` 作为实际 id。

切换到 `Kimi-k2.6` 后，`Opus 4.71M` 仍会保留在菜单第一项，不会被挤掉，也不会再出现两个 Kimi。切回 `Opus 4.71M` 后，Kimi 也会继续保留。

默认对话和 Claude Code 使用不同的模型列表逻辑。本项目会同时补丁这两条路径，避免默认对话回落到 `Sonnet 4.6`。

底部模型按钮和强度标签也会一起兼容固定 Opus，避免只显示模型或只显示强度。Code 页面会保留完整强度菜单：`低 / 中 / 高 / 超高 / 最大`。

Cowork 页面同样固定显示两项模型和五档强度。默认值固定为 `Opus 4.71M · 最大`；旧缓存里的 `kimi-for-coding`、`Kimi-k2.6` 或 `cowork_effort_level=high` 不再决定初始默认。手动切换 `Kimi-k2.6` 后当前界面仍会生效，切回 `Opus 4.71M` 不会清空强度菜单。

Claude Desktop 更新后需要重新运行补丁。每次适配新版都会重点回归 Cowork 和 Code 两个模型菜单：不能出现 `Legacy Model`，不能只剩 `· 高` / `· 最大` 这类空模型按钮，默认必须是 `Opus 4.71M · 最大`，`Kimi-k2.6` 必须可选，五档强度必须都能点击。

Claude Code 新建会话的权限模式默认是 `绕过权限`。脚本会把新版前端里的 `cc-landing-draft-permission-mode` 和 `epitaxy-folder-permission-mode` 改为补丁专用键，避免其他电脑或旧缓存继续沿用官方默认 `接受编辑`。诊断日志里会检查 `code.permission_default_bypass`，这项失败时说明权限默认值补丁没有命中。

Claude Desktop `1.7196.0` 起，旧版补丁点 `Jbt` / `um="ccd-effort-level"` 已经变成 `k5/Fht/Pht` 和 `zm/Um/Hm`。如果更新后“汉化又失效”，先运行诊断；新版脚本会检查这些新锚点，避免继续误判为旧 bundle。

普通默认对话里的旧 `kimi-for-coding` 默认值会归一为 `opus[1m]`，避免模型按钮变成空白。Code 页面里直接选择 `Kimi-k2.6` 时，会写入真实 Kimi id，不再把显示名 `Kimi-k2.6` 当作请求模型名。

注意：这只解决 Claude Desktop 前端识别问题。实际请求仍会携带 `opus[1m]`，第三方网关需要自行把它路由到真实模型，否则网关或上游服务仍可能拒绝请求。

## 注意事项

- 本项目是非官方本地补丁。
- 安装会替换 `/Applications/Claude.app`，但会先备份原应用。
- Claude Desktop 版本变化可能导致补丁点变化；如安装失败，请先恢复官方 Claude.app，再更新本项目后重试。
- 本项目不内置任何 API Key。请自行配置你的 Claude Desktop 或第三方 API 网关。

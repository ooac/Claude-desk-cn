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
- 兼容第三方网关：保留 Opus 伪装显示，但运行时默认模型优先跟随网关真实模型，不把上下文窗口写死为某个固定值。
- 固定 Cowork 和 Code 模型菜单：`Opus 4.71M` 是伪装入口，`Kimi-k2.6` 是真实入口。
- Cowork 和 Code 均显示五档强度：`低 / 中 / 高 / 超高 / 最大`，默认模型为 `Opus 4.71M`，默认强度为 `最大`。
- Claude Code 新建会话默认权限模式为 `绕过权限`，并隔离官方旧缓存里的 `接受编辑`。
- 安装时检查当前未归档的 Claude Code 会话；只有历史里已经出现模型 token limit 错误时，才会先备份到 `Logs/session-backups/`，再瘦身历史截图、thinking 和超长工具结果。
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

如果升级 Claude 或复制到其他电脑后出现 `Legacy Model`、只能看到模型不能看到强度、只能看到 `· 高`、Kimi 不能选择、默认权限又变成 `接受编辑` 等问题，先双击运行项目里的：

```bash
diagnose.command
```

也可以在终端里手动运行：

```bash
cd /path/to/Claude-desk-cn
/usr/bin/python3 patch_claude_zh_cn.py --diagnose --app /Applications/Claude.app --user-home "$HOME"
```

诊断不会修改 Claude.app，不会安装补丁，也不会改 API、网关或模型配置。它只会写入：

```text
Logs/latest.json
Logs/patch-report-YYYYMMDD-HHMMSS.json
```

`Logs/` 就在本项目目录里，和 `install.command` 同级。其他电脑出问题时，直接把这个文件夹发出来即可。日志只记录 Claude 版本、bundle 文件名、补丁点命中状态、已知缺失文案检查、JS 语法检查、签名状态、网关模型发现来源、网关认证探测状态、上下文窗口同步状态和 token-limit 错误检查，不记录 API Key、token 或完整对话内容。安装流程也会写同样的日志；如果 Cowork/Code 必要补丁点、上下文窗口覆盖或已知未汉化文案检查没命中，脚本会中止安装并保留原 Claude.app。

如果诊断显示补丁都通过，但现象是“Cowork 可以用、Code 模式 401 或仍拿错上下文/模型”，双击运行：

```bash
repair_code_runtime.command
```

它不需要 sudo，不替换 `/Applications/Claude.app`，只会退出 Claude、终止旧 Code 子进程，并把当前“配置第三方推理”里的网关地址和 API Key 同步到 `~/.claude/settings.json` 的 Claude Code 运行时环境。修复后重新打开 Claude，再进 Code 模式测试。

## 已实现能力与注意事项速览

- `Opus 4.71M` 是显示兼容入口；Code/Cowork 实际启动 Claude Code CLI、切换 session model、写入 `session_context.model` 时会转成 provider 真实模型，例如当前的 `kimi-for-coding`。上下文窗口不能按显示名伪装，必须跟随网关返回的真实模型能力。安装脚本会优先读取手动模型列表或 `/v1/models` 的第一项作为 Claude Code 默认真实模型，并把 provider 返回的上下文窗口同步到 Claude Code 运行时配置。
- `Kimi-k2.6` 是真实入口，优先使用网关返回的真实 Kimi id，找不到时兜底为 `kimi-for-coding`。
- Cowork 和 Code 都必须固定显示两个模型入口，并显示五档强度：`低 / 中 / 高 / 超高 / 最大`，默认是 `Opus 4.71M · 最大`。
- Code 新建会话默认权限模式是 `绕过权限`。如果其他电脑又显示 `接受编辑`，先看 `Logs/latest.json` 里的 `code.permission_default_bypass`。
- 新版页面若又出现已记录过的英文残留，先看 `Logs/latest.json` 里的 `i18n.known_missing_strings`，它会列出缺失或仍等于英文原文的 i18n key。
- 开启开发者模式后，如果开发者菜单又出现英文，先看 `Logs/latest.json` 里的 `i18n.developer_menu_labels`。
- “配置第三方推理”窗口如果又出现英文，先看 `Logs/latest.json` 里的 `i18n.custom3p_setup_labels`。
- 复制到其他电脑时，推荐复制本项目并在目标电脑重新运行 `install.command`，不要直接复制已经补丁过的 `/Applications/Claude.app`。
- Claude Desktop 每次更新后都要重新运行补丁；如果新版 bundle 结构变化，安装会因 invariant 失败而中止，不会覆盖成半残 app。
- 已适配 Claude Desktop `1.6608.2` 与 `1.7196.0` 的共享模型选择器和第三方模型校验开关；后续版本如再次变动，优先看 `Logs/latest.json` 的失败项。
- `api.kimi.com` 健康横幅补丁只隐藏旧健康检查误报，不保证第三方网关真实请求一定成功；真实请求仍由网关配置、网络和上游模型决定。
- 如果 Cowork 能发消息但 Code 报 `401 API Key invalid`，优先运行 `repair_code_runtime.command`。这类问题通常不是前端菜单补丁失效，而是 Code CLI 使用的 `~/.claude/settings.json > env` 没有同步到当前第三方推理配置。诊断日志里的 `runtime.claude_code_gateway_env` 会检查 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN` 是否与当前网关配置一致，日志不会记录密钥。
- 如果 Code 已打开很久并反复发送截图/大文件，可能不是模型配置错，而是旧会话历史超过当前真实模型上下文。重新运行 `install.command` 后，脚本会同步真实上下文窗口，只会处理已经出现 token limit 错误的当前未归档会话，并在 `Logs/session-sanitize-latest.json` 记录真实 limit/requested 数值。
- 出现异常时先运行 `--diagnose`，把项目根目录里的 `Logs/` 发回来，比截图更容易定位是哪一个补丁点失效。`runtime.gateway_auth_check` 会记录 `/v1/models` 探测是否遇到 `401` / `403` 等认证错误，但不会记录 API Key；`runtime.claude_code_gateway_env` 会检查 Code CLI 鉴权环境是否已同步；`runtime.active_cli_model` 会检查当前 Claude Code 子进程是否仍带着旧的 `--model opus` / `--model opus[1m]`；`runtime.provider_default_ignores_opus_alias` 会检查真实 provider 默认模型是否跳过了 `opus` / `opus[1m]` / `Opus 4.71M` 这些显示别名；`runtime.context_window_root_configured` 会检查 `.claude.json` 顶层运行时窗口是否已经写入真实 provider 上限。

## 复制到其他电脑使用

复制整个项目文件夹即可。至少需要保留：

- `install.command`
- `repair_code_runtime.command`
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
- `diagnose.command`：Mac 双击诊断入口，只生成 `Logs/latest.json`，不修改 Claude.app。
- `repair_code_runtime.command`：Mac 双击修复 Code 运行时入口，不替换 app，只同步第三方网关到 `~/.claude/settings.json`。
- `prepare_official_update.command`：安装官方原版前的准备入口，会解除当前汉化版的覆盖阻碍，但不删除、不移动 app。
- `patch_claude_zh_cn.py`：执行补丁、备份、重签名和验证的主脚本。
- `docs/implementation.md`：当前功能、实现原理和维护逻辑说明。
- `Logs/`：安装、诊断和会话瘦身日志目录，运行脚本后自动生成，不提交到 Git。
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
9. 为第三方网关补充 Opus 默认模型识别兼容，界面显示 `Opus 4.71M`，真实 Claude Code 默认模型优先跟随 provider 模型列表。
10. 重新签名临时 Claude.app。
11. 备份原始 Claude.app。
12. 将补丁后的 Claude.app 安装回 `/Applications/Claude.app`。
13. 写入用户语言配置，按 provider 真实默认模型迁移旧 Opus 伪装会话，并按 token-limit 错误瘦身当前未归档 Claude Code 会话。
14. 启动 Claude。

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

如果使用第三方网关，本项目会在前端模型识别层把 `opus` / `opus[1m]` 都视为有效 Opus 显示入口，即使网关模型列表只返回 Kimi、DeepSeek 或其他模型名。但 Claude Code 的真实默认模型不再写死为 Opus：安装脚本会优先读取第三方推理设置里的模型列表，或请求当前网关 `/v1/models`，取 provider 返回的第一项作为真实 model id。这样以后换成 DeepSeek 或其他 1M 模型时，不需要再把脚本里的上下文上限改一遍。

Code 页面模型菜单固定为两项：`Opus 4.71M` 和 `Kimi-k2.6`。`Opus 4.71M` 是显示兼容入口，但它不会再作为 `--model opus` 传给 Claude Code；补丁会把启动参数和 session model 转成 provider 真实模型。`Kimi-k2.6` 是真实模型入口，会优先使用网关返回的真实 Kimi model id，未返回时使用 `kimi-for-coding` 作为实际 id。上下文窗口以真实请求 model 和网关返回能力为准，不以显示名为准。

切换到 `Kimi-k2.6` 后，`Opus 4.71M` 仍会保留在菜单第一项，不会被挤掉，也不会再出现两个 Kimi。切回 `Opus 4.71M` 后，Kimi 也会继续保留。

默认对话和 Claude Code 使用不同的模型列表逻辑。本项目会同时补丁这两条路径，避免默认对话回落到 `Sonnet 4.6`。

底部模型按钮和强度标签也会一起兼容固定 Opus，避免只显示模型或只显示强度。Code 页面会保留完整强度菜单：`低 / 中 / 高 / 超高 / 最大`。

Cowork 页面同样固定显示两项模型和五档强度。默认值固定为 `Opus 4.71M · 最大`；旧缓存里的 `kimi-for-coding`、`Kimi-k2.6` 或 `cowork_effort_level=high` 不再决定初始默认。手动切换 `Kimi-k2.6` 后当前界面仍会生效，切回 `Opus 4.71M` 不会清空强度菜单。

Claude Desktop 更新后需要重新运行补丁。每次适配新版都会重点回归 Cowork 和 Code 两个模型菜单：不能出现 `Legacy Model`，不能只剩 `· 高` / `· 最大` 这类空模型按钮，默认必须是 `Opus 4.71M · 最大`，`Kimi-k2.6` 必须可选，五档强度必须都能点击。

Claude Code 新建会话的权限模式默认是 `绕过权限`。脚本会把新版前端里的 `cc-landing-draft-permission-mode` 和 `epitaxy-folder-permission-mode` 改为补丁专用键，避免其他电脑或旧缓存继续沿用官方默认 `接受编辑`。诊断日志里会检查 `code.permission_default_bypass`，这项失败时说明权限默认值补丁没有命中。

Claude Desktop `1.7196.0` 起，旧版补丁点 `Jbt` / `um="ccd-effort-level"` 已经变成 `k5/Fht/Pht` 和 `zm/Um/Hm`。如果更新后“汉化又失效”，先运行诊断；新版脚本会检查这些新锚点，避免继续误判为旧 bundle。

普通默认对话里的旧 `kimi-for-coding` 默认值会归一为 `opus`，避免模型按钮变成空白。Code 页面里直接选择 `Kimi-k2.6` 时，会写入真实 Kimi id，不再把显示名 `Kimi-k2.6` 当作请求模型名。

注意：这只解决 Claude Desktop 前端识别问题。上下文容量不能靠补丁猜测；如果网关返回的是 200K、256K 或 1M，就按真实模型处理。安装脚本会把该值同步到 `.claude.json` 顶层的 Claude Code 运行时窗口，并在 `Logs/latest.json` 里记录 `runtime.provider_default_ignores_opus_alias`、`runtime.provider_context_window`、`runtime.claude_code_context_window`、`runtime.context_window_root_configured` 和 `runtime.context_window_match`。只有 `cachedGrowthBookFeatures.tengu_hawthorn_window` 不算通过，因为旧版 Claude Code 子进程可能不会读取它。手动强行使用 `opus[1m]` 仍可能让客户端按错误窗口组织上下文，最终被上游拒绝。

如果已经打开过旧会话，旧会话文件里可能保存了 `model: "opus"` 或 `model: "opus[1m]"`。安装脚本会在能发现真实 provider 默认模型时，把这些已保存会话迁移到真实模型 id，并在安装时终止旧的 Claude Code / disclaimer 子进程；否则正在运行的旧子进程仍可能按 Opus 伪装窗口继续组织上下文。

### API 报 token 上限

如果报错类似：

```text
API Error: 400 Invalid request: Your request exceeded model token limit: 262144
```

先确认当前进程是不是还在使用 Opus 伪装 id，或者 Claude Code 运行时窗口是否仍停在旧值。新版脚本会尽量把默认模型和已保存会话迁移到 provider 真实默认模型，并同步 `tengu_hawthorn_window`。不同 provider 的 limit 可能是 200K、256K、1M 或其他值，不能写死。重新运行 `install.command` 时，脚本只处理已经出现 token limit 错误的未归档会话：原始 jsonl 会备份到 `Logs/session-backups/`，瘦身结果写入 `Logs/session-sanitize-latest.json`。瘦身后再重启 Claude，继续同一会话即可。

### API Key 无效或过期

如果报错类似：

```text
Failed to authenticate. API Error: 401 The API Key appears to be invalid or may have expired.
```

这说明请求已经到达第三方网关，但当前电脑保存的凭据不可用，通常是 API Key 没复制、复制错了、过期了，或认证方式选错。运行 `--diagnose` 后看 `Logs/latest.json`：

- `runtime.gateway_auth_check` 显示 `status=401` 或 `status=403`：先在 Claude 的“配置第三方推理”里重新填写该电脑自己的网关 API 密钥，再运行 `install.command`。
- `runtime.gateway_auth_check` 通过，但 Code 仍 401：运行 `repair_code_runtime.command`，把当前第三方推理配置同步到 Claude Code CLI 的 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`。

### 上下文窗口百分比显示

Code 页上下文窗口有两条来源：历史消息里的 `## Context Usage` 文本，以及底部实时上下文弹窗里的 `contextUsage.rawMaxTokens`。官方值可能因为 `opus[1m]` 显示入口写成 `1.0M`，也可能因为旧运行时配置写成 `200.0k`，百分比还可能被钳制成 `100%`。安装脚本会把 provider 返回的真实窗口同步到 Claude Code 运行时，并注入前端解析器和实时弹窗组件，显示分母和百分比都按真实窗口重算。

对应诊断项必须同时通过：

```text
code.context_usage_window_override
code.live_context_usage_window_override
runtime.context_window_root_configured
runtime.context_window_match
runtime.provider_default_ignores_opus_alias
```

比如真实上限是 `262144` 时，`252.9k` 会显示约 `252.9k / 262.1k (96.5%)`；如果将来 provider 返回 1M，重新运行 `install.command` 后会自动同步为 1M，不需要改脚本常量。如果确实超过上限，则显示真实超限比例，而不是固定 `100%`。

## 注意事项

- 本项目是非官方本地补丁。
- 安装会替换 `/Applications/Claude.app`，但会先备份原应用。
- Claude Desktop 版本变化可能导致补丁点变化；如安装失败，请先恢复官方 Claude.app，再更新本项目后重试。
- 本项目不内置任何 API Key。请自行配置你的 Claude Desktop 或第三方 API 网关。

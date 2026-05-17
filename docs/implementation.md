# Claude Desktop 中文补丁实现说明

本文档记录当前项目已经实现的功能、核心原理、执行流程和边界条件。它面向维护者，用来快速理解补丁为什么能生效，以及后续新增翻译或兼容逻辑时应该改哪里。

## 项目目标

本项目用于给 macOS 版 Claude Desktop 安装本地中文补丁。补丁不包含 Claude Desktop 本体，不提供账号、API Key 或第三方网关服务，只修改本机 `/Applications/Claude.app` 内的资源文件，并对修改后的应用重新签名。

当前目标分为三类：

1. 中文化：安装前端语言包、桌面壳层语言包、macOS 原生菜单翻译和部分硬编码文案。
2. 可用性：自动写入中文语言偏好，重新签名并清除隔离属性，降低补丁后无法启动的风险。
3. 第三方网关兼容：界面保留 `Opus 4.71M` 显示入口，但 Claude Code 真实默认模型优先跟随网关或手动模型列表，不把上下文窗口写死为某个 provider 的当前数值。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `install.command` | Finder 双击入口，负责以 sudo 调用 Python 补丁脚本。 |
| `prepare_official_update.command` | 安装官方原版前的准备入口，解除当前补丁版 app 的覆盖阻碍。 |
| `patch_claude_zh_cn.py` | 主补丁脚本，负责复制、修改、签名、备份、替换和验证。 |
| `resources/frontend-zh-CN.json` | Claude 前端 i18n 文案翻译。 |
| `resources/desktop-zh-CN.json` | Electron 桌面壳层中文资源。 |
| `resources/Localizable.strings` | macOS 原生菜单中文资源。 |
| `resources/statsig-zh-CN.json` | Statsig 相关文案兜底资源。 |
| `resources/manifest.json` | 语言包元信息和统计。 |
| `README.md` | 面向使用者的安装、恢复和常见问题说明。 |
| `docs/implementation.md` | 当前实现原理说明，也就是本文档。 |

## 已实现功能

### 1. 前端中文语言包

脚本会读取 Claude.app 内置的：

```text
Contents/Resources/ion-dist/i18n/en-US.json
```

然后把 `resources/frontend-zh-CN.json` 合并成：

```text
Contents/Resources/ion-dist/i18n/zh-CN.json
```

合并逻辑是：

1. 以官方 `en-US.json` 的 key 为准。
2. 如果中文包存在同 key，使用中文译文。
3. 如果中文包缺少该 key，回退使用英文原文，避免因为缺 key 导致前端读取失败。
4. 中文包中已经过期、官方当前版本不存在的 key 会被忽略。

这样做的好处是：Claude Desktop 更新后，即使官方新增了文案，补丁也能继续生成结构完整的 `zh-CN.json`，不会因为翻译缺口直接崩溃。

### 2. 语言白名单补丁

Claude 前端 bundle 中有可选语言白名单。即使放入 `zh-CN.json`，如果白名单不包含 `zh-CN`，界面也可能不会加载中文。

脚本会扫描：

```text
Contents/Resources/ion-dist/assets/v1/index-*.js
```

并把语言列表从类似：

```js
["en-US","de-DE","fr-FR",...,"id-ID"]
```

扩展为：

```js
["en-US","de-DE","fr-FR",...,"id-ID","zh-CN"]
```

如果已经包含 `zh-CN`，脚本会跳过这一步，保证重复执行基本幂等。

### 3. 桌面壳层和 macOS 原生菜单中文

脚本会把桌面壳层翻译文件复制到：

```text
Contents/Resources/zh-CN.json
```

同时创建并写入：

```text
Contents/Resources/zh-CN.lproj/Localizable.strings
Contents/Resources/zh_CN.lproj/Localizable.strings
```

保留两个目录名是为了兼容 macOS 和 Electron 对 locale 目录命名的不同匹配方式。

原生菜单中有一部分是 macOS role 菜单，例如 Hide、Hide Others、Show All、Services、Minimize 等。它们不是普通 i18n JSON 文案，所以脚本会额外修改 `app.asar` 里的菜单构造代码，把 role 项加上中文 `label`。

### 4. 硬编码前端文案替换

很多界面文字不在 i18n JSON 中，而是被打包进压缩后的 JS bundle。脚本在 `patch_hardcoded_frontend_strings()` 中维护了一张硬编码替换表，覆盖当前已经发现的英文残留。

当前覆盖范围包括但不限于：

- 新建任务、新会话、最近使用、筛选项、批量操作。
- 设置页里的连接、沙盒与工作区、连接器、遥测、模型、MCP、网关配置等文案。
- 菜单项里的主题、字体、退出登录、服务、隐藏窗口等。
- Claude Code 模式菜单里的“强度”“思考模式”等。
- 权限模式默认值和模型识别兼容逻辑。

这类替换是针对 Claude 当前 bundle 形态的精确字符串补丁。Claude Desktop 升级后，如果压缩代码结构变化，个别替换可能失效，需要根据新截图和新 bundle 继续补充。

### 5. 默认权限模式改为“绕过权限”

Claude Code 新建会话的权限模式原本会默认落在“接受编辑”。当前补丁把默认值改成“绕过权限”。

涉及两类前端本地存储键：

```js
cc-landing-draft-permission-mode
epitaxy-folder-permission-mode
```

补丁不会继续使用旧键，而是改用中文补丁专用键：

```js
cc-landing-draft-permission-mode-cn
epitaxy-folder-permission-mode-cn
```

这样做有两个目的：

1. 避免用户以前缓存的“接受编辑”继续覆盖新默认值。
2. 保留用户在中文补丁版本里后续手动选择模式的能力。

当前默认值被写为：

```js
bypassPermissions
```

因此新建会话默认显示和使用“绕过权限”。如果用户在界面中手动切换，新的选择仍会按补丁专用键持久化。

Claude 新版前端存在多套存储 helper。旧版使用 `Ld/Mi`，新版 Code 页面使用 `fc`，新版 landing chunk 使用 `Ks`。补丁必须同时覆盖这些入口：

```js
cc-landing-draft-permission-mode-cn -> bypassPermissions
epitaxy-folder-permission-mode-cn -> account-scoped folder map
```

Code 页面最终计算当前权限时，原逻辑可能优先读取项目默认值 `Gs`，在其他电脑上再次回到 `acceptEdits`。补丁会把顺序改成先使用补丁专用默认值：

```js
en ?? Zs ?? $s ?? Gs ?? "bypassPermissions"
```

含义是：用户当前手动选择 `en` 优先；补丁专用文件夹设置 `Zs` 次之；补丁专用全局默认 `$s` 再次之；官方项目默认 `Gs` 只作为兜底，不能覆盖首次打开时的“绕过权限”。

### 6. 固定 Opus 伪装入口并保留 Kimi 真实入口

Claude Code 的一些能力和前端判断依赖 Opus 入口。用户需要继续保留界面上的：

```text
Opus 4.71M
```

但上下文容量不能跟着显示名伪装。真实请求模型必须优先来自第三方推理配置或网关 `/v1/models`，例如当前是 Kimi 就按 Kimi，之后换成 DeepSeek 就按 DeepSeek。

当前实现分三层：

1. 安装脚本会先读取第三方推理设置中的手动模型列表；如果没有，再请求当前网关 `/v1/models`。能发现真实模型时，`~/.claude/settings.json` 的 `model` 写入 provider 返回的真实默认模型，强度固定为 `max`。
2. 前端模型识别函数会被补丁成：如果当前模型是 `opus` 或 `opus[1m]`，并且第三方网关返回了非空模型列表，就把 Opus 显示入口视为有效模型；但这只影响 UI，不代表上下文窗口固定为 Opus。
3. Code 页面模型菜单会固定重建为 `Opus 4.71M` 和 `Kimi-k2.6` 两项，再追加完整强度菜单；新打开时默认 `Opus 4.71M · 最大`。
4. 默认对话和 Claude Code 分别走 `baku_model` 与 `ccr_model` / `cowork_model` 路径。本项目会同时补丁这两条路径，普通默认对话不再回落到 `Sonnet 4.6`。

补丁后的关键逻辑等价于：

```js
if ((e === "opus" || e === "opus[1m]") && t.length > 0) return e;
```

含义是：

- `e` 是当前默认模型，例如 `opus` 或旧缓存里的 `opus[1m]`。
- `t` 是前端拿到的可用模型列表。
- 只要列表非空，前端就不再报“默认模型无法识别”。
- 返回值只保证 UI 不再报“默认模型无法识别”。真实 Claude Code 默认模型由安装脚本从 provider 配置或 `/v1/models` 动态发现。

这只解决 Claude Desktop 前端识别和功能解锁问题。上下文窗口不能由补丁猜测：Kimi、DeepSeek 或其他模型是多少就按多少。如果 provider 将来把上下文扩到 1M，脚本不需要再改固定常量；如果 provider 只有 200K/256K，客户端也不能因为显示成 Opus 就按 1M 打包请求。安装阶段会把 provider 返回的上下文窗口同步到 Claude Code 运行时配置 `tengu_hawthorn_window`，避免运行时仍按旧 200K 窗口组织上下文。

模型菜单里的两项含义不同：

- `Opus 4.71M` 是固定显示入口，用于保持 Claude Desktop 前端里依赖 Opus 名称的能力入口。它不再承担“声明上下文窗口”或“实际请求模型”的职责。
- Code/Cowork 内部必须拆分显示模型和运行模型：`displayModel` 可以是 `opus` / `Opus 4.71M`，但 `runtimeModel` 必须是 provider 真实模型 id。当前 provider 默认是 `kimi-for-coding` 时，点击或默认显示 `Opus 4.71M` 也只能以 `--model kimi-for-coding` 启动 Claude Code。
- `Kimi-k2.6` 是真实模型入口。补丁会优先使用网关返回列表里的真实 Kimi model id；如果网关暂时没有返回 Kimi，但当前会话已经保存了 Kimi id，也会沿用该 id；如果两者都没有，则用 `kimi-for-coding` 作为实际 id。它不会伪装成 Opus，也不会再用显示名制造第二个 Kimi 项。
- Code 页的上下文窗口不信任上游文本或实时组件里的窗口上限。安装脚本会把 provider 当前真实窗口注入 `## Context Usage` 文本解析器，也会覆盖底部实时弹窗里的 `contextUsage.rawMaxTokens`，再按 `totalTokens / providerContextWindow` 重新计算百分比。进度条可以保持最大宽度 100%，但文字必须显示真实分母和真实百分比。

默认对话路径里，原始默认值是 `claude-sonnet-4-6`。补丁会把它改为 `opus`，并在普通对话模型列表里固定插入 `Opus 4.71M`。这样新建普通对话时不会因为找不到可选模型而显示空白，也不会在发送消息后自动变成 `Sonnet 4.6`。

Code 页面强度菜单不再只依赖官方 `Od(W)` 能力判断。当前模型为 `opus`、`opus[1m]` 或 Kimi-k2.6 时，会强制保留五档：

```text
低 / 中 / 高 / 超高 / 最大
```

默认强度固定为 `最大`。Code 会使用补丁专用键 `ccd-effort-level-cn` / `epitaxy_effort_level_cn`，Cowork 使用 `cowork_effort_level_cn`，避免旧电脑里的 `high` 缓存继续覆盖新默认。用户手动选择强度后仍走原生 `setEffortLevel` / `hs(e)` 流程；创建或切换会话时，`effort` 对 Opus 和 Kimi 都会继续传递。

普通对话模型菜单仍然需要读取模型候选项上的能力元数据。因此固定 Opus 项不能只写 `{ model, name }`，必须保留 `thinking_modes` 等字段。当前实现会从网关返回的模型列表中寻找可复用的 Opus 或支持思考模式的模型项作为模板，再生成固定的 `Opus 4.71M` 入口。

底部模型按钮的强度标签还会走另一处 `Gft()` 模式读取逻辑，它读取的是原始 `allModelOptions`，不一定包含固定注入后的 `opus[1m]`。因此补丁也会让 `Gft()` 在当前模型为 `opus` / `opus[1m]` 且原始列表找不到时，回退到带 `thinking_modes` 的模型模板，保证 `Opus 4.71M · 最大` 这类显示不丢失。

底部触发器本身也有显示对象兜底：如果当前模型或默认模型指向 Opus，但候选列表暂时没有对应显示项，会临时使用 `{ model: "opus", name: "Opus 4.71M" }` 渲染按钮，防止只剩强度标签。

最终渲染前还会检查 `Vft(W)` 的结果。如果显示名为空，会兜底使用 `Opus 4.71M`，保留原本计算出来的强度标签，避免再次出现只有 `· 高` 或 `· 最大` 的状态。

模型按钮组件 `Wft()` 内部也有同样兜底：如果格式化结果为空，直接渲染 `Opus 4.71M`。

模型名格式化函数 `Vft()` 也会把 `opus` / `opus[1m]` 直接显示为 `Opus 4.71M`，避免部分普通对话入口绕过候选项名称后又显示原始 id。

如果父组件传入的 `contextModel` 还是旧 `kimi-for-coding`，默认入口会先归一为 `opus`，再去计算显示名和强度模式。

早期补丁曾把普通默认对话模型写成 `kimi-for-coding`。这个值在默认对话模型列表中通常不存在，会导致前端拿不到当前模型的显示名，只剩下强度标签。当前实现会在默认对话路径把旧的 `kimi-for-coding` 归一为 `opus`。Code 页面模型菜单则单独处理：`Kimi-k2.6` 只是显示名，实际选择时传入网关可识别的 Kimi id，兜底为 `kimi-for-coding`。

Claude Desktop `1.7196.0` 之后，模型菜单实现再次换了锚点：

- Cowork / 共享模型配置从旧 `Jbt` 路径变为 `k5()` + `Fht/Pht`。补丁必须固定 `k5("ccr_model")` 与 `k5("cowork_model")` 的候选项为 `Opus 4.71M` 和 `Kimi-k2.6`，并在 `Fht` 中补 Cowork fallback 强度区。
- Code 强度从旧 `um="ccd-effort-level"` 变为 `zm="ccd-effort-level"`，能力判断集中在 `Um()` / `Hm()`。补丁必须把默认强度改为 `max`，把显示标签改为中文，并让强度 section 无条件使用五档。
- Code 模型菜单项从旧 `pe/gs` 路径拆成 `fe/pe` 与 `modelExtraSections:Ss`。补丁需要同时改当前模型解析、菜单项生成和提交参数里的 `effort` 传递。
- 权限默认值除了 `Ic()`，新版 landing chunk 还会出现 `Ws("cc-landing-draft-permission-mode","acceptEdits",!1)`，也必须改成补丁专用键和 `bypassPermissions`。

维护时不要只搜索旧锚点。`find_frontend_bundles()` 和 `check_frontend_invariants()` 必须同时认识新版 `k5/Fht/Pht` 与 `zm/Um/Hm`，否则升级后会出现“语言包存在但模型、强度、权限全失效”的假阳性安装。

### 7. 第三方模型校验补丁

脚本包含 `patch_custom3p_model_validation()`，尝试修改 `app.asar` 中的第三方模型名校验逻辑。

这个补丁点依赖 Claude 内部压缩代码格式。旧版使用 `Hte`，中间版本曾短暂使用 `_Zt()`，`1.6608.2` 使用 `FLA` 总开关。脚本会按这些结构依次尝试，且必须在安装前 invariant 中通过。

如果当前版本找不到预期 anchor，安装会中止并保留原 `/Applications/Claude.app`，不会继续替换成半残应用。诊断日志中对应项是 `asar.custom3p_validation`。

### 8. Claude 更新后的模型菜单回归项

Claude Desktop 更新后，前端 bundle 文件名和压缩变量名经常变化。每次更新本项目或重新适配新版 Claude 时，必须重新检查这些重复问题：

1. Cowork 不能显示 `Legacy Model`，只能显示 `Opus 4.71M` 和 `Kimi-k2.6`。
2. Cowork 点击 `Kimi-k2.6` 后，勾选必须移动到底部真实 Kimi 项，不能被归一回 `opus[1m]`。
3. Cowork 不应因为 `api.kimi.com` 的旧健康检查结果显示黄色阻断横幅；如果网关真实不可用，只影响实际请求，不应阻止模型菜单。
4. Code 模式底部不能只显示 `· 高`、`· 最大` 或空模型名。
5. Code 模式菜单只能有两个模型入口：`Opus 4.71M` 和 `Kimi-k2.6`，不能出现两个 Kimi。
6. Code 和 Cowork 都必须显示五档强度：`低 / 中 / 高 / 超高 / 最大`。
7. `超高` 和 `最大` 必须可点击，选择后底部标签要同步更新。
8. 新版本如果把共享模型选择器从旧 `Wft/Vft/ogt` 改到新函数，必须补丁新的共享选择器，而不是只修旧 anchor。
9. Code 新建会话权限模式必须默认 `绕过权限`，不能因为新版存储 helper 或旧电脑缓存回到 `接受编辑`。
10. Code 和 Cowork 新打开时必须默认 `Opus 4.71M · 最大`，不能被旧 sticky model、旧 session 默认或旧强度缓存改成 `Kimi-k2.6 · 高`。

如果用户要安装官方原版，不能直接从 DMG 覆盖当前汉化版。补丁版 app 经过本地重签名、xattr 清理和整包替换，另一台电脑上还可能叠加运行中占用、锁定标记或内部权限差异。Finder 覆盖 app bundle 时会逐项复制，遇到内部条目不可写就会报“必须跳过某些项目”。维护策略是先运行 `prepare_official_update.command` 或 `--prepare-official-update`，只解除当前 `/Applications/Claude.app` 的锁定、扩展属性、owner 和用户写权限，然后让用户从官方 DMG 正常拖入覆盖。该流程不删除、不移动 app，也不触碰 `~/Library/Application Support/Claude*` 下的 API、网关和模型配置。

当前 `1.6608.0` / `1.6608.2` 适配点：

- Cowork/普通入口：共享模型选择器 `Jbt`，固定重建两项模型，并移除 `Legacy Model` fallback 对当前菜单的影响。
- `1.6608.2` 中 `Jbt` 从旧的外层 `conversationUuid` 组件拆成 `Jbt=({models:e,currentModelOption...})` 共享列表组件，外层配置仍负责 `Q/X/J`、当前模型和强度 section。脚本必须同时识别两种结构。
- Cowork 默认：共享选择器内部仍显示 `Opus 4.71M` 作为首项，旧 sticky 里的 `kimi-for-coding` / `Kimi-k2.6` 不再绑架新打开 Cowork 的显示默认；真实 Claude Code 请求模型由 provider 默认模型同步逻辑处理。
- Cowork 强度：`Jbt` 只在 Code 传入 `ccdEffortSection` 时有原生强度 section；Cowork 不传该 section，因此补丁会在 `Jbt` 内增加 fallback section。只要原生 section 缺失，就无条件使用 fallback，不再依赖 `activeMode` 字符串判断。默认值为 `max`，显示为“最大”，选中后写入补丁专用 `localStorage["cowork_effort_level_cn"]` 并派发 `cowork-effort-change`。
- Cowork 配置同步：Cowork 配置处监听 `cowork-effort-change`，让 `NT.setYukonSilverConfig({ effort })` 能使用最新强度。这样点击 `超高` 或 `最大` 后，不只更新菜单，也会进入后续会话配置。
- Cowork 健康横幅：新版 `EQt/yW.Unreachable` 结构下，对 `api.kimi.com` 旧健康状态做隐藏处理。
- Code 页面：`zm()` 内的 `W/Q/pe/me` 负责模型菜单，`hm()/gm()` 和 `xs` 负责强度菜单；没有当前界面临时选择时，显示默认仍是 `Opus 4.71M`，强度固定为 `max`。真实请求模型由 `zhRuntimeModelFor()` 映射到 provider 默认模型，启动对象必须使用 `model:zhRuntimeModel`，不能再把显示用的 `model:W` 直接传入。强度必须无条件生成五档，不再依赖 `De`、`Fe`、`Oe`、旧 `ccd-effort-level` 缓存或本机环境。

### 9. 升级诊断日志与必过 invariant

新版 Claude 经常改 bundle 文件名和压缩变量名。为了避免补丁点失效后仍替换 `/Applications/Claude.app`，脚本现在有两套诊断机制：

1. 安装流程会在替换原 app 前运行 `check_frontend_invariants()`。必过项包括 Cowork 两模型、Cowork 五档强度、Cowork 强度同步、Cowork runtime 模型映射、Code 两模型、Code 五档强度、Code runtime 模型映射、Code 启动对象不得直传 `model:W`、Code 默认绕过权限、Code Context Usage 文本窗口覆盖、Code 实时上下文弹窗窗口覆盖、已知未汉化文案检查、开发者菜单汉化检查、第三方推理设置页汉化检查、Kimi 健康横幅隐藏、JS 语法检查、第三方模型校验补丁和签名验证。
2. `--diagnose` 只读模式会检查当前 `/Applications/Claude.app`，并写入诊断日志，不修改任何文件。

为了避免用户在其他电脑手敲路径时误跑安装流程，项目根目录提供 `diagnose.command`。它等价于运行 `patch_claude_zh_cn.py --diagnose --app /Applications/Claude.app --user-home "$HOME"`，只生成 `Logs/latest.json`，不会 sudo、不会替换 app，也不会修改 API、网关或模型配置。

如果 `diagnose.command` 显示前端补丁和网关探测都通过，但实际现象是 Cowork 可用、Code 模式报 401 或继续拿旧上下文窗口，项目根目录提供 `repair_code_runtime.command`。它等价于运行 `patch_claude_zh_cn.py --repair-code-runtime --app /Applications/Claude.app --user-home "$HOME"`，不替换 app、不需要 sudo，只修复用户态 Claude Code 运行时配置：

- 退出 Claude 并终止遗留 Claude Code 子进程。
- 从当前第三方推理配置读取网关地址和静态 API Key。
- 写入 `~/.claude/settings.json > env` 的 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_API_KEY`。
- 继续同步真实 provider 默认模型、`effortLevel=max` 和 `tengu_hawthorn_window`。
- 迁移旧会话里的 Opus 显示别名到真实 provider 模型。

如果只有某个项目 Code 401，另一个项目正常，可以把该项目文件夹拖到 `diagnose.command` 上。脚本会把该路径传给 `patch_claude_zh_cn.py --diagnose --project <path>`。这个模式仍然只读，只检查该项目里的 `.claude/settings*.json` 和 `.env*` 是否覆盖 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_API_KEY` 或认证类 `ANTHROPIC_CUSTOM_HEADERS`，日志事件名为 `runtime.project_env_overrides`。

日志路径：

```text
Logs/latest.json
Logs/patch-report-YYYYMMDD-HHMMSS.json
```

`Logs/` 固定在项目根目录，也就是 `install.command` 同级。这样把项目复制到其他电脑后，异常机器生成的日志也在同一个文件夹里，方便直接打包发回。脚本通过 sudo 运行时，会把 `Logs/` 及生成的 JSON 文件 owner 改回当前用户，避免日志文件变成 root-owned。

日志中的每个补丁点会记录 `passed`、`applied`、`already_patched`、`missing` 或 `failed`，并带上目标 bundle 文件名和 Claude 版本。`i18n.known_missing_strings` 会检查已经记录过的易漏英文文案 key，发现缺失、仍等于 en-US 原文或不含中文字符时会进入 `required_failures`。`i18n.developer_menu_labels` 会检查开发者模式下主进程菜单的额外调试项是否仍是英文。`i18n.custom3p_setup_labels` 会检查“配置第三方推理”窗口的 asar 与前端 bundle 文案是否仍有已记录的英文残留。上下文窗口相关日志必须同时覆盖 `code.context_usage_window_override`、`code.live_context_usage_window_override`、`runtime.gateway_auth_check`、`runtime.gateway_messages_auth_check`、`runtime.claude_code_gateway_env`、`runtime.provider_default_ignores_opus_alias`、`runtime.provider_context_window`、`runtime.claude_code_context_window`、`runtime.context_window_root_configured` 和 `runtime.context_window_match`，用来区分“只改了显示”“只写了 GrowthBook 缓存”“网关认证失败”“消息接口认证失败”“Code CLI 鉴权环境没有同步”“把 Opus 显示别名误当 provider 默认模型”和“真实运行时窗口已经同步”。日志还会记录 provider 默认模型发现来源和当前未归档 Claude Code 会话里的 token-limit 错误，但不记录 API Key、token 或完整对话内容。

`runtime.gateway_auth_check` 只记录 `/v1/models` 探测的 HTTP 状态、网关 endpoint 和错误原因，不记录 API Key。其他电脑出现 `401 The API Key appears to be invalid or may have expired` 时，这个事件应为 `missing`，message 里会包含 `status=401`，用于区分“补丁没命中”和“该电脑凭据不可用”。

`runtime.claude_code_gateway_env` 不记录 API Key，只记录 `base_url_match`、`auth_scheme`、`credential_mode`、`auth_token_present`、`auth_token_matches_gateway`、`api_key_present` 和 `api_key_matches_gateway`。如果 `runtime.gateway_auth_check=passed` 但 `runtime.claude_code_gateway_env=missing`，说明桌面端第三方推理配置可以探测模型，但 Claude Code CLI 没有拿到同一套 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`；这正是“Cowork 能用、Code 401”的典型根因，运行 `repair_code_runtime.command` 即可修复。如果 `credential_mode=credential_helper` 或 `credential_mode=sso`，脚本会明确标记 `static_sync_supported=false`，因为不能从静态 API Key 自动同步。

`runtime.gateway_messages_auth_check` 会额外用极小 `/v1/messages` 请求验证真实推理接口。它会根据第三方推理配置里的认证方案发送 `Authorization: Bearer` 或 `x-api-key`，并在日志里记录 `auth_scheme` 和 `credential_mode`。`runtime.gateway_auth_check` 只代表 `/v1/models` 可读；如果模型探测通过但消息接口返回 401/403，说明当前 Key 对推理接口无效、认证方案选错或已过期，需要重新保存第三方推理 API Key 或检查上游权限。

如果“同一个插件在 A 文件夹可用、B 文件夹 401”，不能只看全局 `~/.claude/settings.json`。`runtime.active_project_env_overrides` 会扫描未归档 Code 会话的项目 `cwd`，检查项目内 `.claude/settings*.json` 和 `.env*` 是否覆盖了 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_API_KEY` 或认证类 `ANTHROPIC_CUSTOM_HEADERS`。`runtime.project_env_overrides` 则来自把项目文件夹拖到 `diagnose.command`，或手动传入 `--project` 的显式项目路径。两者都只记录文件路径和 mismatch 类型，不记录密钥，用来快速定位项目级旧 Key 或旧网关覆盖。

运行时 token-limit 错误检查的事件名是：

```text
runtime.active_sessions_token_limit_errors
```

如果这项 missing，说明当前会话历史里已经出现过 `exceeded model token limit` 错误。安装流程会调用 `sanitize_active_oversized_sessions()`，只处理这些已经报错的未归档当前会话：

1. 原始 jsonl 先备份到 `Logs/session-backups/YYYYMMDD-HHMMSS/`。
2. 历史截图 base64 会替换成短文本占位。
3. assistant thinking 和长 signature 会移除。
4. 超长工具输出和编辑片段会截断。
5. 处理结果写入 `Logs/session-sanitize-latest.json`。

这个流程不修改 API Key、网关配置或项目文件，只瘦身 Claude Code 的历史记录。它解决的是恢复旧会话时已经被上游明确拒绝的 token-limit 问题；如果网关本身不可用、Key 错误或网络不通，仍需要按网关配置排查。

如果其他电脑出现 Cowork 只有模型没有强度、Code 只显示 `· 高`、`Legacy Model`、Kimi 不能切换等问题，优先运行：

```bash
/usr/bin/python3 patch_claude_zh_cn.py --diagnose --app /Applications/Claude.app
```

然后看 `latest.json` 的 `required_failures`。如果失败项是 `cowork.default_opus` / `code.default_opus`，说明默认模型仍可能被旧缓存覆盖；如果是 `cowork.default_max_effort` / `code.default_max_effort`，说明默认最大强度没有命中；如果失败项是 `cowork.fallback_effort`，说明共享选择器强度 fallback 没命中；如果失败项是 `code.full_effort`，说明 Code 的 `xs` 强度构建没有被无条件替换；如果失败项是 `code.permission_default_bypass`，说明默认“绕过权限”补丁没命中新版 bundle；如果失败项是 `i18n.known_missing_strings`，说明已记录过的英文残留 key 没有写入当前 `zh-CN.json`；如果失败项是 `i18n.developer_menu_labels`，说明开发者菜单主进程文案没有写入 `app.asar`；如果失败项是 `i18n.custom3p_setup_labels`，说明“配置第三方推理”窗口的 asar 或前端 bundle 仍有已记录的英文残留；如果是 `syntax.*`，说明 bundle 补丁破坏了 JS 语法，安装流程应当已经中止。

### 10. app.asar 修改和完整性更新

`app.asar` 是 Electron 应用的归档文件。项目里有一套轻量 asar 读写逻辑，用来修改其中的 `.vite/build/index.js`。

核心步骤是：

1. 读取 asar header。
2. 定位目标文件在 asar body 中的 offset 和 size。
3. 替换目标文件内容。
4. 如果文件长度变化，更新后续文件 offset。
5. 重新计算被修改文件的 SHA256 integrity。
6. 重新编码 asar header。
7. 更新 `Contents/Info.plist` 里的 `ElectronAsarIntegrity`。

这一步很关键。只改 `app.asar` 内容而不更新 integrity，Electron 可能在启动或加载资源时认为归档被篡改。

### 11. 重新签名

修改 app bundle 后，原签名必然失效。脚本会对整个 Claude.app 重新做本机 ad-hoc 签名。

签名顺序是：

1. 先签内部 Mach-O 文件，例如 `.dylib`、`.node`、可执行文件。
2. 再签内部 `.framework` 和 helper `.app`。
3. 最后签最外层 `Claude.app`。

如果目标文件原本带 entitlements，脚本会读取并尽量保留，同时加入：

```text
com.apple.security.cs.disable-library-validation
```

原因是 ad-hoc 签名没有真实 Team ID。启用 hardened runtime 时，如果不禁用库验证，Electron 主进程加载内部 framework 或原生模块时可能失败。

脚本会保留并校验官方 Claude.app 的虚拟化权限：

```text
com.apple.security.virtualization
```

如果原始应用没有这个权限，脚本会拒绝继续，要求先恢复或重装官方 Claude.app。

### 12. 清除隔离属性

脚本会执行：

```bash
xattr -dr com.apple.quarantine /Applications/Claude.app
```

这用于减少 macOS 的“应用已损坏”“无法验证开发者”等提示。清除隔离属性不能替代签名，两个步骤都需要保留。

### 13. 用户语言配置

脚本会写入两个 Claude 配置目录：

```text
~/Library/Application Support/Claude/config.json
~/Library/Application Support/Claude-3p/config.json
```

写入内容是：

```json
{
  "locale": "zh-CN"
}
```

如果文件已有其他配置，脚本会保留原字段，只更新 `locale`。如果配置文件不是合法 JSON，会先备份为 `.json.bak-invalid`，再写入新配置。

脚本还会写入 macOS 应用语言偏好：

```text
com.anthropic.claudefordesktop AppleLanguages
com.anthropic.claudefordesktop AppleLocale
```

语言优先级包含：

```text
zh-Hans
zh-Hans-CN
zh-CN
en-CN
en-US
```

这样即使 Claude 内部读取的是系统语言偏好，也会优先拿到中文。

### 14. 备份和替换

脚本不会直接覆盖原应用。安装前会生成时间戳备份：

```text
/Applications/Claude.backup-before-zh-CN-YYYYMMDD-HHMMSS.app
```

然后把临时目录里的补丁版应用移动回：

```text
/Applications/Claude.app
```

脚本只保留最新一个中文补丁备份，旧的 `Claude.backup-before-zh-CN-*.app` 会移动到当前用户废纸篓中的 `Claude-old-backups-*` 目录，避免 `/Applications` 长期堆积多个 Claude 副本。

安装时还会移动 `Cache`、`Code Cache`、`GPUCache`、`Service Worker` 等前端缓存目录到废纸篓。原因是 Claude Desktop 的 Electron 前端可能继续使用旧 bundle 缓存，导致补丁已经写入 app 后，模型按钮或菜单仍显示旧状态。

如果需要恢复，只要退出 Claude，删除当前 `/Applications/Claude.app`，再把备份改名为 `Claude.app` 放回 `/Applications` 即可。

## 执行流程

完整安装流程如下：

1. 检查补丁资源文件是否存在。
2. 检查目标 Claude.app 是否存在。
3. 检查原始 Claude.app 是否有虚拟化权限。
4. 如果不是 dry-run，先退出正在运行的 Claude。
5. 用 `ditto` 把 Claude.app 复制到临时目录。
6. 修改语言白名单。
7. 替换硬编码前端文案。
8. 尝试修改第三方模型校验逻辑。
9. 修改原生菜单 role 标签。
10. 合并并安装前端中文语言包。
11. 安装桌面壳层和 macOS 原生菜单中文资源。
12. 安装 Statsig 中文兜底资源。
13. 重新签名整个 app bundle。
14. 清除隔离属性。
15. 写入用户语言配置。
16. 验证中文语言包、签名和虚拟化权限。
17. 备份原应用。
18. 替换 `/Applications/Claude.app`。
19. 如果带 `--launch`，重新打开 Claude。

## 验证逻辑

脚本内置验证包括：

- 统计 `zh-CN.json` 中包含中文字符的字符串数量。
- 执行 `codesign --verify --deep --strict --verbose=2`。
- 再次检查 `com.apple.security.virtualization` 权限是否存在。
- 输出 TeamIdentifier。ad-hoc 签名下通常是 `not set`，这是预期结果。

维护时建议额外执行：

```bash
python3 -m py_compile patch_claude_zh_cn.py
python3 -m json.tool resources/frontend-zh-CN.json >/dev/null
python3 -m json.tool resources/desktop-zh-CN.json >/dev/null
python3 -m json.tool resources/statsig-zh-CN.json >/dev/null
python3 -m json.tool resources/manifest.json >/dev/null
plutil -lint resources/Localizable.strings
```

如果已经安装到本机，还可以验证当前应用签名：

```bash
codesign --verify --deep --strict --verbose=2 /Applications/Claude.app
```

## 复制到其他电脑的逻辑

复制整个项目目录到其他 Mac 后，可以直接运行 `install.command`。目标电脑需要满足：

1. 已安装官方 Claude Desktop 到 `/Applications/Claude.app`。
2. 当前用户可以授权 sudo。
3. 项目目录至少包含 `install.command`、`patch_claude_zh_cn.py` 和 `resources/`。

补丁不会依赖当前电脑的绝对项目路径。安装时所有资源都从脚本所在目录解析：

```python
ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
```

因此复制项目目录后，路径变化不会影响安装。

## 边界和风险

1. Claude Desktop 更新后，bundle 文件名、压缩代码和 i18n key 都可能变化，需要重新运行补丁，必要时补充新文案或新替换规则。
2. 硬编码替换依赖精确字符串，无法保证跨版本永久有效。
3. `app.asar` 补丁需要正确更新 integrity，否则可能导致启动失败。
4. ad-hoc 签名适合本机使用，不等同于官方开发者签名。
5. 第三方模型兼容只保证前端能显示并选择 Opus 伪装入口，不保证网关一定接受所有长上下文请求。
6. “绕过权限”会让 Claude Code 跳过部分确认流程，适合明确信任的本地环境；在不可信目录或不熟悉命令时要谨慎使用。
7. 本项目不处理账号、订阅、API Key、网关可用性、上游模型能力差异等问题。

## 后续维护建议

新增翻译或补丁时按以下优先级处理：

1. 如果文案有 i18n key，优先补充 `resources/frontend-zh-CN.json`。
2. 如果文案来自桌面壳层，补充 `resources/desktop-zh-CN.json` 或 `resources/Localizable.strings`。
3. 如果文案是压缩 JS 中的硬编码字符串，再补充 `patch_hardcoded_frontend_strings()` 的替换表。
4. 如果涉及 `app.asar`，必须确认 header、offset、size、integrity 和 `Info.plist` 都被正确更新。
5. 每次改完都运行 dry-run 或本机安装验证，并检查签名。

## 当前设计取舍

本项目选择“复制到临时目录后整体替换”，而不是直接原地修改 `/Applications/Claude.app`。原因是：

- 临时目录修改失败不会破坏当前可用的 Claude.app。
- 替换前可以完成签名和验证。
- 原应用会被完整备份，方便恢复。

本项目选择“合并官方 en-US 和中文包”，而不是直接复制固定 `zh-CN.json`。原因是：

- 官方新增 key 时可以自动回退英文，保持结构完整。
- 中文包可跨小版本复用。
- 过期 key 不会污染最终安装包。

本项目选择“保留 Opus 显示，但上下文能力跟随真实 provider 模型”，而不是把上下文窗口绑定到 `Opus 4.71M` 这个显示名。原因是：

- Claude Code 前端能力和模式判断依赖 Opus 名称。
- 用户需要保留 Opus 相关前端能力入口，但不希望上下文窗口被伪装名误导。
- Kimi、DeepSeek 或其他 provider 的上下文窗口会变化，脚本必须动态跟随配置和 `/v1/models`，不能把某一次错误里的 limit 写成长期规则。

安装脚本还会迁移 `~/Library/Application Support/Claude-3p/claude-code-sessions` 和 `local-agent-mode-sessions` 中已保存的旧会话：如果能发现真实 provider 默认模型，就把顶层 `model` 或 `session_context.model` 从 `opus` / `opus[1m]` 改成真实模型 id。这是为了避免用户恢复旧任务时，Claude Code 子进程继续按 Opus 伪装窗口组织上下文。安装时会同时退出 Claude 并终止遗留的 Claude Code / disclaimer 子进程，`--diagnose` 会通过 `runtime.active_cli_model` 报告当前是否仍有 `--model opus` 或 `--model opus[1m]`。

安装脚本还会把当前启用的第三方推理配置同步到 `~/.claude/settings.json > env`。桌面 Cowork 可以直接读取 Claude-3p 的 provider 配置，但 Code CLI 子进程更依赖 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_API_KEY`。如果另一台电脑只复制了桌面设置、旧 `~/.claude/settings.json` 或根本没有 `~/.claude/settings.json`，就会出现 Cowork 可用、Code 401 的分裂状态。当前实现会优先读取 `configLibrary/_meta.json` 的 `appliedId`，跳过 `.before-*` 旧备份配置，必要时创建最小 `~/.claude/settings.json`，避免把过期 Key 同步给 Code。

同一阶段会读取 provider 模型元数据中的 `context_length`、`contextWindow`、`max_input_tokens` 等字段，并同步到 `.claude.json` 顶层的 `tengu_hawthorn_window`。如果新电脑还没有 `.claude.json`，脚本会创建一个只包含该窗口键的最小配置，而不是继续让诊断停在 `missing`。选择 provider 默认模型时必须跳过 `opus` / `opus[1m]` / `Opus 4.71M` 这类显示别名；如果另一台电脑的手动模型列表把 Opus 放在第一项，`runtime.provider_default_ignores_opus_alias` 会记录被跳过的别名数量，并继续寻找真实第三方模型。只写 `cachedGrowthBookFeatures.tengu_hawthorn_window` 不够，因为旧 Claude Code 子进程可能不会把它当作运行时窗口；诊断项 `runtime.context_window_root_configured` 专门防止这种假通过。

这个值也会在安装时注入 Code 前端的两条显示路径：一条是历史消息里的 `## Context Usage` 文本解析逻辑，另一条是底部实时上下文弹窗的 `contextUsage.rawMaxTokens`。`--diagnose` 会记录 `code.context_usage_window_override`、`code.live_context_usage_window_override`、`runtime.provider_context_window`、`runtime.claude_code_context_window` 和 `runtime.context_window_match`：如果 provider 已经是 262144 或 1M，而 Claude Code 仍是 200000，或者实时弹窗仍显示 1.0M/200.0k，日志会直接指出不一致。

另外，恢复旧任务时即使 model 已经改成真实模型，会话历史本身仍可能太大。安装脚本不再按固定体积阈值预判，而是只在历史中已经出现 token-limit 错误时先备份，再移除历史截图 base64、thinking 和超长工具输出。这样可以保留会话文本脉络，同时避免旧历史继续触发同一类上游拒绝。

# Claude Desktop 中文补丁实现说明

本文档记录当前项目已经实现的功能、核心原理、执行流程和边界条件。它面向维护者，用来快速理解补丁为什么能生效，以及后续新增翻译或兼容逻辑时应该改哪里。

## 项目目标

本项目用于给 macOS 版 Claude Desktop 安装本地中文补丁。补丁不包含 Claude Desktop 本体，不提供账号、API Key 或第三方网关服务，只修改本机 `/Applications/Claude.app` 内的资源文件，并对修改后的应用重新签名。

当前目标分为三类：

1. 中文化：安装前端语言包、桌面壳层语言包、macOS 原生菜单翻译和部分硬编码文案。
2. 可用性：自动写入中文语言偏好，重新签名并清除隔离属性，降低补丁后无法启动的风险。
3. 第三方网关兼容：保留 `opus[1m]` 作为 Claude Code 默认模型名，让前端继续解锁 Opus 相关能力，同时允许第三方网关自行把 `opus[1m]` 路由到真实模型。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `install.command` | Finder 双击入口，负责以 sudo 调用 Python 补丁脚本。 |
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

### 6. 固定 Opus 伪装入口并保留 Kimi 真实入口

Claude Code 的一些能力和前端判断依赖默认模型名。用户需要继续保留：

```text
opus[1m]
```

而不是把本地设置改成 `kimi-for-coding`、`Kimi-k2.6`、DeepSeek 或其他第三方模型名。

当前实现分三层：

1. 本项目不修改 `~/.claude/settings.json` 中的 `model` 字段。
2. 前端模型识别函数会被补丁成：如果当前模型是 `opus` 或 `opus[1m]`，并且第三方网关返回了非空模型列表，就把 `opus[1m]` 视为有效模型，但返回值仍然是 `opus[1m]`。
3. Code 页面模型菜单会固定重建为 `Opus 4.71M` 和 `Kimi-k2.6` 两项，再追加完整强度菜单。
4. 默认对话和 Claude Code 分别走 `baku_model` 与 `ccr_model` / `cowork_model` 路径。本项目会同时补丁这两条路径，普通默认对话不再回落到 `Sonnet 4.6`。

补丁后的关键逻辑等价于：

```js
if ((e === "opus" || e === "opus[1m]") && t.length > 0) return e;
```

含义是：

- `e` 是当前默认模型，例如 `opus[1m]`。
- `t` 是前端拿到的可用模型列表。
- 只要列表非空，前端就不再报“默认模型无法识别”。
- 返回值仍是 `e`，所以界面和会话默认模型继续显示 `opus[1m]`。

这只解决 Claude Desktop 前端识别和功能解锁问题。实际请求仍会带着 `opus[1m]` 发给网关，所以第三方网关必须自己把 `opus[1m]` 路由到真实模型。否则，上游仍可能因为不认识 `opus[1m]` 而拒绝请求。

模型菜单里的两项含义不同：

- `Opus 4.71M` 是固定伪装入口。它的真实 model id 始终是 `opus[1m]`，用于继续解锁 Claude Code 里依赖 Opus 名称的能力；实际路由由第三方网关处理。
- `Kimi-k2.6` 是真实模型入口。补丁会优先使用网关返回列表里的真实 Kimi model id；如果网关暂时没有返回 Kimi，但当前会话已经保存了 Kimi id，也会沿用该 id；如果两者都没有，则用 `kimi-for-coding` 作为实际 id。它不会伪装成 Opus，也不会再用显示名制造第二个 Kimi 项。

默认对话路径里，原始默认值是 `claude-sonnet-4-6`。补丁会把它改为 `opus[1m]`，并在普通对话模型列表里固定插入 `Opus 4.71M`。这样新建普通对话时不会因为找不到可选模型而显示空白，也不会在发送消息后自动变成 `Sonnet 4.6`。

Code 页面强度菜单不再只依赖官方 `Od(W)` 能力判断。当前模型为 `opus`、`opus[1m]` 或 Kimi-k2.6 时，会强制保留五档：

```text
低 / 中 / 高 / 超高 / 最大
```

选择强度仍走原生 `setEffortLevel` / `hs(e)` 流程，不另建第二套 localStorage 强度系统。创建或切换会话时，`effort` 对 Opus 和 Kimi 都会继续传递。

普通对话模型菜单仍然需要读取模型候选项上的能力元数据。因此固定 Opus 项不能只写 `{ model, name }`，必须保留 `thinking_modes` 等字段。当前实现会从网关返回的模型列表中寻找可复用的 Opus 或支持思考模式的模型项作为模板，再生成固定的 `Opus 4.71M` 入口。

底部模型按钮的强度标签还会走另一处 `Gft()` 模式读取逻辑，它读取的是原始 `allModelOptions`，不一定包含固定注入后的 `opus[1m]`。因此补丁也会让 `Gft()` 在当前模型为 `opus` / `opus[1m]` 且原始列表找不到时，回退到带 `thinking_modes` 的模型模板，保证 `Opus 4.71M · 高` 这类显示不丢失。

底部触发器本身也有显示对象兜底：如果当前模型或默认模型指向 Opus，但候选列表暂时没有对应显示项，会临时使用 `{ model: "opus[1m]", name: "Opus 4.71M" }` 渲染按钮，防止只剩强度标签。

最终渲染前还会检查 `Vft(W)` 的结果。如果显示名为空，会兜底使用 `Opus 4.71M`，保留原本计算出来的强度标签，避免再次出现只有 `· 高` 的状态。

模型按钮组件 `Wft()` 内部也有同样兜底：如果格式化结果为空，直接渲染 `Opus 4.71M`。

模型名格式化函数 `Vft()` 也会把 `opus` / `opus[1m]` 直接显示为 `Opus 4.71M`，避免部分普通对话入口绕过候选项名称后又显示原始 id。

如果父组件传入的 `contextModel` 还是旧 `kimi-for-coding`，模型选择器内部也会先归一为 `opus[1m]`，再去计算显示名和强度模式。

早期补丁曾把普通默认对话模型写成 `kimi-for-coding`。这个值在默认对话模型列表中通常不存在，会导致前端拿不到当前模型的显示名，只剩下强度标签。当前实现会在默认对话路径把旧的 `kimi-for-coding` 归一为 `opus[1m]`。Code 页面模型菜单则单独处理：`Kimi-k2.6` 只是显示名，实际选择时传入网关可识别的 Kimi id，兜底为 `kimi-for-coding`。

### 7. 可选的第三方模型校验补丁

脚本包含 `patch_custom3p_model_validation()`，尝试修改 `app.asar` 中的第三方模型名校验逻辑。

这个补丁点依赖 Claude 内部压缩代码格式。如果当前版本找不到预期 anchor，脚本会输出警告并继续执行，不会中断整个中文补丁安装。

这是有意设计的降级策略：中文化和签名流程优先保证可执行；模型校验补丁属于兼容增强，失败时不应该让整个安装失败。

### 8. app.asar 修改和完整性更新

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

### 9. 重新签名

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

### 10. 清除隔离属性

脚本会执行：

```bash
xattr -dr com.apple.quarantine /Applications/Claude.app
```

这用于减少 macOS 的“应用已损坏”“无法验证开发者”等提示。清除隔离属性不能替代签名，两个步骤都需要保留。

### 11. 用户语言配置

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

### 12. 备份和替换

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
5. 第三方模型兼容只保证前端认为 `opus[1m]` 有效，不保证网关一定接受 `opus[1m]`。
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

本项目选择“保留 `opus[1m]` 显示和本地设置”，而不是把模型改成第三方模型名。原因是：

- Claude Code 前端能力和模式判断依赖 Opus 名称。
- 用户希望第三方模型在网关层伪装成 Opus，而不是让桌面端失去 Opus 相关功能。
- 这样模型路由责任清晰：桌面端保持 `opus[1m]`，网关负责把它转发到真实模型。

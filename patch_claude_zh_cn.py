#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-click zh-CN patcher for Claude Desktop on macOS.

What it does:
1. Copies /Applications/Claude.app to a temporary working app.
2. Adds zh-CN to Claude Desktop's language whitelist.
3. Installs Chinese desktop-shell and frontend i18n resources.
4. Sets the current user's Claude config locale to zh-CN.
5. Moves the original app to a timestamped backup and installs the patched app.

Run from this folder:
    sudo /usr/bin/python3 patch_claude_zh_cn.py --user-home "$HOME"
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


APP_DEFAULT = Path("/Applications/Claude.app")
LANG_CODE = "zh-CN"
ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"

FRONTEND_TRANSLATION = RESOURCES / "frontend-zh-CN.json"
DESKTOP_TRANSLATION = RESOURCES / "desktop-zh-CN.json"
LOCALIZABLE_STRINGS = RESOURCES / "Localizable.strings"

FRONTEND_I18N_REL = Path("Contents/Resources/ion-dist/i18n")
FRONTEND_ASSETS_REL = Path("Contents/Resources/ion-dist/assets/v1")
DESKTOP_RESOURCES_REL = Path("Contents/Resources")

LANG_LIST_RE = re.compile(
    r'\["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"(.*?)\]'
)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def require_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")


def read_entitlements(path: Path) -> str:
    return run(["codesign", "-d", "--entitlements", "-", str(path)], check=False).stdout


def load_entitlements(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        data = plistlib.loads(result.stdout)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def require_virtualization_entitlement(app: Path) -> None:
    entitlements = read_entitlements(app)
    if "com.apple.security.virtualization" not in entitlements:
        raise SystemExit(
            "Claude.app does not have the required virtualization entitlement. "
            "Restore or reinstall the official Claude.app first, then run this patcher again."
        )


def quit_claude() -> None:
    run(["osascript", "-e", 'tell application "Claude" to quit'], check=False)


def copy_app(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    print(f"Copying app to temporary workspace: {dst}")
    run(["ditto", str(src), str(dst)])


def patch_language_whitelist(app: Path) -> Path:
    assets_dir = app / FRONTEND_ASSETS_REL
    candidates = sorted(assets_dir.glob("index-*.js"))
    if not candidates:
        raise SystemExit(f"Cannot find frontend index bundle in {assets_dir}")

    for path in candidates:
        text = path.read_text(encoding="utf-8")
        if '"zh-CN"' in text:
            print(f"Language whitelist already contains zh-CN: {path.name}")
            return path
        if LANG_LIST_RE.search(text):
            patched = LANG_LIST_RE.sub(
                '["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID","zh-CN"]',
                text,
                count=1,
            )
            path.write_text(patched, encoding="utf-8")
            print(f"Patched language whitelist: {path.name}")
            return path

    raise SystemExit("Could not patch language whitelist. Claude's bundle format may have changed.")


def patch_hardcoded_frontend_strings(app: Path) -> None:
    assets_dir = app / FRONTEND_ASSETS_REL
    replacements = {
        '"New task"': '"新建任务"',
        '"New session"': '"新会话"',
        '"Drag to pin"': '"拖到此处固定"',
        '"Drop here"': '"拖到此处"',
        '"Let go"': '"松开"',
        '"Recents"': '"最近使用"',
        '"View all"': '"查看全部"',
        'title:"Connection"': 'title:"连接"',
        'description:"Choose where Claude Desktop sends inference requests."': 'description:"选择 Claude Desktop 发送推理请求的位置。"',
        'title:"Sandbox & workspace"': 'title:"沙盒与工作区"',
        'title:"Connectors & extensions"': 'title:"连接器与扩展"',
        'title:"Telemetry & updates"': 'title:"遥测与更新"',
        'title:"Usage limits"': 'title:"使用限制"',
        'title:"Plugins & skills"': 'title:"插件与技能"',
        'banner:"Plugins and skills aren\'t set in this configuration. Mount plugin bundles to the folder below using your device-management tool and Cowork will load them at launch."': 'banner:"插件和技能未在此配置中设置。请使用你的设备管理工具将插件包挂载到下方文件夹，Cowork 会在启动时加载它们。"',
        'caption:"Drop plugin folders here. Read-only to the app."': 'caption:"将插件文件夹拖放到这里。应用对此目录为只读。"',
        'title:"Egress Requirements"': 'title:"出站要求"',
        'description:"Hosts your network firewall must allow, derived from your current settings. This list is read-only and updates as you make changes. Traffic is HTTPS on port 443 unless a custom port is specified (OTLP, gateway, or MCP server URLs)."': 'description:"根据当前设置推导出的、主机网络防火墙必须放行的主机。此列表为只读，并会随着你的更改自动更新。除非指定了自定义端口（OTLP、网关或 MCP 服务器 URL），否则流量均为 443 端口上的 HTTPS。"',
        'label:"macOS configuration profile"': 'label:"macOS 配置描述文件"',
        'label:"Windows registry file"': 'label:"Windows 注册表文件"',
        'label:"Plain JSON"': 'label:"纯 JSON"',
        'label:"Firewall allowlist (.txt)"': 'label:"防火墙允许列表（.txt）"',
        'label:"Copy to clipboard (redacted)"': 'label:"复制到剪贴板（已脱敏）"',
        'title:"Source"': 'title:"来源"',
        'group:"Identity & models"': 'group:"身份与模型"',
        'hint:"First entry is the picker default. Aliases like sonnet, opus accepted. Optional for gateway — when set, the picker shows exactly this list instead of /v1/models discovery. Turn on 1M context only for models your provider actually serves with the extended window."': 'hint:"第一项是选择器默认值。支持 sonnet、opus 等别名。网关可选；设置后，选择器会显示此列表，而不是通过 /v1/models 发现。仅当提供商实际支持扩展窗口时才开启 1M 上下文。"',
        'label:"Model ID"': 'label:"模型 ID"',
        'label:"Offer 1M-context variant"': 'label:"提供 1M 上下文变体"',
        'hint:"Tags telemetry events with your org so support can find them. Not used for auth."': 'hint:"为遥测事件标记你的组织，便于支持团队定位。不会用于认证。"',
        'title:"Skip login-mode chooser"': 'title:"启动时跳过登录方式选择"',
        'hint:"Go straight to this provider at launch — users won\\\'t see the option to sign in to Anthropic instead."': 'hint:"启动后直接进入这个提供商，用户将不会看到改为登录 Anthropic 的选项。"',
        'title:"Gateway base URL"': 'title:"网关基础 URL"',
        'description:"Full URL of the inference gateway endpoint."': 'description:"推理网关端点的完整地址。"',
        'title:"Gateway API key"': 'title:"网关 API 密钥"',
        'title:"Gateway auth scheme"': 'title:"网关认证方案"',
        'description:"How to send the gateway credential. \'bearer\' (default) sends Authorization: Bearer. Set \'x-api-key\' only if your gateway requires the x-api-key header instead (e.g. api.anthropic.com). Set \'sso\' to obtain the credential via the gateway\'s own browser-based sign-in (RFC 8414 discovery at `<inferenceGatewayBaseUrl>/.well-known/oauth-authorization-server` + RFC 8628 device-code grant); inferenceGatewayApiKey and inferenceCredentialHelper are not required."': 'description:"如何发送网关凭据。bearer（默认）发送 Authorization: Bearer。仅当网关要求 x-api-key 请求头时才设置为 x-api-key（例如 api.anthropic.com）。设置为 sso 时，将通过网关自己的浏览器登录获取凭据（RFC 8414 发现 + RFC 8628 设备码授权）；无需 inferenceGatewayApiKey 和 inferenceCredentialHelper。"',
        'title:"Gateway extra headers"': 'title:"网关额外请求头"',
        'description:"Extra HTTP headers sent on every inference request. JSON array of \'Name: Value\' strings."': 'description:"每次推理请求都会附带的额外 HTTP 请求头。格式为“名称: 值”字符串组成的 JSON 数组。"',
        'hint:"Bearer (default) sends Authorization: Bearer. x-api-key is for the Anthropic API directly — auto-selected when the URL is *.anthropic.com."': 'hint:"Bearer（默认）发送 Authorization: Bearer。x-api-key 用于直连 Anthropic API；当 URL 为 *.anthropic.com 时会自动选择。"',
        'hint:"Extra headers sent to the gateway, one \'Name: Value\' per entry. For tenant routing, org IDs, etc."': 'hint:"发送到网关的额外请求头，每项格式为“名称: 值”。可用于租户路由、组织 ID 等。"',
        'body:"Sent on every inference and `/v1/models` discovery request (joined into the CLI\'s `ANTHROPIC_CUSTOM_HEADERS`).\\n\\nUse this for fleet-wide constants. For per-user or per-session values, have the **credential helper script** emit JSON with a `headers` field — those are merged over these static entries (helper wins on conflict)."': 'body:"每次推理和 `/v1/models` 发现请求都会发送这些请求头（会合并到 CLI 的 `ANTHROPIC_CUSTOM_HEADERS`）。\\n\\n适合填写全局固定值。针对单个用户或会话的值，请让**凭据辅助脚本**输出包含 `headers` 字段的 JSON；这些值会覆盖此处的静态项（冲突时辅助脚本优先）。"',
        'title:"Inference provider"': 'title:"推理提供商"',
        'description:"Selects the inference backend. Setting this key activates third-party mode."': 'description:"选择推理后端。设置此项会启用第三方模式。"',
        'title:"GCP project ID"': 'title:"GCP 项目 ID"',
        'title:"GCP region"': 'title:"GCP 区域"',
        'title:"GCP credentials file path"': 'title:"GCP 凭据文件路径"',
        'title:"Vertex OAuth client ID"': 'title:"Vertex OAuth 客户端 ID"',
        'title:"Vertex OAuth client secret"': 'title:"Vertex OAuth 客户端密钥"',
        'title:"Vertex OAuth scopes"': 'title:"Vertex OAuth 范围"',
        'title:"Vertex AI base URL"': 'title:"Vertex AI 基础 URL"',
        'title:"AWS region"': 'title:"AWS 区域"',
        'title:"AWS bearer token"': 'title:"AWS Bearer 令牌"',
        'title:"Bedrock base URL"': 'title:"Bedrock 基础 URL"',
        'title:"AWS profile name"': 'title:"AWS 配置文件名称"',
        'title:"AWS config directory"': 'title:"AWS 配置目录"',
        'title:"Bedrock service tier"': 'title:"Bedrock 服务层级"',
        'title:"Azure AI Foundry resource name"': 'title:"Azure AI Foundry 资源名称"',
        'title:"Azure AI Foundry API key"': 'title:"Azure AI Foundry API 密钥"',
        'title:"Model list"': 'title:"模型列表"',
        'title:"Managed MCP servers"': 'title:"托管的 MCP 服务器"',
        'description:\'JSON array of MCP server configs. Each entry: `name` (string, required, unique within array), `url` (https URL, required), `transport` ("http" or "sse", default "http"), `headers` (string→string map, optional, mutually exclusive with `oauth`), `headersHelper` (absolute path to local executable that prints a JSON object of HTTP headers on stdout — for rotating bearers; optional, mutually exclusive with `oauth`; merged over `headers`, helper wins on conflict. The helper runs with the app\'s launch environment, not your shell rc — read credentials from keychain/file or source them explicitly in the script), `headersHelperTtlSec` (positive integer, default 300 — re-runs the helper at most once per TTL across connection attempts), `oauth` (boolean or object, optional — `true` triggers dynamic-registration PKCE; `{"clientId":"<id>"}` skips registration and uses a pre-registered public client (register redirect URI `http://127.0.0.1:53280/callback` on it — Entra/Google accept the portless `http://127.0.0.1/callback`, but providers that match the port exactly need 53280). Optional `tenantId` (Entra Directory ID) pins the authorization server for single-tenant apps; `scope` is required when `tenantId` is set), `toolPolicy` (toolName→"allow"/"ask"/"blocked", optional — locks the per-tool approval state; unset = user controls). Connections are made from a host-side utility process and do not pass through the in-VM allowlist.\'': 'description:\'MCP 服务器配置的 JSON 数组。每项包含：`name`（字符串，必填，数组内唯一）、`url`（https URL，必填）、`transport`（"http" 或 "sse"，默认 "http"）、`headers`（字符串到字符串映射，可选，与 `oauth` 互斥）、`headersHelper`（本地可执行文件绝对路径，会向 stdout 输出 HTTP 请求头 JSON 对象，用于轮换 bearer；可选，与 `oauth` 互斥；会覆盖合并到 `headers`，冲突时辅助脚本优先）、`headersHelperTtlSec`（正整数，默认 300，在 TTL 内连接时最多重新运行一次）、`oauth`（布尔值或对象，可选）、`toolPolicy`（工具名到 "allow"/"ask"/"blocked"，可选，用于锁定每个工具的批准状态；未设置则由用户控制）。连接由主机侧工具进程发起，不经过虚拟机内允许列表。\'',
        'title:"Organization UUID"': 'title:"组织 UUID"',
        'title:"Credential helper script"': 'title:"凭据辅助脚本"',
        'description:"Absolute path to an executable that prints the inference credential to stdout. When set, the static inferenceGatewayApiKey / inferenceFoundryApiKey is optional."': 'description:"可执行文件的绝对路径，该文件会将推理凭据输出到标准输出。设置后，可不填写静态 inferenceGatewayApiKey / inferenceFoundryApiKey。"',
        'hint:"Absolute path to an executable that prints the credential."': 'hint:"输出凭据的可执行文件绝对路径。"',
        'body:\'Claude runs the executable with no arguments and reads **stdout** (trimmed). Exit code must be `0`; any output on **stderr** is logged but ignored. **Stdout must be the credential only** — no banners, prompts, or log lines.\\n\\n**Output format** — either:\\n- a single bare token (the API key / bearer token), or\\n- a JSON object `{"token": "...", "headers": {"Name": "Value", ...}}` when per-request headers are needed (gateway provider only; merged over **Gateway extra headers**, helper wins on conflict)\\n\\nResult is cached for the TTL below. On TTL expiry the helper is re-invoked transparently — no user prompt, no relaunch.\\n\\n**Typical use:** a shell script that pulls from Keychain, 1Password CLI, or an internal secret broker. Example:\\n\\n`security find-generic-password -s anthropic-api -w`\\n\\nIf this field is set, static credential fields (API key, bearer token) are ignored. The helper always wins.\'': 'body:\'Claude 会在不带参数的情况下运行该可执行文件，并读取修剪后的 **标准输出**。退出码必须为 `0`；**标准错误** 的任何输出会被记录但忽略。**标准输出必须只包含凭据**，不能有横幅、提示或日志行。\\n\\n**输出格式**二选一：\\n- 单个纯令牌（API key / bearer token），或\\n- 需要按请求附加请求头时，输出 JSON 对象 `{"token": "...", "headers": {"Name": "Value", ...}}`（仅适用于网关提供商；会与**网关额外请求头**合并，冲突时以辅助脚本为准）。\\n\\n结果会按下方 TTL 缓存。TTL 过期后会自动重新调用辅助脚本，无需用户确认，也无需重启。\\n\\n**常见用法：**通过 shell 脚本从钥匙串、1Password CLI 或内部密钥代理中读取凭据。例如：\\n\\n`security find-generic-password -s anthropic-api -w`\\n\\n设置此字段后，静态凭据字段（API key、bearer token）会被忽略，始终以辅助脚本输出为准。\'',
        'title:"Credential helper TTL"': 'title:"凭据辅助脚本 TTL"',
        'description:"Helper output is cached for this many seconds. Default 3600. Re-runs at the next session start after expiry."': 'description:"辅助脚本输出缓存的秒数。默认 3600。过期后会在下一次会话开始时重新运行。"',
        'title:"Allow desktop extensions"': 'title:"允许桌面扩展"',
        'description:"Permit users to install local desktop extensions (.dxt/.mcpb)."': 'description:"允许用户安装本地桌面扩展（.dxt/.mcpb）。"',
        'egressRequirementsLabel:"Desktop extensions (Python runtime)"': 'egressRequirementsLabel:"桌面扩展（Python 运行时）"',
        'title:"Show extension directory"': 'title:"显示扩展目录"',
        'description:"Show the Anthropic extension directory in the connectors UI."': 'description:"在连接器界面显示 Anthropic 扩展目录。"',
        'title:"Require signed extensions"': 'title:"要求扩展已签名"',
        'description:"Reject desktop extensions that are not signed by a trusted publisher."': 'description:"拒绝未由受信任发布者签名的桌面扩展。"',
        'title:"Allow user-added MCP servers"': 'title:"允许用户添加 MCP 服务器"',
        'description:"Permit users to add their own local (stdio) MCP servers via Developer settings. HTTP/SSE servers are managed separately. When false, only servers from the Managed MCP servers list and org-provisioned plugins are available."': 'description:"允许用户通过开发者设置添加自己的本地（stdio）MCP 服务器。HTTP/SSE 服务器会单独管理。关闭后，仅可使用托管 MCP 服务器列表和组织预配插件中的服务器。"',
        'egressRequirementsLabel:"User-added MCP (Python runtime)"': 'egressRequirementsLabel:"用户添加的 MCP（Python 运行时）"',
        'title:"Allow Claude Code tab"': 'title:"允许 Claude Code 标签页"',
        'description:"Show the Code tab (terminal-based coding sessions). Sessions run on the host, not inside the VM."': 'description:"显示 Code 标签页（基于终端的编码会话）。会话在主机上运行，而不是在虚拟机内运行。"',
        'title:"Secure VM features"': 'title:"安全虚拟机功能"',
        'title:"Require full VM sandbox"': 'title:"要求完整虚拟机沙盒"',
        'description:"Forces the agent loop, file/web tools, and plugin-bundled MCPs to run inside the VM, disabling host-loop mode."': 'description:"强制代理循环、文件/网页工具以及插件内置 MCP 在虚拟机内运行，并禁用主机循环模式。"',
        'title:"Allowed egress hosts"': 'title:"允许的出站主机"',
        'description:`Additional hostnames the Cowork sandbox may reach (web fetch, shell commands, package installs). JSON array; supports *.example.com wildcards. The inference provider host is always allowed. Set to ["*"] to disable VM-level egress filtering entirely. Common hosts to add for dependency installs (pip/npm/apt/cargo/git): ${I.join(", ")}.`': 'description:`Cowork 沙盒可访问的额外主机名（网页抓取、Shell 命令、包安装）。JSON 数组；支持 *.example.com 通配符。推理提供商主机始终允许。设置为 ["*"] 可完全禁用虚拟机级出站过滤。依赖安装（pip/npm/apt/cargo/git）常需添加的主机：${I.join(", ")}。`',
        'egressRequirementsLabel:"Tool egress (VM sandbox)"': 'egressRequirementsLabel:"工具出站（虚拟机沙盒）"',
        'banner:"Prompts, completions, and your data are never sent to Anthropic — telemetry covers crash and usage signals only."': 'banner:"提示词、补全和你的数据绝不会发送给 Anthropic；遥测只包含崩溃和使用信号。"',
        'group:"OpenTelemetry"': 'group:"OpenTelemetry"',
        'group:"Updates"': 'group:"更新"',
        'title:"OpenTelemetry collector endpoint"': 'title:"OpenTelemetry 收集器端点"',
        'title:"OpenTelemetry resource attributes"': 'title:"OpenTelemetry 资源属性"',
        'description:"Base URL of an OpenTelemetry collector. When set, Cowork sessions export logs and metrics (prompts, tool calls, token counts) to this endpoint."': 'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会将日志和指标（提示词、工具调用、令牌计数）导出到此端点。"',
        'description:"Extra OTEL resource attributes as comma-separated key=value pairs (the standard OTEL_RESOURCE_ATTRIBUTES format). Appended to the app\'s built-in attributes; keys that collide with built-ins (e.g. service.name) are dropped. Scoped for bootstrap so per-user values can be returned at sign-in."': 'description:"额外的 OTEL 资源属性，以逗号分隔的 key=value 对填写（标准 OTEL_RESOURCE_ATTRIBUTES 格式）。会追加到应用内置属性；与内置属性冲突的键（如 service.name）会被丢弃。用于 bootstrap 时可在登录时返回按用户设置的值。"',
        'title:"Block essential telemetry"': 'title:"阻止基础遥测"',
        'description:"Blocks crash and error reports (stack traces, app state at failure, device/OS info) and performance timing data sent to Anthropic. Used to investigate bugs and monitor responsiveness."': 'description:"阻止发送给 Anthropic 的崩溃和错误报告（堆栈跟踪、故障时应用状态、设备/系统信息）以及性能计时数据。这些数据用于调查错误并监控响应性。"',
        'title:"Block nonessential telemetry"': 'title:"阻止非必要遥测"',
        'description:"Blocks product-usage analytics sent to Anthropic — feature usage, navigation patterns, UI actions."': 'description:"阻止发送给 Anthropic 的产品使用分析，包括功能使用、导航模式和界面操作。"',
        'title:"Block nonessential services"': 'title:"阻止非必要服务"',
        'description:"Blocks connector favicons (fetched from a third-party favicon service — leaks MCP hostnames) and the artifact-preview sandbox iframe. Connectors fall back to letter icons; artifacts do not render."': 'description:"阻止连接器网站图标（从第三方图标服务获取，可能泄露 MCP 主机名）和 artifact 预览沙盒 iframe。连接器会回退为字母图标，artifact 将无法渲染。"',
        'title:"Auto-update enforcement window"': 'title:"自动更新强制窗口"',
        'description:"When set, forces a pending update to install after this many hours regardless of user activity. When unset, the app uses a 72-hour window but defers installation while the user is active."': 'description:"设置后，无论用户是否正在使用，待处理更新都会在指定小时后强制安装。未设置时，应用使用 72 小时窗口，但会在用户活跃时延后安装。"',
        'title:"Block auto-updates"': 'title:"阻止自动更新"',
        'description:"Blocks the app from checking for and downloading updates from Anthropic. The app will stay on its installed version until updated by other means."': 'description:"阻止应用检查并下载来自 Anthropic 的更新。应用会保持当前已安装版本，直到通过其他方式更新。"',
        'suffix:"hours"': 'suffix:"小时"',
        'title:"Disable essential telemetry"': 'title:"禁用基础遥测"',
        'description:"Disable essential crash and performance telemetry."': 'description:"禁用基础崩溃和性能遥测。"',
        'title:"Disable auto updates"': 'title:"禁用自动更新"',
        'description:"Prevent Claude Desktop from checking for updates automatically."': 'description:"阻止 Claude Desktop 自动检查更新。"',
        'title:"Daily message limit"': 'title:"每日消息限制"',
        'description:"Maximum number of messages a user can send per day."': 'description:"用户每天可发送的最大消息数。"',
        'title:"Max tokens per window"': 'title:"每窗口最大令牌数"',
        'description:"Total input+output tokens permitted per window before further messages are refused. Unset = no cap."': 'description:"每个窗口允许的输入和输出令牌总数；超过后将拒绝继续发送消息。未设置表示不限制。"',
        'title:"Token cap window"': 'title:"令牌限制窗口"',
        'description:"Tumbling window length for the token cap. Max 720 hours (30 days). The counter resets at the end of each window."': 'description:"令牌限制的滚动窗口长度。最大 720 小时（30 天）。每个窗口结束时计数器会重置。"',
        'hint:"Crash and performance reports to Anthropic."': 'hint:"将崩溃和性能报告发送给 Anthropic。"',
        'hint:"Product-usage analytics and diagnostic-report uploads. No message content."': 'hint:"产品使用分析和诊断报告上传。不包含消息内容。"',
        'hint:"Favicon fetch and the artifact-preview iframe origin. Artifacts will not render."': 'hint:"网站图标获取和 artifact 预览 iframe 的来源。Artifacts 将无法渲染。"',
        'hint:"Stop Cowork from fetching updates. You\'ll need to push new versions yourself."': 'hint:"阻止 Cowork 获取更新。后续新版本需要由你自行推送。"',
        'hint:"Hours before a downloaded update force-installs. Blank = 72-hour default."': 'hint:"已下载更新会在多少小时后强制安装。留空则使用默认的 72 小时。"',
        'hint:"Where Cowork sends OpenTelemetry logs and metrics. Leave blank to disable."': 'hint:"Cowork 会将 OpenTelemetry 日志和指标发送到哪里。留空表示禁用。"',
        'hint:"grpc or http/protobuf."': 'hint:"支持 grpc 或 http/protobuf。"',
        'hint:"Optional auth headers for the collector."': 'hint:"发送给收集器的可选认证请求头。"',
        'hint:"Extra resource attributes to attach to every span/metric, e.g. enduser.id=alice@example.com."': 'hint:"附加到每个 span/metric 的额外资源属性，例如 enduser.id=alice@example.com。"',
        'hint:"Per-user soft cap, counted client-side over the duration below. Not a server-enforced quota."': 'hint:"按用户设置的软限制，在下方时长范围内由客户端统计。不是服务器强制执行的配额。"',
        'reason:"Security and compatibility fixes will not install automatically. Make sure IT has another distribution path."': 'reason:"安全和兼容性修复不会自动安装。请确保 IT 有其他分发路径。"',
        'reason:"Usage analytics help us prioritize improvements for third-party inference. Diagnostic-report uploads will also be blocked. No message content is included in either."': 'reason:"使用分析可帮助我们优先改进第三方推理。诊断报告上传也会被阻止。两者都不包含消息内容。"',
        'reason:"This disables artifact previews and connector icons. Artifacts will not render in conversations."': 'reason:"这会禁用 artifact 预览和连接器图标。Artifact 将不会在对话中渲染。"',
        'body:"\\"Essential\\" means the signals Anthropic needs to keep your deployment working: **crash stacks**, **startup failure reasons**, and **version/OS metadata**. No prompts, completions, file contents, or identifiers beyond a random install ID.\\n\\n**What you lose when this is on:** when a Cowork build hits a bug that only reproduces on your OS version or locale, Anthropic can\'t see it unless a user manually reports. Fixes ship slower.\\n\\n**Why this is discouraged, not blocked:** some air-gapped environments require zero outbound telemetry as a matter of policy. The switch exists for them — if you don\'t have that constraint, leave it off."': 'body:"\\"基础\\"是指 Anthropic 为保持你的部署正常运行所需的信号：**崩溃堆栈**、**启动失败原因**以及**版本/系统元数据**。不包含提示词、补全、文件内容，也不包含随机安装 ID 之外的标识符。\\n\\n**开启后会失去什么：**当 Cowork 构建遇到只在你的系统版本或区域设置上复现的问题时，除非用户手动报告，否则 Anthropic 无法看到，修复发布会更慢。\\n\\n**为什么这是不推荐而不是禁止：**某些隔离网络环境因策略要求零出站遥测。此开关就是为这些环境准备的；如果你没有这类约束，请保持关闭。"',
        'body:\'"Nonessential" covers two things: **product-usage analytics** (which features get used, navigation patterns — no prompts or completions) and the **Send** action in Help → Generate Diagnostic Report. Turning this on stops both.\\n\\nDestination for both: `claude.ai`. Already listed under Egress Requirements → Nonessential telemetry.\'': 'body:\'"非必要"包括两类内容：**产品使用分析**（使用了哪些功能、导航模式；不包含提示词或补全）以及「帮助 → 生成诊断报告」中的**发送**操作。开启后会同时停止两者。\\n\\n两者的目标地址都是 `claude.ai`，已列在「出站要求 → 非必要遥测」下。\'',
        'title:"Disabled built-in tools"': 'title:"禁用内置工具"',
        'description:\'JSON array of tool names to remove from the agent tool list (e.g. ["WebSearch"]).\'': 'description:\'要从代理工具列表中移除的工具名称 JSON 数组（例如 ["WebSearch"]）。\'',
        'title:"Allowed workspace folders"': 'title:"允许的工作区文件夹"',
        'description:"JSON array of absolute paths the user may attach as workspace folders. A leading ~ expands to the per-user home directory. Unset means unrestricted."': 'description:"用户可附加为工作区文件夹的绝对路径 JSON 数组。开头的 ~ 会展开为对应用户的主目录。未设置表示不限制。"',
        'hint:"Domains Cowork\'s tools may reach during a turn. Also surfaced under Egress Requirements."': 'hint:"Cowork 工具在一次回合中可访问的域名。也会显示在出站要求中。"',
        'body:"Only affects **tool calls** — inference and MCP traffic are covered by their own allowlists elsewhere.\\n\\nAccepts exact hostnames (`api.github.com`), wildcards (`*.corp.com` matches one subdomain level), and `*` to allow all.\\n\\nWildcards don\'t cross schemes. `*.corp.com` matches `docs.corp.com` but not `corp.com` itself — add both if you need the apex.\\n\\nIP literals and localhost always resolve regardless of this list; this is a public-egress filter, not a sandbox.\\n\\nHosts you add here also need to be open on your network firewall — see **Egress Requirements** for the full allowlist."': 'body:"仅影响**工具调用**；推理和 MCP 流量由其他位置各自的允许列表控制。\\n\\n支持精确主机名（`api.github.com`）、通配符（`*.corp.com` 匹配一级子域）以及用于允许全部的 `*`。\\n\\n通配符不会跨层级匹配。`*.corp.com` 会匹配 `docs.corp.com`，但不匹配 `corp.com` 本身；如需顶级域，请同时添加两者。\\n\\n无论此列表如何设置，IP 字面量和 localhost 始终可解析；这是公共出站过滤器，不是沙盒。\\n\\n你在此处添加的主机也需要在网络防火墙中放行；完整允许列表请参见**出站要求**。"',
        'hint:"Folders users may attach as a workspace. Leave unset for unrestricted access."': 'hint:"用户可附加为工作区的文件夹。留空表示不限制访问。"',
        'hint:"Built-in tools removed from Cowork."': 'hint:"从 Cowork 中移除的内置工具。"',
        'group:"Extensions"': 'group:"扩展"',
        'group:"MCP servers"': 'group:"MCP 服务器"',
        'group:"Anthropic telemetry"': 'group:"Anthropic 遥测"',
        'hint:".dxt and .mcpb installs."': 'hint:".dxt 和 .mcpb 安装。"',
        'hint:"The in-app catalogue of installable extensions. Hide to allow sideload only."': 'hint:"应用内可安装扩展目录。隐藏后仅允许侧载。"',
        'hint:"Local stdio servers added via the Developer settings. Remote servers come from the managed list above, or plugins mounted to a user\'s computer by an organization admin."': 'hint:"通过开发者设置添加的本地 stdio 服务器。远程服务器来自上方托管列表，或来自组织管理员挂载到用户电脑的插件。"',
        'hint:"Org-pushed remote MCP servers. May embed bearer tokens."': 'hint:"组织推送的远程 MCP 服务器。可能嵌入 Bearer 令牌。"',
        'label:"Name"': 'label:"名称"',
        'label:"Transport"': 'label:"传输方式"',
        'label:"Headers"': 'label:"请求头"',
        'label:"Headers helper script"': 'label:"请求头辅助脚本"',
        'label:"Helper cache TTL (sec)"': 'label:"辅助缓存 TTL（秒）"',
        'placeholder:"Absolute path"': 'placeholder:"绝对路径"',
        '"Scheduled"': '"定时任务"',
        '"Pinned"': '"已固定"',
        '"What’s up next?"': '"接下来做什么？"',
        '"Let\'s knock something off your list"': '"先把清单上的一件事做完"',
        'label:"Projects"': 'label:"项目"',
        'label:"Scheduled"': 'label:"计划任务"',
        'label:"Customize"': 'label:"自定义"',
    }
    patched_files = 0
    patched_strings = 0

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched = text
        count = 0
        for source, target in replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    print(f"Patched hardcoded frontend strings: {patched_strings} replacements in {patched_files} files")


def pad_utf8(source: str, target: str) -> bytes:
    source_len = len(source.encode("utf-8"))
    target_bytes = target.encode("utf-8")
    if len(target_bytes) > source_len:
        raise SystemExit(f"Replacement is longer than source: {source} -> {target}")
    return target_bytes + (b" " * (source_len - len(target_bytes)))


def patch_hardcoded_desktop_strings(app: Path) -> None:
    asar = app / DESKTOP_RESOURCES_REL / "app.asar"
    if not asar.exists():
        print(f"Skipping hardcoded desktop strings: {asar} not found")
        return

    replacements = {
        "Enable Main Process Debugger": "启用主进程调试器",
        "Record Performance Trace": "记录性能跟踪",
        "Write Main Process Heap Snapshot": "写入主进程堆快照",
        "Record Memory Trace (auto-stop)": "记录内存跟踪（自动）",
    }
    data = asar.read_bytes()
    patched = data
    patched_strings = 0

    for source, target in replacements.items():
        source_bytes = source.encode("utf-8")
        occurrences = patched.count(source_bytes)
        if occurrences:
            patched = patched.replace(source_bytes, pad_utf8(source, target))
            patched_strings += occurrences

    if patched != data:
        asar.write_bytes(patched)

    print(f"Patched hardcoded desktop strings: {patched_strings} replacements in app.asar")


def update_asar_integrity(app: Path) -> None:
    asar = app / DESKTOP_RESOURCES_REL / "app.asar"
    if not asar.exists():
        print(f"Skipping ASAR integrity update: {asar} not found")
        return

    digest = hashlib.sha256(asar.read_bytes()).hexdigest()
    updated = 0
    for plist_path in (app / "Contents").rglob("Info.plist"):
        try:
            with plist_path.open("rb") as f:
                data = plistlib.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        integrity = data.get("ElectronAsarIntegrity")
        if not isinstance(integrity, dict):
            continue
        entry = integrity.get("Resources/app.asar")
        if not isinstance(entry, dict):
            continue
        if entry.get("hash") == digest:
            continue
        entry["algorithm"] = "SHA256"
        entry["hash"] = digest
        with plist_path.open("wb") as f:
            plistlib.dump(data, f, fmt=plistlib.FMT_XML, sort_keys=False)
        updated += 1

    print(f"Updated Electron ASAR integrity hash in {updated} Info.plist files")


def merge_frontend_locale(app: Path) -> tuple[int, int, int]:
    source = app / FRONTEND_I18N_REL / "en-US.json"
    target = app / FRONTEND_I18N_REL / "zh-CN.json"
    require_file(source)
    require_file(FRONTEND_TRANSLATION)

    en = load_json(source)
    zh_pack = load_json(FRONTEND_TRANSLATION)
    if not isinstance(en, dict) or not isinstance(zh_pack, dict):
        raise SystemExit("Unsupported frontend i18n JSON shape.")

    merged: dict[str, Any] = {}
    translated = 0
    fallback = 0
    for key, value in en.items():
        if key in zh_pack:
            merged[key] = zh_pack[key]
            if zh_pack[key] != value:
                translated += 1
        else:
            merged[key] = value
            fallback += 1

    save_json(target, merged)
    extra = len(set(zh_pack) - set(en))
    print(f"Installed frontend zh-CN: {translated} translated, {fallback} fallback, {extra} extra old keys ignored")
    return translated, fallback, extra


def install_desktop_locale(app: Path) -> None:
    resources_dir = app / DESKTOP_RESOURCES_REL
    require_file(DESKTOP_TRANSLATION)
    require_file(LOCALIZABLE_STRINGS)

    desktop_zh = load_json(DESKTOP_TRANSLATION)
    if not isinstance(desktop_zh, dict):
        raise SystemExit("Unsupported desktop i18n JSON shape.")
    desktop_zh.update(
        {
            "+5pVgQAZZr": "此 URL 正在覆盖的内容",
            "+6PpsCazwm": "端点拒绝了请求。请检查证书信任、IP 白名单或认证请求头。",
            "/m74yeDEuy": "重启以应用此配置。",
            "080C5vUX5n": "未使用 — 打开开关以获取并应用此 URL。",
            "1WcIuIpt9o": "该 URL 返回了 200，但响应正文不是有效的配置文档。",
            "2BEeBiSuPX": "尚未同步",
            "2d4J+BFU/w": "由你的组织管理 — 只读",
            "3VG8rbxa4v": "发生未知错误",
            "3n/mD6y89N": "部分内容 — 不能单独作为有效文件",
            "47FYwba+bI": "取消",
            "62nsdyMyIO": "重试",
            "6RINGEEO1W": "修正 {field} 的无效值",
            "6kv8VKLwHa": "Claude 正在使用缓存的响应。成功同步前，URL 上的新变更不会生效。",
            "77MbQyFrM3": "已登录",
            "79lKyOa3xd": "搜索结果",
            "7xYRPYgjLF": "由引导 URL 设置 · <h>{host}</h>",
            "2hsoiXnrxk": "+ 添加",
            "1gKYoIkZf0": "{reachable} / {total} 可访问",
            "3NCn5oLFoE": "在文件管理器中显示",
            "83Dth0tmbB": "下载 .txt",
            "8tJfYE1sr+": "选择…",
            "960gdhmel/": "复制配置",
            "ArIG0z+TDb": "表单视图",
            "AzZraeBTKe": "无法复制到剪贴板。",
            "DauzyWMEs/": "防火墙允许列表",
            "EU5r34qJd1": "在连接中完成 {count, plural, one {# 个必填字段} other {# 个必填字段}}",
            "EcTe3kuYJI": "登录以获取此配置。",
            "FQSdPxLNeK": "无效：<b>{label}</b>",
            "J+38wFg/kx": "登录失败：",
            "J/Y8HJm8hj": "正在从此 URL 获取配置。",
            "JOFb1Tb7WF": "此配置会在启动时从引导 URL 获取。它提供的字段会在下方锁定。",
            "Jod6k4AK+8": "无法导出配置文件。",
            "K3r6DQW7h+": "删除",
            "K7AkdLoAj6": "显示",
            "HVOVcW54d6": "测试连通性",
            "KJm0ZlJsRQ": "移除 {value}",
            "KQGsuv1Wow": "完整配置",
            "LjOcsYKzL1": "被拒绝（401/403）",
            "LnITqSG0Ju": "{count, plural, one {# 项设置} other {# 项设置}}",
            "MPdK0gXvRA": "引导配置 URL",
            "N2IrpMDHDB": "确认",
            "OnpAzIS6lb": "HTTP {status}",
            "PM9NC083uS": "插件文件夹",
            "p2ASkWwoy9": "从磁盘选择",
            "RCy/fSOZ38": "端点返回了错误。",
            "RCKklDYDtX": "新建配置",
            "sJmvZiIAOf": "未配置。",
            "SQJto27U8S": "登录",
            "ST7O/MGD8w": "输入路径或从磁盘选择",
            "SVwJTM5AmL": "导出",
            "Seanpxav9K": "必填",
            "TRfaIL7S/7": "已同步",
            "TdTXXf940t": "了解更多",
            "UAoPW0hAYR": "此配置由你的组织管理。请联系 IT 管理员进行更改。",
            "UO05zdm59r": "由引导 URL 设置",
            "Uso+kDhaYE": "重启以从此 URL 获取配置。",
            "VA/Z1SW3vH": "隐藏",
            "VZOuJsyqUA": "移除值",
            "W41+8Xj7fP": "复制主机名",
            "W62eS9vb/S": "无法访问",
            "X5Q310+ucD": "隐藏详细信息",
            "XmVt+hh5k+": "立即重启",
            "ZBXWfWmBKK": "正在同步…",
            "ZI2sllkKCJ": "由 MDM 配置文件设置",
            "bZp/WPFEpx": "应用到本地",
            "dqZvCS4tEv": "在文档中阅读",
            "fhfSHcFtZ1": "关闭详细信息",
            "gNc3dJycmL": "配置 JSON",
            "iTe4MkAJ7e": "无法更新已保存的配置",
            "jnLRcxnYL6": "项目 {n}",
            "kNDaX599qM": "搜索设置",
            "lsLEBPpELm": "在所有部分中匹配“{query}”。",
            "mA/24PZHk8": "没有可用的缓存响应。Claude 将改用本地值运行。",
            "mG35sa1b0r": "上次同步失败",
            "md4QkbfaZg": "从不",
            "mlQu8UNVBV": "允许所有",
            "nHLszVLBdV": "无法加载配置",
            "oRZy+mE3Yg": "该 URL 覆盖的设置在下方为只读。",
            "on79ZcGd72": "配置第三方推理",
            "pMWMhEu56d": "无效响应",
            "pdArwtGp/4": "<b>连接</b>需要 {count, plural, one {{label}} other {# 个字段}}",
            "q87c5Fstfm": "需要登录",
            "s2XIgr4I+a": "显示详细信息",
            "tQVvCrCxZY": "将此配置指向引导 URL，以便你的组织远程管理这些设置。",
            "u26GuCLuuF": "重命名配置",
            "7mNRlW06Qt": "添加并按 Enter",
            "u5KHMExsq5": "包含由提供商 {provider} 设置的值",
            "w4MKEU2/Va": "{provider} 凭据",
            "wLCEuvbSge": "没有匹配项。",
            "p556q3uvbn": "已复制",
            "azZNYf2OlD": "虚拟机工具出站不受限制，工具可以访问防火墙允许的任意主机。常见主机（并非完整列表）：",
            "wpSJrTIFJu": "配置名称",
            "xgQtG2vu7a": "此配置包含敏感值。它们将以明文写入导出的文件。",
            "z1fjlgB3Jn": "以 JSON 格式查看",
        }
    )
    save_json(resources_dir / "zh-CN.json", desktop_zh)
    for folder in ["zh-CN.lproj", "zh_CN.lproj"]:
        out_dir = resources_dir / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LOCALIZABLE_STRINGS, out_dir / "Localizable.strings")
    print("Installed desktop shell zh-CN resources")


def install_statsig_locale(app: Path) -> None:
    statsig_dir = app / FRONTEND_I18N_REL / "statsig"
    if not statsig_dir.exists():
        return
    target = statsig_dir / "zh-CN.json"
    bundled = RESOURCES / "statsig-zh-CN.json"
    if bundled.exists():
        shutil.copy2(bundled, target)
    elif (statsig_dir / "en-US.json").exists():
        shutil.copy2(statsig_dir / "en-US.json", target)
    print("Installed statsig zh-CN resource")


def sign_path(path: Path, entitlements_dir: Path) -> None:
    entitlements = load_entitlements(path)
    if entitlements:
        # Ad-hoc signatures do not have a real Team ID. Under hardened runtime,
        # Electron's main process otherwise fails library validation when it loads
        # bundled frameworks, even when the whole bundle is signed consistently.
        entitlements["com.apple.security.cs.disable-library-validation"] = True

    cmd = [
        "codesign",
        "--force",
        "--sign",
        "-",
        "--options",
        "runtime",
        "--preserve-metadata=identifier,flags",
    ]
    if entitlements:
        entitlement_path = entitlements_dir / f"{abs(hash(path.as_posix()))}.plist"
        entitlement_path.write_bytes(plistlib.dumps(entitlements, fmt=plistlib.FMT_XML))
        cmd.extend(["--entitlements", str(entitlement_path)])
    cmd.append(str(path))

    result = run(cmd, check=False)
    if result.returncode != 0:
        print(result.stdout, end="")
        raise SystemExit(f"Failed to re-sign: {path}")


def is_signable_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.suffix in {".dylib", ".node", ".so"}:
        return True
    return os.access(path, os.X_OK)


def resign_app(app: Path) -> None:
    print("Re-signing patched app with local ad-hoc signature, preserving entitlements")
    contents = app / "Contents"
    entitlements_dir = Path(tempfile.mkdtemp(prefix="claude-zh-cn-entitlements."))
    bundle_targets: list[Path] = []
    file_targets: list[Path] = []

    for root, dirs, files in os.walk(contents):
        root_path = Path(root)
        for dirname in dirs:
            path = root_path / dirname
            if path.suffix in {".app", ".framework"}:
                bundle_targets.append(path)
        for filename in files:
            path = root_path / filename
            if is_signable_file(path):
                file_targets.append(path)

    # Sign nested Mach-O files first, then their containing bundles, then the outer app.
    for path in sorted(file_targets, key=lambda p: len(p.parts), reverse=True):
        sign_path(path, entitlements_dir)
    for path in sorted(bundle_targets, key=lambda p: len(p.parts), reverse=True):
        sign_path(path, entitlements_dir)
    sign_path(app, entitlements_dir)


def clear_quarantine(app: Path) -> None:
    result = run(["xattr", "-dr", "com.apple.quarantine", str(app)], check=False)
    if result.returncode == 0:
        print("Cleared Gatekeeper quarantine attribute")


def set_user_locale(user_home: Path) -> None:
    config = user_home / "Library/Application Support/Claude/config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if config.exists():
        try:
            data = load_json(config)
        except Exception:
            backup = config.with_suffix(".json.bak-invalid")
            shutil.copy2(config, backup)
            print(f"Existing config was not valid JSON; backed up to {backup}")
    data["locale"] = LANG_CODE
    save_json(config, data)

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        os.chown(config, int(sudo_uid), int(sudo_gid))
    print(f"Set Claude config locale: {config}")


def backup_and_replace(original: Path, patched: Path, dry_run: bool) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = original.with_name(f"Claude.backup-before-zh-CN-{stamp}.app")
    if dry_run:
        print(f"[dry-run] Would move {original} -> {backup}")
        print(f"[dry-run] Would move {patched} -> {original}")
        return backup

    print(f"Backing up current app: {backup}")
    shutil.move(str(original), str(backup))
    print(f"Installing patched app: {original}")
    shutil.move(str(patched), str(original))
    return backup


def verify(app: Path) -> None:
    frontend = app / FRONTEND_I18N_REL / "zh-CN.json"
    data = load_json(frontend)
    values = [v for v in data.values() if isinstance(v, str)]
    chinese = sum(1 for v in values if re.search(r"[\u4e00-\u9fff]", v))
    print(f"Verified frontend zh-CN JSON: {chinese}/{len(values)} strings contain Chinese")

    verify_result = run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=False)
    if verify_result.returncode == 0:
        print("Verified app signature")
    else:
        print("App signature verification failed:")
        print(verify_result.stdout, end="")

    entitlements = read_entitlements(app)
    if "com.apple.security.virtualization" in entitlements:
        print("Verified virtualization entitlement")
    else:
        print("Warning: virtualization entitlement is missing")

    result = run(["codesign", "-dv", str(app)], check=False).stdout
    for line in result.splitlines():
        if line.startswith("TeamIdentifier="):
            print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Claude Desktop with zh-CN language resources.")
    parser.add_argument("--app", type=Path, default=APP_DEFAULT, help="Path to Claude.app")
    parser.add_argument("--user-home", type=Path, default=Path.home(), help="Home directory whose Claude config should be updated")
    parser.add_argument("--dry-run", action="store_true", help="Prepare and verify a patched temp app, but do not replace /Applications/Claude.app")
    parser.add_argument("--launch", action="store_true", help="Launch Claude after installation")
    args = parser.parse_args()

    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)
    require_file(LOCALIZABLE_STRINGS)
    if not args.app.exists():
        raise SystemExit(f"Claude.app not found: {args.app}")
    require_virtualization_entitlement(args.app)

    try:
        in_applications = args.app.resolve().as_posix().startswith("/Applications/")
    except Exception:
        in_applications = str(args.app).startswith("/Applications/")
    if os.geteuid() != 0 and in_applications:
        print("This usually needs sudo because /Applications is protected.", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] Claude will not be quit.")
    else:
        quit_claude()
    tmp_root = Path(tempfile.mkdtemp(prefix="claude-zh-cn-patch."))
    patched_app = tmp_root / "Claude.app"

    copy_app(args.app, patched_app)
    patch_language_whitelist(patched_app)
    patch_hardcoded_frontend_strings(patched_app)
    merge_frontend_locale(patched_app)
    install_desktop_locale(patched_app)
    install_statsig_locale(patched_app)
    resign_app(patched_app)
    clear_quarantine(patched_app)
    if args.dry_run:
        print(f"[dry-run] Would set Claude config locale under: {args.user_home}")
    else:
        set_user_locale(args.user_home)
    verify(patched_app)

    backup = backup_and_replace(args.app, patched_app, args.dry_run)
    if not args.dry_run:
        print(f"Backup kept at: {backup}")
        if args.launch:
            run(["open", "-a", str(args.app)], check=False)

    print("Done. Select Language -> 中文（中国） in Claude if it is not already selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

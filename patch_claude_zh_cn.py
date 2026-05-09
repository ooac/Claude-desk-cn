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
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import struct
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

APP_ASAR_REL = Path("Contents/Resources/app.asar")
FRONTEND_I18N_REL = Path("Contents/Resources/ion-dist/i18n")
FRONTEND_ASSETS_REL = Path("Contents/Resources/ion-dist/assets/v1")
DESKTOP_RESOURCES_REL = Path("Contents/Resources")
ASAR_PATCH_TARGET = ".vite/build/index.js"
ASAR_INTEGRITY_BLOCK_SIZE = 4 * 1024 * 1024
REPORT_DIR = ROOT / "Logs"

LANG_LIST_RE = re.compile(
    r'\["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"(.*?)\]'
)


@dataclass
class PatchEvent:
    name: str
    status: str
    message: str = ""
    file: str | None = None
    count: int | None = None
    required: bool = False


@dataclass
class PatchReport:
    app: str
    claude_version: str
    mode: str
    started_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    events: list[PatchEvent] = field(default_factory=list)

    def add(
        self,
        name: str,
        status: str,
        message: str = "",
        *,
        file: Path | str | None = None,
        count: int | None = None,
        required: bool = False,
    ) -> None:
        self.events.append(
            PatchEvent(
                name=name,
                status=status,
                message=message,
                file=Path(file).name if isinstance(file, Path) else file,
                count=count,
                required=required,
            )
        )

    def has_required_failures(self) -> bool:
        return any(event.required and event.status in {"missing", "failed"} for event in self.events)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.status] = counts.get(event.status, 0) + 1
        return {
            "app": self.app,
            "claude_version": self.claude_version,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "summary": counts,
            "required_failures": [
                event.__dict__
                for event in self.events
                if event.required and event.status in {"missing", "failed"}
            ],
            "events": [event.__dict__ for event in self.events],
        }


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


def get_claude_version(app: Path) -> str:
    info_plist = app / "Contents/Info.plist"
    if not info_plist.exists():
        return "unknown"
    try:
        with info_plist.open("rb") as f:
            info = plistlib.load(f)
        return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "unknown")
    except Exception:
        return "unknown"


def write_patch_report(report: PatchReport) -> Path:
    report_dir = REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    data = report.to_dict()
    report_path = report_dir / f"patch-report-{stamp}.json"
    latest_path = report_dir / "latest.json"
    save_json(report_path, data)
    save_json(latest_path, data)
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        for path in [report_dir, report_path, latest_path]:
            try:
                os.chown(path, int(sudo_uid), int(sudo_gid))
            except PermissionError:
                pass
    print(f"Patch report written: {latest_path}")
    return latest_path


def find_frontend_bundles(app: Path) -> dict[str, Path | None]:
    assets_dir = app / FRONTEND_ASSETS_REL
    result: dict[str, Path | None] = {"index": None, "code": None}
    if not assets_dir.exists():
        return result
    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if result["index"] is None and (
            "const Jbt=({conversationUuid" in text
            or "Jbt=({models:e,currentModelOption" in text
        ):
            result["index"] = path
        if result["code"] is None and 'const um="ccd-effort-level"' in text and "modelExtraSections:xs" in text:
            result["code"] = path
    return result


def check_js_syntax(path: Path) -> tuple[bool, str]:
    if not shutil.which("node"):
        return False, "node command not found"
    result = run(["node", "--check", str(path)], check=False)
    return result.returncode == 0, result.stdout.strip()


def check_custom3p_validation_patched(app: Path) -> bool:
    path = app / APP_ASAR_REL
    if not path.exists():
        return False
    try:
        data = path.read_bytes()
        header_size, _header_string, header = read_asar_header(data, path)
        entry = get_asar_file_entry(header, ASAR_PATCH_TARGET)
        content_offset = 8 + header_size + int(entry["offset"])
        content_size = int(entry["size"])
        content = data[content_offset : content_offset + content_size]
    except Exception:
        return False
    return (
        b"const Hte=false" in content
        or b"const FLA=false" in content
        or b"function _Zt(e,A){return null;" in content
    )


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
        '["active","Active"]': '["active","活跃"]',
        '["archived","Archived"]': '["archived","已归档"]',
        '["all","All"]': '["all","全部"]',
        'jl="Local"': 'jl="本地"',
        'Cl="Cloud"': 'Cl="云端"',
        'Ml="Remote Control"': 'Ml="远程控制"',
        'Il="All"': 'Il="全部"',
        '["alpha","Alphabetically"]': '["alpha","按字母"]',
        '["created","Created time"]': '["created","创建时间"]',
        '["recency","Recency"]': '["recency","最近使用"]',
        '["1","1d"]': '["1","1天"]',
        '["3","3d"]': '["3","3天"]',
        '["7","7d"]': '["7","7天"]',
        '["30","30d"]': '["30","30天"]',
        '["0","All"]': '["0","全部"]',
        '["date","Date"]': '["date","日期"]',
        '["project","Project"]': '["project","项目"]',
        '["state","State"]': '["state","状态"]',
        '["environment","Environment"]': '["environment","环境"]',
        '["none","None"]': '["none","无"]',
        'label:"Status"': 'label:"状态"',
        'label:"Environment"': 'label:"环境"',
        'label:"Last activity"': 'label:"上次活动"',
        'label:"Group by"': 'label:"分组方式"',
        'label:"Sort by"': 'label:"排序方式"',
        'children:"Project"': 'children:"项目"',
        'children:"All projects"': 'children:"全部项目"',
        'children:"Clear filters"': 'children:"清除筛选"',
        '0===e.length?"All":': '0===e.length?"全部":',
        '`${e.length} selected`': '`${e.length} 项已选`',
        'children:"Batch archive…"': 'children:"批量归档…"',
        'children:"Batch delete…"': 'children:"批量删除…"',
        'children:"Sign out"': 'children:"退出登录"',
        'defaultMessage:"Theme"': 'defaultMessage:"主题"',
        'defaultMessage:"Match system"': 'defaultMessage:"跟随系统"',
        'defaultMessage:"Font"': 'defaultMessage:"字体"',
        'defaultMessage:"Anthropic Sans"': 'defaultMessage:"Anthropic 无衬线体"',
        'defaultMessage:"Effort"': 'defaultMessage:"强度"',
        'defaultMessage:"Transcript view"': 'defaultMessage:"思考模式"',
        'defaultMessage:"Transcript view mode"': 'defaultMessage:"思考模式"',
        'defaultMessage:"Connectors have moved to <link>Customize</link>."': 'defaultMessage:"连接器已移至<link>自定义</link>。"',
        'defaultMessage:"Skills have moved to <link>Customize</link>."': 'defaultMessage:"技能已移至<link>自定义</link>。"',
        'defaultMessage:"Generate code, documents, and designs in a dedicated window alongside your conversation."': 'defaultMessage:"在对话旁的专用窗口中生成代码、文档和设计。"',
        'defaultMessage:"Get notified when Claude has finished a response. Useful for long-running tasks."': 'defaultMessage:"Claude 完成响应后通知你，适合长时间运行的任务。"',
        'defaultMessage:"Artifacts"': 'defaultMessage:"创作物"',
        'defaultMessage:"Settings default model not recognized"': 'defaultMessage:"设置中的默认模型无法识别"',
        'Ld("cc-landing-draft-permission-mode","acceptEdits")': 'Ld("cc-landing-draft-permission-mode-cn","bypassPermissions")',
        'Mi("cc-landing-draft-permission-mode","acceptEdits",!1)': 'Mi("cc-landing-draft-permission-mode-cn","bypassPermissions",!1)',
        'fc("cc-landing-draft-permission-mode","acceptEdits")': 'fc("cc-landing-draft-permission-mode-cn","bypassPermissions")',
        'Ks("cc-landing-draft-permission-mode","acceptEdits",!1)': 'Ks("cc-landing-draft-permission-mode-cn","bypassPermissions",!1)',
        'Ld("epitaxy-folder-permission-mode",Kp,{scope:"account"})': 'Ld("epitaxy-folder-permission-mode-cn",Kp,{scope:"account"})',
        'fc("epitaxy-folder-permission-mode",Rm,{scope:"account"})': 'fc("epitaxy-folder-permission-mode-cn",Rm,{scope:"account"})',
        'yc("baku_model","model","claude-sonnet-4-6",l())': 'yc("baku_model","model","opus[1m]",l())',
        'c=yc(e,"model","claude-sonnet-4-5-20250929",l()),': 'c=(e=>e==="kimi-for-coding"?"opus[1m]":e)(yc(e,"model","opus[1m]",l())),',
        'i=yc("baku_model","model","opus[1m]",l()),o=$u(()=>null,null)||i;': 'i=yc("baku_model","model","opus[1m]",l()),o=(e=>e==="kimi-for-coding"?"opus[1m]":e)($u(()=>null,null)||i);',
        'R=N,O=I': 'R=(e=>e==="kimi-for-coding"?"opus[1m]":e)(N),O=I',
        'F=z?.sessionData?.session_context?.model??null,': 'F=(e=>e==="kimi-for-coding"?"opus[1m]":e)(z?.sessionData?.session_context?.model??null),',
        'return t.sessionModel??t.sessionData?.session_context?.model})??null': 'return(e=>e==="kimi-for-coding"?"opus[1m]":e)(t.sessionModel??t.sessionData?.session_context?.model)})??null',
        'const n=s.find(t=>t.model===e),r=(n?.thinking_modes??[]).map': 'const n=s.find(t=>t.model===e)??(("opus"===e||"opus[1m]"===e)?s.find(e=>"opus[1m]"===e.model)??s.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??s.find(e=>e.thinking_modes?.length):void 0),r=(n?.thinking_modes??[]).map',
        'W||(W=F.find(e=>e.model===L)??sgt);': 'W||(W=F.find(e=>e.model===L)??(("opus"===V||"opus[1m]"===V||"opus"===L||"opus[1m]"===L)?{model:"opus[1m]",name:"Opus 4.71M",inactive:!1,overflow:!1}:sgt));',
        'W||(W=F.find(e=>e.model===L)??(("opus"===V||"opus[1m]"===V||"opus"===L||"opus[1m]"===L)?{model:"opus[1m]",name:"Opus 4.71M",inactive:!1,overflow:!1}:sgt));const G=': 'W||(W=F.find(e=>e.model===L)??(("opus"===V||"opus[1m]"===V||"opus"===L||"opus[1m]"===L)?{model:"opus[1m]",name:"Opus 4.71M",inactive:!1,overflow:!1}:sgt));(\"\"===Vft(W)||\"opus\"===V||\"opus[1m]\"===V||\"opus\"===L||\"opus[1m]\"===L)&&(W={...W,model:\"opus[1m]\",name:\"Opus 4.71M\",inactive:!1,overflow:!1});const G=',
        '""===Vft(W)&&(W={model:"opus[1m]",name:"Opus 4.7 1M",inactive:!1,overflow:!1});const G=': '(""===Vft(W)||"opus"===V||"opus[1m]"===V||"opus"===L||"opus[1m]"===L)&&(W={...W,model:"opus[1m]",name:"Opus 4.71M",inactive:!1,overflow:!1});const G=',
        'z=r??A,{allModelOptions:F,mainModels:U,overflowModels:q}=R': 'z=(e=>e==="kimi-for-coding"?"opus[1m]":e)(r??A),{allModelOptions:F,mainModels:U,overflowModels:q}=R',
        '{activeMode:te}=Gft(z,Z),se=O?void 0:te?.label,{toggleConversationSetting:ne}=O6({source:"modelSelector"})': '{activeMode:te}=Gft(z,Z),[me,he]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||"high"}catch{return"high"}}),fe=n.useMemo(()=>_??{current:me,options:[{value:"low",label:"低"},{value:"medium",label:"中"},{value:"high",label:"高"},{value:"xhigh",label:"超高"},{value:"max",label:"最大"}],onSelect:e=>{he(e);try{localStorage.setItem("cowork_effort_level",e),window.dispatchEvent(new CustomEvent("cowork-effort-change",{detail:e}))}catch{}}},[_,me]),se=O?{low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}[me]:te?.label,{toggleConversationSetting:ne}=O6({source:"modelSelector"})',
        '_&&a.jsxs(a.Fragment,{children:[a.jsx(ol,{className:IR}),a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),a.jsx(igt,{section:_,compactMenu:j})]})': 'fe&&a.jsxs(a.Fragment,{children:[a.jsx(ol,{className:IR}),a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),a.jsx(igt,{section:fe,compactMenu:j})]})',
        'fe&&a.jsxs(a.Fragment,{children:[a.jsx(ol,{className:IR}),a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),a.jsx(igt,{section:fe,compactMenu:j})]})': 'a.jsxs(a.Fragment,{children:[a.jsx(ol,{className:IR}),a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),a.jsx(igt,{section:{current:me,options:[{value:"low",label:"低"},{value:"medium",label:"中"},{value:"high",label:"高"},{value:"xhigh",label:"超高"},{value:"max",label:"最大"}],onSelect:e=>{he(e);try{localStorage.setItem("cowork_effort_level",e),window.dispatchEvent(new CustomEvent("cowork-effort-change",{detail:e}))}catch{}}},compactMenu:j})]})',
        'const Wft=({model:e,compact:t=!1,thinkingLabel:s})=>{const n=Vft(e,{mutedSuffix:!0});return': 'const Wft=({model:e,compact:t=!1,thinkingLabel:s})=>{let n=Vft(e,{mutedSuffix:!0});""===n&&(n="Opus 4.71M");return',
        'function Vft(e,t={}){const s=e.model?Z9(e.model):null;': 'function Vft(e,t={}){if("opus[1m]"===e?.model||"opus"===e?.model)return"Opus 4.71M";const s=e.model?Z9(e.model):null;',
        'j=pc("cowork_effort_level","medium",Wu),M=pc("cowork_model",vJt,yJt),': 'j=(()=>{const e=pc("cowork_effort_level","high",Wu),[t,s]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||e}catch{return e}});return n.useEffect(()=>{const e=()=>{try{s(localStorage.getItem("cowork_effort_level")||"high")}catch{s("high")}};if("undefined"==typeof window)return;e();return window.addEventListener("cowork-effort-change",e),()=>window.removeEventListener("cowork-effort-change",e)},[]),t})(),M=pc("cowork_model",vJt,yJt),',
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

    model_alias_re = re.compile(
        r'function eee\(e,t\)\{if\(!e\)return null;if\(t\.includes\(e\)\)return e;'
        r'(?:if\(\(e==="opus"\|\|e==="opus\[1m\]"\)&&(?:t\.includes\("kimi-for-coding"\)|t\.length>0)\)return\s*(?:"kimi-for-coding"|e);)*'
    )
    model_alias_target = (
        'function eee(e,t){if(!e)return null;if(t.includes(e))return e;'
        'if((e==="opus"||e==="opus[1m]")&&t.length>0)return e;'
    )
    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched, count = model_alias_re.subn(model_alias_target, text)
        if count and patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    pinned_opus_re = re.compile(
        r'(let e=\(e=>\{if\(0===d\.length\)return e;const t=new Set\(e\.map\(e=>e\.model\)\),s=\[\];'
        r'for\(const n of e\)\{if\(s\.push\(n\),!d\.includes\(n\.model\)\)continue;'
        r'const e=`\$\{n\.model\}\[1m\]`;t\.has\(e\)\|\|s\.push\(\{\.\.\.n,model:e,'
        r'name:i\.formatMessage\(\{defaultMessage:"\{modelName\} \(1M context\)",id:"4jU30\+bnSv"\},'
        r'\{modelName:n\.name\}\),name_i18n_key:void 0\}\)\}return s\}\)\(s\)\.filter\(e=>o\.includes\(e\.model\)\)'
        r'\.map\(e=>e\.inactive\?\{\.\.\.e,inactive:!1\}:e\);)'
        r'.*?const n=e\.some\(e=>e\.model===c\);',
        re.DOTALL,
    )
    pinned_opus_target = (
        'if(s.length>0&&!e.some(e=>"opus[1m]"===e.model)){'
        'const l=s.find(e=>"opus[1m]"===e.model)??s.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??'
        's.find(e=>e.thinking_modes?.length)??s[0];'
        'e=[{...l,model:"opus[1m]",name:"Opus 4.71M",name_i18n_key:void 0,inactive:!1,overflow:!1},...e]}'
        'if(c&&!e.some(e=>e.model===c)){const m=s.find(e=>e.model===c)??s.find(e=>e.thinking_modes?.length)??s[0];'
        'e=[{...m,model:c,name:m?.name??tee(c),name_i18n_key:void 0,inactive:!1,overflow:!1},...e]}'
        'const n=e.some(e=>e.model===c);'
    )
    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched, count = pinned_opus_re.subn(lambda match: match.group(1) + pinned_opus_target, text)
        if count and patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    baku_opus_re = re.compile(
        r'(let c=e\.filter\(e=>a\.includes\(e\.model\)\)\.map\(e=>e\.inactive\?\{\.\.\.e,inactive:!1\}:e\);)'
        r'.*?const d=c\.some\(e=>e\.model===o\);',
        re.DOTALL,
    )
    baku_opus_target = (
        'if(e.length>0&&!c.some(e=>"opus[1m]"===e.model)){'
        'const n=e.find(e=>"opus[1m]"===e.model)??e.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??'
        'e.find(e=>e.thinking_modes?.length)??e[0];'
        'c=[{...n,model:"opus[1m]",name:"Opus 4.71M",name_i18n_key:void 0,inactive:!1,overflow:!1},...c]}'
        'if(o&&!c.some(e=>e.model===o)){const d=e.find(e=>e.model===o)??e.find(e=>e.thinking_modes?.length)??e[0];'
        'c=[{...d,model:o,name:d?.name??tee(o),name_i18n_key:void 0,inactive:!1,overflow:!1},...c]}'
        'const d=c.some(e=>e.model===o);'
    )
    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched, count = baku_opus_re.subn(lambda match: match.group(1) + baku_opus_target, text)
        if count and patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    epitaxy_files, epitaxy_count = patch_epitaxy_model_menu(assets_dir)
    patched_files += epitaxy_files
    patched_strings += epitaxy_count
    cowork_files, cowork_count = patch_cowork_model_menu(assets_dir)
    patched_files += cowork_files
    patched_strings += cowork_count
    cache_files, cache_count = patch_epitaxy_cache_bust(app / "Contents/Resources/ion-dist", assets_dir)
    patched_files += cache_files
    patched_strings += cache_count
    health_files, health_count = patch_kimi_gateway_health_banner(assets_dir)
    patched_files += health_files
    patched_strings += health_count
    permission_files, permission_count = patch_permission_defaults(assets_dir)
    patched_files += permission_files
    patched_strings += permission_count

    print(f"Patched hardcoded frontend strings: {patched_strings} replacements in {patched_files} files")


def patch_permission_defaults(assets_dir: Path) -> tuple[int, int]:
    """把 Code 新建会话权限默认值固定为绕过权限，并隔离旧 localStorage。"""
    regex_replacements: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(r'\b(?P<fn>Ld|fc)\("cc-landing-draft-permission-mode","acceptEdits"\)'),
            r'\g<fn>("cc-landing-draft-permission-mode-cn","bypassPermissions")',
        ),
        (
            re.compile(r'\b(?P<fn>Mi|Ks)\("cc-landing-draft-permission-mode","acceptEdits",!1\)'),
            r'\g<fn>("cc-landing-draft-permission-mode-cn","bypassPermissions",!1)',
        ),
        (
            re.compile(
                r'\b(?P<fn>Ld|fc)\("epitaxy-folder-permission-mode",'
                r'(?P<default>[A-Za-z_$][\w$]*),\{scope:"account"\}\)'
            ),
            r'\g<fn>("epitaxy-folder-permission-mode-cn",\g<default>,{scope:"account"})',
        ),
    ]
    literal_replacements = {
        'const e=en??Zs??Gs??$s;return sn?wt(e,Os):e': (
            'const e=en??Zs??$s??Gs??"bypassPermissions";return sn?wt(e,Os):e'
        ),
        'const e=en??Zs??$s??Gs??"bypassPermissions";return sn?wt(e,Os):e': (
            'const e=en??Zs??$s??Gs??"bypassPermissions";return sn?wt(e,Os):e'
        ),
    }
    patched_files = 0
    patched_strings = 0

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "cc-landing-draft-permission-mode" not in text and "epitaxy-folder-permission-mode" not in text:
            continue
        patched = text
        count = 0
        for pattern, target in regex_replacements:
            patched, n = pattern.subn(target, patched)
            count += n
        for source, target in literal_replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    return patched_files, patched_strings


def patch_kimi_gateway_health_banner(assets_dir: Path) -> tuple[int, int]:
    """隐藏 Kimi 网关可连通时仍被旧健康状态标成 Unreachable 的 Cowork 横幅。"""
    replacements = {
        (
            'if(d||!l||!w)return null;'
            'const k=l.state===xV.InvalidConfig||l.state===xV.AuthFailed||l.state===xV.BootstrapError'
        ): (
            'if(d||!l||!w||l.state===xV.Unreachable&&'
            '/api\\.kimi\\.com(?:\\/coding)?/i.test(String(l.endpoint??l.requestUrl??"")))return null;'
            'const k=l.state===xV.InvalidConfig||l.state===xV.AuthFailed||l.state===xV.BootstrapError'
        ),
        (
            'if(d||!l||!w)return null;'
            'const k=l.state===yW.InvalidConfig||l.state===yW.AuthFailed||l.state===yW.BootstrapError'
        ): (
            'if(d||!l||!w||l.state===yW.Unreachable&&'
            '/api\\.kimi\\.com(?:\\/coding)?/i.test(String(l.endpoint??l.requestUrl??"")))return null;'
            'const k=l.state===yW.InvalidConfig||l.state===yW.AuthFailed||l.state===yW.BootstrapError'
        ),
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

    return patched_files, patched_strings


def patch_epitaxy_cache_bust(ion_dist_dir: Path, assets_dir: Path) -> tuple[int, int]:
    """给 Code 入口相关资源加版本参数，避免复制到其他电脑后命中旧缓存。"""
    patched_files = 0
    patched_strings = 0
    code_chunk = next(
        (
            path
            for path in sorted(assets_dir.glob("*.js"))
            if (
                "function em(t){const s=i()" in path.read_text(encoding="utf-8", errors="ignore")
                and "modelExtraSections:gs" in path.read_text(encoding="utf-8", errors="ignore")
            )
            or (
                'const um="ccd-effort-level"' in path.read_text(encoding="utf-8", errors="ignore")
                and "modelExtraSections:xs" in path.read_text(encoding="utf-8", errors="ignore")
            )
        ),
        None,
    )
    if not code_chunk:
        return 0, 0

    version_source = Path(__file__).read_bytes()
    version = "zhcn-" + hashlib.sha256(version_source).hexdigest()[:12]
    query_re = re.compile(r"\?v=zhcn-[0-9a-f]{12}")

    def with_version(value: str) -> str:
        return query_re.sub("", value) + f"?v={version}"

    names = {code_chunk.name}
    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if code_chunk.name in text and not path.name.startswith("index-"):
            names.add(path.name)

    index_html = ion_dist_dir / "index.html"
    if index_html.exists():
        text = index_html.read_text(encoding="utf-8")
        patched = re.sub(
            r'(?P<prefix><script type="module" crossorigin src="/assets/v1/)(?P<name>index-[^"?]+\.js)(?:\?v=zhcn-[0-9a-f]{12})?(?P<suffix>"></script>)',
            lambda match: f"{match.group('prefix')}{with_version(match.group('name'))}{match.group('suffix')}",
            text,
            count=1,
        )
        if patched != text:
            index_html.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += 1

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched = text
        count = 0
        for name in sorted(names, key=len, reverse=True):
            patterns = [
                (f'./{name}', f'./{with_version(name)}'),
                (f'assets/v1/{name}', f'assets/v1/{with_version(name)}'),
                (f'/assets/v1/{name}', f'/assets/v1/{with_version(name)}'),
            ]
            for source, target in patterns:
                source_re = re.compile(re.escape(source) + r"(?:\?v=zhcn-[0-9a-f]{12})?")
                patched, n = source_re.subn(target, patched)
                count += n
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    return patched_files, patched_strings


def patch_cowork_model_menu(assets_dir: Path) -> tuple[int, int]:
    """把 Cowork 模型菜单固定为 Opus 伪装入口、Kimi 真实入口和完整强度。"""
    patched_files = 0
    patched_strings = 0

    # Claude 1.6608+：Cowork 与普通入口共用 Jbt 模型选择器。
    # 这里直接把共享选择器的候选项重建为固定两项，避免 Missing/Legacy fallback。
    jbt_model_re = re.compile(
        r'z=(?:r\?\?A|\(e=>e==="kimi-for-coding"\?"opus\[1m\]":e\)\(r\?\?A\)),'
        r'\{allModelOptions:F,mainModels:U,overflowModels:q\}=R,'
        r'B=ud\("sticky_model_selector"\),\[\$,V\]=n\.useState\(null\),H=!B&&\$\?\$:z;'
        r'let W=F\.find\(e=>e\.model===H\);W\|\|\(W=F\.find\(e=>e\.model===L\)\?\?Kbt\);'
        r'const G=n\.useRef\(null\),K=S7\("paprika_mode"\);Dbt\(z\);'
        r'const Y=Abt\(\),Z=!h&&!O,Q=Z\?\[W\]:U,X=Z\?U\.filter\(e=>e\.model!==H\):\[\],'
        r'J=Z\?q\.filter\(e=>e\.model!==H\):q,',
        re.DOTALL,
    )
    jbt_model_target = (
        'z=(e=>{const t=String(e??"").toLowerCase();'
        'if("kimi-for-coding"===t||"kimi-k2.6"===t||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e)))return"kimi-for-coding";'
        'if("opus"===t||"opus[1m]"===t)return"opus[1m]";return"opus[1m]"})(r??A),'
        '{allModelOptions:F}=R,U=[],q=[],B=ud("sticky_model_selector"),[$,V]=n.useState(null),H=$??z,'
        'rr={...(F.find(e=>"opus[1m]"===e.model)??F.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??F.find(e=>e.thinking_modes?.length)??{}),model:"opus[1m]",name:"Opus 4.71M",name_i18n_key:void 0,inactive:!1,overflow:!1},'
        'oo=F.find(e=>{const t=String(e.model??"").toLowerCase(),s=String(e.name??"").toLowerCase();'
        'return"kimi-for-coding"===t||"kimi-for-coding"===s||"kimi-k2.6"===t||"kimi-k2.6"===s||/kimi.*k2\\.6/i.test(t)||/kimi.*k2\\.6/i.test(s)}),'
        'll=oo?.model??"kimi-for-coding",'
        'cc={...(oo??F.find(e=>e.thinking_modes?.length)??{}),model:ll,name:"Kimi-k2.6",name_i18n_key:void 0,inactive:!1,overflow:!1};'
        'let W="kimi-for-coding"===String(H).toLowerCase()||/kimi/i.test(String(H))&&/k2\\.6/i.test(String(H))?cc:rr;'
        'const G=n.useRef(null),K=S7("paprika_mode");Dbt(W.model);'
        'const Y=Abt(),Z=!h&&!O,Q=[rr,cc],X=[],J=[],'
    )
    jbt_handler_re = re.compile(
        r'const de=e=>\{if\(e\.model===H\)return;if\(ne\(e\.model\)\)return;'
        r'if\(ae\|\|!Ybt\(e\.model,!1,!re,L,le\)\)\{',
        re.DOTALL,
    )
    jbt_handler_target = (
        'const de=e=>{const t=String(e.model??"").toLowerCase(),s="opus"===t||"opus[1m]"===t||'
        '"kimi-for-coding"===t||/kimi/i.test(String(e.model))&&/k2\\.6/i.test(String(e.model));'
        'if(e.model===H)return;if(!s&&ne(e.model))return;if(s||ae||!Ybt(e.model,!1,!re,L,le)){'
    )
    jbt_effort_re = re.compile(
        r'\{activeMode:ee\}=Fbt\(z,K\),'
        r'(?:\[cw,Sw\]=n\.useState\(\(\)=>\{try\{return localStorage\.getItem\("cowork_effort_level"\)\|\|"high"\}catch\{return"high"\}\}\),'
        r'Fw=n\.useMemo\(\(\)=>_\?\?(?:\("cowork"===I\?)?\{current:cw,options:\[\{value:"low",label:"低"\},\{value:"medium",label:"中"\},\{value:"high",label:"高"\},\{value:"xhigh",label:"超高"\},\{value:"max",label:"最大"\}\],onSelect:e=>\{Sw\(e\);try\{localStorage\.setItem\("cowork_effort_level",e\),window\.dispatchEvent\(new CustomEvent\("cowork-effort-change",\{detail:e\}\)\)\}catch\{\}\}\}(?::void 0\))?,\[_,cw(?:,I)?\]\),)?'
        r'te=.*?,\{toggleConversationSetting:se\}=E7\(\{source:"modelSelector"\}\)',
        re.DOTALL,
    )
    jbt_effort_target = (
        '{activeMode:ee}=Fbt(z,K),'
        '[cw,Sw]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||"high"}catch{return"high"}}),'
        'Fw=n.useMemo(()=>_??{current:cw,options:[{value:"low",label:"低"},{value:"medium",label:"中"},{value:"high",label:"高"},{value:"xhigh",label:"超高"},{value:"max",label:"最大"}],'
        'onSelect:e=>{Sw(e);try{localStorage.setItem("cowork_effort_level",e),window.dispatchEvent(new CustomEvent("cowork-effort-change",{detail:e}))}catch{}}},[_,cw]),'
        'te=Fw?{low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}[Fw.current]??ee?.label:O?void 0:ee?.label,'
        '{toggleConversationSetting:se}=E7({source:"modelSelector"})'
    )
    jbt_effort_render_re = re.compile(
        r'(?:_&&|Fw&&)a\.jsxs\(a\.Fragment,\{children:\[a\.jsx\(tl,\{className:_de\}\),'
        r'a\.jsx\("div",\{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a\.jsx\(c,\{defaultMessage:"强度",id:"VKZ/U8vAsk"\}\)\}\),'
        r'a\.jsx\(Xbt,\{section:(?:_|Fw),compactMenu:j\}\)\]\}\)',
        re.DOTALL,
    )
    jbt_effort_render_target = (
        'Fw&&a.jsxs(a.Fragment,{children:[a.jsx(tl,{className:_de}),'
        'a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),'
        'a.jsx(Xbt,{section:Fw,compactMenu:j})]})'
    )
    jbt_state_replacements = {
        'Y(e.model)||se("compass_mode",null),B||V(e.model),D(e.model),i?.(e)}': (
            'Y(e.model)||se("compass_mode",null),V(e.model),D(e.model),i?.(e)}'
        ),
        'Pbt(W),te].filter(Boolean).join(" ")': (
            'Pbt(W),te].filter(Boolean).join(" ")'
        ),
    }

    pte_replacements = {
        'R=(e=>e==="kimi-for-coding"?"opus[1m]":e)(N),O=I': 'R=N,O=I',
        'F=(e=>e==="kimi-for-coding"?"opus[1m]":e)(z?.sessionData?.session_context?.model??null),': (
            'F=z?.sessionData?.session_context?.model??null,'
        ),
        'return(e=>e==="kimi-for-coding"?"opus[1m]":e)(t.sessionModel??t.sessionData?.session_context?.model)})??null': (
            'return t.sessionModel??t.sessionData?.session_context?.model})??null'
        ),
        '_=yc("cowork_effort_level","medium",Mp),j=yc("cowork_model",T0t,I0t),': (
            '_=(()=>{const e=yc("cowork_effort_level","high",Mp),[t,s]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||e}catch{return e}});return n.useEffect(()=>{const e=()=>{try{s(localStorage.getItem("cowork_effort_level")||"high")}catch{s("high")}};if("undefined"==typeof window)return;e();return window.addEventListener("cowork-effort-change",e),()=>window.removeEventListener("cowork-effort-change",e)},[]),t})(),j=yc("cowork_model",T0t,I0t),'
        ),
        '_=(()=>{const e=yc("cowork_effort_level","high",Mp),[t,s]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||e}catch{return e}});return n.useEffect(()=>{const e=()=>{try{s(localStorage.getItem("cowork_effort_level")||"high")}catch{s("high")}};if("undefined"==typeof window)return;e();return window.addEventListener("cowork-effort-change",e),()=>window.removeEventListener("cowork-effort-change",e)},[]),t})(),j=yc("cowork_model",T0t,I0t),': (
            '_=(()=>{const e=yc("cowork_effort_level","high",Mp),[t,s]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||e}catch{return e}});return n.useEffect(()=>{const e=()=>{try{s(localStorage.getItem("cowork_effort_level")||"high")}catch{s("high")}};if("undefined"==typeof window)return;e();return window.addEventListener("cowork-effort-change",e),()=>window.removeEventListener("cowork-effort-change",e)},[]),t})(),j=yc("cowork_model",T0t,I0t),'
        ),
    }

    # Claude 1.6608.2：Jbt 变成独立共享模型列表组件，外层配置仍在同一 bundle。
    # 这里按新版变量名补丁，确保升级后 Cowork 不会退回 Legacy Model 或丢失强度。
    jbt_v2_model_re = re.compile(
        r'z=(?:r\?\?A|\(e=>e==="kimi-for-coding"\?"opus\[1m\]":e\)\(r\?\?A\)),'
        r'\{allModelOptions:F,mainModels:U,overflowModels:q\}=R,'
        r'B=ud\("sticky_model_selector"\),\[\$,V\]=n\.useState\(null\),H=!B&&\$\?\$:z;'
        r'let W=F\.find\(e=>e\.model===H\);W\|\|\(W=F\.find\(e=>e\.model===L\)\?\?Zbt\);'
        r'const G=n\.useRef\(null\),K=S7\("paprika_mode"\);zbt\(z\);'
        r'const Y=Rbt\(\),Z=!h&&!O,Q=Z\?\[W\]:U,X=Z\?U\.filter\(e=>e\.model!==H\):\[\],'
        r'J=Z\?q\.filter\(e=>e\.model!==H\):q,',
        re.DOTALL,
    )
    jbt_v2_model_target = (
        'z=(e=>{const t=String(e??"").toLowerCase();'
        'if("kimi-for-coding"===t||"kimi-k2.6"===t||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e)))return"kimi-for-coding";'
        'if("opus"===t||"opus[1m]"===t)return"opus[1m]";return"opus[1m]"})(r??A),'
        '{allModelOptions:F}=R,U=[],q=[],B=ud("sticky_model_selector"),[$,V]=n.useState(null),H=$??z,'
        'rr={...(F.find(e=>"opus[1m]"===e.model)??F.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??F.find(e=>e.thinking_modes?.length)??{}),model:"opus[1m]",name:"Opus 4.71M",name_i18n_key:void 0,inactive:!1,overflow:!1},'
        'oo=F.find(e=>{const t=String(e.model??"").toLowerCase(),s=String(e.name??"").toLowerCase();'
        'return"kimi-for-coding"===t||"kimi-for-coding"===s||"kimi-k2.6"===t||"kimi-k2.6"===s||/kimi.*k2\\.6/i.test(t)||/kimi.*k2\\.6/i.test(s)}),'
        'll=oo?.model??"kimi-for-coding",'
        'cc={...(oo??F.find(e=>e.thinking_modes?.length)??{}),model:ll,name:"Kimi-k2.6",name_i18n_key:void 0,inactive:!1,overflow:!1};'
        'let W="kimi-for-coding"===String(H).toLowerCase()||/kimi/i.test(String(H))&&/k2\\.6/i.test(String(H))?cc:rr;'
        'const G=n.useRef(null),K=S7("paprika_mode");zbt(W.model);'
        'const Y=Rbt(),Z=!h&&!O,Q=[rr,cc],X=[],J=[],'
    )
    jbt_v2_effort_re = re.compile(
        r'\{activeMode:ee\}=qbt\(z,K\),te=O\?void 0:ee\?\.label,'
        r'\{toggleConversationSetting:se\}=E7\(\{source:"modelSelector"\}\)',
        re.DOTALL,
    )
    jbt_v2_effort_target = (
        '{activeMode:ee}=qbt(z,K),'
        '[cw,Sw]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||"high"}catch{return"high"}}),'
        'Fw=n.useMemo(()=>_??{current:cw,options:[{value:"low",label:"低"},{value:"medium",label:"中"},{value:"high",label:"高"},{value:"xhigh",label:"超高"},{value:"max",label:"最大"}],'
        'onSelect:e=>{Sw(e);try{localStorage.setItem("cowork_effort_level",e),window.dispatchEvent(new CustomEvent("cowork-effort-change",{detail:e}))}catch{}}},[_,cw]),'
        'te=Fw?{low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}[Fw.current]??ee?.label:O?void 0:ee?.label,'
        '{toggleConversationSetting:se}=E7({source:"modelSelector"})'
    )
    jbt_v2_handler_re = re.compile(
        r'const de=e=>\{if\(e\.model===H\)return;if\(ne\(e\.model\)\)return;'
        r'if\(ae\|\|!Qbt\(e\.model,!1,!re,L,le\)\)\{',
        re.DOTALL,
    )
    jbt_v2_handler_target = (
        'const de=e=>{const t=String(e.model??"").toLowerCase(),s="opus"===t||"opus[1m]"===t||'
        '"kimi-for-coding"===t||/kimi/i.test(String(e.model))&&/k2\\.6/i.test(String(e.model));'
        'if(e.model===H)return;if(!s&&ne(e.model))return;if(s||ae||!Qbt(e.model,!1,!re,L,le)){'
    )
    jbt_v2_render_re = re.compile(
        r'_&&a\.jsxs\(a\.Fragment,\{children:\[a\.jsx\(tl,\{className:Mde\}\),'
        r'a\.jsx\("div",\{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a\.jsx\(c,\{defaultMessage:"(?:Effort|强度)",id:"VKZ/U8vAsk"\}\)\}\),'
        r'a\.jsx\(eyt,\{section:_,compactMenu:j\}\)\]\}\)',
        re.DOTALL,
    )
    jbt_v2_render_target = (
        'Fw&&a.jsxs(a.Fragment,{children:[a.jsx(tl,{className:Mde}),'
        'a.jsx("div",{className:"text-xs text-text-500 pt-2 pb-1 px-2",children:a.jsx(c,{defaultMessage:"强度",id:"VKZ/U8vAsk"})}),'
        'a.jsx(eyt,{section:Fw,compactMenu:j})]})'
    )
    jbt_v2_state_source = 'Y(e.model)||se("compass_mode",null),B||V(e.model),D(e.model),i?.(e)}'
    jbt_v2_state_target = 'Y(e.model)||se("compass_mode",null),V(e.model),D(e.model),i?.(e)}'
    cowork_effort_config_re = re.compile(
        r'_=yc\("cowork_effort_level","medium",([A-Za-z0-9_$]+)\),'
        r'j=yc\("cowork_model",([^)]*)\),',
        re.DOTALL,
    )

    def cowork_effort_config_target(match: re.Match[str]) -> str:
        validator = match.group(1)
        cowork_model_args = match.group(2)
        return (
            f'_=(()=>{{const e=yc("cowork_effort_level","high",{validator}),'
            '[t,s]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||e}catch{return e}});'
            'return n.useEffect(()=>{const e=()=>{try{s(localStorage.getItem("cowork_effort_level")||"high")}catch{s("high")}};'
            'if("undefined"==typeof window)return;e();return window.addEventListener("cowork-effort-change",e),'
            '()=>window.removeEventListener("cowork-effort-change",e)},[]),t})(),'
            f'j=yc("cowork_model",{cowork_model_args}),'
        )

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "Jbt=({models:e,currentModelOption" not in text:
            continue
        patched = text
        count = 0
        patched, n = jbt_v2_model_re.subn(jbt_v2_model_target, patched, count=1)
        count += n
        patched, n = jbt_v2_effort_re.subn(jbt_v2_effort_target, patched, count=1)
        count += n
        patched, n = jbt_v2_handler_re.subn(jbt_v2_handler_target, patched, count=1)
        count += n
        patched, n = jbt_v2_render_re.subn(jbt_v2_render_target, patched, count=1)
        count += n
        occurrences = patched.count(jbt_v2_state_source)
        if occurrences:
            patched = patched.replace(jbt_v2_state_source, jbt_v2_state_target)
            count += occurrences
        for source, target in pte_replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        patched, n = cowork_effort_config_re.subn(cowork_effort_config_target, patched, count=1)
        count += n
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "const Jbt=({conversationUuid" not in text:
            continue
        patched = text
        count = 0
        patched, n = jbt_model_re.subn(jbt_model_target, patched, count=1)
        count += n
        patched, n = jbt_handler_re.subn(jbt_handler_target, patched, count=1)
        count += n
        patched, n = jbt_effort_re.subn(jbt_effort_target, patched, count=1)
        count += n
        patched, n = jbt_effort_render_re.subn(jbt_effort_render_target, patched, count=1)
        count += n
        for source, target in jbt_state_replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        for source, target in pte_replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        patched, n = cowork_effort_config_re.subn(cowork_effort_config_target, patched, count=1)
        count += n
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    vft_re = re.compile(
        r'function Vft\(e,t=\{\}\)\{(?:if\("opus\[1m\]"===e\?\.model\|\|"opus"===e\?\.model\)'
        r'return"Opus 4\.7(?:1)? ?1?M";)?const s=e\.model\?Z9\(e\.model\):null;',
        re.DOTALL,
    )
    vft_target = (
        'function Vft(e,t={}){const r=String(e?.model??e?.name??"");'
        'if("opus[1m]"===e?.model||"opus"===e?.model)return"Opus 4.71M";'
        'if("kimi-for-coding"===r.toLowerCase()||/kimi/i.test(r)&&/k2\\.6/i.test(r))return"Kimi-k2.6";'
        'const s=e.model?Z9(e.model):null;'
    )

    wft_patterns = {
        '""===n&&(n="Opus 4.7 1M");return': '""===n&&(n="Opus 4.71M");return',
        '""===n&&(n="Opus 4.71M");return': '""===n&&(n="Opus 4.71M");return',
    }

    ogt_model_res = [
        re.compile(
            r'z=\(e=>\{const t=String\(e\?\?""\).*?'
            r'const Y=Uft\(\),Q=!0,X=\[Ne,Re\],J=\[\],ee=\[\],',
            re.DOTALL,
        ),
        re.compile(
            r'z=.*?,\{allModelOptions:F,mainModels:U,overflowModels:q\}=R,'
            r'B=Xc\("sticky_model_selector"\),\[\$,H\]=n\.useState\(null\),V=.*?'
            r'const G=n\.useRef\(null\),Z=L6\("paprika_mode"\);Hft\(z\);'
            r'const Y=Uft\(\),Q=.*?,X=.*?,J=.*?,ee=.*?,',
            re.DOTALL,
        ),
    ]
    ogt_model_target = (
        'z=(e=>{const t=String(e??"").toLowerCase();'
        'if("kimi-for-coding"===t||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e)))return"kimi-for-coding";'
        'if("opus"===t||"opus[1m]"===t)return"opus[1m]";'
        'return"opus[1m]"})(r??A),{allModelOptions:F}=R,'
        'B=Xc("sticky_model_selector"),[$,H]=n.useState(null),'
        'ke=e=>{const t=String(e??"").toLowerCase();return"kimi-for-coding"===t||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e))?"kimi-for-coding":"opus[1m]"},'
        'Ce=F.find(e=>{const t=String(e.model??"").toLowerCase(),s=String(e.name??"").toLowerCase();'
        'return"kimi-for-coding"===t||"kimi-for-coding"===s||"kimi-k2.6"===t||"kimi-k2.6"===s||/kimi.*k2\\.6/i.test(t)||/kimi.*k2\\.6/i.test(s)}),'
        'Se=Ce?.model??"kimi-for-coding",'
        'Ne={...(F.find(e=>"opus[1m]"===e.model)??F.find(e=>/opus/i.test(e.model)&&/\\[1m\\]/i.test(e.model))??F.find(e=>e.thinking_modes?.length)??{}),model:"opus[1m]",name:"Opus 4.71M",name_i18n_key:void 0,inactive:!1,overflow:!1},'
        'Re={...(Ce??F.find(e=>e.thinking_modes?.length)??{}),model:Se,name:"Kimi-k2.6",name_i18n_key:void 0,inactive:!1,overflow:!1},'
        'V=$??z,W="kimi-for-coding"===ke(V)?Re:Ne,'
        'G=n.useRef(null),Z=L6("paprika_mode");Hft(z);'
        'const Y=Uft(),Q=!0,X=[Ne,Re],J=[],ee=[],'
    )

    effort_re = re.compile(
        r'\{activeMode:te\}=Gft\(z,Z\),.*?,se=O\?.*?:te\?\.label,'
        r'\{toggleConversationSetting:ne\}=O6\(\{source:"modelSelector"\}\)',
        re.DOTALL,
    )
    effort_target = (
        '{activeMode:te}=Gft(z,Z),'
        '[me,he]=n.useState(()=>{try{return localStorage.getItem("cowork_effort_level")||"high"}catch{return"high"}}),'
        'fe=n.useMemo(()=>{const e=e=>{he(e);try{localStorage.setItem("cowork_effort_level",e),'
        'window.dispatchEvent(new CustomEvent("cowork-effort-change",{detail:e}))}catch{}},'
        't=_?.current??me,s=_?.onSelect??e;return{current:t,'
        'options:[{value:"low",label:"低"},{value:"medium",label:"中"},{value:"high",label:"高"},{value:"xhigh",label:"超高"},{value:"max",label:"最大"}],'
        'onSelect:e=>{s(e);_?.onSelect||he(e)}}},[_,me]),'
        'se=O?{low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}[fe.current]:te?.label,'
        '{toggleConversationSetting:ne}=O6({source:"modelSelector"})'
    )

    handler_re = re.compile(
        r'const ue=e=>\{if\(e\.model===V\)return;if\(ae\(e\.model\)\)return;'
        r'if\(re\|\|!ngt\(e\.model,!1,!ie,L,ce\)\)\{',
        re.DOTALL,
    )
    handler_target = (
        'const ue=e=>{const t=String(e.model??"").toLowerCase(),s="opus"===t||"opus[1m]"===t||'
        '"kimi-for-coding"===t||/kimi/i.test(String(e.model))&&/k2\\.6/i.test(String(e.model));'
        'if(e.model===V)return;if(!s&&ae(e.model))return;if(s||re||!ngt(e.model,!1,!ie,L,ce)){'
    )
    handler_state_patterns = {
        'Y(e.model)||ne("compass_mode",null),B||H(e.model),D(e.model),i?.(e)}': (
            'Y(e.model)||ne("compass_mode",null),H(e.model),D(e.model),i?.(e)}'
        ),
        'Y(e.model)||ne("compass_mode",null),H(e.model),D(e.model),i?.(e)}': (
            'Y(e.model)||ne("compass_mode",null),H(e.model),D(e.model),i?.(e)}'
        ),
    }

    menu_effort_re = re.compile(
        r'a\.jsx\(igt,\{section:\{current:me,options:\[\{value:"low",label:"低"\},'
        r'\{value:"medium",label:"中"\},\{value:"high",label:"高"\}(?:,\{value:"xhigh",label:"超高"\},\{value:"max",label:"最大"\})?\],'
        r'onSelect:e=>\{he\(e\);try\{localStorage\.setItem\("cowork_effort_level",e\),'
        r'window\.dispatchEvent\(new CustomEvent\("cowork-effort-change",\{detail:e\}\)\)\}catch\{\}\}\},compactMenu:j\}\)',
        re.DOTALL,
    )
    menu_effort_target = 'a.jsx(igt,{section:fe,compactMenu:j})'

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "cowork_model" not in text or "const ogt=({conversationUuid" not in text:
            continue
        patched = text
        count = 0
        patched, n = vft_re.subn(vft_target, patched, count=1)
        count += n
        for source, target in wft_patterns.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        ogt_start = patched.find("ogt=({conversationUuid")
        if ogt_start >= 0:
            ogt_end = min(len(patched), ogt_start + 30000)
            ogt_chunk = patched[ogt_start:ogt_end]
            ogt_count = 0
            for ogt_model_re in ogt_model_res:
                ogt_chunk, n = ogt_model_re.subn(ogt_model_target, ogt_chunk, count=1)
                ogt_count += n
                if n:
                    break
            ogt_chunk, n = effort_re.subn(effort_target, ogt_chunk, count=1)
            ogt_count += n
            ogt_chunk, n = handler_re.subn(handler_target, ogt_chunk, count=1)
            ogt_count += n
            for source, target in handler_state_patterns.items():
                occurrences = ogt_chunk.count(source)
                if occurrences:
                    ogt_chunk = ogt_chunk.replace(source, target)
                    ogt_count += occurrences
            ogt_chunk, n = menu_effort_re.subn(menu_effort_target, ogt_chunk, count=1)
            ogt_count += n
            if ogt_count:
                patched = patched[:ogt_start] + ogt_chunk + patched[ogt_end:]
                count += ogt_count
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    return patched_files, patched_strings


def patch_epitaxy_model_menu(assets_dir: Path) -> tuple[int, int]:
    """把 Claude Code 模型菜单固定为 Opus 伪装入口、Kimi 真实入口和完整强度。"""
    patched_files = 0
    patched_strings = 0

    # Claude 1.6608+：Code 页模型菜单在 zm() 内部生成，强度来自 hm()/gm() 与 xs。
    # 旧版 ps/Od(W) 类补丁无法覆盖这里，所以单独处理新版结构。
    code_current_re = re.compile(
        r'const K=e\.useCallback\(e=>null!==e&&M\.some\(t=>t\.model===e\),\[M\]\)\(S\)\?S:null,'
        r'W=H\?\?O\?\?L\?\?K\?\?k,V=M\.find\(e=>e\.model===W\),G=V\?null:st\(W\),'
        r'Q=e\.useMemo\(\(\)=>V\?Fm\(V\):G,\[V,G\]\),X=nt\(\)',
        re.DOTALL,
    )
    code_current_target = (
        'const K=e.useCallback(e=>null!==e&&M.some(t=>t.model===e),[M])(S)?S:null,'
        'W=(e=>{const t=String(e??"").toLowerCase();'
        'if(!e)return"opus[1m]";'
        'if("kimi-for-coding"===t||"kimi-k2.6"===t)return"kimi-for-coding";'
        'if("opus"===t||"opus[1m]"===t)return"opus[1m]";'
        'const s=M.find(e=>String(e.model??"").toLowerCase()===t||String(e.name??"").toLowerCase()===t);'
        'return s?s.model:(/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e))?"kimi-for-coding":"opus[1m]")'
        '})(H??O??L??K??k),'
        'V=M.find(e=>e.model===W),'
        'G=V?null:("opus"===W||"opus[1m]"===W?"Opus 4.71M":'
        '("kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W))?"Kimi-k2.6":st(W))),'
        'Q=e.useMemo(()=>("kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?'
        '"Kimi-k2.6":V?Fm(V):G,[V,G,W]),X=nt()'
    )
    code_items_re = re.compile(
        r'pe=e\.useMemo\(\(\)=>\{const e=M\.map\(e=>\{const t=C\.includes\(e\.model\);return\{label:t\?.*?'
        r'\},\[M,C,W,ue,ie,oe,G,s\]\),me=e\.useMemo\(\(\)=>\{if\(!de\)return pe;'
        r'const\[e,\.\.\.t\]=pe;return e\?\[de,\{...e,separatorBefore:!0\},\.\.\.t\]:\[de\]\},\[de,pe\]\)',
        re.DOTALL,
    )
    code_items_target = (
        'pe=e.useMemo(()=>{'
        'const i=e=>"kimi-for-coding"===String(e).toLowerCase()||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e)),'
        'o=M.find(e=>{const t=String(e.model??"").toLowerCase(),s=String(e.name??"").toLowerCase();'
        'return"kimi-for-coding"===t||"kimi-for-coding"===s||"kimi-k2.6"===t||"kimi-k2.6"===s||/kimi.*k2\\.6/i.test(t)||/kimi.*k2\\.6/i.test(s)}),'
        'l=o?.model??"kimi-for-coding";'
        'return['
        '{label:"Opus 4.71M",checked:"opus"===W||"opus[1m]"===W,onSelect:()=>ue.current("opus[1m]"),disabled:ie},'
        '{label:"Kimi-k2.6",checked:String(W).toLowerCase()===String(l).toLowerCase()||i(W),onSelect:()=>ue.current(l),disabled:ie}'
        ']},[M,W,ue,ie]),me=pe'
    )
    code_xs_re = re.compile(
        r'xs=e\.useMemo\(\(\)=>\{const e=\[\];if\(ms\)\{const t=fm\.filter\(e=>\("max"!==e\|\|Fe\)&&\("xhigh"!==e\|\|Oe\)\);'
        r'e\.push\(\{key:"effort",header:s\.formatMessage\(Pm\.effortHeader\),items:t\.map\(e=>\(\{label:s\.formatMessage\(Em\[e\]\),checked:e===hs,onSelect:\(\)=>gs\(e\)\}\)\)\}\)\}'
        r'if\(ls\)\{const t=null!==cs;e\.push\(\{key:"fastMode",header:s\.formatMessage\(Pm\.fastModeHeader\),items:\[\{label:s\.formatMessage\(Pm\.fastModeToggleLabel\),'
        r'keepOpen:!0,disabled:t,onSelect:t\?void 0:\(\)=>Pe\(!Ee\),tooltip:cs\?\?s\.formatMessage\(Pm\.fastModeToggleHint\),tooltipSide:"left",tooltipMultiline:!0,'
        r'trailing:c\.jsx\(Tu,\{checked:!t&&Ee,disabled:t,"aria-hidden":!0,tabIndex:-1\}\)\}\]\}\)\}return e\},\[ms,Fe,Oe,hs,gs,ls,cs,Ee,Pe,s\]\)',
        re.DOTALL,
    )
    code_xs_target = (
        'xs=e.useMemo(()=>{const e=[],t=fm;'
        'e.push({key:"effort",header:s.formatMessage(Pm.effortHeader),items:t.map(e=>({label:s.formatMessage(Em[e]),checked:e===hs,onSelect:()=>gs(e)}))});'
        'if(ls){const t=null!==cs;e.push({key:"fastMode",header:s.formatMessage(Pm.fastModeHeader),items:[{label:s.formatMessage(Pm.fastModeToggleLabel),'
        'keepOpen:!0,disabled:t,onSelect:t?void 0:()=>Pe(!Ee),tooltip:cs??s.formatMessage(Pm.fastModeToggleHint),tooltipSide:"left",tooltipMultiline:!0,'
        'trailing:c.jsx(Tu,{checked:!t&&Ee,disabled:t,"aria-hidden":!0,tabIndex:-1})}]})}return e},[hs,gs,ls,cs,Ee,Pe,s])'
    )
    code_new_replacements = {
        'pm={low:"Low",medium:"Medium",high:"High",xhigh:"Extra high",max:"Max"}': (
            'pm={low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}'
        ),
        'g="max"===h&&!r||"xhigh"===h&&!o?"high":h;return{effortLevel:g,spawnEffortLevel:u&&null===p&&null===f?void 0:g,setEffortLevel:e.useCallback(e=>{localStorage.setItem(um,e),m(e)},[]),modelSupportsEffort:i,modelSupportsMaxEffort:r,modelSupportsXhighEffort:o}': (
            'g=h;return{effortLevel:g,spawnEffortLevel:g,setEffortLevel:e.useCallback(e=>{localStorage.setItem(um,e),m(e)},[]),modelSupportsEffort:!0,modelSupportsMaxEffort:!0,modelSupportsXhighEffort:!0}'
        ),
        'x=g.success?"max"===g.data&&!p||"xhigh"===g.data&&!m?"high":g.data:void 0,v=h.current!==n&&void 0!==x?x:c,b=f&&(void 0!==n?!!l&&!!n:s);return{section:e.useMemo(()=>{if(!b)return;const e=fm.filter(e=>("max"!==e||p)&&("xhigh"!==e||m));return{current:v,options:e.map(e=>({value:e,label:pm[e]})),onSelect:e=>{': (
            'x=g.success?g.data:void 0,v=h.current!==n&&void 0!==x?x:c,b=!0;return{section:e.useMemo(()=>{const e=fm;return{current:v,options:e.map(e=>({value:e,label:pm[e]})),onSelect:e=>{'
        ),
        'spawnEffort:b?d:void 0}': 'spawnEffort:d}',
        'ms=De&&(t?!!ps:"bridge"!==rs),hs=': 'ms=!0,hs=',
        'He=Ue.success?"max"===Ue.data&&!Fe||"xhigh"===Ue.data&&!Oe?"high":Ue.data:void 0': (
            'He=Ue.success?Ue.data:void 0'
        ),
        'effort:De?_e:void 0,repoInfo': 'effort:_e,repoInfo',
    }

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if 'const um="ccd-effort-level"' not in text or "modelExtraSections:xs" not in text:
            continue
        patched = text
        count = 0
        patched, n = code_current_re.subn(code_current_target, patched, count=1)
        count += n
        patched, n = code_items_re.subn(code_items_target, patched, count=1)
        count += n
        patched, n = code_xs_re.subn(code_xs_target, patched, count=1)
        count += n
        for source, target in code_new_replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count
    kimi_match = r'("kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\.6/i.test(String(W)))'
    custom_effort_support = f'("opus"===W||"opus[1m]"===W||{kimi_match})'
    effort_support = f'(_e||"opus"===W||"opus[1m]"===W||{kimi_match})'
    effort_menu_support = f'({custom_effort_support}||(_e&&(t?!!fs:"bridge"!==is)))'

    z_name_re = re.compile(
        r'function Zp\(e\)\{(?:if\("opus\[1m\]"===e\?\.model\|\|"opus"===e\?\.model\)'
        r'return"Opus 4\.7(?: ?1)?M";)?const t=Ct\(e\.model\);'
    )
    z_name_target = (
        'function Zp(e){if("opus[1m]"===e?.model||"opus"===e?.model)'
        'return"Opus 4.71M";const t=Ct(e.model);'
    )

    current_model_re = re.compile(
        r'const K=e\.useCallback\(e=>null!==e&&M\.some\(t=>t\.model===e\),\[M\]\)\(S\)\?S:null,'
        r'W=.*?,V=M\.find\(e=>e\.model===W\),G=.*?,'
        r'X=e\.useMemo\(\(\)=>V\?Zp\(V\):G,\[V,G\]\),Q=Xe\(\)',
        re.DOTALL,
    )
    current_model_target = (
        'const K=e.useCallback(e=>null!==e&&M.some(t=>t.model===e),[M])(S)?S:null,'
        'W=(e=>{const t=String(e??"").toLowerCase();'
        'if(!e)return"opus[1m]";'
        'if("kimi-for-coding"===t||"kimi-k2.6"===t)return"kimi-for-coding";'
        'if("opus"===t||"opus[1m]"===t)return"opus[1m]";'
        'const s=M.find(e=>String(e.model??"").toLowerCase()===t||String(e.name??"").toLowerCase()===t);'
        'return s?s.model:(/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e))?'
        '(t==="kimi-k2.6"?"kimi-for-coding":e):"opus[1m]")'
        '})(U??O??L??K??k),'
        'V=M.find(e=>e.model===W),'
        'G=V?null:("opus"===W||"opus[1m]"===W?"Opus 4.71M":'
        '("kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W))?"Kimi-k2.6":Ge(W))),'
        'X=e.useMemo(()=>("kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?'
        '"Kimi-k2.6":V?Zp(V):G,[V,G,W]),Q=Xe()'
    )

    model_items_res = [
        re.compile(
            r'pe=e\.useMemo\(\(\)=>\{const e=\{label:"Opus 4\.71M".*?return\[e,a\]\},\[M,W,ue,ie\]\)',
            re.DOTALL,
        ),
        re.compile(
            r'pe=e\.useMemo\(\(\)=>\{.*?\},\[M,C,W,ue,ie,oe,G,s\]\)',
            re.DOTALL,
        ),
    ]
    model_items_target = (
        'pe=e.useMemo(()=>{'
        'const i=e=>"kimi-for-coding"===String(e).toLowerCase()||/kimi/i.test(String(e))&&/k2\\.6/i.test(String(e)),'
        'o=e=>{const t=String(e??"").toLowerCase();return"kimi-k2.6"===t?'
        '"kimi-for-coding":(i(e)?e:"kimi-for-coding")},'
        'e={label:"Opus 4.71M",checked:"opus"===W||"opus[1m]"===W,'
        'onSelect:()=>ue.current("opus[1m]"),disabled:ie},'
        't=M.find(e=>{const t=String(e.model??"").toLowerCase(),s=String(e.name??"").toLowerCase();'
        'return"kimi-for-coding"===t||"kimi-for-coding"===s||"kimi-k2.6"===t||"kimi-k2.6"===s||/kimi.*k2\\.6/i.test(t)||/kimi.*k2\\.6/i.test(s)}),'
        'n=t?.model??o(W),'
        'a={label:"Kimi-k2.6",checked:String(W).toLowerCase()===String(n).toLowerCase()||i(W)&&i(n),'
        'onSelect:()=>ue.current(n),disabled:ie};'
        'return[e,a]},[M,W,ue,ie])'
    )

    effort_state_res = [
        re.compile(
            r'ms=Ct\.current!==fs&&void 0!==It\?It:Ee,hs=e\.useCallback\(e=>\{.*?\},'
            r'\[ms,Ae,fs,cs,us,Z,ne,a,s\]\),',
            re.DOTALL,
        ),
        re.compile(
            r'\[codeEffort,setCodeEffort\]=e\.useState\(\(\)=>\{try\{return localStorage\.getItem\("epitaxy_effort_level"\)\|\|null\}catch\{return null\}\}\),'
            r'ms=.*?hs=e\.useCallback\(t=>\{.*?\},\[ms,Ae,fs,cs,us,Z,ne,a,s\]\),',
            re.DOTALL,
        ),
    ]
    effort_state_target = (
        '[codeEffort,setCodeEffort]=e.useState(()=>{try{return localStorage.getItem("epitaxy_effort_level")||null}catch{return null}}),'
        'ms=codeEffort??(Ct.current!==fs&&void 0!==It?It:Ee),'
        'hs=e.useCallback(t=>{if(t===ms)return;Ct.current=fs,setCodeEffort(t);'
        'try{localStorage.setItem("epitaxy_effort_level",t)}catch{}'
        'Ae(t);const e=e=>{a(s.formatMessage({defaultMessage:"Effort change couldn\'t be applied. You can try again.",id:"NiIv1JQ3Vw"}),'
        '{error:e,errorContext:{tags:{source:"epitaxy_set_effort"}},messageForLogging:"Effort change couldn\'t be applied. You can try again."})};'
        'us?Z(us)?.setEffort?.(us.id,t).then(()=>ne(us,{effort:t})).catch(e):'
        'fs&&cs&&Promise.resolve(cs(fs,t)).then(()=>ne({id:fs,type:"local"},{effort:t})).catch(e)},'
        '[ms,Ae,fs,cs,us,Z,ne,a,s]),'
    )
    effort_section_target = (
        'gs=e.useMemo(()=>{const e=[],t=["low","medium","high","xhigh","max"];'
        'e.push({key:"effort",header:s.formatMessage(Gp.effortHeader),'
        'items:t.map(e=>({label:s.formatMessage(Vp[e]),checked:e===ms,onSelect:()=>hs(e)}))});'
        'if(os){const t=null!==ls;e.push({key:"fastMode",header:s.formatMessage(Gp.fastModeHeader),'
        'items:[{label:s.formatMessage(Gp.fastModeToggleLabel),keepOpen:!0,disabled:t,'
        'onSelect:t?void 0:()=>Pe(!Ie),tooltip:ls??s.formatMessage(Gp.fastModeToggleHint),'
        'tooltipSide:"left",tooltipMultiline:!0,'
        'trailing:r.jsx(Id,{checked:!t&&Ie,disabled:t,"aria-hidden":!0,tabIndex:-1})}]})}'
        'return e},[ms,hs,os,ls,Ie,Pe,s])'
    )

    effort_patterns = {
        'ps=_e&&(t?!!fs:"bridge"!==is),ms=': f'ps={effort_menu_support},ms=',
        'ps=(_e||"opus"===W||"opus[1m]"===W)&&(t?!!fs:"bridge"!==is),ms=': f'ps={effort_menu_support},ms=',
        'ps=(_e||"opus"===W||"opus[1m]"===W||"Kimi-k2.6"===W)&&(t?!!fs:"bridge"!==is),ms=': f'ps={effort_menu_support},ms=',
        'ps=(_e||"opus"===W||"opus[1m]"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))&&(t?!!fs:"bridge"!==is),ms=': f'ps={effort_menu_support},ms=',
        'ps=(_e||"opus"===W||"opus[1m]"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))&&(t?!!fs:"bridge"!==is),[codeEffort': f'ps={effort_menu_support},[codeEffort',
        f'ps={effort_support}&&(t?!!fs:"bridge"!==is),ms=': f'ps={effort_menu_support},ms=',
        f'ps={effort_support}&&(t?!!fs:"bridge"!==is),[codeEffort': f'ps={effort_menu_support},[codeEffort',
        f'ps={effort_menu_support},ms=': f'ps={effort_menu_support},ms=',
        f'ps={effort_menu_support},[codeEffort': f'ps={effort_menu_support},[codeEffort',
        'effort:_e?Te:void 0,repoInfo': f'effort:{effort_support}?ms:void 0,repoInfo',
        'effort:(_e||"opus"===W||"opus[1m]"===W)?Te:void 0,repoInfo': f'effort:{effort_support}?ms:void 0,repoInfo',
        'effort:(_e||"opus"===W||"opus[1m]"===W||"Kimi-k2.6"===W)?Te:void 0,repoInfo': f'effort:{effort_support}?ms:void 0,repoInfo',
        'effort:(_e||"opus"===W||"opus[1m]"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?Te:void 0,repoInfo': f'effort:{effort_support}?ms:void 0,repoInfo',
        f'effort:{effort_support}?Te:void 0,repoInfo': f'effort:{effort_support}?ms:void 0,repoInfo',
        'const t=Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));': (
            'const t=("opus"===W||"opus[1m]"===W||"kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))'
            '?["low","medium","high","xhigh","max"]:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));'
        ),
        'const t=("opus"===W||"opus[1m]"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?Ud:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));': (
            'const t=("opus"===W||"opus[1m]"===W||"kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))'
            '?["low","medium","high","xhigh","max"]:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));'
        ),
        'const t=("opus"===W||"opus[1m]"===W||"kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?["low","medium","high","xhigh","max"]:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));': (
            'const t=("opus"===W||"opus[1m]"===W||"kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))'
            '?["low","medium","high","xhigh","max"]:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));'
        ),
        'gs=e.useMemo(()=>{const e=[];if(ps){const t=Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));e.push({key:"effort",header:s.formatMessage(Gp.effortHeader),items:t.map(e=>({label:s.formatMessage(Vp[e]),checked:e===ms,onSelect:()=>hs(e)}))})}if(os){const t=null!==ls;e.push({key:"fastMode",header:s.formatMessage(Gp.fastModeHeader),items:[{label:s.formatMessage(Gp.fastModeToggleLabel),keepOpen:!0,disabled:t,onSelect:t?void 0:()=>Pe(!Ie),tooltip:ls??s.formatMessage(Gp.fastModeToggleHint),tooltipSide:"left",tooltipMultiline:!0,trailing:r.jsx(Id,{checked:!t&&Ie,disabled:t,"aria-hidden":!0,tabIndex:-1})}]})}return e},[ps,De,ze,ms,hs,os,ls,Ie,Pe,s])': effort_section_target,
        'gs=e.useMemo(()=>{const e=[];if(ps){const t=("opus"===W||"opus[1m]"===W||"kimi-for-coding"===W||/kimi/i.test(String(W))&&/k2\\.6/i.test(String(W)))?["low","medium","high","xhigh","max"]:Ud.filter(e=>("max"!==e||De)&&("xhigh"!==e||ze));e.push({key:"effort",header:s.formatMessage(Gp.effortHeader),items:t.map(e=>({label:s.formatMessage(Vp[e]),checked:e===ms,onSelect:()=>hs(e)}))})}if(os){const t=null!==ls;e.push({key:"fastMode",header:s.formatMessage(Gp.fastModeHeader),items:[{label:s.formatMessage(Gp.fastModeToggleLabel),keepOpen:!0,disabled:t,onSelect:t?void 0:()=>Pe(!Ie),tooltip:ls??s.formatMessage(Gp.fastModeToggleHint),tooltipSide:"left",tooltipMultiline:!0,trailing:r.jsx(Id,{checked:!t&&Ie,disabled:t,"aria-hidden":!0,tabIndex:-1})}]})}return e},[ps,De,ze,ms,hs,os,ls,Ie,Pe,s,W])': effort_section_target,
        effort_section_target: effort_section_target,
        '[ps,De,ze,ms,hs,os,ls,Ie,Pe,s])': '[ps,De,ze,ms,hs,os,ls,Ie,Pe,s,W])',
    }

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "function em(t){const s=i()" not in text or "modelExtraSections:gs" not in text:
            continue
        patched = text
        count = 0
        patched, n = z_name_re.subn(z_name_target, patched, count=1)
        count += n
        patched, n = current_model_re.subn(current_model_target, patched, count=1)
        count += n
        for model_items_re in model_items_res:
            patched, n = model_items_re.subn(model_items_target, patched, count=1)
            count += n
            if n:
                break
        for effort_state_re in effort_state_res:
            patched, n = effort_state_re.subn(effort_state_target, patched, count=1)
            count += n
            if n:
                break
        for source, target in effort_patterns.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            patched_files += 1
            patched_strings += count

    return patched_files, patched_strings


def align4(value: int) -> int:
    return value + ((4 - (value % 4)) % 4)


def read_asar_header(data: bytes, path: Path) -> tuple[int, str, dict[str, Any]]:
    if len(data) < 16:
        raise SystemExit(f"Unsupported app.asar header in {path}")

    size_pickle_payload = struct.unpack_from("<I", data, 0)[0]
    header_size = struct.unpack_from("<I", data, 4)[0]
    if size_pickle_payload != 4 or header_size <= 0 or len(data) < 8 + header_size:
        raise SystemExit(f"Unsupported app.asar size pickle in {path}")

    header_pickle = data[8 : 8 + header_size]
    header_payload_size = struct.unpack_from("<I", header_pickle, 0)[0]
    header_string_size = struct.unpack_from("<i", header_pickle, 4)[0]
    expected_payload_size = align4(4 + header_string_size)
    if header_payload_size != expected_payload_size or header_size != 4 + header_payload_size:
        raise SystemExit(f"Unsupported app.asar header pickle in {path}")

    header_start = 8
    header_end = header_start + header_string_size
    header_string = header_pickle[header_start:header_end].decode("utf-8")
    header = json.loads(header_string)
    if not isinstance(header, dict):
        raise SystemExit(f"Unsupported app.asar header JSON in {path}")
    return header_size, header_string, header


def encode_asar_header(header_string: str, expected_header_size: int | None = None) -> bytes:
    header_bytes = header_string.encode("utf-8")
    header_payload_size = align4(4 + len(header_bytes))
    header_pickle = (
        struct.pack("<I", header_payload_size)
        + struct.pack("<i", len(header_bytes))
        + header_bytes
        + b"\0" * (header_payload_size - 4 - len(header_bytes))
    )
    if expected_header_size is not None and len(header_pickle) != expected_header_size:
        raise SystemExit("Internal patch error: app.asar header length changed.")
    return struct.pack("<I", 4) + struct.pack("<I", len(header_pickle)) + header_pickle


def get_asar_file_entry(header: dict[str, Any], file_path: str) -> dict[str, Any]:
    node: dict[str, Any] = header
    for part in file_path.split("/"):
        files = node.get("files")
        if not isinstance(files, dict) or part not in files:
            raise SystemExit(f"Could not find {file_path} in app.asar header.")
        child = files[part]
        if not isinstance(child, dict):
            raise SystemExit(f"Unsupported app.asar header entry for {file_path}.")
        node = child
    for key in ["size", "offset", "integrity"]:
        if key not in node:
            raise SystemExit(f"Missing {key} for {file_path} in app.asar header.")
    return node


def calculate_file_integrity(data: bytes) -> dict[str, Any]:
    blocks = [
        hashlib.sha256(data[offset : offset + ASAR_INTEGRITY_BLOCK_SIZE]).hexdigest()
        for offset in range(0, len(data), ASAR_INTEGRITY_BLOCK_SIZE)
    ]
    if not blocks:
        blocks.append(hashlib.sha256(data).hexdigest())
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": ASAR_INTEGRITY_BLOCK_SIZE,
        "blocks": blocks,
    }


def update_electron_asar_integrity(app: Path, header_string: str) -> None:
    info_plist = app / "Contents/Info.plist"
    require_file(info_plist)
    with info_plist.open("rb") as f:
        info = plistlib.load(f)

    integrity = info.get("ElectronAsarIntegrity")
    if not isinstance(integrity, dict):
        raise SystemExit("Info.plist is missing ElectronAsarIntegrity.")
    app_asar = integrity.get("Resources/app.asar")
    if not isinstance(app_asar, dict) or app_asar.get("algorithm") != "SHA256":
        raise SystemExit("Info.plist has unsupported ElectronAsarIntegrity format.")

    app_asar["hash"] = hashlib.sha256(header_string.encode("utf-8")).hexdigest()
    tmp = info_plist.with_suffix(info_plist.suffix + ".tmp")
    with tmp.open("wb") as f:
        plistlib.dump(info, f, fmt=plistlib.FMT_XML)
    os.replace(tmp, info_plist)


def patch_custom3p_model_validation(app: Path) -> bool:
    path = app / APP_ASAR_REL
    require_file(path)

    old_expr = b'process.env.NODE_ENV!=="production"'
    new_expr = b"false"
    replacement = new_expr + b" " * (len(old_expr) - len(new_expr))
    anchor = b"const Hte=" + old_expr + b"||!1,eRt="
    patched = b"const Hte=" + replacement + b"||!1,eRt="
    if len(anchor) != len(patched):
        raise SystemExit("Internal patch error: custom 3P validation replacement changed length.")

    data = bytearray(path.read_bytes())
    header_size, _header_string, header = read_asar_header(data, path)
    entry = get_asar_file_entry(header, ASAR_PATCH_TARGET)
    content_offset = 8 + header_size + int(entry["offset"])
    content_size = int(entry["size"])
    content_end = content_offset + content_size
    if content_offset < 0 or content_end > len(data):
        raise SystemExit(f"Unsupported app.asar file bounds for {ASAR_PATCH_TARGET}.")

    content = bytes(data[content_offset:content_end])
    if content.count(patched) == 1:
        print("Custom 3P model-name validation already patched in app.asar")
        return True

    count = content.count(anchor)
    if count == 1:
        patched_content = content.replace(anchor, patched, 1)
    else:
        # Claude 1.6608.2 moved the 3P model-name validation gate to FLA.
        fla_anchor = b'const FLA=process.env.NODE_ENV!=="production"||!1,Yxe='
        fla_replacement = b"const FLA=" + replacement + b"||!1,Yxe="
        if len(fla_anchor) != len(fla_replacement):
            raise SystemExit("Internal patch error: custom 3P FLA replacement changed length.")
        if content.count(fla_replacement) == 1:
            print("Custom 3P model-name validation already patched in app.asar")
            return True
        if content.count(fla_anchor) == 1:
            patched_content = content.replace(fla_anchor, fla_replacement, 1)
        else:
            # Claude 1.6608+ temporarily moved the model-name validation into _Zt().
            # Make that validator a no-op while preserving app.asar file length.
            new_anchor = b"function _Zt(e,A){if(!bbA||!(A!=null&&A.length))return null;"
            new_expr = b"return null;"
            new_patched = b"function _Zt(e,A){" + new_expr + b" " * (len(new_anchor) - len(b"function _Zt(e,A){") - len(new_expr))
            if len(new_anchor) != len(new_patched):
                raise SystemExit("Internal patch error: custom 3P validation replacement changed length.")
            if content.count(new_patched) == 1:
                print("Custom 3P model-name validation already patched in app.asar")
                return True
            if content.count(new_anchor) != 1:
                print(
                    "Warning: Could not patch custom 3P model validation. "
                    "Claude bundle format may have changed. Continue without this optional patch."
                )
                return False
            patched_content = content.replace(new_anchor, new_patched, 1)

    if len(patched_content) != len(content):
        raise SystemExit("Internal patch error: app.asar length changed during custom 3P patch.")
    data[content_offset:content_end] = patched_content

    entry["integrity"] = calculate_file_integrity(patched_content)
    updated_header_string = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    updated_header = encode_asar_header(updated_header_string, header_size)
    data[: len(updated_header)] = updated_header

    path.write_bytes(data)
    update_electron_asar_integrity(app, updated_header_string)
    print("Patched custom 3P model-name validation in app.asar")
    return True


def walk_asar_file_entries(header: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        files = node.get("files")
        if not isinstance(files, dict):
            return
        for child in files.values():
            if not isinstance(child, dict):
                continue
            if "files" in child:
                walk(child)
            elif "offset" in child and "size" in child:
                entries.append(child)

    walk(header)
    return entries


def patch_asar_file_with_replacements(app: Path, file_path: str, replacements: dict[str, str]) -> int:
    path = app / APP_ASAR_REL
    require_file(path)
    data = path.read_bytes()
    header_size, _header_string, header = read_asar_header(data, path)
    entry = get_asar_file_entry(header, file_path)
    content_offset = 8 + header_size + int(entry["offset"])
    content_size = int(entry["size"])
    content_end = content_offset + content_size
    if content_offset < 0 or content_end > len(data):
        raise SystemExit(f"Unsupported app.asar file bounds for {file_path}.")

    content = data[content_offset:content_end].decode("utf-8")
    patched = content
    count = 0
    for source, target in replacements.items():
        occurrences = patched.count(source)
        if occurrences:
            patched = patched.replace(source, target)
            count += occurrences

    if patched == content:
        return 0

    patched_bytes = patched.encode("utf-8")
    delta = len(patched_bytes) - content_size
    original_offset = int(entry["offset"])
    for item in walk_asar_file_entries(header):
        item_offset = int(item["offset"])
        if item is entry:
            item["size"] = len(patched_bytes)
            item["integrity"] = calculate_file_integrity(patched_bytes)
        elif item_offset > original_offset:
            item["offset"] = str(item_offset + delta)

    updated_header_string = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    updated_header = encode_asar_header(updated_header_string)
    body = data[8 + header_size :]
    body = body[:original_offset] + patched_bytes + body[original_offset + content_size :]
    path.write_bytes(updated_header + body)
    update_electron_asar_integrity(app, updated_header_string)
    return count


def patch_native_menu_role_labels(app: Path) -> None:
    replacements = {
        '{role:"services"}': '{label:"服务",role:"services"}',
        '{role:"hide"}': '{label:"隐藏 Claude",role:"hide"}',
        '{role:"hideOthers"}': '{label:"隐藏其他",role:"hideOthers"}',
        '{role:"unhide"}': '{label:"全部显示",role:"unhide"}',
        '{role:"minimize"}': '{label:"最小化",role:"minimize"}',
        '{role:"front"}': '{label:"全部置于前面",role:"front"}',
    }
    count = patch_asar_file_with_replacements(app, ASAR_PATCH_TARGET, replacements)
    print(f"Patched native menu role labels in app.asar: {count} replacements")


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

    shutil.copy2(DESKTOP_TRANSLATION, resources_dir / "zh-CN.json")
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
        # Ad-hoc signatures cannot legitimately claim Anthropic's Team ID.
        # Newer macOS builds can kill the app at launch when these restricted
        # identifiers remain after local re-signing.
        for restricted_key in [
            "com.apple.application-identifier",
            "com.apple.developer.team-identifier",
            "keychain-access-groups",
        ]:
            entitlements.pop(restricted_key, None)
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
    cleared: list[str] = []
    for attr in ["com.apple.quarantine", "com.apple.provenance"]:
        result = run(["xattr", "-dr", attr, str(app)], check=False)
        if result.returncode == 0:
            cleared.append(attr)
    if cleared:
        print(f"Cleared Gatekeeper attributes: {', '.join(cleared)}")


def set_locale_config(config: Path) -> None:
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


def set_app_language_defaults(user_home: Path) -> None:
    user = os.environ.get("SUDO_USER")
    if not user or user == "root":
        user = user_home.name

    defaults_prefix: list[str] = []
    if os.geteuid() == 0 and user and user != "root":
        defaults_prefix = ["sudo", "-u", user]

    domain = "com.anthropic.claudefordesktop"
    run(
        defaults_prefix
        + [
            "defaults",
            "write",
            domain,
            "AppleLanguages",
            "-array",
            "zh-Hans",
            "zh-Hans-CN",
            "zh-CN",
            "en-CN",
            "en-US",
        ],
        check=False,
    )
    run(defaults_prefix + ["defaults", "write", domain, "AppleLocale", "-string", "zh_CN"], check=False)
    print(f"Set Claude app language defaults for user: {user}")


def set_user_locale(user_home: Path) -> None:
    for support_dir in ["Claude", "Claude-3p"]:
        set_locale_config(user_home / f"Library/Application Support/{support_dir}/config.json")
    set_app_language_defaults(user_home)


def clear_frontend_cache(user_home: Path, dry_run: bool) -> None:
    cache_names = ["Cache", "Code Cache", "GPUCache", "Service Worker", "DawnCache", "ShaderCache"]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    moved = 0
    for support_dir in ["Claude", "Claude-3p"]:
        base = user_home / f"Library/Application Support/{support_dir}"
        trash_dir = user_home / ".Trash" / f"{support_dir}-frontend-cache-{stamp}"
        for name in cache_names:
            path = base / name
            if not path.exists():
                continue
            target = trash_dir / name
            if dry_run:
                print(f"[dry-run] Would move frontend cache to trash: {path} -> {target}")
                moved += 1
                continue
            trash_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved += 1
    if moved:
        print(f"Cleared {moved} frontend cache folder(s)")


def check_frontend_invariants(app: Path, report: PatchReport, *, require: bool = True) -> bool:
    bundles = find_frontend_bundles(app)
    index = bundles["index"]
    code = bundles["code"]

    if index is None:
        report.add("frontend.index_bundle", "missing", "找不到包含 Jbt 的主前端 bundle", required=require)
    else:
        text = index.read_text(encoding="utf-8", errors="ignore")
        checks = {
            "cowork.two_models": (
                'Q=[rr,cc],X=[],J=[]' in text
                and 'name:"Opus 4.71M"' in text
                and 'name:"Kimi-k2.6"' in text
            ),
            "cowork.fallback_effort": (
                'cowork_effort_level")||"high"' in text
                and 'Fw=n.useMemo(()=>_??{current:cw' in text
                and '"cowork"===I?{current:cw' not in text
                and 'section:Fw' in text
                and 'value:"xhigh",label:"超高"' in text
                and 'value:"max",label:"最大"' in text
            ),
            "cowork.effort_sync": (
                'window.addEventListener("cowork-effort-change"' in text
                and 'NT?.setYukonSilverConfig?.({...k,effort:_' in text
            ),
            "cowork.kimi_health_hidden": (
                'l.state===yW.Unreachable&&/api\\.kimi\\.com' in text
                or 'l.state===xV.Unreachable&&/api\\.kimi\\.com' in text
            ),
        }
        for name, ok in checks.items():
            report.add(name, "passed" if ok else "missing", file=index, required=require)
        ok, message = check_js_syntax(index)
        report.add("syntax.index_bundle", "passed" if ok else "failed", message, file=index, required=require)

    if code is None:
        report.add("frontend.code_bundle", "missing", "找不到包含 Code 模型菜单的 bundle", required=require)
    else:
        text = code.read_text(encoding="utf-8", errors="ignore")
        checks = {
            "code.two_models": (
                'return[{label:"Opus 4.71M"' in text
                and '{label:"Kimi-k2.6"' in text
            ),
            "code.full_effort": (
                'xs=e.useMemo(()=>{const e=[],t=fm;' in text
                and 'modelSupportsEffort:!0,modelSupportsMaxEffort:!0,modelSupportsXhighEffort:!0' in text
                and 'pm={low:"低",medium:"中",high:"高",xhigh:"超高",max:"最大"}' in text
            ),
        }
        for name, ok in checks.items():
            report.add(name, "passed" if ok else "missing", file=code, required=require)
        ok, message = check_js_syntax(code)
        report.add("syntax.code_bundle", "passed" if ok else "failed", message, file=code, required=require)

    assets_dir = app / FRONTEND_ASSETS_REL
    permission_files: list[Path] = []
    bad_permission_files: list[str] = []
    has_draft_default = False
    has_folder_key = False
    has_bypass_priority = False
    if assets_dir.exists():
        for path in sorted(assets_dir.glob("*.js")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "cc-landing-draft-permission-mode" not in text and "epitaxy-folder-permission-mode" not in text:
                continue
            permission_files.append(path)
            if '"cc-landing-draft-permission-mode","acceptEdits"' in text:
                bad_permission_files.append(path.name)
            if '"epitaxy-folder-permission-mode",' in text:
                bad_permission_files.append(path.name)
            has_draft_default = has_draft_default or '"cc-landing-draft-permission-mode-cn","bypassPermissions"' in text
            has_folder_key = has_folder_key or '"epitaxy-folder-permission-mode-cn"' in text
            has_bypass_priority = has_bypass_priority or 'en??Zs??$s??Gs??"bypassPermissions"' in text
    permission_ok = has_draft_default and has_folder_key and has_bypass_priority and not bad_permission_files
    report.add(
        "code.permission_default_bypass",
        "passed" if permission_ok else "missing",
        (
            ""
            if permission_ok
            else f"permission_files={[path.name for path in permission_files]}, bad={sorted(set(bad_permission_files))}"
        ),
        required=require,
    )

    custom3p_ok = check_custom3p_validation_patched(app)
    report.add("asar.custom3p_validation", "passed" if custom3p_ok else "missing", required=require)

    signature = run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=False)
    report.add(
        "codesign.verify",
        "passed" if signature.returncode == 0 else "failed",
        signature.stdout.strip(),
        required=require,
    )

    return not report.has_required_failures()


def print_report_summary(report: PatchReport) -> None:
    data = report.to_dict()
    summary = data["summary"]
    print("Patch diagnostics summary:")
    for key in ["passed", "applied", "already_patched", "missing", "failed"]:
        if key in summary:
            print(f"  {key}: {summary[key]}")
    failures = data["required_failures"]
    if failures:
        print("Required failures:")
        for item in failures:
            location = f" ({item['file']})" if item.get("file") else ""
            print(f"  - {item['name']}{location}: {item['status']}")


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


def prune_old_backups(original: Path, keep: Path, user_home: Path, dry_run: bool, keep_count: int = 1) -> None:
    backups = sorted(original.parent.glob(f"{original.stem}.backup-before-zh-CN-*.app"))
    if not backups:
        return

    keep_paths = set(backups[-keep_count:])
    keep_paths.add(keep)
    stale = [path for path in backups if path not in keep_paths]
    if not stale:
        return

    trash_dir = user_home / ".Trash" / f"Claude-old-backups-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if dry_run:
        for path in stale:
            print(f"[dry-run] Would move old backup to trash: {path} -> {trash_dir / path.name}")
        return

    trash_dir.mkdir(parents=True, exist_ok=True)
    print(f"Moving {len(stale)} old backup(s) to: {trash_dir}")
    for path in stale:
        shutil.move(str(path), str(trash_dir / path.name))

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        for path in [trash_dir, *trash_dir.iterdir()]:
            os.chown(path, int(sudo_uid), int(sudo_gid))


def prepare_official_update(app: Path, user_home: Path, dry_run: bool) -> Path:
    """解除当前补丁版 Claude.app 的覆盖阻碍，允许 Finder 直接拖官方 DMG 覆盖安装。"""
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")

    if dry_run:
        print(f"[dry-run] Would prepare {app} for Finder overwrite without moving or deleting it")
        print("[dry-run] Would clear uchg/schg flags, Gatekeeper attributes, owner and user-writable permissions")
        print(f"[dry-run] Would not touch user config under {user_home}/Library/Application Support/Claude*")
        return app

    print(f"Preparing current Claude.app for official DMG overwrite: {app}")
    run(["chflags", "-R", "nouchg,noschg", str(app)], check=False)
    clear_quarantine(app)

    if sudo_uid and sudo_gid:
        uid = int(sudo_uid)
        gid = int(sudo_gid)
        for root, dirs, files in os.walk(app):
            for name in [root, *[str(Path(root) / item) for item in dirs], *[str(Path(root) / item) for item in files]]:
                try:
                    os.chown(name, uid, gid)
                except PermissionError:
                    pass

    run(["chmod", "-R", "u+rwX", str(app)], check=False)
    print("Claude.app remains in /Applications. It is now prepared for Finder overwrite.")
    print("Now drag the official Claude.app from the DMG into Applications and choose Replace.")
    print("Your API, gateway and model settings under Application Support were not changed.")
    return app


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
    parser.add_argument("--diagnose", action="store_true", help="Only inspect the current Claude.app patch status and write a diagnostic report")
    parser.add_argument("--prepare-official-update", action="store_true", help="Prepare the patched Claude.app so Finder can overwrite it from the official DMG")
    parser.add_argument("--launch", action="store_true", help="Launch Claude after installation")
    args = parser.parse_args()

    if not args.app.exists():
        raise SystemExit(f"Claude.app not found: {args.app}")

    if args.diagnose:
        report = PatchReport(str(args.app), get_claude_version(args.app), "diagnose")
        check_frontend_invariants(args.app, report, require=True)
        write_patch_report(report)
        print_report_summary(report)
        return 1 if report.has_required_failures() else 0

    if args.prepare_official_update:
        report = PatchReport(str(args.app), get_claude_version(args.app), "prepare-official-update")
        if args.dry_run:
            print("[dry-run] Claude will not be quit.")
        else:
            quit_claude()
        target = prepare_official_update(args.app, args.user_home, args.dry_run)
        report.add(
            "official_update.prepare_overwrite",
            "passed",
            f"prepared_app={target}; moved=false; user_config_untouched=true",
            required=True,
        )
        write_patch_report(report)
        print_report_summary(report)
        return 0

    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)
    require_file(LOCALIZABLE_STRINGS)
    require_virtualization_entitlement(args.app)
    report = PatchReport(str(args.app), get_claude_version(args.app), "dry-run" if args.dry_run else "install")

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
    model_validation_patched = patch_custom3p_model_validation(patched_app)
    report.add(
        "asar.custom3p_validation.patch",
        "applied" if model_validation_patched else "missing",
        required=False,
    )
    patch_native_menu_role_labels(patched_app)
    merge_frontend_locale(patched_app)
    install_desktop_locale(patched_app)
    install_statsig_locale(patched_app)
    resign_app(patched_app)
    clear_quarantine(patched_app)
    if args.dry_run:
        print(f"[dry-run] Would set Claude config locale under: {args.user_home}")
    else:
        set_user_locale(args.user_home)
        clear_frontend_cache(args.user_home, args.dry_run)
    verify(patched_app)
    if not check_frontend_invariants(patched_app, report, require=True):
        write_patch_report(report)
        print_report_summary(report)
        raise SystemExit("Required frontend invariants failed. Original Claude.app was left untouched.")

    backup = backup_and_replace(args.app, patched_app, args.dry_run)
    if not args.dry_run:
        print(f"Backup kept at: {backup}")
        prune_old_backups(args.app, backup, args.user_home, args.dry_run)
        if args.launch:
            run(["open", "-a", str(args.app)], check=False)

    if not model_validation_patched:
        print("Note: optional 3P model-name validation patch was skipped for this Claude version.")
    write_patch_report(report)
    print_report_summary(report)
    print("Done. Select Language -> 中文（中国） in Claude if it is not already selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

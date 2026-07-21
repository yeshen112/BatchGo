"""
应用扫描模块 —— 双源扫描：
1. Get-StartApps (PowerShell) → 覆盖传统桌面应用 + 微软商店 UWP 应用
2. Start Menu .lnk 解析    → 补充快捷方式参数、工作目录等元数据
"""
import os
import sys
import json
import subprocess
from typing import Optional
from dataclasses import dataclass, field

try:
    import pythoncom
    import win32com.client
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


@dataclass
class AppInfo:
    """单个应用的信息"""
    name: str                              # 显示名称
    path: str                              # .exe 路径 或 UWP AUMID
    arguments: str = ""                    # 快捷方式自带参数
    working_dir: str = ""                  # 工作目录
    description: str = ""                  # 分类目录名
    is_system_tool: bool = False           # 是否为系统工具
    is_uwp: bool = False                   # 是否为微软商店/UWP 应用

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "arguments": self.arguments,
            "working_dir": self.working_dir,
            "description": self.description,
            "is_system_tool": self.is_system_tool,
            "is_uwp": self.is_uwp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppInfo":
        return cls(
            name=d.get("name", ""),
            path=d.get("path", ""),
            arguments=d.get("arguments", ""),
            working_dir=d.get("working_dir", ""),
            description=d.get("description", ""),
            is_system_tool=d.get("is_system_tool", False),
            is_uwp=d.get("is_uwp", False),
        )


# ── 过滤模式 ──────────────────────────────────────────────────────
_EXCLUDE_PATTERNS = [
    "unins", "uninst", "uninstall", "unwise",
    "help", "readme", "license",
    "changelog", "whatsnew", "what's new",
    "website", "url", "homepage",
    "configure", "config ", "settings",
    "repair", "diagnose", "diagnostic",
    "check for updates", "update ", "updater",
    "register", "registration",
    "order", "purchase",
    "safe mode",
]

# UWP AppID 特征：包含 ! 或 不是文件路径格式
_UWP_ID_PATTERNS = ["!", "{", "com.", "microsoft.", "apple.", "spotify.", "adobe."]


def _looks_like_uwp(app_id: str) -> bool:
    """判断 AppID 是否像 UWP 应用的 AUMID"""
    if not app_id:
        return False
    # 真正的文件路径
    if os.path.isabs(app_id) and os.path.exists(app_id):
        return False
    if app_id.endswith(".exe"):
        return False
    return True


# ── 系统工具判定 ───────────────────────────────────────────────────

_SYSTEM_TOOL_NAMES = {
    "about windows", "administrative tools", "application verifier",
    "azure", "backup and restore", "bitlocker",
    "calculator", "calendar", "certificate", "character map",
    "cleanmgr", "command prompt", "cmd", "component services",
    "computer management", "control panel", "credential manager", "cttune",
    "default apps", "default programs", "device manager",
    "device pair", "disk cleanup", "disk defragmenter",
    "disk management", "display", "dpapi", "dxdiag",
    "ease of access", "event viewer",
    "file explorer", "file history",
    "fonts", "free up disk space", "getting started",
    "help", "hyper-v", "iis", "indexing options",
    "internet explorer", "iscsi", "isoburn",
    "journal", "keyboard", "language options",
    "magnify", "mail", "malicious software removal",
    "map network", "math input panel", "memory diagnostic",
    "microsoft edge", "migautoplay", "mip", "mobsync", "mouse",
    "mrt", "mstsc", "narrator", "network", "notepad", "notification",
    "odbc", "office", "onedrive", "onenote", "osk",
    "paint", "performance monitor", "phone", "power options",
    "powershell", "print", "print management",
    "private character editor", "problem steps",
    "programs and features", "project", "publisher",
    "recovery", "region", "registry editor", "regedit",
    "remote desktop", "resource monitor", "run",
    "services", "snipping", "snip & sketch",
    "sound", "speech", "steps recorder", "sticky notes",
    "storage", "subscription activation", "sync center",
    "system configuration", "system information",
    "system monitor", "system settings",
    "tablet", "task manager", "task scheduler", "taskbar",
    "telnet", "terminal", "tpm", "troubleshoot",
    "user accounts", "view local services", "virus",
    "visio", "volume mixer", "windows",
    "wordpad", "xbox", "xps", "zip", "7-zip",
}

_SYSTEM_DIR_KEYWORDS = [
    "system32", "syswow64",
    "\\windows\\", "c:\\windows\\",
    "\\windowsapps\\",          # UWP 系统应用目录
]


def _is_system_tool(name: str, target_path: str, is_uwp: bool = False) -> bool:
    """判定是否为系统工具"""
    lower_name = name.lower()
    lower_path = (target_path or "").lower()

    # 1. 路径在系统目录
    for kw in _SYSTEM_DIR_KEYWORDS:
        if kw in lower_path:
            return True

    # 2. 名称匹配已知系统工具列表
    if lower_name in _SYSTEM_TOOL_NAMES:
        return True
    for sys_name in _SYSTEM_TOOL_NAMES:
        if len(sys_name) > 5 and sys_name in lower_name:
            return True

    # 3. UWP 应用来自微软发布者，大概率是系统/系统相关
    if is_uwp and lower_path.startswith("microsoft."):
        return True

    return False


def _find_main_exe(uninstaller_path: str, lnk_name: str = "") -> Optional[str]:
    """
    当 .lnk 指向 Uninstall.exe 时，尝试找到真正的 exe。
    只搜同一目录和相邻兄弟目录的直接文件（不递归，避免卡死）。
    """
    parent = os.path.dirname(uninstaller_path)
    candidates: list[tuple[str, float]] = []
    bad_kw = ["uninst", "update", "setup", "crashpad", "handler", "wetype", "ocr", "player", "wechatocr"]

    def _good(fpath: str) -> bool:
        n = os.path.basename(fpath).lower()
        return n.endswith(".exe") and not any(k in n for k in bad_kw)

    # 1. 同目录
    if os.path.isdir(parent):
        try:
            for f in os.listdir(parent):
                fp = os.path.join(parent, f)
                if _good(fp):
                    candidates.append((fp, os.path.getmtime(fp)))
        except OSError:
            pass

    # 2. 兄弟目录 + 其子目录（最多 2 层，不递归整个树）
    grandparent = os.path.dirname(parent)
    if os.path.isdir(grandparent) and not candidates:
        try:
            for item in os.listdir(grandparent):
                sib = os.path.join(grandparent, item)
                if not os.path.isdir(sib) or sib == parent:
                    continue
                # 兄弟目录的直接文件
                try:
                    for f in os.listdir(sib):
                        fp = os.path.join(sib, f)
                        if os.path.isfile(fp) and _good(fp):
                            candidates.append((fp, os.path.getmtime(fp)))
                except OSError:
                    pass
                # 再深一层
                try:
                    for sub in os.listdir(sib):
                        sub_dir = os.path.join(sib, sub)
                        if not os.path.isdir(sub_dir):
                            continue
                        for f in os.listdir(sub_dir):
                            fp = os.path.join(sub_dir, f)
                            if os.path.isfile(fp) and _good(fp):
                                candidates.append((fp, os.path.getmtime(fp)))
                except OSError:
                    pass
        except OSError:
            pass

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _is_valid_exe_path(target: str) -> bool:
    """检查传统 .exe 路径是否有效"""
    if not target:
        return False
    lower_name = os.path.basename(target).lower()
    if not lower_name.endswith(".exe"):
        return False
    for pattern in _EXCLUDE_PATTERNS:
        if pattern in lower_name:
            return False
    if not os.path.isfile(target):
        return False
    return True


# ── 数据源 1：PowerShell Get-StartApps ────────────────────────────

def _scan_get_start_apps() -> list[tuple[str, str]]:
    """
    通过 PowerShell Get-StartApps 获取所有应用（含 UWP）。
    输出到临时文件避免编码问题。
    """
    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), "batchgo_apps.json")
    ps_cmd = (
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-StartApps | Select-Object Name, AppID | "
        f'ConvertTo-Json -Compress | Out-File -FilePath "{tmp_path}" -Encoding UTF8'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if not os.path.exists(tmp_path):
            return []
        with open(tmp_path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        os.unlink(tmp_path)
        if not raw.strip():
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return [(item["Name"], item["AppID"]) for item in data if item.get("Name")]
    except Exception:
        return []


def _is_fake_app(name: str, app_id: str) -> bool:
    """过滤掉 Get-StartApps 中的假应用（URL、帮助链接、系统管理单元等）"""
    lower_name = name.lower()
    lower_id = app_id.lower()

    # URL 类
    if app_id.startswith("http://") or app_id.startswith("https://"):
        return True
    # 帮助/FAQ/许可
    for kw in ["faq", "documentation", "support center", "release notes",
               "changelog", "license", "readme", "uninstall", "website",
               "eula", "end user license"]:
        if kw in lower_name:
            return True
    # .msc / .cpl 管理控制台
    if lower_id.endswith(".msc}") or "{" in app_id and (".msc}" in lower_id or ".cpl}" in lower_id):
        return True
    # 纯空壳 CLSID / GUID 路径
    if app_id.startswith("::{") and app_id.endswith("}"):
        return True
    if app_id.startswith("{") and "}\\{" in app_id:
        return True
    # 命令行工具
    if lower_name in {"cmd", "powershell", "bash", "wsl"}:
        return True
    # Visual Studio 工具容器（EULA、帮助等）
    if "{7c5a40ef-" in lower_id and any(
        k in lower_name for k in ["eula", "documentation", "faq"]
    ):
        return True
    return False


# ── 数据源 2：Start Menu .lnk 解析 ────────────────────────────────

def _scan_lnk_files(progress_callback=None) -> list[AppInfo]:
    """遍历 Start Menu 解析 .lnk 文件（传统桌面应用）"""
    if not _HAS_WIN32:
        return []

    _com_initialized = False
    try:
        pythoncom.CoInitialize()
        _com_initialized = True
    except Exception:
        pass

    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        start_menu_dirs = _get_start_menu_dirs()
        apps: dict[str, AppInfo] = {}

        all_lnk_files: list[tuple[str, str]] = []
        for sm_dir in start_menu_dirs:
            for root, dirs, files in os.walk(sm_dir):
                category = os.path.relpath(root, sm_dir)
                if category == ".":
                    category = ""
                for f in files:
                    if f.lower().endswith(".lnk"):
                        all_lnk_files.append((os.path.join(root, f), category))

        total = len(all_lnk_files)
        for idx, (lnk_path, category) in enumerate(all_lnk_files):
            if progress_callback:
                progress_callback(idx + 1, total)
            try:
                shortcut = shell.CreateShortcut(lnk_path)
                target = os.path.expandvars(shortcut.TargetPath or "")
                if not target:
                    continue

                # 如果目标是卸载程序，尝试抢救真正的 exe
                original_target = target
                name = os.path.splitext(os.path.basename(lnk_path))[0]
                if not _is_valid_exe_path(target):
                    rescued = _find_main_exe(target, lnk_name=name)
                    if rescued:
                        target = rescued
                    else:
                        continue
                dedup_key = (name.lower(), target.lower())
                if dedup_key not in apps:
                    apps[dedup_key] = AppInfo(
                        name=name, path=target,
                        arguments=shortcut.Arguments or "",
                        working_dir=shortcut.WorkingDirectory or "",
                        description=category,
                        is_system_tool=_is_system_tool(name, target),
                        is_uwp=False,
                    )
            except Exception:
                continue
            finally:
                pass

        return list(apps.values())
    finally:
        try:
            del shell
        except Exception:
            pass
        if _com_initialized:
            pythoncom.CoUninitialize()


def _get_start_menu_dirs() -> list[str]:
    dirs = []
    common = os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu")
    if os.path.isdir(common):
        dirs.append(common)
    user = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu")
    if os.path.isdir(user):
        dirs.append(user)
    return dirs


# ── 合并 & 主扫描入口 ─────────────────────────────────────────────

def scan_apps(progress_callback=None) -> list[AppInfo]:
    """
    双源扫描：Get-StartApps + Start Menu .lnk，合并去重。
    """
    # 1. 从 Get-StartApps 获取全量列表（含 UWP）
    start_apps = _scan_get_start_apps()

    # 2. 从 .lnk 获取传统应用的详细元数据
    lnk_apps = _scan_lnk_files(progress_callback)
    lnk_index: dict[str, AppInfo] = {}
    for a in lnk_apps:
        lnk_index[(a.name.lower(), a.path.lower())] = a

    # 3. 合并：
    #    - 如果 .lnk 已有 → 使用 .lnk 的丰富数据
    #    - 如果只有 Get-StartApps → 新应用（通常是 UWP 或未建快捷方式的）
    merged: dict[str, AppInfo] = {}
    # 先用 .lnk 数据（更丰富）
    for a in lnk_apps:
        merged[(a.name.lower(), a.path.lower())] = a

    # 再补 Get-StartApps 独有的
    seen_names: set = set()
    for a in merged.values():
        seen_names.add(a.name.lower())

    for name, app_id in start_apps:
        name_lower = name.lower()

        # 过滤假应用
        if _is_fake_app(name, app_id):
            continue

        # 过滤：帮助/文档/非应用
        skip = False
        lower_id = app_id.lower()
        for pattern in _EXCLUDE_PATTERNS:
            if pattern in name_lower or pattern in lower_id:
                skip = True
                break
        if any(lower_id.endswith(ext) for ext in [".chm", ".hlp", ".pdf", ".html", ".htm"]):
            skip = True
        if skip:
            continue

        is_uwp = _looks_like_uwp(app_id)

        # 传统应用：用路径匹配 .lnk
        if not is_uwp:
            key = (name_lower, app_id.lower())
            if key in lnk_index:
                continue  # .lnk 已有，跳过
            # 按名称去重
            if name_lower in seen_names:
                continue
            if not _is_valid_exe_path(app_id):
                continue
            seen_names.add(name_lower)
            merged[(name_lower, app_id.lower())] = AppInfo(
                name=name, path=app_id,
                is_system_tool=_is_system_tool(name, app_id),
                is_uwp=False,
            )
        else:
            # UWP 应用
            key = (name_lower, app_id.lower())
            if key in merged:
                continue
            if name_lower in seen_names:
                continue
            seen_names.add(name_lower)
            merged[key] = AppInfo(
                name=name, path=app_id,
                is_system_tool=_is_system_tool(name, app_id, is_uwp=True),
                is_uwp=True,
            )

    # 4. 排序
    common = [a for a in merged.values() if not a.is_system_tool]
    system = [a for a in merged.values() if a.is_system_tool]
    common.sort(key=lambda a: a.name.lower())
    system.sort(key=lambda a: a.name.lower())
    return common + system


def scan_and_cache(config_path: str, progress_callback=None) -> list[AppInfo]:
    apps = scan_apps(progress_callback)
    if apps:
        _update_cache(config_path, apps)
    return apps


def load_cached_apps(config_path: str) -> list[AppInfo]:
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cache = data.get("apps_cache", [])
            if cache:
                return [AppInfo.from_dict(d) for d in cache]
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return []


def _update_cache(config_path: str, apps: list[AppInfo]):
    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    data["apps_cache"] = [a.to_dict() for a in apps]
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

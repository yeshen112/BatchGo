"""
应用扫描模块 —— 双源扫描，完整覆盖桌面 + UWP 应用。

数据源 1：Start Menu .lnk 解析（pywin32 COM）
  → 传统桌面应用，含启动参数/工作目录等丰富元数据

数据源 2：Shell.AppsFolder COM 枚举
  → 覆盖微软商店 UWP 应用 + 所有已注册应用（原生 Unicode，无编码问题）
"""
import os
import sys
import json
from typing import Optional
from dataclasses import dataclass, field

try:
    import pythoncom
    import win32com.client
    from win32com.client import Dispatch
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


@dataclass
class AppInfo:
    """单个应用的信息"""
    name: str                              # 显示名称
    path: str                              # .exe 路径 或 UWP AUMID
    arguments: str = ""                    # 启动参数（仅 .lnk 数据源）
    working_dir: str = ""                  # 工作目录（仅 .lnk 数据源）
    description: str = ""                  # 分类目录名
    is_system_tool: bool = False           # 是否为系统工具
    is_uwp: bool = False                   # 是否为 UWP / 商店应用

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path,
            "arguments": self.arguments, "working_dir": self.working_dir,
            "description": self.description,
            "is_system_tool": self.is_system_tool, "is_uwp": self.is_uwp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppInfo":
        return cls(
            name=d.get("name", ""), path=d.get("path", ""),
            arguments=d.get("arguments", ""), working_dir=d.get("working_dir", ""),
            description=d.get("description", ""),
            is_system_tool=d.get("is_system_tool", False),
            is_uwp=d.get("is_uwp", False),
        )


# ── 过滤词 ────────────────────────────────────────────────────────
_EXCLUDE_PATTERNS = [
    "unins", "uninst", "uninstall", "unwise",
    "help", "readme", "license", "changelog", "whatsnew",
    "what's new", "website", "url", "homepage",
    "configure", "config ", "settings", "repair",
    "diagnose", "diagnostic", "check for updates",
    "update ", "updater", "register", "registration",
    "order", "purchase", "safe mode", "eula", "faq",
    "documentation", "support center", "release notes",
]


# ── 系统工具判定 ───────────────────────────────────────────────────

_SYSTEM_TOOL_NAMES = {
    "about windows", "administrative tools", "application verifier",
    "azure", "backup and restore", "bitlocker",
    "calculator", "calendar", "certificate", "character map",
    "cleanmgr", "command prompt", "cmd", "component services",
    "computer management", "control panel", "credential manager",
    "default apps", "default programs", "device manager",
    "device pair", "disk cleanup", "disk defragmenter",
    "disk management", "display", "dxdiag", "ease of access",
    "event viewer", "file explorer", "file history", "fonts",
    "free up disk space", "getting started",
    "hyper-v", "iis", "indexing options", "internet explorer",
    "iscsi", "isoburn", "journal", "keyboard", "language options",
    "magnify", "mail", "malicious software removal",
    "map network", "math input panel", "memory diagnostic",
    "microsoft edge", "mrt", "mstsc", "narrator",
    "network", "notepad", "notification", "odbc",
    "office language", "onedrive", "onenote", "osk",
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
    "system monitor", "system settings", "tablet",
    "task manager", "task scheduler", "taskbar",
    "telnet", "terminal", "tpm", "troubleshoot",
    "user accounts", "view local services", "virus",
    "visio", "volume mixer", "wordpad", "xbox", "xps",
}

_SYSTEM_DIRS = {"system32", "syswow64", "\\windows\\", "c:\\windows\\", "\\windowsapps\\"}


def _is_system_tool(name: str, target: str, is_uwp: bool = False) -> bool:
    lower_name = name.lower()
    lower_path = (target or "").lower()

    for d in _SYSTEM_DIRS:
        if d in lower_path:
            return True
    if lower_name in _SYSTEM_TOOL_NAMES:
        return True
    for sys_name in _SYSTEM_TOOL_NAMES:
        if len(sys_name) > 5 and sys_name in lower_name:
            return True
    if is_uwp and lower_path.startswith("microsoft."):
        return True
    return False


# ── 过滤 ──────────────────────────────────────────────────────────

def _is_bad_exe(path: str) -> bool:
    """检查 .exe 路径是否为应排除的类型"""
    if not path:
        return True
    lower = os.path.basename(path).lower()
    if not lower.endswith(".exe"):
        return True
    for p in _EXCLUDE_PATTERNS:
        if p in lower:
            return True
    return False


def _is_fake_app(name: str, app_id: str) -> bool:
    """过滤 URL、帮助链接、控制面板等非真实应用"""
    lower_name = name.lower()
    lower_id = app_id.lower()

    if app_id.startswith("http://") or app_id.startswith("https://"):
        return True
    for kw in ["eula", "faq", "documentation", "support center",
               "release notes", "changelog", "license", "readme", "website"]:
        if kw in lower_name:
            return True
    if ".msc}" in lower_id or ".cpl}" in lower_id:
        return True
    if app_id.startswith("::{") and app_id.endswith("}"):
        return True
    return False


def _looks_like_uwp(app_id: str) -> bool:
    """判断 AppID 是否为 UWP AUMID（而非 .exe 路径）"""
    if not app_id:
        return False
    if os.path.isabs(app_id) and os.path.exists(app_id):
        return False
    if app_id.lower().endswith(".exe"):
        return False
    # AUMID 特征：包含 ! 或看起来像包名
    return ("!" in app_id) or ("_" in app_id and not os.path.exists(app_id))


# ── 数据源 1：Start Menu .lnk ─────────────────────────────────────

def _get_start_menu_dirs() -> list[str]:
    dirs = []
    for env_var in ["PROGRAMDATA", "APPDATA"]:
        base = os.environ.get(env_var, "")
        sm = os.path.join(base, "Microsoft", "Windows", "Start Menu")
        if os.path.isdir(sm):
            dirs.append(sm)
    return dirs


def _scan_lnk_files(progress_callback=None) -> list[AppInfo]:
    """遍历 Start Menu 解析 .lnk 文件"""
    if not _HAS_WIN32:
        return []

    _com_ok = False
    try:
        pythoncom.CoInitialize()
        _com_ok = True
    except Exception:
        pass

    try:
        shell = Dispatch("WScript.Shell")
        apps: dict[str, AppInfo] = {}
        all_lnks: list[tuple[str, str]] = []

        for sm_dir in _get_start_menu_dirs():
            for root, dirs, files in os.walk(sm_dir):
                cat = os.path.relpath(root, sm_dir)
                if cat == ".":
                    cat = ""
                for f in files:
                    if f.lower().endswith(".lnk"):
                        all_lnks.append((os.path.join(root, f), cat))

        total = len(all_lnks)
        for idx, (lnk_path, category) in enumerate(all_lnks):
            if progress_callback:
                progress_callback(idx + 1, total)

            try:
                sc = shell.CreateShortcut(lnk_path)
                target = os.path.expandvars(sc.TargetPath or "")
                if not target or _is_bad_exe(target):
                    continue
                if not os.path.isfile(target):
                    continue

                name = os.path.splitext(os.path.basename(lnk_path))[0]
                key = (name.lower(), target.lower())
                if key not in apps:
                    apps[key] = AppInfo(
                        name=name, path=target,
                        arguments=sc.Arguments or "",
                        working_dir=sc.WorkingDirectory or "",
                        description=category,
                        is_system_tool=_is_system_tool(name, target),
                        is_uwp=False,
                    )
            except Exception:
                continue

        return list(apps.values())

    finally:
        if _com_ok:
            pythoncom.CoUninitialize()


# ── 数据源 2：Shell.AppsFolder COM ────────────────────────────────

def _scan_shell_apps() -> list[tuple[str, str]]:
    """
    枚举 shell:AppsFolder，获取所有已安装应用。
    返回 [(名称, AppID), ...]，纯原生 Unicode。
    """
    if not _HAS_WIN32:
        return []

    _com_ok = False
    try:
        pythoncom.CoInitialize()
        _com_ok = True
    except Exception:
        pass

    try:
        sh = Dispatch("Shell.Application")
        folder = sh.NameSpace("shell:AppsFolder")
        if folder is None:
            # fallback: CLSID
            folder = sh.NameSpace("::{1E87508D-53C1-48DD-BD26-B9CFD9A0E082}")
        if folder is None:
            return []

        results: list[tuple[str, str]] = []
        for item in folder.Items():
            try:
                name = item.Name
                # 优先取 AUMID，取不到用 Path
                app_id = ""
                try:
                    app_id = item.ExtendedProperty("System.AppUserModel.ID")
                except Exception:
                    pass
                if not app_id:
                    try:
                        app_id = item.Path
                    except Exception:
                        pass
                if name and app_id:
                    results.append((name, app_id))
            except Exception:
                continue
        return results

    finally:
        if _com_ok:
            pythoncom.CoUninitialize()


# ── 合并 & 主入口 ─────────────────────────────────────────────────

def scan_apps(progress_callback=None) -> list[AppInfo]:
    """双源扫描并合并去重"""

    # 1. .lnk 扫描（丰富元数据）
    lnk_apps = _scan_lnk_files(progress_callback)
    lnk_index: dict[tuple, AppInfo] = {}
    for a in lnk_apps:
        lnk_index[(a.name.lower(), a.path.lower())] = a

    # 2. Shell.AppsFolder 扫描（全量 + UWP）
    shell_apps = _scan_shell_apps()

    # 3. 合并
    merged: dict[str, AppInfo] = {}
    # 先用 .lnk 数据
    for a in lnk_apps:
        merged[(a.name.lower(), a.path.lower())] = a

    seen_names: set = {a.name.lower() for a in lnk_apps}

    for name, app_id in shell_apps:
        if _is_fake_app(name, app_id):
            continue
        lower_name = name.lower()

        is_uwp = _looks_like_uwp(app_id)

        if not is_uwp:
            # 传统应用：检查 .exe 合法性
            if _is_bad_exe(app_id):
                continue
            key = (lower_name, app_id.lower())
            if key in lnk_index:
                continue  # .lnk 已有，跳过
            if lower_name in seen_names:
                continue
            seen_names.add(lower_name)
            merged[(lower_name, app_id.lower())] = AppInfo(
                name=name, path=app_id,
                is_system_tool=_is_system_tool(name, app_id),
                is_uwp=False,
            )
        else:
            # UWP 应用
            key = (lower_name, app_id.lower())
            if key in merged:
                continue
            if lower_name in seen_names:
                continue
            seen_names.add(lower_name)
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
    except Exception:
        pass
    return []


def _update_cache(config_path: str, apps: list[AppInfo]):
    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["apps_cache"] = [a.to_dict() for a in apps]
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

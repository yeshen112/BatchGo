"""
应用扫描模块 —— 遍历 Start Menu 目录解析 .lnk 快捷方式，
生成已安装应用列表。
"""
import os
import sys
import json
from typing import Optional
from dataclasses import dataclass, field

# 尝试导入 pywin32，不可用时给出明确提示
try:
    import pythoncom
    import win32com.client
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


@dataclass
class AppInfo:
    """单个应用的信息"""
    name: str                              # 显示名称（取自 .lnk 文件名）
    path: str                              # 目标可执行文件路径
    arguments: str = ""                    # 快捷方式自带参数
    working_dir: str = ""                  # 工作目录
    description: str = ""                  # 描述（.lnk 文件所在子目录名，如 "办公"）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "arguments": self.arguments,
            "working_dir": self.working_dir,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppInfo":
        return cls(
            name=d.get("name", ""),
            path=d.get("path", ""),
            arguments=d.get("arguments", ""),
            working_dir=d.get("working_dir", ""),
            description=d.get("description", ""),
        )


# ── 需要过滤掉的模式 ──────────────────────────────────────────────
_EXCLUDE_PATTERNS = [
    "unins", "uninst", "uninstall", "unwise",       # 卸载程序
    "help", "readme", "license",                     # 帮助/文档
    "changelog", "whatsnew", "what's new",
    "website", "url", "homepage",
    "configure", "config ", "settings",
    "repair", "diagnose", "diagnostic",
    "check for updates", "update ", "updater",
    "register", "registration",
    "order", "purchase",
    "safe mode",
    ".chm", ".hlp", ".pdf", ".html", ".htm", ".url",  # 非可执行文件
]


def _is_valid_app(name: str, target: str) -> bool:
    """过滤掉卸载程序、帮助文档等非应用条目"""
    lower_name = name.lower()
    lower_target = os.path.basename(target).lower()

    for pattern in _EXCLUDE_PATTERNS:
        if pattern in lower_name or pattern in lower_target:
            return False

    # 必须是 .exe 文件
    if not lower_target.endswith(".exe"):
        return False

    # 目标文件必须存在
    if not os.path.isfile(target):
        return False

    return True


def _get_start_menu_dirs() -> list[str]:
    """获取所有需要扫描的 Start Menu 目录"""
    dirs = []

    # 公共 Start Menu
    common = os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu")
    if os.path.isdir(common):
        dirs.append(common)

    # 当前用户 Start Menu
    user = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu")
    if os.path.isdir(user):
        dirs.append(user)

    return dirs


def _resolve_target(path: str) -> Optional[str]:
    """
    尝试解析目标路径中的环境变量和相对路径。
    某些 .lnk 的目标可能包含 %ProgramFiles% 等变量。
    """
    if not path:
        return None
    # 展开环境变量
    expanded = os.path.expandvars(path)
    expanded = os.path.expanduser(expanded)
    return expanded


def scan_apps(progress_callback=None) -> list[AppInfo]:
    """
    扫描 Start Menu 中的所有 .lnk 快捷方式，返回 AppInfo 列表。

    Args:
        progress_callback: 可选，每处理一个 .lnk 调用一次 callback(current, total)

    Returns:
        已去重排序的 AppInfo 列表
    """
    if not _HAS_WIN32:
        print("[Scanner] pywin32 未安装，无法解析 .lnk 文件。请执行: pip install pywin32")
        return []

    # 初始化 COM（线程安全处理）
    _com_initialized = False
    try:
        pythoncom.CoInitialize()
        _com_initialized = True
    except Exception:
        # COM 可能已被其他方式初始化，继续尝试
        pass

    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        start_menu_dirs = _get_start_menu_dirs()
        apps: dict[str, AppInfo] = {}  # key = (name.lower(), target.lower()) 用于去重

        # 先收集所有 .lnk 文件
        all_lnk_files: list[tuple[str, str]] = []  # (lnk_path, category)
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

            shortcut = None
            try:
                shortcut = shell.CreateShortcut(lnk_path)
                target = _resolve_target(shortcut.TargetPath)

                if not target:
                    continue

                # 应用名 = .lnk 文件名（去掉扩展名）
                name = os.path.splitext(os.path.basename(lnk_path))[0]

                if not _is_valid_app(name, target):
                    continue

                # 去重 key
                dedup_key = (name.lower(), target.lower())

                if dedup_key not in apps:
                    apps[dedup_key] = AppInfo(
                        name=name,
                        path=target,
                        arguments=shortcut.Arguments or "",
                        working_dir=shortcut.WorkingDirectory or "",
                        description=category,
                    )
            except Exception:
                # 个别 .lnk 解析失败不影响整体
                continue
            finally:
                # 释放 COM 对象避免泄漏
                if shortcut is not None:
                    try:
                        del shortcut
                    except Exception:
                        pass

        # 按名称排序
        result = sorted(apps.values(), key=lambda a: a.name.lower())
        return result

    finally:
        # 释放 shell
        try:
            del shell
        except Exception:
            pass
        if _com_initialized:
            pythoncom.CoUninitialize()


def scan_and_cache(config_path: str, progress_callback=None) -> list[AppInfo]:
    """
    扫描应用并缓存到配置文件。
    如果配置文件已有缓存，直接读取；否则扫描后写入。
    """
    apps = scan_apps(progress_callback)
    if apps:
        _update_cache(config_path, apps)
    return apps


def load_cached_apps(config_path: str) -> list[AppInfo]:
    """从配置文件加载缓存的应用列表"""
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
    """更新配置文件中的 apps_cache 字段，保留其他字段不变"""
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

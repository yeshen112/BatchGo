"""
启动引擎 —— 按分组批量启动应用程序。
支持浏览器 + URL、独立窗口启动。
子进程完全脱离父进程，关闭终端不影响已启动的应用。
支持以管理员身份运行（UAC 提权）。
"""
import os
import sys
import subprocess
import webbrowser
import traceback
import ctypes

from config_manager import AppGroup, AppEntry

# 模块级 logger
import logging
_log = logging.getLogger("BatchGo")


# ── 管理员提权启动 ──────────────────────────────────────────────────

def _run_as_admin(exe_path: str, args: str = "", cwd: str = "") -> bool:
    """通过 ShellExecute runas 以管理员身份启动，触发 UAC 弹窗"""
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,                # hwnd
            "runas",             # 提权动词
            exe_path,            # 可执行文件
            args or None,        # 参数
            cwd or None,         # 工作目录
            1,                   # SW_SHOWNORMAL
        )
        # ShellExecute 返回值 > 32 表示成功
        return ret > 32
    except Exception:
        _log.error(f"ShellExecute runas failed:\n{traceback.format_exc()}")
        return False


# ── 已知浏览器可执行文件名（小写） ────────────────────────────────
_BROWSER_NAMES = {
    "chrome.exe", "firefox.exe", "msedge.exe", "iexplore.exe",
    "opera.exe", "brave.exe", "vivaldi.exe", "chromium.exe",
    "safari.exe", "360chrome.exe", "360se.exe", "sogouexplorer.exe",
    "maxthon.exe", "qqbrowser.exe", "theworld.exe", "seamonkey.exe",
    "waterfox.exe", "palemoon.exe", "tor.exe",
}


def is_browser(path: str) -> bool:
    """判断可执行文件是否为浏览器"""
    if not path:
        return False
    basename = os.path.basename(path).lower()
    return basename in _BROWSER_NAMES


def launch_app(entry: AppEntry) -> bool:
    """
    启动单个应用。子进程完全脱离父进程。

    规则：
    - 文件夹/文件 → 系统关联程序打开（os.startfile）
    - UWP 应用 → 通过 shell:AppsFolder 启动
    - 仅有 URL → 默认浏览器打开
    - 有 path → Popen 启动（独立进程组）

    Returns:
        True 表示启动成功，False 表示失败
    """
    path = entry.path
    url = entry.url.strip() if entry.url else ""
    args_str = entry.arguments.strip() if entry.arguments else ""
    working_dir = entry.working_dir if entry.working_dir else None
    is_uwp = getattr(entry, "is_uwp", False)

    try:
        # 情况 0：文件夹/文件 → 系统关联程序打开
        if getattr(entry, "is_folder", False) or getattr(entry, "is_file", False):
            if path and os.path.exists(path):
                os.startfile(path)
                if url:
                    webbrowser.open(url)
                return True
            return False

        # 情况 1：UWP 应用 → 通过 shell:AppsFolder 启动
        if is_uwp and path:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{path}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                return True
            return False

        # 情况 2：仅有 URL → 默认浏览器打开
        if not path and url:
            webbrowser.open(url)
            return True

        if not path:
            return False

        # 情况 3：传统应用
        cmd = [path]
        if url:
            cmd.append(url)
        if args_str:
            cmd.extend(args_str.split())

        cwd = working_dir if working_dir and os.path.isdir(working_dir) else None

        # 管理员身份运行（UAC 提权）
        if getattr(entry, "run_as_admin", False) and sys.platform == "win32":
            return _run_as_admin(path, args_str, cwd or "")

        if sys.platform == "win32":
            subprocess.Popen(
                cmd, cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                ),
            )
        else:
            subprocess.Popen(
                cmd, cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

        return True

    except FileNotFoundError:
        _log.warning(f"File not found: {path}\n{traceback.format_exc()}")
        if sys.platform == "win32" and path and os.path.exists(path):
            try:
                os.startfile(path)
                if url:
                    webbrowser.open(url)
                return True
            except Exception:
                pass
        return False
    except Exception:
        _log.error(f"Launch failed [{entry.name} / {path}]:\n{traceback.format_exc()}")
        # 兜底：用 os.startfile 尝试绕过权限等问题
        if sys.platform == "win32" and path and os.path.exists(path):
            try:
                os.startfile(path)
                if url:
                    webbrowser.open(url)
                return True
            except Exception:
                pass
        return False


def launch_group(group: AppGroup) -> tuple[int, int]:
    """
    启动一个分组中的所有应用。

    Returns:
        (成功数, 失败数)
    """
    success = 0
    failed = 0
    for entry in group.entries:
        if launch_app(entry):
            success += 1
        else:
            failed += 1
    return success, failed

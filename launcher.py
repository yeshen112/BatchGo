"""
启动引擎 —— 按分组批量启动应用程序。
支持浏览器 + URL、独立窗口启动。
"""
import os
import sys
import subprocess
import webbrowser

from config_manager import AppGroup, AppEntry


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
    启动单个应用。

    规则：
    - 如果 path 为空但 url 不为空 → 用默认浏览器打开 URL
    - 如果 path 为浏览器且 url 不为空 → 启动浏览器并打开 URL
    - 否则 → 直接启动应用

    Returns:
        True 表示启动成功，False 表示失败
    """
    path = entry.path
    url = entry.url.strip() if entry.url else ""
    args_str = entry.arguments.strip() if entry.arguments else ""
    working_dir = entry.working_dir if entry.working_dir else None

    try:
        # 情况 1：仅有 URL，无应用路径
        if not path and url:
            webbrowser.open(url)
            return True

        # 情况 2：无有效路径
        if not path:
            return False

        # 构建命令行参数
        cmd = [path]

        # URL 作为参数
        if url:
            cmd.append(url)

        # 额外的启动参数（简单按空格分割）
        if args_str:
            cmd.extend(args_str.split())

        # 工作目录
        cwd = working_dir if working_dir and os.path.isdir(working_dir) else None

        # Windows 独立进程启动（不弹出命令行窗口）
        if sys.platform == "win32":
            subprocess.Popen(
                cmd,
                cwd=cwd,
                creationflags=subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                cmd,
                cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        return True

    except FileNotFoundError:
        # 文件不存在，尝试用 os.startfile（仅 Windows）
        if sys.platform == "win32" and os.path.exists(path):
            try:
                os.startfile(path)
                if url:
                    webbrowser.open(url)
                return True
            except Exception:
                pass
        return False
    except Exception:
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

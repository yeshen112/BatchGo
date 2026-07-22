"""
BatchGo — 批量应用启动工具
系统托盘驻留，左键选择分组一键启动，右键打开配置面板。
全局热键 Ctrl+Alt+B 呼出分组菜单。
"""
import os
import sys
import json
import traceback
import logging
import webbrowser
import urllib.request
import ctypes
from ctypes import wintypes
from datetime import datetime
from functools import wraps

from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox,
)
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction, QCursor,
)
from PySide6.QtCore import (Qt, QSharedMemory, QTimer, QThread, Signal,
                            QPoint, QAbstractNativeEventFilter)

from scanner import scan_and_cache, load_cached_apps
from config_manager import ConfigManager, AppGroup
from launcher import launch_group


# ── 日志 ──────────────────────────────────────────────────────────

def _setup_logging():
    """配置日志：开发→项目目录/logs，打包→%APPDATA%/BatchGo/logs，按日期归档"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BatchGo")
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(log_dir, f"batchgo_{today}.log")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    return logging.getLogger("BatchGo")


_log = _setup_logging()


def _log_errors(func):
    """装饰器：捕获函数内的异常并写入日志，避免闪退"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            _log.error(f"CRASH in {func.__name__}:\n{traceback.format_exc()}")
    return wrapper


# 捕获 Qt 内部的警告消息
def _qt_message_handler(mode, context, message):
    _log.warning(f"Qt: {message}")


# 全局未捕获异常兜底
def _global_exception_handler(exc_type, exc_value, exc_tb):
    _log.critical(
        f"UNHANDLED EXCEPTION:\n"
        f"{''.join(traceback.format_exception(exc_type, exc_value, exc_tb))}"
    )


sys.excepthook = _global_exception_handler


# ── 常量 ──────────────────────────────────────────────────────────

APP_NAME = "BatchGo"
APP_VERSION = "1.3.0"
REPO_URL = "https://github.com/yeshen112/BatchGo"


# ── 后台扫描线程 ──────────────────────────────────────────────────

class ScanThread(QThread):
    """后台扫描，不阻塞托盘 UI"""
    finished = Signal(list)

    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path

    def run(self):
        try:
            apps = scan_and_cache(self.config_path)
            self.finished.emit(apps)
        except Exception:
            _log.error(f"ScanThread crash:\n{traceback.format_exc()}")
            self.finished.emit([])


# ── 图标生成 ──────────────────────────────────────────────────────

def create_tray_icon() -> QIcon:
    """用 QPainter 绘制托盘图标：蓝色圆角方形 + 白色 'B' """
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.setBrush(QColor("#2563EB"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, size - 8, size - 8, 14, 14)

    font = QFont("Segoe UI", 36, QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "B")

    painter.end()
    return QIcon(pixmap)


# ── 开机自启管理 ──────────────────────────────────────────────────

def enable_auto_start():
    """通过注册表 Run 键启用开机自启（比 VBS 可靠，无路径编码问题）"""
    import winreg
    if getattr(sys, 'frozen', False):
        cmd = sys.executable
    else:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        cmd = f'"{pythonw}" "{main_py}"'
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "BatchGo", 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        _log.info("Auto-start enabled")
        # 清理旧的 VBS 遗留
        _cleanup_old_vbs()
    except Exception:
        _log.error(f"Failed to set auto-start: {traceback.format_exc()}")


def disable_auto_start():
    """从注册表 Run 键移除开机自启"""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, "BatchGo")
        winreg.CloseKey(key)
        _log.info("Auto-start disabled")
    except FileNotFoundError:
        pass
    except Exception:
        _log.error(f"Failed to disable auto-start: {traceback.format_exc()}")
    _cleanup_old_vbs()


def is_auto_start_enabled() -> bool:
    """检查注册表 Run 键中是否存在 BatchGo"""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        winreg.QueryValueEx(key, "BatchGo")
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _cleanup_old_vbs():
    """删除旧版 VBS 自启文件（迁移到注册表后清理遗留）"""
    vbs_path = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        "BatchGo.vbs",
    )
    if os.path.exists(vbs_path):
        try:
            os.remove(vbs_path)
            _log.info("Cleaned up old VBS file")
        except OSError:
            pass


# ── 单实例控制 ────────────────────────────────────────────────────

def ensure_single_instance() -> QSharedMemory:
    shared_mem = QSharedMemory("BatchGo_SingleInstance_Key")
    if shared_mem.attach():
        return None
    if not shared_mem.create(1):
        return None
    return shared_mem


# ── 全局热键 ──────────────────────────────────────────────────────

MOD_CONTROL = 0x0002
MOD_ALT     = 0x0001
MOD_SHIFT   = 0x0004
MOD_WIN     = 0x0008
WM_HOTKEY   = 0x0312
HOTKEY_ID   = 1

# 键名 → Win32 VK 映射
_KEY_NAME_TO_VK = {}
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _KEY_NAME_TO_VK[_ch] = ord(_ch)
for _i in range(10):
    _KEY_NAME_TO_VK[str(_i)] = 0x30 + _i
for _i in range(1, 13):
    _KEY_NAME_TO_VK[f"F{_i}"] = 0x6F + _i
_KEY_NAME_TO_VK.update({
    "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Esc": 0x1B, "Escape": 0x1B,
    "Backspace": 0x08, "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24,
    "End": 0x23, "PgUp": 0x21, "PgDown": 0x22, "Left": 0x25,
    "Right": 0x27, "Up": 0x26, "Down": 0x28, "Pause": 0x13,
    "Print": 0x2C, "Scroll": 0x91, "NumLock": 0x90,
    "OEM_3": 0xC0,   # ~ ` 键
    "OEM_MINUS": 0xBD, "OEM_PLUS": 0xBB, "OEM_COMMA": 0xBC,
    "OEM_PERIOD": 0xBE, "OEM_1": 0xBA, "OEM_2": 0xBF,
    "OEM_4": 0xDB, "OEM_5": 0xDC, "OEM_6": 0xDD, "OEM_7": 0xDE,
})
# 补充：Qt 用字母键名区分修饰侧，都映射到同一 VK
for _side, _ch in [("L", "Ctrl"), ("R", "Ctrl"), ("L", "Alt"), ("R", "Alt"),
                   ("L", "Shift"), ("R", "Shift"), ("L", "Win"), ("R", "Win")]:
    pass  # Qt KeySequence 表示修饰键，不需要 VK

_MOD_NAME_TO_FLAG = {
    "Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Meta": MOD_WIN,
}

DEFAULT_HOTKEY = "Ctrl+Alt+E"


def _parse_hotkey_str(hotkey_str: str) -> tuple[int, int]:
    """解析 'Ctrl+Alt+E' → (mods, vk)，失败返回默认值"""
    parts = [p.strip() for p in hotkey_str.split("+")]
    mods = 0
    vk = None
    for p in parts:
        flag = _MOD_NAME_TO_FLAG.get(p)
        if flag:
            mods |= flag
        else:
            vk = _KEY_NAME_TO_VK.get(p.upper())
    if vk is None:
        # 回退到默认值
        return MOD_CONTROL | MOD_ALT, 0x45
    return mods, vk

class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    """全局热键监听，支持运行时修改热键组合"""
    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._registered = False
        self._mod = 0
        self._vk = 0

    def update_hotkey(self, hwnd: int, hotkey_str: str) -> bool:
        """更换热键（先注销旧的再注册新的）"""
        self.unregister(hwnd)
        self._mod, self._vk = _parse_hotkey_str(hotkey_str)
        return self._do_register(hwnd)

    def register(self, hwnd: int, hotkey_str: str):
        self._mod, self._vk = _parse_hotkey_str(hotkey_str)
        self._do_register(hwnd)

    def _do_register(self, hwnd: int) -> bool:
        if self._registered:
            return True
        if not self._mod or not self._vk:
            return False
        ok = ctypes.windll.user32.RegisterHotKey(
            wintypes.HWND(hwnd),
            HOTKEY_ID,
            self._mod,
            self._vk,
        )
        self._registered = bool(ok)
        if not self._registered:
            _log.warning(f"RegisterHotKey failed (maybe already taken): "
                         f"error={ctypes.get_last_error()}")
        else:
            _log.info(f"Hotkey registered: mod={self._mod:#x} vk={self._vk:#x}")
        return self._registered

    def unregister(self, hwnd: int):
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(
                wintypes.HWND(hwnd), HOTKEY_ID,
            )
            self._registered = False

    def nativeEventFilter(self, event_type, message):
        msg = ctypes.c_void_p(int(message))
        m = ctypes.cast(msg, ctypes.POINTER(MSG)).contents
        if m.message == WM_HOTKEY and m.wParam == HOTKEY_ID:
            self._callback()
            return True, 0
        return False, 0


# MSG struct for native event filter
class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",    wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam",  wintypes.WPARAM),
        ("lParam",  wintypes.LPARAM),
        ("time",    wintypes.DWORD),
        ("pt_x",    wintypes.LONG),
        ("pt_y",    wintypes.LONG),
    ]


# ── 主应用 ────────────────────────────────────────────────────────

class BatchGoApp:
    """系统托盘应用主类"""

    def __init__(self):
        _log.info(f"BatchGo v{APP_VERSION} starting...")
        self._groups_menu = None  # 保持引用防止 GC

        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)

        # 配置管理器
        self.config = ConfigManager()
        self.config.load()
        groups = self.config.get_groups()
        _log.info(f"Config loaded: {len(groups)} groups, "
                  f"{len(self.config.get_cached_apps())} cached apps")

        # 开机自启默认开启：首次运行或配置为 True 时确保 VBS 文件存在
        if self.config.get_auto_start() and not is_auto_start_enabled():
            enable_auto_start()

        # 托盘图标
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(create_tray_icon())
        self.tray.setToolTip(f"{APP_NAME} - 批量启动工具")

        # 构建右键菜单
        self._rebuild_context_menu()

        # 托盘事件
        self.tray.activated.connect(self._on_tray_activated)

        # 显示托盘
        self.tray.show()
        _log.info("Tray icon shown")

        # 全局热键
        self._hotkey = GlobalHotkeyFilter(self._on_hotkey)
        self.app.installNativeEventFilter(self._hotkey)
        # 注册热键需要窗口句柄，放在事件循环开始后
        self._hotkey_str = self.config.get_hotkey()
        QTimer.singleShot(100, self._register_hotkey)

        # 预热 Qt 菜单渲染，消除首次点击托盘时的卡顿
        warmup = self._build_groups_menu()
        warmup.adjustSize()
        warmup.deleteLater()

        # 首次启动：如果没有缓存，自动扫描
        cached = self.config.get_cached_apps()
        if not cached:
            _log.info("No app cache, starting first scan...")
            QTimer.singleShot(500, self._first_scan)

    # ── 首次扫描 ──────────────────────────────────────────────

    def _first_scan(self):
        """首次启动时后台扫描应用"""
        _log.info("First scan started")
        self._safe_tray_msg("正在扫描已安装应用，请稍候...", QSystemTrayIcon.Information, 2000)
        self._scan_thread = ScanThread(self.config.config_path)
        self._scan_thread.finished.connect(self._on_first_scan_finished)
        self._scan_thread.start()

    def _on_first_scan_finished(self, apps):
        """首次扫描完成"""
        self.config.load()
        _log.info(f"First scan done: {len(apps)} apps")
        self._safe_tray_msg(
            f"扫描完成！发现 {len(apps)} 个应用",
            QSystemTrayIcon.Information, 3000,
        )

    # ── 安全托盘消息 ──────────────────────────────────────────

    def _safe_tray_msg(self, text: str, icon=QSystemTrayIcon.Information, duration=3000):
        """托盘消息（包装 try/except 防止 showMessage 崩溃）"""
        try:
            self.tray.showMessage(APP_NAME, text, icon, duration)
        except Exception:
            _log.error(f"showMessage failed:\n{traceback.format_exc()}")

    # ── 构建菜单 ──────────────────────────────────────────────

    def _rebuild_context_menu(self):
        """重构右键菜单"""
        menu = QMenu()

        action_config = QAction("⚙ 配置面板", menu)
        action_config.triggered.connect(lambda: self._safe_call(self._open_config))
        menu.addAction(action_config)

        menu.addSeparator()

        auto_start = is_auto_start_enabled()
        action_autostart = QAction(f"{'☑' if auto_start else '☐'} 开机自启", menu)
        action_autostart.triggered.connect(lambda: self._safe_call(self._toggle_auto_start))
        menu.addAction(action_autostart)

        menu.addSeparator()

        action_about = QAction("关于 BatchGo", menu)
        action_about.triggered.connect(lambda: self._safe_call(self._show_about))
        menu.addAction(action_about)

        # 查看日志
        action_log = QAction("📋 查看日志", menu)
        action_log.triggered.connect(lambda: self._safe_call(self._open_log))
        menu.addAction(action_log)

        action_exit = QAction("❌ 退出", menu)
        action_exit.triggered.connect(self._quit)
        menu.addAction(action_exit)

        self._context_menu = menu

    def _build_groups_menu(self) -> QMenu:
        """构建左键分组菜单"""
        menu = QMenu()
        groups = self.config.get_groups()

        if not groups:
            action_empty = QAction("（暂无组合，请右键 → 配置面板）", menu)
            action_empty.setEnabled(False)
            menu.addAction(action_empty)
            menu.addSeparator()
            action_cfg = QAction("⚙ 打开配置面板...", menu)
            action_cfg.triggered.connect(lambda: self._safe_call(self._open_config))
            menu.addAction(action_cfg)
        else:
            for group in groups:
                count = len(group.entries)
                action = QAction(f"▶ {group.name}  ({count}个应用)", menu)
                # 用默认参数捕获当前 group 值
                action.triggered.connect(
                    lambda checked=False, g=group: self._safe_call(self._launch_group, g)
                )
                menu.addAction(action)

            menu.addSeparator()
            action_cfg = QAction("⚙ 配置面板...", menu)
            action_cfg.triggered.connect(lambda: self._safe_call(self._open_config))
            menu.addAction(action_cfg)

        return menu

    # ── 安全调用包装 ──────────────────────────────────────────

    def _safe_call(self, func, *args, **kwargs):
        """统一的安全调用包装，异常写入日志"""
        try:
            return func(*args, **kwargs)
        except Exception:
            _log.error(f"SAFE_CALL {func.__name__} crashed:\n{traceback.format_exc()}")

    # ── 托盘事件 ──────────────────────────────────────────────

    @_log_errors
    def _on_tray_activated(self, reason):
        """托盘图标被点击"""
        _log.debug(f"Tray activated: reason={reason}")
        if reason == QSystemTrayIcon.Context:
            self._show_context_menu()
        elif reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick,
                        QSystemTrayIcon.MiddleClick):
            self._show_groups_menu()

    @staticmethod
    def _menu_pos(menu: QMenu) -> QPoint:
        """计算菜单位置：鼠标右上角"""
        pos = QCursor.pos()
        h = menu.sizeHint().height()
        pos.setY(max(0, pos.y() - h))
        return pos

    def _show_context_menu(self):
        """在鼠标右上角显示右键菜单"""
        _log.debug("Showing context menu")
        try:
            menu = self._context_menu
            if menu is None:
                return
            menu.exec(self._menu_pos(menu))
        except Exception:
            _log.error(f"Show context menu failed:\n{traceback.format_exc()}")

    def _show_groups_menu(self):
        """在鼠标右上角显示分组选择菜单"""
        _log.debug("Showing groups menu")
        try:
            menu = self._build_groups_menu()
            self._groups_menu = menu  # 保持引用
            menu.exec(self._menu_pos(menu))
        except Exception:
            _log.error(f"Show groups menu failed:\n{traceback.format_exc()}")
        finally:
            self._groups_menu = None

    # ── 操作 ──────────────────────────────────────────────────

    @_log_errors
    def _launch_group(self, group: AppGroup):
        """启动一个分组中的所有应用"""
        _log.info(f"Launching group '{group.name}' ({len(group.entries)} apps)")
        for entry in group.entries:
            _log.debug(f"  -> {entry.name} | {entry.path} | url={entry.url}")
        success, failed = launch_group(group)
        _log.info(f"Group '{group.name}' done: {success} ok, {failed} fail")
        if failed == 0:
            self._safe_tray_msg(
                f"「{group.name}」全部启动成功！({success}个应用)",
                QSystemTrayIcon.Information, 3000,
            )
        else:
            self._safe_tray_msg(
                f"「{group.name}」启动完成：成功 {success} 个，失败 {failed} 个",
                QSystemTrayIcon.Warning, 3000,
            )

    @_log_errors
    def _open_config(self):
        """打开配置面板"""
        _log.info("Opening config dialog")
        from config_dialog import ConfigDialog
        dialog = ConfigDialog(self.config, parent=None)
        if dialog.exec():
            self.config.load()
            self._rebuild_context_menu()
            self._hotkey_str = self.config.get_hotkey()
            try:
                hwnd = int(self.app.effectiveWinId())
            except Exception:
                hwnd = 0
            self._hotkey.update_hotkey(hwnd, self._hotkey_str)
            _log.info("Config dialog closed with changes")

    def _refresh_apps(self):
        """手动刷新应用列表（后台扫描）"""
        _log.info("Manual refresh started")
        self._safe_tray_msg("正在后台扫描应用...", QSystemTrayIcon.Information, 2000)
        self._scan_thread = ScanThread(self.config.config_path)
        self._scan_thread.finished.connect(self._on_refresh_finished)
        self._scan_thread.start()

    def _on_refresh_finished(self, apps):
        """扫描完成回调"""
        self.config.load()
        common = sum(1 for a in apps if not a.is_system_tool)
        sys_tools = sum(1 for a in apps if a.is_system_tool)
        _log.info(f"Refresh done: {len(apps)} apps (common={common}, system={sys_tools})")
        self._safe_tray_msg(
            f"刷新完成！{len(apps)} 个应用（常用 {common}，系统工具 {sys_tools}）",
            QSystemTrayIcon.Information, 3000,
        )

    @_log_errors
    def _toggle_auto_start(self):
        """切换开机自启状态"""
        if is_auto_start_enabled():
            disable_auto_start()
            self.config.set_auto_start(False)
            self._safe_tray_msg("已关闭开机自启", QSystemTrayIcon.Information, 2000)
        else:
            enable_auto_start()
            self.config.set_auto_start(True)
            self._safe_tray_msg("已开启开机自启", QSystemTrayIcon.Information, 2000)
        self._rebuild_context_menu()

    @_log_errors
    def _show_about(self):
        """显示关于对话框"""
        msg = QMessageBox()
        msg.setWindowTitle(f"关于 {APP_NAME}")
        msg.setIcon(QMessageBox.Information)

        # 文字内容：可选中复制
        msg.setText(
            f"<h3>{APP_NAME} v{APP_VERSION}</h3>"
            f"<p>批量应用启动工具 — 一键启动自定义应用组合</p>"
            f"<p>"
            f"🔗 <a href='{REPO_URL}'>{REPO_URL}</a><br>"
            f"📧 1712274966@qq.com"
            f"</p>"
        )
        msg.setTextFormat(Qt.RichText)
        msg.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

        # 去掉默认按钮，自己加
        msg.setStandardButtons(QMessageBox.NoButton)

        # 检查更新按钮（无图标前缀）
        btn_update = msg.addButton("检查更新", QMessageBox.ActionRole)
        btn_update.clicked.connect(lambda: self._check_update(msg))

        btn_close = msg.addButton("关闭", QMessageBox.RejectRole)
        msg.setEscapeButton(btn_close)

        msg.exec()

    def _check_update(self, parent: QMessageBox | None = None):
        """从 GitHub Releases API 检查最新版本"""
        import re
        api_url = "https://api.github.com/repos/yeshen112/BatchGo/releases/latest"

        try:
            req = urllib.request.Request(api_url)
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("User-Agent", f"{APP_NAME}/{APP_VERSION}")
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            _log.warning(f"Update check failed: {e}")
            if parent:
                QMessageBox.warning(
                    parent, "检查更新",
                    f"无法获取更新信息。\n\n{_simple_error(e)}",
                )
            else:
                self._safe_tray_msg("检查更新失败，请检查网络", QSystemTrayIcon.Warning, 3000)
            return

        latest_tag = data.get("tag_name", "")
        latest_ver = latest_tag.lstrip("v")
        html_url = data.get("html_url", REPO_URL)

        # 简单版本比较
        try:
            newer = _version_newer(latest_ver, APP_VERSION)
        except Exception:
            newer = None

        if newer is None:
            info_text = (
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：{latest_tag}\n\n"
                f"前往下载：{html_url}"
            )
            m = QMessageBox.information(
                parent, "检查更新", info_text,
                QMessageBox.Ok,
            )
        elif newer:
            info_text = (
                f"🎉 发现新版本！\n\n"
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：<b>{latest_tag}</b>\n\n"
                f"{data.get('body', '').strip()[:200]}\n\n"
                f"是否前往下载？"
            )
            reply = QMessageBox.question(
                parent, "检查更新", info_text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                webbrowser.open(html_url)
        else:
            QMessageBox.information(
                parent, "检查更新",
                f"✅ 已是最新版本！\n\n"
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：{latest_tag}",
                QMessageBox.Ok,
            )

    def _open_log(self):
        """用记事本打开日志文件"""
        log_path = _get_log_path()
        if os.path.exists(log_path):
            os.startfile(log_path)
        else:
            self._safe_tray_msg("日志文件不存在", QSystemTrayIcon.Warning, 2000)

    def _register_hotkey(self):
        """延迟注册全局热键（需要窗口句柄）"""
        try:
            hwnd = int(self.app.effectiveWinId())
        except Exception:
            hwnd = 0
        self._hotkey.register(hwnd, self._hotkey_str)

    def _on_hotkey(self):
        """热键回调：呼出分组菜单"""
        _log.debug("Hotkey pressed")
        self._show_groups_menu()

    def set_hotkey(self, hotkey_str: str):
        """运行时修改全局热键"""
        self._hotkey_str = hotkey_str
        try:
            hwnd = int(self.app.effectiveWinId())
        except Exception:
            hwnd = 0
        self._hotkey.update_hotkey(hwnd, hotkey_str)
        self.config.set_hotkey(hotkey_str)
        _log.info(f"Hotkey changed to {hotkey_str}")

    def _quit(self):
        """退出应用"""
        _log.info("BatchGo exiting...")
        try:
            hwnd = int(self.app.effectiveWinId())
        except Exception:
            hwnd = 0
        self._hotkey.unregister(hwnd)
        self.tray.hide()
        self.app.quit()

    # ── 运行 ──────────────────────────────────────────────────

    def run(self):
        """启动应用主循环"""
        _log.info("Event loop starting")
        return self.app.exec()


def _get_log_path() -> str:
    if getattr(sys, 'frozen', False):
        base_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BatchGo")
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(base_dir, "logs", f"batchgo_{today}.log")


def _simple_error(exc: Exception) -> str:
    """提取异常的精简描述，用于弹窗提示"""
    msg = str(exc).strip()
    if len(msg) > 120:
        msg = msg[:120] + "..."
    return msg or type(exc).__name__


def _version_newer(latest: str, current: str) -> bool | None:
    """比较语义版本号，latest > current 返回 True；解析失败返回 None"""
    try:
        lp = [int(x) for x in latest.split(".")]
        cp = [int(x) for x in current.split(".")]
        # 补齐长度
        while len(lp) < len(cp):
            lp.append(0)
        while len(cp) < len(lp):
            cp.append(0)
        return lp > cp
    except (ValueError, TypeError):
        return None


# ── 入口 ──────────────────────────────────────────────────────────

def main():
    try:
        # 单实例检查
        shared_mem = ensure_single_instance()
        if shared_mem is None:
            _log.info("Another instance detected, exiting")
            app_temp = QApplication(sys.argv)
            tray_temp = QSystemTrayIcon()
            tray_temp.setIcon(create_tray_icon())
            tray_temp.show()
            try:
                tray_temp.showMessage(
                    APP_NAME,
                    "BatchGo 已在运行中（查看右下角托盘图标）",
                    QSystemTrayIcon.Information,
                    3000,
                )
            except Exception:
                pass
            QTimer.singleShot(3500, app_temp.quit)
            app_temp.exec()
            return

        app = BatchGoApp()
        sys.exit(app.run())

    except Exception:
        _log.critical(f"Fatal startup error:\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()

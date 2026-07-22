"""
BatchGo — 批量应用启动工具
系统托盘驻留，左键选择分组一键启动，右键打开配置面板。
"""
import os
import sys
import traceback
import logging
from datetime import datetime
from functools import wraps

from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox,
)
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction, QCursor,
)
from PySide6.QtCore import Qt, QSharedMemory, QTimer, QThread, Signal

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
APP_VERSION = "1.1.0"


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

def get_startup_vbs_path() -> str:
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    return os.path.join(startup_dir, "BatchGo.vbs")


def enable_auto_start():
    vbs_path = get_startup_vbs_path()
    os.makedirs(os.path.dirname(vbs_path), exist_ok=True)
    if getattr(sys, 'frozen', False):
        vbs_content = f'CreateObject("WScript.Shell").Run """{sys.executable}""", 0, False'
    else:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        vbs_content = f'CreateObject("WScript.Shell").Run """{pythonw}"" ""{main_py}""", 0, False'
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
    _log.info("Auto-start enabled")


def disable_auto_start():
    vbs_path = get_startup_vbs_path()
    if os.path.exists(vbs_path):
        try:
            os.remove(vbs_path)
        except OSError:
            pass
    _log.info("Auto-start disabled")


def is_auto_start_enabled() -> bool:
    return os.path.exists(get_startup_vbs_path())


# ── 单实例控制 ────────────────────────────────────────────────────

def ensure_single_instance() -> QSharedMemory:
    shared_mem = QSharedMemory("BatchGo_SingleInstance_Key")
    if shared_mem.attach():
        return None
    if not shared_mem.create(1):
        return None
    return shared_mem


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

        self.tray.setContextMenu(menu)
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
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick,
                       QSystemTrayIcon.MiddleClick):
            self._show_groups_menu()

    def _show_groups_menu(self):
        """在鼠标位置显示分组选择菜单（同步 exec，避免 GC 导致的 segfault）"""
        _log.debug("Showing groups menu")
        try:
            menu = self._build_groups_menu()
            self._groups_menu = menu  # 保持引用
            # 用 exec() 替代 popup()：同步执行，阻塞直到用户选择或取消
            # 彻底避免 popup() 异步模式下 Qt 事件处理时的 C++ 层崩溃
            menu.exec(QCursor.pos())
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
        QMessageBox.about(
            None,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "批量应用启动工具<br>"
            "左键托盘图标快速启动应用组合<br>"
            "右键托盘图标打开配置面板<br><br>"
            "支持浏览器 + 网址一键打开<br>"
            f"<br><small>日志文件：{_get_log_path()}</small>",
        )

    def _open_log(self):
        """用记事本打开日志文件"""
        log_path = _get_log_path()
        if os.path.exists(log_path):
            os.startfile(log_path)
        else:
            self._safe_tray_msg("日志文件不存在", QSystemTrayIcon.Warning, 2000)

    def _quit(self):
        """退出应用"""
        _log.info("BatchGo exiting...")
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

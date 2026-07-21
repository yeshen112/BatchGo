"""
BatchGo — 批量应用启动工具
系统托盘驻留，左键选择分组一键启动，右键打开配置面板。
"""
import os
import sys
import json

from PySide6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
)
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QPainter,
    QColor,
    QFont,
    QAction,
    QCursor,
)
from PySide6.QtCore import Qt, QSharedMemory, QTimer

from scanner import scan_and_cache, load_cached_apps
from config_manager import ConfigManager, AppGroup
from launcher import launch_group


# ── 常量 ──────────────────────────────────────────────────────────

APP_NAME = "BatchGo"
APP_VERSION = "1.0.0"


# ── 图标生成 ──────────────────────────────────────────────────────

def create_tray_icon() -> QIcon:
    """用 QPainter 绘制托盘图标：蓝色圆角方形 + 白色 "B" """
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 蓝色圆角背景
    painter.setBrush(QColor("#2563EB"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, size - 8, size - 8, 14, 14)

    # 白色 "B"
    font = QFont("Segoe UI", 36, QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "B")

    painter.end()
    return QIcon(pixmap)


# ── 开机自启管理 ──────────────────────────────────────────────────

def get_startup_vbs_path() -> str:
    """获取 Startup 文件夹下 .vbs 启动脚本的路径"""
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    return os.path.join(startup_dir, "BatchGo.vbs")


def get_target_exe_path() -> str:
    """获取目标可执行文件路径（开发时是 python，打包后是 exe）"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        # 开发环境：创建启动 python main.py 的 vbs
        return sys.executable


def enable_auto_start():
    """启用开机自启：在 Startup 文件夹创建 .vbs 脚本"""
    vbs_path = get_startup_vbs_path()
    os.makedirs(os.path.dirname(vbs_path), exist_ok=True)

    if getattr(sys, 'frozen', False):
        # 打包后的 exe 直接启动
        exe_path = sys.executable
        vbs_content = f'CreateObject("WScript.Shell").Run """{exe_path}""", 0, False'
    else:
        # 开发环境：用 pythonw 启动 main.py（无控制台窗口）
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        vbs_content = f'CreateObject("WScript.Shell").Run """{pythonw}"" ""{main_py}""", 0, False'

    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)


def disable_auto_start():
    """禁用开机自启：删除 Startup 文件夹中的 .vbs"""
    vbs_path = get_startup_vbs_path()
    if os.path.exists(vbs_path):
        try:
            os.remove(vbs_path)
        except OSError:
            pass


def is_auto_start_enabled() -> bool:
    """检查 Startup 中是否存在启动脚本"""
    return os.path.exists(get_startup_vbs_path())


# ── 单实例控制 ────────────────────────────────────────────────────

def ensure_single_instance() -> QSharedMemory:
    """使用 QSharedMemory 确保只有一个实例运行"""
    shared_mem = QSharedMemory("BatchGo_SingleInstance_Key")
    if shared_mem.attach():
        # 已有实例在运行
        return None
    if not shared_mem.create(1):
        return None
    return shared_mem


# ── 主应用 ────────────────────────────────────────────────────────

class BatchGoApp:
    """系统托盘应用主类"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)

        # 配置管理器
        self.config = ConfigManager()
        self.config.load()

        # 托盘图标
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(create_tray_icon())
        self.tray.setToolTip(f"{APP_NAME} - 批量启动工具")

        # 构建菜单
        self._rebuild_context_menu()

        # 左键点击 → 显示分组菜单
        self.tray.activated.connect(self._on_tray_activated)

        # 托盘消息
        self.tray.show()

        # 首次启动：如果没有缓存，自动扫描
        cached = self.config.get_cached_apps()
        if not cached:
            QTimer.singleShot(500, self._first_scan)

    # ── 首次扫描 ──────────────────────────────────────────────

    def _first_scan(self):
        """首次启动时自动扫描应用"""
        self.tray.showMessage(
            APP_NAME,
            "正在扫描已安装应用，请稍候...",
            QSystemTrayIcon.Information,
            2000,
        )
        apps = scan_and_cache(self.config.config_path)
        self.config.load()  # 重新加载
        self.tray.showMessage(
            APP_NAME,
            f"扫描完成！发现 {len(apps)} 个应用",
            QSystemTrayIcon.Information,
            3000,
        )

    # ── 构建菜单 ──────────────────────────────────────────────

    def _rebuild_context_menu(self):
        """重构右键菜单（包含配置、刷新等）"""
        menu = QMenu()

        # 配置面板
        action_config = QAction("⚙ 配置面板", menu)
        action_config.triggered.connect(self._open_config)
        menu.addAction(action_config)

        # 刷新应用列表
        action_refresh = QAction("🔄 刷新应用列表", menu)
        action_refresh.triggered.connect(self._refresh_apps)
        menu.addAction(action_refresh)

        menu.addSeparator()

        # 开机自启
        auto_start = is_auto_start_enabled()
        action_autostart = QAction(
            f"{'☑' if auto_start else '☐'} 开机自启", menu
        )
        action_autostart.triggered.connect(self._toggle_auto_start)
        menu.addAction(action_autostart)

        menu.addSeparator()

        # 关于
        action_about = QAction("关于 BatchGo", menu)
        action_about.triggered.connect(self._show_about)
        menu.addAction(action_about)

        # 退出
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
            action_empty = QAction("（暂无组合，请右键配置）", menu)
            action_empty.setEnabled(False)
            menu.addAction(action_empty)
        else:
            for group in groups:
                count = len(group.entries)
                action = QAction(f"▶ {group.name}  ({count}个应用)", menu)
                action.setData(group.name)
                action.triggered.connect(
                    lambda checked=False, g=group: self._launch_group(g)
                )
                menu.addAction(action)

        return menu

    # ── 托盘事件 ──────────────────────────────────────────────

    def _on_tray_activated(self, reason):
        """托盘图标被点击"""
        if reason == QSystemTrayIcon.Trigger:  # 左键
            self._show_groups_menu()
        elif reason == QSystemTrayIcon.Context:  # 右键（备用，contextMenu 已处理）
            pass

    def _show_groups_menu(self):
        """在鼠标位置显示分组选择菜单"""
        menu = self._build_groups_menu()
        menu.popup(QCursor.pos())

    # ── 操作 ──────────────────────────────────────────────────

    def _launch_group(self, group: AppGroup):
        """启动一个分组中的所有应用"""
        success, failed = launch_group(group)
        if failed == 0:
            self.tray.showMessage(
                APP_NAME,
                f"「{group.name}」全部启动成功！({success}个应用)",
                QSystemTrayIcon.Information,
                3000,
            )
        else:
            self.tray.showMessage(
                APP_NAME,
                f"「{group.name}」启动完成：成功 {success} 个，失败 {failed} 个",
                QSystemTrayIcon.Warning,
                3000,
            )

    def _open_config(self):
        """打开配置面板"""
        from config_dialog import ConfigDialog
        dialog = ConfigDialog(self.config, parent=None)
        if dialog.exec():
            # 配置已保存，更新菜单
            self.config.load()
            self._rebuild_context_menu()

    def _refresh_apps(self):
        """手动刷新应用列表"""
        self.tray.showMessage(
            APP_NAME,
            "正在扫描应用...",
            QSystemTrayIcon.Information,
            2000,
        )
        apps = scan_and_cache(self.config.config_path)
        self.config.load()
        self.tray.showMessage(
            APP_NAME,
            f"刷新完成！发现 {len(apps)} 个应用",
            QSystemTrayIcon.Information,
            3000,
        )

    def _toggle_auto_start(self):
        """切换开机自启状态"""
        if is_auto_start_enabled():
            disable_auto_start()
            self.config.set_auto_start(False)
            self.tray.showMessage(APP_NAME, "已关闭开机自启", QSystemTrayIcon.Information, 2000)
        else:
            enable_auto_start()
            self.config.set_auto_start(True)
            self.tray.showMessage(APP_NAME, "已开启开机自启", QSystemTrayIcon.Information, 2000)
        self._rebuild_context_menu()

    def _show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            None,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "批量应用启动工具<br>"
            "左键托盘图标快速启动应用组合<br>"
            "右键托盘图标打开配置面板<br><br>"
            "支持浏览器 + 网址一键打开",
        )

    def _quit(self):
        """退出应用"""
        self.tray.hide()
        self.app.quit()

    # ── 运行 ──────────────────────────────────────────────────

    def run(self):
        """启动应用主循环"""
        return self.app.exec()


# ── 入口 ──────────────────────────────────────────────────────────

def main():
    # 单实例检查
    shared_mem = ensure_single_instance()
    if shared_mem is None:
        # 已有实例运行中
        print(f"[{APP_NAME}] 已有实例在运行，退出。")
        # 系统托盘通知已有实例
        app_temp = QApplication(sys.argv)
        tray_temp = QSystemTrayIcon()
        tray_temp.setIcon(create_tray_icon())
        tray_temp.show()
        tray_temp.showMessage(
            APP_NAME,
            "BatchGo 已在运行中（查看右下角托盘图标）",
            QSystemTrayIcon.Information,
            3000,
        )
        # 短暂显示后退出
        QTimer.singleShot(3500, app_temp.quit)
        app_temp.exec()
        return

    app = BatchGoApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()

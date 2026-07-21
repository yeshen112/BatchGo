"""
配置面板 —— 管理应用分组：新建/删除/重命名分组，向分组添加/移除应用，
设置浏览器 URL。支持「常用 / 系统工具」分 Tab 查看。
"""
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLineEdit, QLabel, QTabWidget,
    QMessageBox, QInputDialog, QAbstractItemView,
    QProgressBar, QStatusBar, QWidget,
)
from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtGui import QIcon

from scanner import scan_and_cache, load_cached_apps, AppInfo
from config_manager import ConfigManager, AppEntry, AppGroup
from icon_utils import get_app_icon, clear_cache as clear_icon_cache


# ── 后台扫描线程 ──────────────────────────────────────────────────

class ScanThread(QThread):
    """后台线程执行应用扫描，避免阻塞 UI"""
    progress = Signal(int, int)
    finished = Signal(list)

    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path

    def run(self):
        apps = scan_and_cache(self.config_path, progress_callback=self._on_progress)
        self.finished.emit(apps)

    def _on_progress(self, current, total):
        self.progress.emit(current, total)


# ── 主对话框 ──────────────────────────────────────────────────────

class ConfigDialog(QDialog):
    """配置面板对话框"""

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config
        self._apps: list[AppInfo] = []
        self._current_group_name: str = ""

        self.setWindowTitle("BatchGo 配置面板")
        self.resize(860, 580)
        self.setMinimumSize(720, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._load_apps()
        self._refresh_group_list()
        self._refresh_available_apps()

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 8)

        # ── 上半部分：分组管理 + 分组应用列表 ───────────────────
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.addWidget(self._build_group_panel())
        top_splitter.addWidget(self._build_entries_panel())
        top_splitter.setSizes([220, 600])
        main_layout.addWidget(top_splitter, stretch=3)

        # ── 下半部分：可用应用列表（Tab 切换常用/系统工具）─────
        bottom_panel = self._build_available_apps_panel()
        main_layout.addWidget(bottom_panel, stretch=2)

        # ── 状态栏 ──────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.status_bar.showMessage("就绪")
        main_layout.addWidget(self.status_bar)

        # ── 底部按钮 ──────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        main_layout.addLayout(btn_layout)

    def _build_group_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("<b>📁 应用组合</b>"))

        self.group_list = QListWidget()
        self.group_list.setAlternatingRowColors(True)
        self.group_list.currentItemChanged.connect(self._on_group_selected)
        layout.addWidget(self.group_list)

        btn_layout = QHBoxLayout()
        btn_new = QPushButton("+ 新建")
        btn_new.clicked.connect(self._add_group)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._remove_group)
        btn_rename = QPushButton("重命名")
        btn_rename.clicked.connect(self._rename_group)
        btn_layout.addWidget(btn_new)
        btn_layout.addWidget(btn_del)
        btn_layout.addWidget(btn_rename)
        layout.addLayout(btn_layout)

        return widget

    def _build_entries_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("<b>📋 组合中的应用</b>"))

        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["应用名称", "URL/网址", "启动参数"])
        for col in range(3):
            self.entry_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Stretch)
        self.entry_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.entry_table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.entry_table.verticalHeader().setVisible(False)
        self.entry_table.setAlternatingRowColors(True)
        self.entry_table.itemChanged.connect(self._on_entry_changed)
        layout.addWidget(self.entry_table)

        btn_layout = QHBoxLayout()
        btn_remove = QPushButton("移除选中")
        btn_remove.clicked.connect(self._remove_entry)
        btn_layout.addWidget(btn_remove)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        hint = QLabel(
            '<span style="color:#888;">💡 双击 URL 列可编辑网址（如 https://mail.google.com）</span>'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return widget

    def _build_available_apps_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>🔍 可用应用</b>"))

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("输入关键字过滤...")
        self.search_box.textChanged.connect(self._filter_apps)
        header.addWidget(self.search_box, stretch=1)

        btn_refresh = QPushButton("🔄 重新扫描")
        btn_refresh.clicked.connect(self._rescan_apps)
        header.addWidget(btn_refresh)
        layout.addLayout(header)

        self.scan_progress = QProgressBar()
        self.scan_progress.setVisible(False)
        layout.addWidget(self.scan_progress)

        # Tab 分页：常用 / 系统工具
        self.app_tabs = QTabWidget()

        self.common_list = QListWidget()
        self.common_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.common_list.setAlternatingRowColors(True)
        self.common_list.setIconSize(QSize(32, 32))
        self.app_tabs.addTab(self.common_list, "⭐ 常用应用")

        self.system_list = QListWidget()
        self.system_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.system_list.setAlternatingRowColors(True)
        self.system_list.setIconSize(QSize(32, 32))
        self.app_tabs.addTab(self.system_list, "🔧 系统工具")

        # 切换 Tab 时更新搜索过滤
        self.app_tabs.currentChanged.connect(lambda: self._filter_apps(self.search_box.text()))

        layout.addWidget(self.app_tabs)

        btn_add = QPushButton("➕ 添加到当前组合")
        btn_add.clicked.connect(self._add_apps_to_group)
        layout.addWidget(btn_add)

        return widget

    # ── 数据加载 ──────────────────────────────────────────────

    def _load_apps(self):
        cached = self.config.get_cached_apps()
        if cached:
            self._apps = cached
        else:
            self._apps = load_cached_apps(self.config.config_path)

    def _rescan_apps(self):
        self.scan_progress.setVisible(True)
        self.scan_progress.setValue(0)
        self.status_bar.showMessage("正在扫描应用...")
        clear_icon_cache()

        self.scan_thread = ScanThread(self.config.config_path)
        self.scan_thread.progress.connect(self._on_scan_progress)
        self.scan_thread.finished.connect(self._on_scan_finished)
        self.scan_thread.start()

    def _on_scan_progress(self, current, total):
        self.scan_progress.setMaximum(total)
        self.scan_progress.setValue(current)

    def _on_scan_finished(self, apps: list[AppInfo]):
        self.scan_progress.setVisible(False)
        self._apps = apps
        self.config.load()
        self._refresh_available_apps()
        common_count = sum(1 for a in apps if not a.is_system_tool)
        sys_count = sum(1 for a in apps if a.is_system_tool)
        self.status_bar.showMessage(f"扫描完成：{common_count} 个常用应用，{sys_count} 个系统工具")

    # ── 分组列表操作 ──────────────────────────────────────────

    def _refresh_group_list(self):
        self.group_list.clear()
        for g in self.config.get_groups():
            item = QListWidgetItem(f"📁 {g.name}  ({len(g.entries)})")
            item.setData(Qt.UserRole, g.name)
            self.group_list.addItem(item)

    def _on_group_selected(self, current, previous):
        if current is None:
            self._current_group_name = ""
            self.entry_table.setRowCount(0)
            return
        self._current_group_name = current.data(Qt.UserRole)
        self._refresh_entry_table()

    def _add_group(self):
        name, ok = QInputDialog.getText(self, "新建组合", "请输入组合名称：")
        if ok and name.strip():
            name = name.strip()
            if self.config.add_group(name):
                self._refresh_group_list()
                for i in range(self.group_list.count()):
                    item = self.group_list.item(i)
                    if item.data(Qt.UserRole) == name:
                        self.group_list.setCurrentItem(item)
                        break
                self.status_bar.showMessage(f"已创建组合「{name}」")
            else:
                QMessageBox.warning(self, "提示", f"组合「{name}」已存在！")

    def _remove_group(self):
        item = self.group_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个组合")
            return
        name = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除组合「{name}」吗？\n（不会删除应用本身）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.config.remove_group(name)
            self._current_group_name = ""
            self._refresh_group_list()
            self._refresh_entry_table()
            self.config.load()
            self.status_bar.showMessage(f"已删除组合「{name}」")

    def _rename_group(self):
        item = self.group_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个组合")
            return
        old_name = item.data(Qt.UserRole)
        new_name, ok = QInputDialog.getText(
            self, "重命名组合", "请输入新名称：", text=old_name,
        )
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            if self.config.rename_group(old_name, new_name):
                self._current_group_name = new_name
                self.config.load()
                self._refresh_group_list()
                self._refresh_entry_table()
                self.status_bar.showMessage(f"「{old_name}」→「{new_name}」")
            else:
                QMessageBox.warning(self, "提示", f"名称「{new_name}」已存在或重命名失败！")

    # ── 应用条目表操作 ────────────────────────────────────────

    def _refresh_entry_table(self):
        self.entry_table.setRowCount(0)
        if not self._current_group_name:
            return

        group = self.config.get_group(self._current_group_name)
        if group is None:
            return

        self.entry_table.blockSignals(True)
        for entry in group.entries:
            row = self.entry_table.rowCount()
            self.entry_table.insertRow(row)

            name_item = QTableWidgetItem(entry.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setToolTip(entry.path)
            name_item.setData(Qt.UserRole, entry.path)
            name_item.setData(Qt.UserRole + 1, entry.working_dir)

            # 使用 icon_utils 获取高质量图标
            icon = get_app_icon(entry.path)
            if icon and not icon.isNull():
                name_item.setIcon(icon)
            self.entry_table.setItem(row, 0, name_item)

            url_item = QTableWidgetItem(entry.url)
            url_item.setToolTip("浏览器启动时打开的网址")
            self.entry_table.setItem(row, 1, url_item)

            args_item = QTableWidgetItem(entry.arguments)
            args_item.setToolTip("额外启动参数")
            self.entry_table.setItem(row, 2, args_item)

        self.entry_table.blockSignals(False)

    def _on_entry_changed(self, item: QTableWidgetItem):
        row = item.row()
        if row < 0:
            return
        name_item = self.entry_table.item(row, 0)
        url_item = self.entry_table.item(row, 1)
        args_item = self.entry_table.item(row, 2)
        if name_item is None:
            return

        entry = AppEntry(
            name=name_item.text(),
            path=name_item.data(Qt.UserRole),
            working_dir=name_item.data(Qt.UserRole + 1),
            url=url_item.text().strip() if url_item else "",
            arguments=args_item.text().strip() if args_item else "",
        )
        self.config.update_entry(self._current_group_name, row, entry)

    def _remove_entry(self):
        current_row = self.entry_table.currentRow()
        if current_row < 0 or not self._current_group_name:
            QMessageBox.information(self, "提示", "请先选择要移除的应用")
            return

        name = self.entry_table.item(current_row, 0).text() if self.entry_table.item(current_row, 0) else ""
        self.config.remove_entry(self._current_group_name, current_row)
        self.config.load()
        self._refresh_entry_table()
        self._refresh_group_list()
        self.status_bar.showMessage(f"已从组合中移除「{name}」")

    # ── 可用应用列表 ──────────────────────────────────────────

    @property
    def _current_available_list(self) -> QListWidget:
        """获取当前 Tab 对应的列表控件"""
        return self.common_list if self.app_tabs.currentIndex() == 0 else self.system_list

    def _refresh_available_apps(self, filter_text: str = ""):
        """刷新可用应用列表（分别填充常用和系统工具两个 Tab）"""
        self.common_list.clear()
        self.system_list.clear()
        ft = filter_text.lower()

        for app in self._apps:
            # 搜索过滤
            if ft and ft not in app.name.lower() and ft not in app.description.lower():
                continue

            text = app.name
            if app.description:
                text += f"  — {app.description}"

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, app.to_dict())
            item.setToolTip(app.path)

            # 图标
            if app.path:
                icon = get_app_icon(app.path)
                if icon and not icon.isNull():
                    item.setIcon(icon)

            # 分配到对应的 Tab
            if app.is_system_tool:
                self.system_list.addItem(item)
            else:
                self.common_list.addItem(item)

        # 更新 Tab 标签上的计数
        self.app_tabs.setTabText(0, f"⭐ 常用应用 ({self.common_list.count()})")
        self.app_tabs.setTabText(1, f"🔧 系统工具 ({self.system_list.count()})")

    def _filter_apps(self, text: str):
        self._refresh_available_apps(text)

    def _add_apps_to_group(self):
        """将选中的可用应用添加到当前分组"""
        if not self._current_group_name:
            QMessageBox.information(self, "提示", "请先在左侧选择或新建一个组合")
            return

        current_list = self._current_available_list
        selected = current_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先在应用列表中选中要添加的应用")
            return

        added = 0
        for item in selected:
            data = item.data(Qt.UserRole)
            app_info = AppInfo.from_dict(data)
            entry = AppEntry.from_app_info(app_info)
            if self.config.add_entry(self._current_group_name, entry):
                added += 1

        self.config.load()
        self._refresh_entry_table()
        self._refresh_group_list()

        if added > 0:
            self.status_bar.showMessage(f"已添加 {added} 个应用到「{self._current_group_name}」")

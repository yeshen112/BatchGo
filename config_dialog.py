"""
配置面板 —— 管理应用分组：新建/删除/重命名分组，向分组添加/移除应用，
设置浏览器 URL。
"""
import os
import sys

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLineEdit,
    QLabel,
    QGroupBox,
    QMessageBox,
    QInputDialog,
    QAbstractItemView,
    QProgressBar,
    QWidget,
    QStyle,
    QFileIconProvider,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon

from scanner import scan_and_cache, load_cached_apps, AppInfo
from config_manager import ConfigManager, AppEntry, AppGroup


# ── 后台扫描线程 ──────────────────────────────────────────────────

class ScanThread(QThread):
    """后台线程执行应用扫描，避免阻塞 UI"""
    progress = Signal(int, int)       # current, total
    finished = Signal(list)           # 返回 AppInfo 列表

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
        self._apps: list[AppInfo] = []          # 完整可用应用列表
        self._current_group_name: str = ""       # 当前选中的分组名称
        self._icon_provider = QFileIconProvider()

        self.setWindowTitle("BatchGo 配置面板")
        self.resize(860, 560)
        self.setMinimumSize(700, 450)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._load_apps()
        self._refresh_group_list()
        self._refresh_available_apps()

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self):
        """构建整体界面布局"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 上半部分：分组管理 + 分组应用列表 ───────────────────
        top_splitter = QSplitter(Qt.Horizontal)

        # 左侧：分组列表
        left_panel = self._build_group_panel()
        top_splitter.addWidget(left_panel)

        # 右侧：分组内的应用条目
        right_panel = self._build_entries_panel()
        top_splitter.addWidget(right_panel)

        top_splitter.setSizes([220, 600])
        main_layout.addWidget(top_splitter, stretch=3)

        # ── 下半部分：可用应用列表 ─────────────────────────────
        bottom_panel = self._build_available_apps_panel()
        main_layout.addWidget(bottom_panel, stretch=2)

        # ── 底部按钮 ──────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_close = QPushButton("关闭")
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)

        main_layout.addLayout(btn_layout)

    def _build_group_panel(self) -> QWidget:
        """左侧：分组管理面板"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("<b>📁 应用组合</b>")
        layout.addWidget(title)

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
        """右侧：当前分组的应用条目列表"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("<b>📋 组合中的应用</b>")
        layout.addWidget(title)

        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["应用名称", "URL/网址", "启动参数"])
        self.entry_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.entry_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.entry_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
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

        # 提示
        hint = QLabel(
            '<span style="color:#888;">💡 双击 URL 列可编辑网址；选中应用后右键可添加 URL</span>'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return widget

    def _build_available_apps_panel(self) -> QWidget:
        """底部：可用应用搜索 + 列表"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        title = QLabel("<b>🔍 可用应用</b>")
        header.addWidget(title)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("输入关键字过滤应用...")
        self.search_box.textChanged.connect(self._filter_apps)
        header.addWidget(self.search_box, stretch=1)

        btn_refresh = QPushButton("🔄 重新扫描")
        btn_refresh.clicked.connect(self._rescan_apps)
        header.addWidget(btn_refresh)

        layout.addLayout(header)

        # 进度条（扫描时显示）
        self.scan_progress = QProgressBar()
        self.scan_progress.setVisible(False)
        layout.addWidget(self.scan_progress)

        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.available_list.setAlternatingRowColors(True)
        layout.addWidget(self.available_list)

        btn_add = QPushButton("➕ 添加到当前组合")
        btn_add.clicked.connect(self._add_apps_to_group)
        layout.addWidget(btn_add)

        return widget

    # ── 数据加载 ──────────────────────────────────────────────

    def _load_apps(self):
        """加载应用列表（优先缓存）"""
        cached = self.config.get_cached_apps()
        if cached:
            self._apps = cached
        else:
            # 加载配置时已确保 apps_cache 被读取
            self._apps = load_cached_apps(self.config.config_path)

    def _rescan_apps(self):
        """后台扫描应用"""
        self.scan_progress.setVisible(True)
        self.scan_progress.setValue(0)

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

    # ── 分组列表操作 ──────────────────────────────────────────

    def _refresh_group_list(self):
        """刷新左侧分组列表"""
        self.group_list.clear()
        groups = self.config.get_groups()
        for g in groups:
            item = QListWidgetItem(f"📁 {g.name}  ({len(g.entries)})")
            item.setData(Qt.UserRole, g.name)
            self.group_list.addItem(item)

    def _on_group_selected(self, current, previous):
        """分组选中变化 → 刷新右侧应用条目表"""
        if current is None:
            self._current_group_name = ""
            self.entry_table.setRowCount(0)
            return

        self._current_group_name = current.data(Qt.UserRole)
        self._refresh_entry_table()

    def _add_group(self):
        """新建分组"""
        name, ok = QInputDialog.getText(
            self, "新建组合", "请输入组合名称："
        )
        if ok and name.strip():
            name = name.strip()
            if self.config.add_group(name):
                self._refresh_group_list()
                # 自动选中
                for i in range(self.group_list.count()):
                    item = self.group_list.item(i)
                    if item.data(Qt.UserRole) == name:
                        self.group_list.setCurrentItem(item)
                        break
            else:
                QMessageBox.warning(self, "提示", f"组合「{name}」已存在！")

    def _remove_group(self):
        """删除选中的分组"""
        item = self.group_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个组合")
            return

        name = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除组合「{name}」吗？\n（不会删除应用本身）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.config.remove_group(name)
            self._current_group_name = ""
            self._refresh_group_list()
            self._refresh_entry_table()
            self.config.load()

    def _rename_group(self):
        """重命名选中分组"""
        item = self.group_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个组合")
            return

        old_name = item.data(Qt.UserRole)
        new_name, ok = QInputDialog.getText(
            self, "重命名组合",
            "请输入新名称：",
            text=old_name,
        )
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            if self.config.rename_group(old_name, new_name):
                self._current_group_name = new_name
                self.config.load()
                self._refresh_group_list()
                self._refresh_entry_table()
            else:
                QMessageBox.warning(self, "提示", f"名称「{new_name}」已存在或重命名失败！")

    # ── 应用条目表操作 ────────────────────────────────────────

    def _refresh_entry_table(self):
        """刷新右侧应用条目表格"""
        self.entry_table.setRowCount(0)
        if not self._current_group_name:
            return

        group = self.config.get_group(self._current_group_name)
        if group is None:
            return

        self.entry_table.blockSignals(True)  # 阻止 itemChanged 信号
        for entry in group.entries:
            row = self.entry_table.rowCount()
            self.entry_table.insertRow(row)

            # 应用名称（不可编辑）
            name_item = QTableWidgetItem(entry.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setToolTip(entry.path)
            icon = self._get_app_icon(entry.path)
            if icon:
                name_item.setIcon(icon)
            self.entry_table.setItem(row, 0, name_item)

            # URL（可编辑）
            url_item = QTableWidgetItem(entry.url)
            url_item.setToolTip("浏览器启动时打开的网址，如 https://mail.google.com")
            self.entry_table.setItem(row, 1, url_item)

            # 参数（可编辑）
            args_item = QTableWidgetItem(entry.arguments)
            args_item.setToolTip("额外启动参数")
            self.entry_table.setItem(row, 2, args_item)

            # 存储原始数据
            name_item.setData(Qt.UserRole, entry.path)
            name_item.setData(Qt.UserRole + 1, entry.working_dir)

        self.entry_table.blockSignals(False)

    def _get_app_icon(self, path: str) -> QIcon:
        """获取应用的图标"""
        if path and os.path.exists(path):
            try:
                return self._icon_provider.icon(path)
            except Exception:
                pass
        return QIcon()

    def _on_entry_changed(self, item: QTableWidgetItem):
        """表格单元格被编辑后保存"""
        row = item.row()
        if row < 0:
            return

        # 读取整行数据
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
        """移除选中的条目"""
        current_row = self.entry_table.currentRow()
        if current_row < 0 or not self._current_group_name:
            QMessageBox.information(self, "提示", "请先选择要移除的应用")
            return

        self.config.remove_entry(self._current_group_name, current_row)
        self.config.load()
        self._refresh_entry_table()
        self._refresh_group_list()

    # ── 可用应用列表 ──────────────────────────────────────────

    def _refresh_available_apps(self, filter_text: str = ""):
        """刷新可用应用列表，支持搜索过滤"""
        self.available_list.clear()
        ft = filter_text.lower()

        for app in self._apps:
            if ft and ft not in app.name.lower() and ft not in app.description.lower():
                continue

            text = app.name
            if app.description:
                text += f"  ── {app.description}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, app.to_dict())
            item.setToolTip(app.path)

            # 图标
            icon = self._get_app_icon(app.path)
            if icon:
                item.setIcon(icon)

            self.available_list.addItem(item)

    def _filter_apps(self, text: str):
        self._refresh_available_apps(text)

    def _add_apps_to_group(self):
        """将选中的可用应用添加到当前分组"""
        if not self._current_group_name:
            QMessageBox.information(self, "提示", "请先在左侧选择或新建一个组合")
            return

        selected = self.available_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先在可用应用列表中选中要添加的应用")
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
            self.temp_status = f"已添加 {added} 个应用"

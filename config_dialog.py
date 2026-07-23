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
    QFileDialog, QFormLayout, QDialogButtonBox,
    QKeySequenceEdit, QFileIconProvider, QComboBox,
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
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        self._build_ui()
        self._load_apps()
        self._refresh_group_list()
        self._refresh_available_apps()
        # 初始化热键显示，并连接保存信号
        self.hotkey_edit.setKeySequence(self.config.get_hotkey())
        self.hotkey_edit.editingFinished.connect(self._on_hotkey_changed)

    def closeEvent(self, event):
        """右上角 X 按钮行为与「关闭」按钮一致"""
        self.accept()

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(10, 10, 10, 6)

        # ── 可拖拽的主分割器（上下） ────────────────────────────
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(5)

        # ── 上半部分：分组 + 条目（左右可拖拽） ────────────────
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setHandleWidth(5)
        top_splitter.addWidget(self._build_group_panel())
        top_splitter.addWidget(self._build_entries_panel())
        top_splitter.setSizes([220, 600])

        main_splitter.addWidget(top_splitter)

        # ── 下半部分：可用应用列表 ─────────────────────────────
        main_splitter.addWidget(self._build_available_apps_panel())
        main_splitter.setSizes([350, 200])
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 2)

        main_layout.addWidget(main_splitter, stretch=1)

        # ── 状态栏 ──────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.status_bar.showMessage("就绪")
        main_layout.addWidget(self.status_bar)

        # ── 快捷键设置 ──────────────────────────────────────────
        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("<b>⌨ 全局热键：</b>"))
        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setMaximumWidth(180)
        self.hotkey_edit.setToolTip("点击后按下组合键即可修改，例如 Ctrl+Alt+E")
        hotkey_row.addWidget(self.hotkey_edit)
        hotkey_row.addStretch()
        main_layout.addLayout(hotkey_row)

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
        self.group_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.group_list.setDefaultDropAction(Qt.MoveAction)
        self.group_list.currentItemChanged.connect(self._on_group_selected)
        # 拖拽排序后保存新顺序
        self.group_list.model().rowsMoved.connect(self._on_groups_reordered)
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

        self.entry_table = QTableWidget(0, 4)
        self.entry_table.setHorizontalHeaderLabels(["应用名称", "URL/网址", "启动参数", "管理员"])
        # 第 4 列（管理员）固定宽度，其余 stretch
        self.entry_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.entry_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.entry_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.entry_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.entry_table.setColumnWidth(3, 56)
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

        btn_row = QHBoxLayout()
        btn_add = QPushButton("➕ 添加到当前组合")
        btn_add.clicked.connect(self._add_apps_to_group)
        btn_row.addWidget(btn_add)

        btn_custom = QPushButton("✏️ 自定义添加...")
        btn_custom.clicked.connect(self._custom_add_app)
        btn_row.addWidget(btn_custom)
        layout.addLayout(btn_row)

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
        # 记住当前选中的分组，刷新后恢复
        current = self.group_list.currentItem()
        saved_name = current.data(Qt.UserRole) if current else self._current_group_name

        self.group_list.blockSignals(True)
        self.group_list.clear()

        for g in self.config.get_groups():
            item = QListWidgetItem(f"📁 {g.name}  ({len(g.entries)})")
            item.setData(Qt.UserRole, g.name)
            self.group_list.addItem(item)

        self.group_list.blockSignals(False)

        # 恢复之前选中的分组
        if saved_name:
            for i in range(self.group_list.count()):
                item = self.group_list.item(i)
                if item.data(Qt.UserRole) == saved_name:
                    self.group_list.setCurrentItem(item)
                    break

    def _on_groups_reordered(self):
        """拖拽排序分组后保存新顺序"""
        names = []
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            if item:
                names.append(item.data(Qt.UserRole))
        if names:
            self.config.reorder_groups(names)
            self.config.load()
            self._refresh_group_list()

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
            name_item.setToolTip(entry.path)
            name_item.setData(Qt.UserRole, entry.path)
            name_item.setData(Qt.UserRole + 1, entry.working_dir)

            # 图标：文件夹用文件夹图标，文件用类型图标，应用用 exe 图标
            if getattr(entry, "is_folder", False):
                icon = QFileIconProvider().icon(QFileIconProvider.Folder)
            elif getattr(entry, "is_file", False):
                info = os.path.join(
                    entry.working_dir or os.path.dirname(entry.path) or "",
                    os.path.basename(entry.path),
                ) if entry.path else ""
                icon = QFileIconProvider().icon(QFileIconProvider.Drive)  # fallback
                if info:
                    from PySide6.QtCore import QFileInfo
                    icon = QFileIconProvider().icon(QFileInfo(info))
            else:
                icon = get_app_icon(entry.path)
            if icon and not icon.isNull():
                name_item.setIcon(icon)
            self.entry_table.setItem(row, 0, name_item)

            url_item = QTableWidgetItem(entry.url)
            url_item.setToolTip("浏览器启动时打开的网址，多个用空格隔开")
            self.entry_table.setItem(row, 1, url_item)

            args_item = QTableWidgetItem(entry.arguments)
            args_item.setToolTip("额外启动参数")
            self.entry_table.setItem(row, 2, args_item)

            # 管理员勾选框
            admin_item = QTableWidgetItem("")
            admin_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            admin_item.setCheckState(
                Qt.Checked if getattr(entry, "run_as_admin", False) else Qt.Unchecked
            )
            admin_item.setToolTip("以管理员身份运行（启动时弹出 UAC 确认）")
            self.entry_table.setItem(row, 3, admin_item)

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

        admin_item = self.entry_table.item(row, 3)
        entry = AppEntry(
            name=name_item.text(),
            path=name_item.data(Qt.UserRole),
            working_dir=name_item.data(Qt.UserRole + 1),
            url=url_item.text().strip() if url_item else "",
            arguments=args_item.text().strip() if args_item else "",
            run_as_admin=admin_item.checkState() == Qt.Checked if admin_item else False,
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
        common_count = self.common_list.count()
        sys_count = self.system_list.count()
        self.app_tabs.setTabText(0, f"⭐ 常用应用 ({common_count})")
        self.app_tabs.setTabText(1, f"🔧 系统工具 ({sys_count})")

        # 智能切换 Tab：一边为空另一边有结果时自动跳转
        if common_count == 0 and sys_count > 0:
            self.app_tabs.setCurrentIndex(1)
        elif sys_count == 0 and common_count > 0:
            self.app_tabs.setCurrentIndex(0)

    def _on_hotkey_changed(self):
        """快捷键修改时立即保存到配置"""
        seq = self.hotkey_edit.keySequence().toString()
        if seq:
            self.config.set_hotkey(seq)
            self.status_bar.showMessage(f"快捷键已设为 {seq}")

    def _filter_apps(self, text: str):
        self._refresh_available_apps(text)

    _MAX_APPS_WARN = 10  # 超过此数量弹警告

    def _warn_too_many(self, add_count: int) -> bool:
        """检查添加后是否超过警戒线，超过则弹窗确认。返回 True 表示继续。"""
        group = self.config.get_group(self._current_group_name)
        current = len(group.entries) if group else 0
        after = current + add_count
        if after <= self._MAX_APPS_WARN:
            return True
        reply = QMessageBox.question(
            self, "确认添加",
            f"组合「{self._current_group_name}」当前已有 {current} 个应用，\n"
            f"添加后将达到 {after} 个。\n\n"
            "同时启动过多应用可能导致系统卡顿，是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

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

        if not self._warn_too_many(len(selected)):
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

    _ENTRY_TYPES = [
        ("应用程序", "可执行文件 (.exe) 或 UWP AUMID"),
        ("文件夹",   "文件夹路径，启动时用资源管理器打开"),
        ("文件",     "任意文件（文档/图片/音乐等），用系统关联程序打开"),
    ]

    def _custom_add_app(self):
        """手动添加扫不到的应用 / 文件夹 / 文件"""
        if not self._current_group_name:
            QMessageBox.information(self, "提示", "请先在左侧选择或新建一个组合")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("自定义添加")
        dialog.setMinimumWidth(500)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("例如：论文资料")
        form.addRow("名称：", name_edit)

        # 类型下拉框
        type_combo = QComboBox()
        for label, _tip in self._ENTRY_TYPES:
            type_combo.addItem(label)
        form.addRow("类型：", type_combo)

        # 路径行：输入框 + 浏览按钮
        path_row = QHBoxLayout()
        path_edit = QLineEdit()
        path_edit.setPlaceholderText(self._ENTRY_TYPES[0][1])
        path_row.addWidget(path_edit)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(lambda: self._browse_path(path_edit, name_edit, type_combo.currentIndex()))
        path_row.addWidget(btn_browse)
        path_label = QLabel("路径：")
        form.addRow(path_label, path_row)

        # 工作目录 & 参数（仅应用程序需要）
        workdir_edit = QLineEdit()
        workdir_edit.setPlaceholderText("留空则使用程序所在目录")
        workdir_label = QLabel("工作目录：")
        form.addRow(workdir_label, workdir_edit)

        args_edit = QLineEdit()
        args_edit.setPlaceholderText("例如：--no-sandbox")
        args_label = QLabel("启动参数：")
        form.addRow(args_label, args_edit)

        url_edit = QLineEdit()
        url_edit.setPlaceholderText("浏览器网址，多个用空格隔开（如：https://a.com https://b.com）")
        form.addRow("附加网址：", url_edit)

        # 类型切换时：更新占位符、显隐工作目录和参数行
        def _on_type_changed(idx: int):
            path_edit.setPlaceholderText(self._ENTRY_TYPES[idx][1])
            if idx == 0:  # 应用程序
                workdir_label.setVisible(True)
                workdir_edit.setVisible(True)
                args_label.setVisible(True)
                args_edit.setVisible(True)
            else:  # 文件夹 / 文件
                workdir_label.setVisible(False)
                workdir_edit.setVisible(False)
                args_label.setVisible(False)
                args_edit.setVisible(False)
        type_combo.currentIndexChanged.connect(_on_type_changed)

        layout.addLayout(form)

        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        name = name_edit.text().strip()
        path = path_edit.text().strip()
        idx = type_combo.currentIndex()

        if not name:
            QMessageBox.warning(self, "提示", "请输入名称")
            return
        if not path:
            QMessageBox.warning(self, "提示", "请填写路径或点击「浏览」选择")
            return

        if not self._warn_too_many(1):
            return

        # 根据类型构造 AppEntry
        if idx == 0:
            # 应用程序
            entry = AppEntry(
                name=name, path=path,
                arguments=args_edit.text().strip(),
                working_dir=workdir_edit.text().strip(),
                url=url_edit.text().strip(),
                is_uwp=("!" in path and not os.path.exists(path)),
            )
        elif idx == 1:
            # 文件夹
            entry = AppEntry(
                name=name, path=path,
                url=url_edit.text().strip(),
                is_folder=True,
            )
        else:
            # 文件
            entry = AppEntry(
                name=name, path=path,
                url=url_edit.text().strip(),
                is_file=True,
            )

        if self.config.add_entry(self._current_group_name, entry):
            self.config.load()
            self._refresh_entry_table()
            self._refresh_group_list()
            type_label = self._ENTRY_TYPES[idx][0]
            self.status_bar.showMessage(f"已添加{type_label}「{name}」到「{self._current_group_name}」")
        else:
            QMessageBox.warning(self, "提示", "添加失败，可能已存在同名同路径的条目")

    def _browse_path(self, path_edit: QLineEdit, name_edit: QLineEdit, type_idx: int):
        """根据类型弹出文件或文件夹选择框"""
        if type_idx == 1:
            # 文件夹
            folder = QFileDialog.getExistingDirectory(
                self, "选择文件夹",
                path_edit.text() or os.path.expanduser("~"),
            )
            if folder:
                path_edit.setText(folder)
                if not name_edit.text().strip():
                    name_edit.setText(os.path.basename(folder))
        else:
            # 应用程序 / 文件
            if type_idx == 0:
                title = "选择可执行文件"
                filter_str = "可执行文件 (*.exe);;所有文件 (*.*)"
                default_dir = path_edit.text() or os.environ.get("ProgramFiles", "C:\\")
            else:
                title = "选择文件"
                filter_str = "所有文件 (*.*)"
                default_dir = path_edit.text() or os.path.expanduser("~")
            file_path, _ = QFileDialog.getOpenFileName(
                self, title, default_dir, filter_str,
            )
            if file_path:
                path_edit.setText(file_path)
                if not name_edit.text().strip():
                    name_edit.setText(os.path.splitext(os.path.basename(file_path))[0])

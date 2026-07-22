# BatchGo — 批量应用启动工具

Windows 系统托盘驻留工具，一键批量启动自定义应用组合。
支持浏览器 + 网址一键打开。

## 功能

- 🔍 **双源扫描** — Start Menu .lnk + Shell.AppsFolder COM，覆盖桌面应用和 UWP
- 📁 **自定义组合** — 创建应用分组（如"上班""游戏"），每个分组可添加任意应用
- ✏️ **手动添加** — 扫不到的应用支持自定义路径 + 浏览选择 exe
- ⌨ **全局热键** — `Ctrl+Alt+E` 呼出分组菜单，可自定义，无需鼠标
- 🚀 **一键启动** — 左键托盘图标 → 选择分组 → 全部启动
- 🌐 **浏览器+网址** — 支持为每个应用设置启动时打开的 URL
- ⚙ **配置面板** — 可视化管理分组和应用
- 🔄 **开机自启** — 默认开启，右键托盘可切换
- 📌 **托盘驻留** — 隐藏在右下角系统托盘，不占任务栏空间
- 🏷 **智能分类** — 自动区分"常用应用"和"系统工具"，搜索时自动跳 Tab
- ⚠️ **数量警告** — 单个组合超过 10 个应用时弹窗确认

## 技术栈

| 层面 | 选择 |
|------|------|
| GUI | PySide6 (Qt for Python) |
| 应用扫描 | pywin32 COM — .lnk 解析 + Shell.AppsFolder 枚举 |
| 图标提取 | QFileIconProvider |
| 配置存储 | JSON (`%APPDATA%/BatchGo/config.json`) |
| 打包 | PyInstaller → 单文件 .exe |

## 快速开始

### 开发环境运行

```bash
# 安装依赖
pip install PySide6 pywin32

# 运行
python main.py
```

### 打包为 exe

```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包
build.bat

# 输出: dist/BatchGo.exe
```

## 使用说明

| 操作 | 说明 |
|------|------|
| **左键托盘** | 弹出分组菜单（右上角），点击批量启动 |
| **右键托盘** | 配置面板 / 开机自启 / 日志 / 退出 |
| **新建组合** | 右键 → 配置面板 → 左侧 [+新建] |
| **添加应用** | 选中分组 → 下方列表多选 → [添加到当前组合] |
| **全局热键** | `Ctrl+Alt+E` 呼出分组菜单，可在配置面板自定义 |
| **手动添加** | 选中分组 → 下方 [自定义添加] → 填写或浏览 exe |
| **设置网址** | 表格中双击 URL 列，输入完整网址 |
| **重新扫描** | 配置面板右上角 [🔄 重新扫描] |

## 项目结构

```
BatchGo/
├── main.py            # 入口：QApplication + 系统托盘 + 菜单
├── scanner.py         # 应用扫描：.lnk + Shell.AppsFolder 双源
├── config_manager.py  # 配置读写：分组 JSON 持久化
├── config_dialog.py   # 配置面板 UI
├── launcher.py        # 启动引擎：批量启动 + URL + 异常兜底
├── icon_utils.py      # 图标提取工具
├── requirements.txt   # PySide6 + pywin32
├── build.bat          # PyInstaller 打包脚本
└── README.md          # 本文件
```


# BatchGo — 批量应用启动工具

Windows 系统托盘驻留工具，一键批量启动自定义应用组合。
支持浏览器 + 网址一键打开。

## 功能

- 🔍 **自动扫描** — 遍历 Start Menu 提取已安装应用（首次自动扫描，后续读缓存秒开）
- 📁 **自定义组合** — 创建应用分组（如"工作""娱乐""开发"），每个分组可添加任意应用
- 🚀 **一键启动** — 左键托盘图标 → 选择分组 → 全部启动
- 🌐 **浏览器+网址** — 支持为每个应用设置启动时打开的 URL
- ⚙ **配置面板** — 可视化管理分组和应用，拖拽式操作
- 🔄 **开机自启** — 右键托盘可切换，向 Startup 文件夹写入启动脚本
- 📌 **托盘驻留** — 隐藏在右下角系统托盘，不占任务栏空间
- 🏷 **智能分类** — 自动区分"常用应用"和"系统工具"，方便筛选
- 🖼 **应用图标** — 提取 exe 原生大图标，卡片式展示

## 技术栈

| 层面 | 选择 |
|------|------|
| GUI | PySide6 (Qt for Python) |
| 应用扫描 | pywin32 COM → 解析 .lnk 快捷方式 |
| 图标提取 | QFileIconProvider + win32gui.ExtractIconEx |
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
| **左键托盘** | 弹出分组菜单，点击即可批量启动 |
| **右键托盘** | 配置面板 / 刷新应用 / 开机自启 / 退出 |
| **新建组合** | 右键 → 配置面板 → 左侧 [+新建] |
| **添加应用** | 选中分组 → 在下方列表多选 → [+添加到当前组合] |
| **设置网址** | 表格中双击 URL 列，输入完整网址 |

## 项目结构

```
BatchGo/
├── main.py            # 入口：QApplication + 系统托盘 + 菜单
├── scanner.py         # 应用扫描：解析 Start Menu .lnk
├── config_manager.py  # 配置读写：分组 JSON 持久化
├── config_dialog.py   # 配置面板 UI
├── launcher.py        # 启动引擎：批量启动 + URL 支持
├── icon_utils.py      # 图标提取工具
├── requirements.txt   # PySide6 + pywin32
├── build.bat          # PyInstaller 打包脚本
└── README.md          # 本文件
```

## TODO

- [ ] 应用列表支持大图标卡片视图
- [ ] 拖拽排序分组中的应用
- [ ] 导入/导出分组配置
- [ ] 快捷键全局唤起分组菜单
- [ ] 延迟启动（设置每个应用启动间隔）
- [ ] 分组启动前关闭已有同名进程
- [ ] 暗色模式适配
- [ ] 应用图标缓存（加速列表加载）

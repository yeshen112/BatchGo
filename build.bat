@echo off
chcp 65001 >nul
echo ========================================
echo   BatchGo 打包脚本
echo ========================================
echo.

:: 检查 PyInstaller
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Installing PyInstaller...
    pip install pyinstaller
)

echo [*] Generating icon...
python -c "from main import create_tray_icon; pixmap = create_tray_icon().pixmap(64,64); pixmap.save('app_icon.png')"

echo [*] Cleaning old builds...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

echo [*] Building...
pyinstaller --onefile --windowed --name BatchGo ^
    --add-data "scanner.py;." ^
    --add-data "config_manager.py;." ^
    --add-data "config_dialog.py;." ^
    --add-data "launcher.py;." ^
    --add-data "icon_utils.py;." ^
    --hidden-import win32com ^
    --hidden-import win32com.client ^
    --hidden-import pythoncom ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    main.py

echo.
echo [OK] Build complete! Output: dist\BatchGo.exe
echo.
echo To enable auto-start: Run BatchGo.exe, right-click tray icon, select auto-start.
pause

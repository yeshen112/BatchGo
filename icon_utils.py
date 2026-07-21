"""
图标提取工具 —— 从 .exe / .lnk 文件中提取图标。

使用 Windows 文件关联获取图标（QFileIconProvider）。
带内存缓存，同一路径只提取一次。
"""
import os
from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtCore import QFileInfo
from PySide6.QtWidgets import QFileIconProvider


# ── 图标缓存 ──────────────────────────────────────────────────────
_icon_cache: dict[str, QIcon] = {}
_provider: Optional[QFileIconProvider] = None


def _get_provider() -> QFileIconProvider:
    """获取或创建全局图标提供器（单例）"""
    global _provider
    if _provider is None:
        _provider = QFileIconProvider()
    return _provider


def clear_cache():
    """清空图标缓存"""
    _icon_cache.clear()


def get_app_icon(file_path: str, size: int = 32) -> QIcon:
    """
    获取应用的图标（从 Windows 文件关联提取）。

    Args:
        file_path: 可执行文件路径（也支持 .lnk）
        size: 期望尺寸（用于缓存 key 区分）

    Returns:
        QIcon 对象，提取失败时返回空 QIcon
    """
    if not file_path:
        return QIcon()

    # 对于 .lnk 文件，暂时返回空图标
    # QFileIconProvider 对 .lnk 返回的是快捷方式图标而不是目标图标
    # 实际使用中我们用 app.path 而不是 .lnk 路径
    if file_path.lower().endswith(".lnk"):
        return QIcon()

    cache_key = f"{file_path}@{size}"
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    try:
        if os.path.isfile(file_path):
            fi = QFileInfo(file_path)
            icon = _get_provider().icon(fi)
            _icon_cache[cache_key] = icon
            return icon
    except Exception:
        pass

    empty = QIcon()
    _icon_cache[cache_key] = empty
    return empty

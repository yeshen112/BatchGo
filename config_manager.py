"""
配置管理模块 —— 管理应用分组、配置文件的读写。
"""
import os
import json
from dataclasses import dataclass, field
from typing import Optional

from scanner import AppInfo


# ── 数据模型 ──────────────────────────────────────────────────────

@dataclass
class AppEntry:
    """分组中的一个应用条目"""
    name: str = ""              # 应用名称
    path: str = ""              # .exe 路径 或 UWP AUMID
    arguments: str = ""         # 额外启动参数
    working_dir: str = ""       # 工作目录
    url: str = ""               # 浏览器打开时附加的网址
    is_uwp: bool = False        # 是否为 UWP / 微软商店应用

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "arguments": self.arguments,
            "working_dir": self.working_dir,
            "url": self.url,
            "is_uwp": self.is_uwp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppEntry":
        return cls(
            name=d.get("name", ""),
            path=d.get("path", ""),
            arguments=d.get("arguments", ""),
            working_dir=d.get("working_dir", ""),
            url=d.get("url", ""),
            is_uwp=d.get("is_uwp", False),
        )

    @classmethod
    def from_app_info(cls, info: AppInfo, url: str = "") -> "AppEntry":
        """从 AppInfo 创建一个条目"""
        return cls(
            name=info.name,
            path=info.path,
            arguments=info.arguments,
            working_dir=info.working_dir,
            url=url,
            is_uwp=info.is_uwp,
        )


@dataclass
class AppGroup:
    """一个应用组合（分组）"""
    name: str = ""
    entries: list[AppEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppGroup":
        return cls(
            name=d.get("name", ""),
            entries=[AppEntry.from_dict(e) for e in d.get("entries", [])],
        )


# ── 配置管理器 ────────────────────────────────────────────────────

class ConfigManager:
    """配置文件的读写管理"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
            config_dir = os.path.join(appdata, "BatchGo")
            config_path = os.path.join(config_dir, "config.json")
        self.config_path = config_path
        self.config_dir = os.path.dirname(config_path)
        self._data: dict = {}

    # ── 底层读写 ──────────────────────────────────────────────

    def load(self):
        """从文件读取配置；文件不存在时创建默认配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        # 确保关键字段存在
        self._data.setdefault("groups", [])
        self._data.setdefault("apps_cache", [])
        self._data.setdefault("auto_start", True)

    def save(self):
        """将当前配置写入文件"""
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ── 分组操作 ──────────────────────────────────────────────

    def get_groups(self) -> list[AppGroup]:
        """获取所有分组"""
        return [AppGroup.from_dict(g) for g in self._data.get("groups", [])]

    def get_group(self, name: str) -> Optional[AppGroup]:
        """按名称获取分组"""
        for g in self._data.get("groups", []):
            if g["name"] == name:
                return AppGroup.from_dict(g)
        return None

    def add_group(self, name: str) -> bool:
        """添加新分组，重名返回 False"""
        if self.get_group(name) is not None:
            return False
        self._data["groups"].append({"name": name, "entries": []})
        self.save()
        return True

    def remove_group(self, name: str) -> bool:
        """删除分组"""
        groups = self._data["groups"]
        for i, g in enumerate(groups):
            if g["name"] == name:
                groups.pop(i)
                self.save()
                return True
        return False

    def rename_group(self, old_name: str, new_name: str) -> bool:
        """重命名分组"""
        if old_name == new_name:
            return True
        if self.get_group(new_name) is not None:
            return False  # 新名称已存在
        group = self.get_group(old_name)
        if group is None:
            return False
        group.name = new_name
        # 更新原始数据
        for g in self._data["groups"]:
            if g["name"] == old_name:
                g["name"] = new_name
                self.save()
                return True
        return False

    # ── 应用条目操作 ──────────────────────────────────────────

    def add_entry(self, group_name: str, entry: AppEntry) -> bool:
        """向分组添加一个应用条目"""
        for g in self._data["groups"]:
            if g["name"] == group_name:
                # 检查重复
                for e in g["entries"]:
                    if e["path"] == entry.path and e["name"] == entry.name:
                        return False
                g["entries"].append(entry.to_dict())
                self.save()
                return True
        return False

    def remove_entry(self, group_name: str, entry_index: int) -> bool:
        """从分组删除指定索引的应用条目"""
        for g in self._data["groups"]:
            if g["name"] == group_name:
                if 0 <= entry_index < len(g["entries"]):
                    g["entries"].pop(entry_index)
                    self.save()
                    return True
        return False

    def update_entry_url(self, group_name: str, entry_index: int, url: str) -> bool:
        """更新某个条目的 URL"""
        for g in self._data["groups"]:
            if g["name"] == group_name:
                if 0 <= entry_index < len(g["entries"]):
                    g["entries"][entry_index]["url"] = url
                    self.save()
                    return True
        return False

    def update_entry(self, group_name: str, entry_index: int, entry: AppEntry) -> bool:
        """更新某个条目的全部字段"""
        for g in self._data["groups"]:
            if g["name"] == group_name:
                if 0 <= entry_index < len(g["entries"]):
                    g["entries"][entry_index] = entry.to_dict()
                    self.save()
                    return True
        return False

    # ── 应用缓存 ──────────────────────────────────────────────

    def get_cached_apps(self) -> list[AppInfo]:
        """获取缓存的应用列表"""
        return [AppInfo.from_dict(d) for d in self._data.get("apps_cache", [])]

    # ── 开机自启 ──────────────────────────────────────────────

    def get_auto_start(self) -> bool:
        return self._data.get("auto_start", False)

    def set_auto_start(self, enabled: bool):
        self._data["auto_start"] = enabled
        self.save()

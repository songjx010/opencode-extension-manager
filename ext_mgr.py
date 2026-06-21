#!/usr/bin/env python3
"""opencode 扩展管理器 — 通过 TUI 界面管理扩展符号链接"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List

GROUP_TO_TYPE = {
    "skills": "skill",
    "agents": "agent",
    "commands": "command",
    "plugins": "plugin",
}
TYPE_TO_GROUP = {v: k for k, v in GROUP_TO_TYPE.items()}


class Status:
    SUCCESS = "success"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    ERROR = "error"
    MISSING = "missing"
    BROKEN = "broken"
    UNEXPECTED = "unexpected"
    OK = "ok"


class Format:
    BOLD = "\\Zb"
    RED = "\\Z1"
    GREEN = "\\Z2"
    YELLOW = "\\Z3"
    BLUE = "\\Z4"
    MAGENTA = "\\Z5"
    RESET = "\\Zn"
    OK_MARK = "\\Zb\\Z2 OK \\Zn"
    WARN_MARK = "\\Zr !! \\ZR"


DEFAULT_TARGET_DIR = "~/.config/opencode"


def parse_depends(depends_list):
    ext_deps = []
    path_deps = []
    for item in depends_list:
        if isinstance(item, str):
            ext_deps.append(item)
        elif isinstance(item, dict):
            path_deps.append(item)
    return ext_deps, path_deps


@dataclass
class PathDep:
    """路径依赖（source→target 符号链接映射）。"""
    source: str
    target: str


@dataclass
class Extension:
    """单个扩展的领域模型。"""
    name: str
    type: str
    enabled: bool
    description: str
    ext_deps: List[str] = field(default_factory=list)
    path_deps: List[PathDep] = field(default_factory=list)
    visible: bool = True


@dataclass
class Config:
    """整体配置。extensions 为扁平 dict[name -> Extension]。"""
    version: int
    extensions: Dict[str, Extension]
    warnings: List[str] = field(default_factory=list)
    extra: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeSet:
    """resolve_changes 的不可变返回值。"""
    to_enable: List[str]
    to_disable: List[str]
    cascade_disabled: List[str]
    rejected: List[Dict]


class ConfigError(Exception):
    pass


class ConfigManager:
    def __init__(self, config_path: str):
        self._config_path = config_path

    def load(self) -> Config:
        if not os.path.isfile(self._config_path):
            raise ConfigError(f"配置文件 {self._config_path} 不存在")
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"JSON 解析失败: {e}")

        warnings = self._validate(raw)
        extensions = self._build_extensions(raw["extensions"])
        self._check_circular_deps(extensions)
        extra = {k: v for k, v in raw.items() if k not in ("version", "extensions")}
        return Config(
            version=raw["version"],
            extensions=extensions,
            warnings=warnings,
            extra=extra,
        )

    def save(self, config: Config) -> None:
        nested = {group: {} for group in TYPE_TO_GROUP.values()}
        for name, ext in config.extensions.items():
            group = TYPE_TO_GROUP.get(ext.type)
            if group is None:
                continue
            ext_data = {"enabled": ext.enabled}
            if not ext.visible:
                ext_data["visible"] = False
            ext_data["description"] = ext.description
            deps = list(ext.ext_deps) + [
                {"source": p.source, "target": p.target} for p in ext.path_deps
            ]
            if deps:
                ext_data["depends"] = deps
            nested[group][name] = ext_data

        data = dict(config.extra)
        data["version"] = config.version
        data["extensions"] = nested
        content = json.dumps(data, indent=2, ensure_ascii=False)

        dir_name = os.path.dirname(self._config_path) or "."
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._config_path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _build_extensions(self, raw_groups) -> Dict[str, Extension]:
        flat = {}
        for group, exts_in_group in raw_groups.items():
            if not isinstance(exts_in_group, dict):
                continue
            ext_type = GROUP_TO_TYPE[group]
            for name, attrs in exts_in_group.items():
                ext_deps, path_deps = parse_depends(attrs.get("depends", []))
                flat[name] = Extension(
                    name=name,
                    type=ext_type,
                    enabled=attrs.get("enabled", False),
                    description=attrs.get("description", ""),
                    ext_deps=ext_deps,
                    path_deps=[PathDep(p["source"], p["target"]) for p in path_deps],
                    visible=attrs.get("visible", True),
                )
        return flat

    def _validate(self, raw: dict) -> list:
        errors = []
        warnings = []

        if "version" not in raw:
            raise ConfigError("缺少 version 字段")
        if raw["version"] != 3:
            raise ConfigError(f"不支持的 version: {raw['version']}")
        if "extensions" not in raw:
            raise ConfigError("缺少 extensions 字段")
        if not isinstance(raw["extensions"], dict):
            raise ConfigError("extensions 必须为对象")

        raw_groups = raw["extensions"]
        for group in raw_groups:
            if group not in GROUP_TO_TYPE:
                errors.append(
                    f"未知的扩展分类 '{group}'，"
                    f"必须为 {', '.join(GROUP_TO_TYPE.keys())}"
                )
        if errors:
            raise ConfigError("; ".join(errors))

        all_names = set()
        for group, exts_in_group in raw_groups.items():
            if not isinstance(exts_in_group, dict):
                errors.append(f"分类 '{group}' 必须为对象")
                continue
            for name, attrs in exts_in_group.items():
                if not isinstance(attrs, dict):
                    errors.append(f"扩展 '{name}' 必须为对象")
                    continue
                if name in all_names:
                    errors.append(f"扩展名 '{name}' 在多个分类中重复")
                    continue
                all_names.add(name)
        if errors:
            raise ConfigError("; ".join(errors))

        for group, exts_in_group in raw_groups.items():
            for name, attrs in exts_in_group.items():
                if not isinstance(attrs, dict):
                    continue
                if "enabled" not in attrs:
                    errors.append(f"扩展 '{name}' 缺少 enabled 字段")
                if "description" not in attrs:
                    errors.append(f"扩展 '{name}' 缺少 description 字段")
                vis = attrs.get("visible")
                if vis is not None and not isinstance(vis, bool):
                    errors.append(f"扩展 '{name}' 的 visible 必须为布尔值")
                if "/" in name:
                    errors.append(f"扩展键名 '{name}' 格式错误，应为纯名称（不含 /）")
                if ".." in name:
                    errors.append(f"扩展名称 '{name}' 包含非法字符 '..'")
                if name.startswith("/"):
                    errors.append(f"扩展名称 '{name}' 包含非法字符（绝对路径）")
                for dep in attrs.get("depends", []):
                    if isinstance(dep, str):
                        if not dep:
                            errors.append(f"扩展 '{name}' 的扩展依赖名称不能为空")
                        elif "/" in dep or ".." in dep or dep.startswith("/"):
                            errors.append(f"扩展 '{name}' 的扩展依赖 '{dep}' 格式错误")
                        elif dep not in all_names:
                            warnings.append(f"扩展 '{name}' 的依赖 '{dep}' 不存在")
                    elif isinstance(dep, dict):
                        if "source" not in dep or "target" not in dep:
                            errors.append(
                                f"扩展 '{name}' 的路径依赖缺少 source 或 target 字段"
                            )
                    else:
                        errors.append(
                            f"扩展 '{name}' 的依赖类型不合法: {type(dep).__name__}"
                        )
        if errors:
            raise ConfigError("; ".join(errors))

        return warnings

    def _check_circular_deps(self, exts: Dict[str, Extension]) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in exts}

        def dfs(name, path):
            color[name] = GRAY
            path.append(name)
            for dep in exts[name].ext_deps:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle_start = path.index(dep)
                    cycle = path[cycle_start:] + [dep]
                    raise ConfigError(f"循环依赖: {' → '.join(cycle)}")
                if color[dep] == WHITE:
                    dfs(dep, path)
            path.pop()
            color[name] = BLACK

        for name in exts:
            if color[name] == WHITE:
                dfs(name, [])


class DependencyGraph:
    """会话内单例的依赖邻接表（forward / reverse）。

    依赖结构在会话内不变；仅 ``enabled`` 状态可变，存于 extensions dict。
    由 ExtensionStore 构造一次。
    """

    def __init__(self, extensions):
        self._forward: Dict[str, set] = {}
        self._reverse: Dict[str, set] = {}
        for name in extensions:
            self._forward.setdefault(name, set())
            self._reverse.setdefault(name, set())
        for name, ext in extensions.items():
            for dep in ext.ext_deps:
                self._forward[name].add(dep)
                self._reverse.setdefault(dep, set()).add(name)

    def deps_of(self, name):
        return set(self._forward.get(name, set()))

    def dependents_of(self, name):
        return set(self._reverse.get(name, set()))

    def has_enabled_dependent(self, name, extensions):
        return any(
            extensions[d].enabled
            for d in self._reverse.get(name, set())
            if d in extensions
        )


class ExtensionStore:
    """扩展状态的唯一拥有者：持有 extensions dict 与会话内邻接表。

    封装所有领域操作（toggle / 级联 / 解析 / 可用性检查）。UI 与 I/O 层
    通过本类访问领域状态，不直接操作 extensions dict 或邻接结构。
    """

    def __init__(self, extensions, source_dir=""):
        self._extensions = extensions
        self._source_dir = source_dir
        self._graph = DependencyGraph(extensions)

    @property
    def extensions(self):
        return self._extensions

    def get(self, name):
        return self._extensions.get(name)

    def names(self):
        return list(self._extensions.keys())

    def by_type(self, ext_type):
        return [e for e in self._extensions.values() if e.type == ext_type]

    def set_enabled(self, name, enabled):
        if name in self._extensions:
            self._extensions[name].enabled = enabled

    def deps_of(self, name):
        return self._graph.deps_of(name)

    def dependents_of(self, name):
        return self._graph.dependents_of(name)

    def has_enabled_dependent(self, name):
        return self._graph.has_enabled_dependent(name, self._extensions)

    def check_availability(self, name):
        """返回缺失依赖列表（扩展依赖名 + 路径依赖 source）。"""
        missing = []
        ext = self._extensions.get(name)
        if ext is None:
            return missing
        for dep in ext.ext_deps:
            if dep not in self._extensions:
                missing.append(dep)
        for dep in ext.path_deps:
            source_path = os.path.join(self._source_dir, dep.source)
            if not os.path.exists(source_path):
                missing.append(dep.source)
        return missing

    def cascade_disable(self, seed):
        """前向级联：seed 刚被关掉 → BFS 其 forward deps，若某 dep 已无任何
        enabled dependent，则禁用它并入队继续。返回 seed+新级联 的完整集合。
        会改写 enabled 标志。"""
        disabled = set(seed)
        for name in seed:
            if name in self._extensions:
                self._extensions[name].enabled = False
        queue = list(seed)
        visited = set()
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            for dep in self._graph.deps_of(cur):
                ext = self._extensions.get(dep)
                if ext is None or not ext.enabled:
                    continue
                if not self._graph.has_enabled_dependent(dep, self._extensions):
                    ext.enabled = False
                    disabled.add(dep)
                    queue.append(dep)
        return disabled

    def resolve_changes(self, selected):
        """计算给定选择下的变更集，并同步 store 的 enabled 状态。

        - to_enable = selected ∪ 其 forward 依赖闭包
        - to_disable_all = 全部 − to_enable
        - rejected = to_disable_all 中仍被 to_enable 依赖者（不可禁）
        - actual_disable = to_disable_all − rejected
        - cascade_disabled = actual_disable 中 dependent 全部也在 actual_disable 者
        """
        selected_set = set(selected)
        to_enable = set(selected_set)
        for name in selected_set:
            self._collect_forward(name, to_enable)

        to_disable_all = set(self._extensions.keys()) - to_enable

        rejected = []
        for name in sorted(to_disable_all):
            enabled_dependents = sorted(
                d for d in self._graph.dependents_of(name) if d in to_enable
            )
            if enabled_dependents:
                rejected.append({
                    "name": name,
                    "reason": "被依赖",
                    "dependents": enabled_dependents,
                })
        actual_disable = to_disable_all - {r["name"] for r in rejected}

        cascade_disabled = self._classify_for_display(actual_disable)
        explicit_disable = actual_disable - cascade_disabled

        for n in to_enable:
            if n in self._extensions:
                self._extensions[n].enabled = True
        for n in actual_disable:
            if n in self._extensions:
                self._extensions[n].enabled = False

        return ChangeSet(
            to_enable=sorted(to_enable),
            to_disable=sorted(explicit_disable),
            cascade_disabled=sorted(cascade_disabled),
            rejected=rejected,
        )

    def _collect_forward(self, name, collected):
        for dep in self._graph.deps_of(name):
            if dep in self._extensions and dep not in collected:
                collected.add(dep)
                self._collect_forward(dep, collected)

    def _classify_for_display(self, actual_disable):
        """actual_disable 中，所有 dependent 也都属于 actual_disable 的 name
        归入 cascade_disabled。纯查询，不改状态。"""
        cascade = set()
        changed = True
        while changed:
            changed = False
            for name in actual_disable - cascade:
                dependents = self._graph.dependents_of(name)
                if dependents and all(d in actual_disable for d in dependents):
                    cascade.add(name)
                    changed = True
        return cascade


class SymlinkManager:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def apply_changes(self, to_enable, to_disable, extensions):
        results = []
        for name in to_enable:
            results.extend(self.apply_for_extension(name, extensions, "create"))
        for name in to_disable:
            results.extend(self.apply_for_extension(name, extensions, "remove"))
        return results

    def apply_for_extension(self, ext_name, extensions, action):
        ext = extensions.get(ext_name)
        path_deps = ext.path_deps if ext else []
        results = []
        for dep in path_deps:
            if action == "create":
                results.append(self._create_symlink(dep.source, dep.target))
            else:
                results.append(self._remove_symlink(dep.source, dep.target))
        if not path_deps:
            results.append(
                {"name": ext_name, "status": Status.SKIPPED, "detail": "无路径依赖"}
            )
        return results

    def _create_symlink(self, source_rel, target_rel):
        source = os.path.join(self._source_dir, source_rel)
        target = os.path.join(self._target_dir, target_rel)
        self._ensure_subdir(os.path.dirname(target))
        if os.path.islink(target):
            existing = os.readlink(target)
            if os.path.abspath(existing) == os.path.abspath(source):
                return {"name": target_rel, "status": Status.SKIPPED, "detail": ""}
            return {"name": target_rel, "status": Status.CONFLICT,
                    "detail": f"符号链接已指向 {existing}"}
        if os.path.exists(target):
            return {"name": target_rel, "status": Status.CONFLICT,
                    "detail": f"目标路径 {target} 已存在"}
        try:
            os.symlink(source, target)
            return {"name": target_rel, "status": Status.SUCCESS, "detail": ""}
        except OSError as e:
            return {"name": target_rel, "status": Status.ERROR, "detail": str(e)}

    def _remove_symlink(self, source_rel, target_rel):
        source = os.path.join(self._source_dir, source_rel)
        target = os.path.join(self._target_dir, target_rel)
        if not os.path.islink(target):
            if not os.path.exists(target):
                return {"name": target_rel, "status": Status.SKIPPED, "detail": ""}
            return {"name": target_rel, "status": Status.CONFLICT,
                    "detail": f"目标路径 {target} 存在但非符号链接"}
        existing = os.readlink(target)
        if os.path.abspath(existing) != os.path.abspath(source):
            return {"name": target_rel, "status": Status.CONFLICT,
                    "detail": f"符号链接指向 {existing}，非预期目标"}
        try:
            os.unlink(target)
            return {"name": target_rel, "status": Status.SUCCESS, "detail": ""}
        except OSError as e:
            return {"name": target_rel, "status": Status.ERROR, "detail": str(e)}

    def _ensure_subdir(self, dir_path):
        os.makedirs(dir_path, exist_ok=True)


class Validator:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def validate(self, extensions):
        results = []
        if not os.path.isdir(self._target_dir):
            for name, ext in extensions.items():
                if ext.enabled and ext.path_deps:
                    results.append({"name": name, "status": Status.MISSING,
                                    "detail": "目标目录不存在"})
            return results

        for name, ext in extensions.items():
            if ext.enabled:
                for dep in ext.path_deps:
                    target = os.path.join(self._target_dir, dep.target)
                    source = os.path.join(self._source_dir, dep.source)
                    if not os.path.islink(target):
                        results.append({"name": f"{name}:{dep.target}",
                                        "status": Status.MISSING,
                                        "detail": "符号链接缺失"})
                    else:
                        actual = os.readlink(target)
                        if os.path.abspath(actual) != os.path.abspath(source):
                            results.append({"name": f"{name}:{dep.target}",
                                            "status": Status.BROKEN,
                                            "detail": f"指向错误目标: {actual}"})
            else:
                for dep in ext.path_deps:
                    target = os.path.join(self._target_dir, dep.target)
                    if os.path.islink(target):
                        results.append({"name": f"{name}:{dep.target}",
                                        "status": Status.UNEXPECTED,
                                        "detail": "已禁用但符号链接仍存在"})

        if not results:
            results.append({"name": "", "status": Status.OK, "detail": "所有扩展状态正常"})
        return results


class DialogAdapter:
    @staticmethod
    def check_available() -> bool:
        return shutil.which("dialog") is not None

    @staticmethod
    def _term_size():
        try:
            cols = int(subprocess.run(
                ["tput", "cols"], capture_output=True, text=True
            ).stdout.strip())
            lines = int(subprocess.run(
                ["tput", "lines"], capture_output=True, text=True
            ).stdout.strip())
            return max(lines, 24), max(cols, 80)
        except (ValueError, FileNotFoundError):
            return 24, 80

    def run_menu(self, title, items):
        term_h, term_w = DialogAdapter._term_size()
        h = min(len(items) + 8, max(term_h - 4, 20))
        w = max(term_w - 10, 70)
        menu_h = min(len(items) + 2, h - 8)
        y = max((term_h - h) // 2, 0)
        x = max((term_w - w) // 2, 0)
        args = ["dialog", "--stdout", "--colors", "--begin", str(y), str(x),
                "--menu", title, str(h), str(w), str(menu_h)]
        for tag, text in items:
            args.extend([tag, text])
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            if result.returncode == 0:
                return 0, result.stdout.strip()
            return result.returncode, ""
        except FileNotFoundError:
            return -1, ""

    def run_checklist(self, title, items, unavailable=None):
        unavailable = unavailable or set()
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        list_h = min(len(items) + 2, h - 8)
        args = ["dialog", "--stdout", "--item-help", "--colors",
                "--checklist", title, str(h), str(w), str(list_h)]
        for tag, status, text, help_text in items:
            args.extend([tag, text, "on" if status else "off", help_text])
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            if result.returncode == 0:
                raw = result.stdout.strip()
                selected = [s.strip('"') for s in raw.split()] if raw else []
                invalid = [s for s in selected if s in unavailable]
                return 0, selected, invalid
            return result.returncode, [], []
        except FileNotFoundError:
            return -1, [], []

    def run_inputbox(self, title, default=""):
        _, term_w = DialogAdapter._term_size()
        w = max(term_w - 10, 60)
        args = ["dialog", "--stdout", "--inputbox", title, "8", str(w), default]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            if result.returncode == 0:
                return 0, result.stdout.strip()
            return result.returncode, ""
        except FileNotFoundError:
            return -1, ""

    def run_msgbox(self, title, text):
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--colors", "--msgbox", text, str(h), str(w)]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            return result.returncode
        except FileNotFoundError:
            return -1

    def run_yesno(self, title, text):
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--colors", "--yesno", text, str(h), str(w)]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            return result.returncode
        except FileNotFoundError:
            return -1


class DialogUI:
    TYPES_LABELS = {
        "skill": "Skills  — 技能扩展",
        "agent": "Agents — 智能体",
        "command": "Commands — 命令编排",
        "plugin": "Plugins — 插件扩展",
    }
    TYPES_ORDER = ["skill", "agent", "command", "plugin"]

    def __init__(self, adapter, store, config_manager):
        self._adapter = adapter
        self._store = store
        self._config = config_manager
        self._target_dir = os.path.expanduser(DEFAULT_TARGET_DIR)

    @staticmethod
    def _visible_len(s):
        return len(re.sub(r'\\Z[b0-7nrR]', '', s))

    def _pad_label(self, label, width):
        return label + " " * max(width - self._visible_len(label), 1)

    def ask_target_dir(self):
        while True:
            code, value = self._adapter.run_inputbox("目标目录", self._target_dir)
            if code != 0:
                return "cancel"
            if value.strip():
                self._target_dir = value.strip()
                return self._target_dir
            self._adapter.run_msgbox("错误", "目标目录不能为空")

    def _build_checklist_items(self, ext_type):
        items = []
        unavailable = set()
        for ext in self._store.by_type(ext_type):
            if not ext.visible:
                continue
            missing = self._store.check_availability(ext.name)
            if missing:
                unavailable.add(ext.name)
                mark = Format.WARN_MARK
                help_text = "缺失依赖: " + ", ".join(missing)
            else:
                mark = Format.OK_MARK
                help_text = ext.description
            text = f"{mark} {ext.description}"
            items.append((ext.name, ext.enabled, text, help_text))
        return items, unavailable

    def _count_stats(self, ext_type):
        total = enabled = ok = 0
        for ext in self._store.by_type(ext_type):
            if not ext.visible:
                continue
            total += 1
            if ext.enabled:
                enabled += 1
            if not self._store.check_availability(ext.name):
                ok += 1
        return total, enabled, ok

    def show_extension_list(self):
        while True:
            menu_items = []
            max_label_w = 0
            stats_per_type = {}
            for t in self.TYPES_ORDER:
                total, enabled, ok = self._count_stats(t)
                stats_per_type[t] = (total, enabled, ok)
                if total > 0:
                    max_label_w = max(max_label_w,
                                      self._visible_len(self.TYPES_LABELS.get(t, t)))
            for t in self.TYPES_ORDER:
                total, enabled, ok = stats_per_type[t]
                if total == 0:
                    continue
                label = self._pad_label(self.TYPES_LABELS.get(t, t), max_label_w)
                stats = (f"\t\\Zb\\Z1{enabled}/{total} 启用\\Zn"
                         f"\t\\Zb\\Z5{ok}/{total} 可用\\Zn")
                menu_items.append((t, label + stats))
            menu_items.append(("apply", "\\Zb\\Z2确认并应用变更\\Zn"))
            menu_items.append(("quit", "退出"))

            code, choice = self._adapter.run_menu("扩展管理", menu_items)
            if code != 0 or choice == "quit":
                return "cancel", []
            if choice == "apply":
                return "ok", [n for n, e in self._store.extensions.items() if e.enabled]
            if choice in self.TYPES_ORDER:
                action = self._show_type_checklist(choice)
                if action == "apply":
                    return "ok", [n for n, e in self._store.extensions.items() if e.enabled]

    def _show_type_checklist(self, ext_type):
        items, unavailable = self._build_checklist_items(ext_type)
        if not items:
            self._adapter.run_msgbox("提示", "该分类下没有扩展")
            return "back"
        while True:
            label = self.TYPES_LABELS.get(ext_type, ext_type)
            title = f"{label}  (OK=齐全  !!=缺失,不可选)"
            code, selected, invalid = self._adapter.run_checklist(title, items, unavailable)
            if code != 0:
                return "back"
            if invalid:
                self._adapter.run_msgbox(
                    "错误",
                    "以下扩展文件不完整，无法启用:\n\n"
                    + "\n".join(f"  - {n}" for n in invalid)
                    + "\n\n请取消勾选后重试",
                )
                continue

            newly_disabled = set()
            for ext in self._store.by_type(ext_type):
                if not ext.visible:
                    continue
                was_enabled = ext.enabled
                now_enabled = ext.name in selected
                self._store.set_enabled(ext.name, now_enabled)
                if was_enabled and not now_enabled:
                    newly_disabled.add(ext.name)

            self._store.cascade_disable(newly_disabled)
            items, unavailable = self._build_checklist_items(ext_type)
            return "back"

    def show_change_summary(self, changes):
        lines = ["\\Zb\\Z4变更摘要:\\Zn\n"]
        if changes.to_enable:
            lines.append("\\Zb\\Z5启用:\\Zn")
            for n in changes.to_enable:
                lines.append(f"  + {n}")
        if changes.to_disable:
            lines.append("\n\\Zb\\Z1禁用:\\Zn")
            for n in changes.to_disable:
                lines.append(f"  - {n}")
        if changes.cascade_disabled:
            lines.append("\n\\Zb\\Z3级联禁用:\\Zn")
            for n in changes.cascade_disabled:
                lines.append(f"  ~ {n}")
        if changes.rejected:
            for r in changes.rejected:
                lines.append(
                    f"拒绝禁用 {r['name']}: {r['reason']} "
                    f"({', '.join(r.get('dependents', []))})"
                )
        return self._adapter.run_yesno("确认", "\n".join(lines)) == 0

    def show_results(self, results):
        ok_status = {Status.SUCCESS, Status.OK}
        skip_status = {Status.SKIPPED}
        groups = {"ok": [], "fail": [], "skip": []}
        for r in results:
            if r["status"] in ok_status:
                groups["ok"].append(r)
            elif r["status"] in skip_status:
                groups["skip"].append(r)
            else:
                groups["fail"].append(r)
        ordered = groups["ok"] + groups["fail"] + groups["skip"]
        max_name_w = max((len(r["name"]) for r in ordered if r["name"]), default=0)
        lines = []
        for key in ("ok", "fail", "skip"):
            if lines and groups[key]:
                lines.append("")
            for r in groups[key]:
                name = (r["name"] or "(全部)").ljust(max_name_w)
                if r["status"] in ok_status:
                    color = "\\Zb\\Z4"
                elif r["status"] in skip_status:
                    color = "\\Zb\\Z5"
                else:
                    color = "\\Zb\\Z1"
                lines.append(f"{name}\t{color}{r['status']}\\Zn")
        self._adapter.run_msgbox("操作结果", "\n".join(lines))

    def show_error(self, message):
        self._adapter.run_msgbox("错误", message)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if not DialogAdapter.check_available():
        print("错误: dialog 工具未安装，请先安装 dialog", file=sys.stderr)
        sys.exit(1)

    config_mgr = ConfigManager(os.path.join(script_dir, "extensions.json"))
    try:
        config = config_mgr.load()
    except ConfigError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    store = ExtensionStore(config.extensions, source_dir=script_dir)
    ui = DialogUI(DialogAdapter(), store, config_mgr)

    target = ui.ask_target_dir()
    if target == "cancel":
        sys.exit(0)

    symlink_mgr = SymlinkManager(script_dir, target)

    while True:
        action, selected = ui.show_extension_list()
        if action == "cancel":
            break

        changes = store.resolve_changes(selected)

        if changes.rejected:
            for r in changes.rejected:
                ui.show_error(
                    f"扩展 {r['name']} 被以下已选择扩展依赖: "
                    f"{', '.join(r.get('dependents', []))}"
                )
            continue

        if not changes.to_enable and not changes.to_disable:
            ui.show_error("无变更")
            continue

        if not ui.show_change_summary(changes):
            continue

        all_disable = changes.to_disable + changes.cascade_disabled
        results = symlink_mgr.apply_changes(
            changes.to_enable, all_disable, store.extensions
        )
        ui.show_results(results)

        try:
            config_mgr.save(config)
        except Exception as e:
            ui.show_error(f"配置文件写入失败: {e}")


if __name__ == "__main__":
    main()

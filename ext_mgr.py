#!/usr/bin/env python3
"""opencode 扩展管理器 — 通过 TUI 界面管理扩展符号链接"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from typing import Dict, List

log = logging.getLogger("ext_mgr")


def setup_logging(log_dir: str) -> None:
    handler = RotatingFileHandler(
        os.path.join(log_dir, "ext_mgr.log"),
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger = logging.getLogger("ext_mgr")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(handler)

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

_WIN_DRIVE_RE = re.compile(r'^([A-Za-z]):[\\/](.*)')


def _to_posix_drive_path(path):
    """Convert a Windows drive path (``C:\\...`` or ``C:/...``) to the POSIX
    form (``/C/...``) recognized by Git Bash / MSYS2. Already-POSIX paths and
    non-drive paths are returned unchanged; the conversion is idempotent.
    """
    m = _WIN_DRIVE_RE.match(path)
    if m:
        return "/" + m.group(1).upper() + "/" + m.group(2).replace("\\", "/")
    return path


def resolve_target_dir(path):
    """Expand ~ and convert Windows drive paths to POSIX form
    (e.g. ``C:\\Users\\name/.config/opencode`` -> ``/C/Users/name/.config/opencode``),
    the form recognized by Git Bash / MSYS2. Conversion is unconditional so the
    result is consistent regardless of the host Python's os.name.
    """
    result = _to_posix_drive_path(os.path.expanduser(path))
    log.debug("resolve_target_dir: %r -> %r", path, result)
    return result


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
        log.info("Loading config from %s", self._config_path)
        if not os.path.isfile(self._config_path):
            log.error("Config file does not exist: %s", self._config_path)
            raise ConfigError(f"Config file {self._config_path} does not exist")
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            log.error("JSON parsing failed for %s: %s", self._config_path, e)
            raise ConfigError(f"JSON parsing failed: {e}")

        warnings = self._validate(raw)
        extensions = self._build_extensions(raw["extensions"])
        self._check_circular_deps(extensions)
        extra = {k: v for k, v in raw.items() if k not in ("version", "extensions")}
        log.info(
            "Config loaded: version=%s, extensions=%d", raw["version"], len(extensions)
        )
        return Config(
            version=raw["version"],
            extensions=extensions,
            warnings=warnings,
            extra=extra,
        )

    def save(self, config: Config) -> None:
        log.info("Saving config to %s", self._config_path)
        nested = {group: {} for group in TYPE_TO_GROUP.values()}
        for name, ext in config.extensions.items():
            group = TYPE_TO_GROUP.get(ext.type)
            if group is None:
                continue
            ext_data = {"enabled": ext.enabled, "visible": ext.visible}
            ext_data["description"] = ext.description
            dep_strs = []
            for dep_name in ext.ext_deps:
                dep_ext = config.extensions.get(dep_name)
                if dep_ext is not None and dep_ext.type in TYPE_TO_GROUP:
                    dep_strs.append(f"{TYPE_TO_GROUP[dep_ext.type]}/{dep_name}")
                else:
                    dep_strs.append(dep_name)
            deps = dep_strs + [
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
            log.info("Config saved successfully")
        except Exception:
            log.exception("Failed to save config to %s", self._config_path)
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
                raw_ext_deps, path_deps = parse_depends(attrs.get("depends", []))
                ext_deps = [d.split("/", 1)[1] for d in raw_ext_deps]
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
            raise ConfigError("Missing 'version' field")
        if raw["version"] != 4:
            raise ConfigError(f"Unsupported version: {raw['version']}")
        if "extensions" not in raw:
            raise ConfigError("Missing 'extensions' field")
        if not isinstance(raw["extensions"], dict):
            raise ConfigError("'extensions' must be an object")

        raw_groups = raw["extensions"]
        for group in raw_groups:
            if group not in GROUP_TO_TYPE:
                errors.append(
                    f"Unknown extension category '{group}', "
                    f"must be one of {', '.join(GROUP_TO_TYPE.keys())}"
                )
        if errors:
            raise ConfigError("; ".join(errors))

        all_names = set()
        name_to_group = {}
        for group, exts_in_group in raw_groups.items():
            if not isinstance(exts_in_group, dict):
                errors.append(f"Category '{group}' must be an object")
                continue
            for name, attrs in exts_in_group.items():
                if not isinstance(attrs, dict):
                    errors.append(f"Extension '{name}' must be an object")
                    continue
                if name in all_names:
                    errors.append(f"Extension name '{name}' is duplicated across multiple categories")
                    continue
                all_names.add(name)
                name_to_group[name] = group
        if errors:
            raise ConfigError("; ".join(errors))

        for group, exts_in_group in raw_groups.items():
            for name, attrs in exts_in_group.items():
                if not isinstance(attrs, dict):
                    continue
                if "enabled" not in attrs:
                    errors.append(f"Extension '{name}' missing 'enabled' field")
                if "visible" not in attrs:
                    errors.append(f"Extension '{name}' missing 'visible' field")
                if "description" not in attrs:
                    errors.append(f"Extension '{name}' missing 'description' field")
                vis = attrs.get("visible")
                if vis is not None and not isinstance(vis, bool):
                    errors.append(f"Extension '{name}': 'visible' must be a boolean")
                if "/" in name:
                    errors.append(f"Extension key '{name}' has invalid format, expected a plain name (without '/')")
                if ".." in name:
                    errors.append(f"Extension name '{name}' contains illegal characters '..'")
                if name.startswith("/"):
                    errors.append(f"Extension name '{name}' contains illegal characters (absolute path)")
                for dep in attrs.get("depends", []):
                    if isinstance(dep, str):
                        if not dep:
                            errors.append(f"Extension '{name}' has an empty dependency name")
                            continue
                        if dep.count("/") != 1 or dep.startswith("/") or dep.endswith("/"):
                            errors.append(
                                f"Extension '{name}' dependency '{dep}' has invalid format, "
                                f"expected 'category/name' (e.g. agents/foo)"
                            )
                            continue
                        dep_group, dep_name = dep.split("/", 1)
                        if dep_group not in GROUP_TO_TYPE:
                            errors.append(
                                f"Extension '{name}' dependency '{dep}' has invalid category '{dep_group}', "
                                f"must be one of {', '.join(GROUP_TO_TYPE.keys())}"
                            )
                            continue
                        if ".." in dep_name:
                            errors.append(f"Extension '{name}' dependency '{dep}' name contains illegal characters '..'")
                            continue
                        if dep_name not in all_names:
                            errors.append(f"Extension '{name}' dependency '{dep}' does not exist")
                            continue
                        actual_group = name_to_group.get(dep_name)
                        if actual_group != dep_group:
                            errors.append(
                                f"Extension '{name}' dependency '{dep}' category mismatch, "
                                f"'{dep_name}' actually belongs to '{actual_group}'"
                            )
                    elif isinstance(dep, dict):
                        if "source" not in dep or "target" not in dep:
                            errors.append(
                                f"Extension '{name}' path dependency missing 'source' or 'target' field"
                            )
                    else:
                        errors.append(
                            f"Extension '{name}' has invalid dependency type: {type(dep).__name__}"
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
                    raise ConfigError(f"Circular dependency: {' → '.join(cycle)}")
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
                    "reason": "required by others",
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
        # target_dir usually comes from resolve_target_dir() already in Git Bash
        # POSIX form (/C/..). On native Windows os.path.abspath corrupts such a
        # leading-'/' path (treats it as the current drive's root, e.g.
        # /C/Users -> C:\C\Users), so preserve already-POSIX values verbatim.
        if os.name == "nt" and target_dir.startswith("/") and not target_dir.startswith("//"):
            self._target_dir = target_dir
        else:
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
                {"name": ext_name, "status": Status.SKIPPED, "detail": "no path dependencies"}
            )
        return results

    def _create_symlink(self, source_rel, target_rel):
        source = os.path.join(self._source_dir, source_rel)
        target = os.path.join(self._target_dir, target_rel)
        if os.name == "nt":
            source = _to_posix_drive_path(source)
            target = _to_posix_drive_path(target)
        self._ensure_subdir(os.path.dirname(target))
        try:
            if os.path.islink(target) or os.path.exists(target):
                log.info("Removing existing path before (re)creating symlink: %s", target)
                self._remove_existing(target)
            os.symlink(source, target)
            log.info("Symlink (re)created: %s -> %s", target, source)
            return {"name": target_rel, "status": Status.SUCCESS, "detail": ""}
        except OSError as e:
            log.error("Failed to create symlink %s -> %s: %s", target, source, e)
            return {"name": target_rel, "status": Status.ERROR, "detail": str(e)}

    def _remove_symlink(self, source_rel, target_rel):
        target = os.path.join(self._target_dir, target_rel)
        if os.name == "nt":
            target = _to_posix_drive_path(target)
        if not os.path.islink(target) and not os.path.exists(target):
            log.debug("No symlink to remove: %s", target)
            return {"name": target_rel, "status": Status.SKIPPED, "detail": ""}
        try:
            self._remove_existing(target)
            log.info("Symlink removed: %s", target)
            return {"name": target_rel, "status": Status.SUCCESS, "detail": ""}
        except OSError as e:
            log.error("Failed to remove symlink %s: %s", target, e)
            return {"name": target_rel, "status": Status.ERROR, "detail": str(e)}

    @staticmethod
    def _remove_existing(target):
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)

    def _ensure_subdir(self, dir_path):
        os.makedirs(dir_path, exist_ok=True)


class NpmDependencyManager:
    """使能 plugin 扩展时，在其 source 目录的 package.json 所在处执行 npm install。"""

    NPM_INSTALL_TIMEOUT = 300
    MAX_INSTALL_RETRIES = 3

    def __init__(self, source_dir: str):
        self._source_dir = os.path.abspath(source_dir)

    def install_for(self, to_enable, extensions, on_progress=None):
        """对 to_enable 中的 plugin 扩展，定位 source 目录下的 package.json，
        在该目录执行 npm install。返回 [{name, status, detail}] 结果列表。
        on_progress(pkg_dir) 在每个目录开始安装前被调用（用于 UI 进度提示）。

        npm install 退出码为 0 并不一定代表依赖真正写入磁盘，因此每次执行后
        会校验 node_modules；未通过校验或退出码非 0 时最多重试
        MAX_INSTALL_RETRIES 次。超时与意外异常不重试。

        Windows (os.name == 'nt') 下不执行 npm install，直接返回单条 SKIPPED。"""
        if os.name == "nt":
            log.info("Windows detected (os.name=nt); skipping npm install")
            return [{"name": "npm install", "status": Status.SKIPPED,
                     "detail": "skipped on Windows"}]
        install_dirs = self._collect_install_dirs(to_enable, extensions)
        if not install_dirs:
            return []
        npm_available = shutil.which("npm") is not None
        if not npm_available:
            log.warning("npm not found on PATH; dependency installation will be skipped")
        results = []
        for pkg_dir in install_dirs:
            disp = self._display_path(pkg_dir)
            if not npm_available:
                results.append({"name": disp, "status": Status.ERROR,
                                "detail": "npm not installed, skipping dependency installation"})
                continue
            if on_progress is not None:
                on_progress(pkg_dir)
            results.append(self._install_with_retry(pkg_dir, disp))
        return results

    def _install_with_retry(self, pkg_dir, disp):
        """对单个目录执行 npm install，并在退出码为 0 后校验是否真正安装成功；
        未安装成功（退出码非 0 或校验失败）则最多重试 MAX_INSTALL_RETRIES 次。
        超时与意外异常视为不可重试，立即返回错误。"""
        attempts = 1 + self.MAX_INSTALL_RETRIES
        last_detail = "npm install failed"
        for attempt in range(1, attempts + 1):
            log.info("npm install attempt %d/%d in %s", attempt, attempts, pkg_dir)
            try:
                proc = subprocess.run(
                    ["npm", "install"], cwd=pkg_dir,
                    capture_output=True, text=True,
                    timeout=self.NPM_INSTALL_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                log.error("npm install timed out in %s after %ds",
                          pkg_dir, self.NPM_INSTALL_TIMEOUT)
                return {"name": disp, "status": Status.ERROR,
                        "detail": "npm install timed out"}
            except Exception as e:
                log.exception("Unexpected error during npm install in %s", pkg_dir)
                return {"name": disp, "status": Status.ERROR, "detail": str(e)}
            if proc.returncode != 0:
                tail = (proc.stderr or "")[-500:]
                log.error("npm install failed in %s (rc=%d): %s",
                          pkg_dir, proc.returncode, tail.strip())
                last_detail = tail or "npm install failed (rc=%d)" % proc.returncode
                continue
            if self._verify_install(pkg_dir):
                log.info("npm install succeeded in %s on attempt %d", pkg_dir, attempt)
                return {"name": disp, "status": Status.SUCCESS, "detail": "npm install"}
            log.warning("npm install verification failed in %s (attempt %d)",
                        pkg_dir, attempt)
            last_detail = "npm install verification failed (missing dependencies)"
        log.error("npm install gave up in %s after %d attempts", pkg_dir, attempts)
        return {"name": disp, "status": Status.ERROR, "detail": last_detail}

    def _verify_install(self, pkg_dir):
        """校验 npm install 是否真正安装成功：package.json 中声明的
        dependencies / devDependencies 是否都存在于 node_modules 下。
        未声明依赖或无法读取 package.json 时视为通过（无可校验项）。"""
        pkg_json = os.path.join(pkg_dir, "package.json")
        try:
            with open(pkg_json, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log.warning("verify: cannot read package.json in %s; skip verification", pkg_dir)
            return True
        deps = {}
        deps.update(data.get("dependencies") or {})
        deps.update(data.get("devDependencies") or {})
        if not deps:
            return True
        node_modules = os.path.join(pkg_dir, "node_modules")
        for name in deps:
            parts = name.split("/") if name.startswith("@") else [name]
            if not os.path.isdir(os.path.join(node_modules, *parts)):
                log.warning("verify: missing dependency %s under %s", name, node_modules)
                return False
        return True

    def _collect_install_dirs(self, to_enable, extensions):
        """收集需执行 npm install 的唯一 package 目录（按发现顺序去重）。"""
        seen = []
        seen_set = set()
        for name in to_enable:
            ext = extensions.get(name)
            if ext is None or ext.type != "plugin":
                continue
            for dep in ext.path_deps:
                pkg_dir = self._resolve_pkg_dir(dep.source)
                if pkg_dir is None or pkg_dir in seen_set:
                    continue
                seen_set.add(pkg_dir)
                seen.append(pkg_dir)
        return seen

    def _resolve_pkg_dir(self, source):
        """定位 package.json 所在目录：从 source 起始目录起，依次向上查找
        起始目录、父目录、祖父目录（共 3 个候选），返回首个含 package.json
        的目录；均无则返回 None。"""
        abs_source = os.path.normpath(os.path.join(self._source_dir, source))
        cur = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
        for _ in range(3):
            if os.path.isfile(os.path.join(cur, "package.json")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    def _display_path(self, pkg_dir):
        """在 source_dir 下显示相对路径，否则绝对路径。"""
        try:
            rel = os.path.relpath(pkg_dir, self._source_dir)
            if not rel.startswith(".."):
                return rel
        except ValueError:
            pass
        return pkg_dir


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
                                    "detail": "target directory does not exist"})
            return results

        for name, ext in extensions.items():
            if ext.enabled:
                for dep in ext.path_deps:
                    target = os.path.join(self._target_dir, dep.target)
                    source = os.path.join(self._source_dir, dep.source)
                    if not os.path.islink(target):
                        results.append({"name": f"{name}:{dep.target}",
                                        "status": Status.MISSING,
                                        "detail": "symlink missing"})
                    else:
                        actual = os.readlink(target)
                        if os.path.abspath(actual) != os.path.abspath(source):
                            results.append({"name": f"{name}:{dep.target}",
                                            "status": Status.BROKEN,
                                            "detail": f"points to wrong target: {actual}"})
            else:
                for dep in ext.path_deps:
                    target = os.path.join(self._target_dir, dep.target)
                    if os.path.islink(target):
                        results.append({"name": f"{name}:{dep.target}",
                                        "status": Status.UNEXPECTED,
                                        "detail": "disabled but symlink still exists"})

        if not results:
            results.append({"name": "", "status": Status.OK, "detail": "All extensions are in good state"})
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

    def run_infobox(self, text):
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--colors", "--infobox", text,
                str(h), str(w)]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    env=os.environ.copy())
            return result.returncode
        except FileNotFoundError:
            return -1


class DialogUI:
    TYPES_LABELS = {
        "skill": "Skills",
        "agent": "Agents",
        "command": "Commands",
        "plugin": "Plugins",
    }
    TYPES_ORDER = ["skill", "agent", "command", "plugin"]

    def __init__(self, adapter, store, config_manager):
        self._adapter = adapter
        self._store = store
        self._config = config_manager
        self._target_dir = resolve_target_dir(DEFAULT_TARGET_DIR)
        log.info("Default target directory: %s", self._target_dir)

    @staticmethod
    def _visible_len(s):
        return len(re.sub(r'\\Z[b0-7nrR]', '', s))

    def _pad_label(self, label, width):
        return label + " " * max(width - self._visible_len(label), 1)

    def ask_target_dir(self):
        while True:
            log.info("Showing target directory prompt, displayed: %s", self._target_dir)
            code, value = self._adapter.run_inputbox("Target Directory", self._target_dir)
            if code != 0:
                log.info("Target directory prompt cancelled")
                return "cancel"
            if not value.strip():
                self._adapter.run_msgbox("Error", "Target directory cannot be empty")
                continue
            resolved = resolve_target_dir(value.strip())
            log.info("Target directory entered=%r resolved=%r", value.strip(), resolved)
            if not os.path.isdir(resolved):
                try:
                    os.makedirs(resolved, exist_ok=True)
                    log.info("Created target directory: %s", resolved)
                except OSError as e:
                    log.error("Cannot create target directory %s: %s", resolved, e)
                    self._adapter.run_msgbox("Error", f"Cannot create target directory: {e}")
                    continue
            self._target_dir = resolved
            return self._target_dir

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
                help_text = "Missing dependencies: " + ", ".join(missing)
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
                stats = (f"\t{Format.BOLD}{Format.RED}{enabled}/{total} enabled{Format.RESET}"
                         f"\t{Format.BOLD}{Format.MAGENTA}{ok}/{total} available{Format.RESET}")
                menu_items.append((t, label + stats))
            menu_items.append(("apply", f"{Format.BOLD}{Format.GREEN}Confirm and apply changes{Format.RESET}"))
            menu_items.append(("quit", "Quit"))

            code, choice = self._adapter.run_menu("Extension Management", menu_items)
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
            self._adapter.run_msgbox("Info", "No extensions in this category")
            return "back"
        while True:
            label = self.TYPES_LABELS.get(ext_type, ext_type)
            title = f"{label}  (OK=intact  !=missing, unselectable)"
            code, selected, invalid = self._adapter.run_checklist(title, items, unavailable)
            if code != 0:
                return "back"
            if invalid:
                self._adapter.run_msgbox(
                    "Error",
                    "The following extensions are incomplete and cannot be enabled:\n\n"
                    + "\n".join(f"  - {n}" for n in invalid)
                    + "\n\nPlease uncheck them and try again",
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
        lines = [f"{Format.BOLD}{Format.BLUE}Change Summary:{Format.RESET}\n"]
        if changes.to_enable:
            lines.append(f"{Format.BOLD}{Format.MAGENTA}Enable:{Format.RESET}")
            for n in changes.to_enable:
                lines.append(f"  + {n}")
        if changes.to_disable:
            lines.append(f"\n{Format.BOLD}{Format.RED}Disable:{Format.RESET}")
            for n in changes.to_disable:
                lines.append(f"  - {n}")
        if changes.cascade_disabled:
            lines.append(f"\n{Format.BOLD}{Format.YELLOW}Cascade disabled:{Format.RESET}")
            for n in changes.cascade_disabled:
                lines.append(f"  ~ {n}")
        if changes.rejected:
            for r in changes.rejected:
                lines.append(
                    f"Refused to disable {r['name']}: {r['reason']} "
                    f"({', '.join(r.get('dependents', []))})"
                )
        return self._adapter.run_yesno("Confirm", "\n".join(lines)) == 0

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
                name = (r["name"] or "(all)").ljust(max_name_w)
                if r["status"] in ok_status:
                    color = Format.BOLD + Format.BLUE
                elif r["status"] in skip_status:
                    color = Format.BOLD + Format.MAGENTA
                else:
                    color = Format.BOLD + Format.RED
                lines.append(f"{name}\t{color}{r['status']}{Format.RESET}")
        self._adapter.run_msgbox("Results", "\n".join(lines))

    def show_error(self, message):
        self._adapter.run_msgbox("Error", message)

    def show_installing_progress(self, pkg_dir):
        self._adapter.run_infobox(
            f"Installing plugin dependencies, please wait...\nInstall directory: {pkg_dir}"
        )


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    setup_logging(script_dir)
    log.info("=== opencode extension manager starting (dir=%s) ===", script_dir)

    if not DialogAdapter.check_available():
        log.error("'dialog' tool is not installed")
        print("Error: the 'dialog' tool is not installed; please install it first", file=sys.stderr)
        sys.exit(1)

    config_mgr = ConfigManager(os.path.join(script_dir, "extensions.json"))
    try:
        config = config_mgr.load()
    except ConfigError as e:
        log.error("Config load failed: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    store = ExtensionStore(config.extensions, source_dir=script_dir)
    ui = DialogUI(DialogAdapter(), store, config_mgr)

    target = ui.ask_target_dir()
    if target == "cancel":
        log.info("User cancelled at target directory prompt")
        sys.exit(0)
    log.info("Target directory: %s", target)

    symlink_mgr = SymlinkManager(script_dir, target)
    npm_mgr = NpmDependencyManager(script_dir)

    while True:
        action, selected = ui.show_extension_list()
        if action == "cancel":
            log.info("User quit without applying changes")
            break

        log.info("Applying selection: %d extension(s) selected", len(selected))
        changes = store.resolve_changes(selected)
        log.info(
            "Resolved changes: enable=%d, disable=%d, cascade=%d, rejected=%d",
            len(changes.to_enable), len(changes.to_disable),
            len(changes.cascade_disabled), len(changes.rejected),
        )

        if changes.rejected:
            for r in changes.rejected:
                log.warning(
                    "Rejected disable of %s (required by %s)",
                    r["name"], ", ".join(r.get("dependents", [])),
                )
                ui.show_error(
                    f"Extension {r['name']} is required by the following selected extensions: "
                    f"{', '.join(r.get('dependents', []))}"
                )
            continue

        if not changes.to_enable and not changes.to_disable:
            log.info("No changes to apply")
            ui.show_error("No changes")
            continue

        if not ui.show_change_summary(changes):
            log.info("User cancelled at change summary confirmation")
            continue

        all_disable = changes.to_disable + changes.cascade_disabled
        results = symlink_mgr.apply_changes(
            changes.to_enable, all_disable, store.extensions
        )
        failures = [r for r in results if r["status"] == Status.ERROR]
        log.info(
            "Symlink apply: %d result(s), %d error(s)", len(results), len(failures)
        )
        has_plugin = any(
            store.extensions[n].type == "plugin"
            for n in changes.to_enable if n in store.extensions
        )
        if has_plugin:
            log.info("Plugin(s) enabled; running npm dependency installation")
            results += npm_mgr.install_for(
                changes.to_enable, store.extensions,
                on_progress=lambda d: ui.show_installing_progress(d),
            )
        ui.show_results(results)

        try:
            config_mgr.save(config)
        except Exception as e:
            log.exception("Failed to write config file")
            ui.show_error(f"Failed to write config file: {e}")

    log.info("=== opencode extension manager exiting ===")


if __name__ == "__main__":
    main()

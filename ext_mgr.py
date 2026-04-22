#!/usr/bin/env python3
"""opencode 扩展管理器 — 通过 TUI 界面管理扩展符号链接"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

VALID_TYPES = {"skill", "agent", "command", "plugin"}


def parse_depends(depends_list):
    ext_deps = []
    path_deps = []
    for item in depends_list:
        if isinstance(item, str):
            ext_deps.append(item)
        elif isinstance(item, dict):
            path_deps.append(item)
    return ext_deps, path_deps


class ConfigError(Exception):
    pass


class ConfigManager:
    def __init__(self, config_path: str):
        self._config_path = config_path

    @staticmethod
    def check_dialog_available() -> bool:
        return shutil.which("dialog") is not None

    def load(self) -> dict:
        if not os.path.isfile(self._config_path):
            raise ConfigError(f"配置文件 {self._config_path} 不存在")

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"JSON 解析失败: {e}")

        warnings = self._validate(config)
        config["warnings"] = warnings
        return config

    def save(self, config: dict) -> None:
        data = {k: v for k, v in config.items() if k != "warnings"}
        content = json.dumps(data, indent=2, ensure_ascii=False)

        dir_name = os.path.dirname(self._config_path) or "."
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._config_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _validate(self, config: dict) -> list:
        errors = []
        warnings = []

        if "version" not in config:
            raise ConfigError("缺少 version 字段")
        if config["version"] != 2:
            raise ConfigError(f"不支持的 version: {config['version']}")

        if "extensions" not in config:
            raise ConfigError("缺少 extensions 字段")
        if not isinstance(config["extensions"], dict):
            raise ConfigError("extensions 必须为对象")

        exts = config["extensions"]
        for name, ext in exts.items():
            if not isinstance(ext, dict):
                errors.append(f"扩展 '{name}' 必须为对象")
                continue

            if "enabled" not in ext:
                errors.append(f"扩展 '{name}' 缺少 enabled 字段")
            if "description" not in ext:
                errors.append(f"扩展 '{name}' 缺少 description 字段")

            if "type" not in ext:
                errors.append(f"扩展 '{name}' 缺少 type 字段")
            elif ext["type"] not in VALID_TYPES:
                errors.append(
                    f"扩展 '{name}' 的 type '{ext['type']}' 不合法，"
                    f"必须为 {', '.join(sorted(VALID_TYPES))}"
                )

            if "/" in name:
                errors.append(f"扩展键名 '{name}' 格式错误，应为纯名称（不含 /）")
            if ".." in name:
                errors.append(f"扩展名称 '{name}' 包含非法字符 '..'")
            if name.startswith("/"):
                errors.append(f"扩展名称 '{name}' 包含非法字符（绝对路径）")

            for dep in ext.get("depends", []):
                if isinstance(dep, str):
                    if not dep:
                        errors.append(f"扩展 '{name}' 的扩展依赖名称不能为空")
                    elif "/" in dep or ".." in dep or dep.startswith("/"):
                        errors.append(f"扩展 '{name}' 的扩展依赖 '{dep}' 格式错误")
                    elif dep not in exts:
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

        self._check_circular_deps(exts)

        return warnings

    def _check_circular_deps(self, exts: dict) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in exts}

        def dfs(name: str, path: list) -> None:
            color[name] = GRAY
            path.append(name)
            for dep in exts[name].get("depends", []):
                if not isinstance(dep, str):
                    continue
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


class DependencyResolver:
    def resolve(self, selected: list, extensions: dict) -> dict:
        to_enable = set(selected)
        to_disable = set()

        for name in selected:
            self._collect_deps(name, extensions, to_enable)

        all_names = set(extensions.keys())
        to_disable = all_names - to_enable

        rejected = []
        for name in list(to_disable):
            dependents = self._find_dependents(name, extensions, to_enable)
            if dependents:
                rejected.append(
                    {"name": name, "reason": "被依赖", "dependents": dependents}
                )
                to_disable.discard(name)

        return {
            "to_enable": sorted(to_enable),
            "to_disable": sorted(to_disable),
            "rejected": rejected,
        }

    def _collect_deps(self, name: str, extensions: dict, collected: set) -> None:
        if name not in extensions:
            return
        for dep in extensions[name].get("depends", []):
            if dep not in collected and dep in extensions:
                collected.add(dep)
                self._collect_deps(dep, extensions, collected)

    def _find_dependents(
        self, name: str, extensions: dict, selected: set
    ) -> list:
        dependents = []
        for ext_name, ext_data in extensions.items():
            if ext_name in selected and name in ext_data.get("depends", []):
                dependents.append(ext_name)
        return sorted(dependents)


class SymlinkManager:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def apply_changes(
        self, to_enable: list, to_disable: list
    ) -> list:
        results = []
        for name in to_enable:
            results.append(self.create_symlink(name))
        for name in to_disable:
            results.append(self.remove_symlink(name))
        return results

    def create_symlink(self, ext_name: str) -> dict:
        source, target = self._resolve_path(ext_name)
        self._ensure_subdir(os.path.dirname(target))

        if os.path.islink(target):
            existing = os.readlink(target)
            if os.path.abspath(existing) == os.path.abspath(source):
                return {"name": ext_name, "status": "skipped", "detail": ""}
            return {
                "name": ext_name,
                "status": "conflict",
                "detail": f"符号链接已指向 {existing}",
            }

        if os.path.exists(target):
            return {
                "name": ext_name,
                "status": "conflict",
                "detail": f"目标路径 {target} 已存在",
            }

        try:
            os.symlink(source, target)
            return {"name": ext_name, "status": "success", "detail": ""}
        except OSError as e:
            return {"name": ext_name, "status": "error", "detail": str(e)}

    def remove_symlink(self, ext_name: str) -> dict:
        source, target = self._resolve_path(ext_name)

        if not os.path.islink(target):
            if not os.path.exists(target):
                return {"name": ext_name, "status": "skipped", "detail": ""}
            return {
                "name": ext_name,
                "status": "conflict",
                "detail": f"目标路径 {target} 存在但非符号链接",
            }

        existing = os.readlink(target)
        if os.path.abspath(existing) != os.path.abspath(source):
            return {
                "name": ext_name,
                "status": "conflict",
                "detail": f"符号链接指向 {existing}，非预期目标",
            }

        try:
            os.unlink(target)
            return {"name": ext_name, "status": "success", "detail": ""}
        except OSError as e:
            return {"name": ext_name, "status": "error", "detail": str(e)}

    def _resolve_path(self, ext_name: str) -> tuple:
        source = os.path.join(self._source_dir, ext_name)
        target = os.path.join(self._target_dir, ext_name)
        return source, target

    def _ensure_subdir(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)


class Validator:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def validate(self, extensions: dict) -> list:
        results = []
        if not os.path.isdir(self._target_dir):
            for name, ext in extensions.items():
                if ext.get("enabled", False):
                    results.append(
                        {
                            "name": name,
                            "status": "missing",
                            "detail": "目标目录不存在",
                        }
                    )
            return results

        for name, ext in extensions.items():
            enabled = ext.get("enabled", False)
            target = os.path.join(self._target_dir, name)

            if enabled:
                if not os.path.islink(target):
                    results.append(
                        {"name": name, "status": "missing", "detail": "符号链接缺失"}
                    )
                else:
                    expected = os.path.join(self._source_dir, name)
                    actual = os.readlink(target)
                    if os.path.abspath(actual) != os.path.abspath(expected):
                        results.append(
                            {
                                "name": name,
                                "status": "broken",
                                "detail": f"指向错误目标: {actual}",
                            }
                        )
            else:
                if os.path.islink(target):
                    results.append(
                        {
                            "name": name,
                            "status": "unexpected",
                            "detail": "已禁用但符号链接仍存在",
                        }
                    )

        if not results:
            results.append({"name": "", "status": "ok", "detail": "所有扩展状态正常"})

        return results


class DialogAdapter:
    @staticmethod
    def _term_size() -> tuple:
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

    @staticmethod
    def run_menu(title: str, items: list) -> tuple:
        term_h, term_w = DialogAdapter._term_size()
        h = min(len(items) + 8, max(term_h - 4, 20))
        w = max(term_w - 10, 70)
        menu_h = min(len(items) + 2, h - 8)
        y = max((term_h - h) // 2, 0)
        x = max((term_w - w) // 2, 0)
        args = [
            "dialog", "--stdout", "--colors",
            "--begin", str(y), str(x),
            "--menu", title, str(h), str(w), str(menu_h),
        ]
        for tag, text in items:
            args.extend([tag, text])
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            if result.returncode == 0:
                return 0, result.stdout.strip()
            return result.returncode, ""
        except FileNotFoundError:
            return -1, ""

    @staticmethod
    def run_checklist(
        title: str, items: list, unavailable: set = None
    ) -> tuple:
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        list_h = min(len(items) + 2, h - 8)
        unavailable = unavailable or set()
        args = [
            "dialog", "--stdout", "--item-help", "--colors",
            "--checklist", title, str(h), str(w), str(list_h),
        ]
        for tag, status, text, help_text in items:
            args.extend([tag, text, "on" if status else "off", help_text])
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            if result.returncode == 0:
                raw = result.stdout.strip()
                selected = [s.strip('"') for s in raw.split()] if raw else []
                invalid = [s for s in selected if s in unavailable]
                return 0, selected, invalid
            return result.returncode, [], []
        except FileNotFoundError:
            return -1, [], []

    @staticmethod
    def run_inputbox(title: str, default: str = "") -> tuple:
        _, term_w = DialogAdapter._term_size()
        w = max(term_w - 10, 60)
        args = ["dialog", "--stdout", "--inputbox", title, "8", str(w), default]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            if result.returncode == 0:
                return 0, result.stdout.strip()
            return result.returncode, ""
        except FileNotFoundError:
            return -1, ""

    @staticmethod
    def run_msgbox(title: str, text: str) -> int:
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--colors", "--msgbox", text, str(h), str(w)]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            return result.returncode
        except FileNotFoundError:
            return -1

    @staticmethod
    def run_yesno(title: str, text: str) -> int:
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--colors", "--yesno", text, str(h), str(w)]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            return result.returncode
        except FileNotFoundError:
            return -1

    @staticmethod
    def run_textbox(title: str, text: str) -> int:
        term_h, term_w = DialogAdapter._term_size()
        h = max(term_h - 4, 20)
        w = max(term_w - 10, 70)
        args = ["dialog", "--stdout", "--textbox", text, str(h), str(w)]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, env=os.environ.copy()
            )
            return result.returncode
        except FileNotFoundError:
            return -1


class DialogUI:
    CATEGORY_LABELS = {
        "skills": "Skills  — 技能扩展",
        "agents": "Agents — 智能体",
        "commands": "Commands — 命令编排",
    }
    CATEGORY_ORDER = ["skills", "agents", "commands"]

    def __init__(self, adapter: DialogAdapter, config_manager: ConfigManager, source_dir: str):
        self._adapter = adapter
        self._config = config_manager
        self._source_dir = source_dir
        self._target_dir = os.path.expanduser("~/.config/opencode")

    @staticmethod
    def _visible_len(s: str) -> int:
        return len(re.sub(r'\\Z[b0-7rR]', '', s))

    def _pad_label(self, label: str, width: int) -> str:
        pad = width - self._visible_len(label)
        return label + " " * max(pad, 1)

    def _check_availability(self, name: str, extensions: dict) -> list:
        missing = []
        path = os.path.join(self._source_dir, name)
        if not os.path.exists(path):
            missing.append(name)
        for dep in extensions.get(name, {}).get("depends", []):
            dep_path = os.path.join(self._source_dir, dep)
            if not os.path.exists(dep_path):
                missing.append(dep)
        return missing

    def _build_checklist_items(self, extensions: dict, category: str) -> tuple:
        items = []
        unavailable = set()
        for name, ext in extensions.items():
            if not name.startswith(category + "/"):
                continue
            missing = self._check_availability(name, extensions)
            if missing:
                unavailable.add(name)
                mark = "\\Zr !! \\ZR"
                dep_missing = [d for d in missing if d != name]
                help_text = "扩展文件不存在"
                if any(d == name for d in missing) and dep_missing:
                    help_text = "扩展文件不存在, 依赖: " + ", ".join(dep_missing)
                elif dep_missing:
                    help_text = "缺失依赖: " + ", ".join(dep_missing)
            else:
                mark = "\\Zb\\Z2 OK \\Zn"
                help_text = ext.get("description", "")
            text = f"{mark} {ext.get('description', '')}"
            items.append((name, ext.get("enabled", False), text, help_text))
        return items, unavailable

    def _count_stats(self, extensions: dict, category: str) -> tuple:
        total = 0
        ok = 0
        enabled = 0
        for name, ext in extensions.items():
            if not name.startswith(category + "/"):
                continue
            total += 1
            if ext.get("enabled", False):
                enabled += 1
            if not self._check_availability(name, extensions):
                ok += 1
        return total, enabled, ok

    def show_target_dir_input(self) -> str:
        code, value = self._adapter.run_inputbox("目标目录", self._target_dir)
        if code != 0:
            return "cancel"
        if not value.strip():
            self._adapter.run_msgbox("错误", "目标目录不能为空")
            return self.show_target_dir_input()
        self._target_dir = value.strip()
        return self._target_dir

    def show_extension_list(self, extensions: dict) -> tuple:
        while True:
            menu_items = []
            max_label_w = 0
            for cat in self.CATEGORY_ORDER:
                total, _, _ = self._count_stats(extensions, cat)
                if total > 0:
                    max_label_w = max(
                        max_label_w,
                        self._visible_len(self.CATEGORY_LABELS.get(cat, cat)),
                    )
            for cat in self.CATEGORY_ORDER:
                total, enabled, ok = self._count_stats(extensions, cat)
                if total == 0:
                    continue
                label = self._pad_label(self.CATEGORY_LABELS.get(cat, cat), max_label_w)
                stats = (
                    f"\t\\Zb\\Z1{enabled}/{total} 启用\\Zn"
                    f"\t\\Zb\\Z5{ok}/{total} 可用\\Zn"
                )
                menu_items.append((cat, label + stats))
            menu_items.append(("apply", "\\Zb\\Z2确认并应用变更\\Zn"))
            menu_items.append(("quit", "退出"))

            code, choice = self._adapter.run_menu("扩展管理", menu_items)
            if code != 0 or choice == "quit":
                return "cancel", []

            if choice == "apply":
                return "ok", [
                    name for name, ext in extensions.items()
                    if ext.get("enabled", False)
                ]

            if choice in self.CATEGORY_ORDER:
                action = self._show_category_checklist(extensions, choice)
                if action == "apply":
                    return "ok", [
                        name for name, ext in extensions.items()
                        if ext.get("enabled", False)
                    ]

    def _show_category_checklist(self, extensions: dict, category: str) -> str:
        items, unavailable = self._build_checklist_items(extensions, category)
        if not items:
            self._adapter.run_msgbox("提示", f"该分类下没有扩展")
            return "back"

        while True:
            label = self.CATEGORY_LABELS.get(category, category)
            title = f"{label}  (OK=齐全  !!=缺失,不可选)"
            code, selected, invalid = self._adapter.run_checklist(
                title, items, unavailable
            )

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

            for name, ext in extensions.items():
                if name.startswith(category + "/"):
                    ext["enabled"] = name in selected

            items, unavailable = self._build_checklist_items(extensions, category)
            return "back"

    def show_change_summary(self, changes: dict) -> bool:
        lines = ["\\Zb\\Z4变更摘要:\\Zn\n"]
        if changes.get("to_enable"):
            lines.append("\\Zb\\Z5启用:\\Zn")
            for n in changes["to_enable"]:
                lines.append(f"  + {n}")
        if changes.get("to_disable"):
            lines.append("")
            lines.append("\\Zb\\Z1禁用:\\Zn")
            for n in changes["to_disable"]:
                lines.append(f"  - {n}")
        if changes.get("rejected"):
            for r in changes["rejected"]:
                lines.append(f"拒绝禁用 {r['name']}: {r['reason']} ({', '.join(r.get('dependents', []))})")
        return self._adapter.run_yesno("确认", "\n".join(lines)) == 0

    def show_results(self, results: list) -> None:
        ok_status = {"success", "ok"}
        skip_status = {"skipped"}
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

    def show_validation_results(self, results: list) -> None:
        lines = []
        for r in results:
            lines.append(f"{r['name']}: {r['status']} - {r['detail']}")
        self._adapter.run_msgbox("校验结果", "\n".join(lines))

    def show_error(self, message: str) -> None:
        self._adapter.run_msgbox("错误", message)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if not ConfigManager.check_dialog_available():
        print("错误: dialog 工具未安装，请先安装 dialog", file=sys.stderr)
        sys.exit(1)

    config_path = os.path.join(script_dir, "extensions.json")
    config_mgr = ConfigManager(config_path)

    try:
        config = config_mgr.load()
    except ConfigError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    extensions = config["extensions"]

    adapter = DialogAdapter()
    ui = DialogUI(adapter, config_mgr, script_dir)

    target = ui.show_target_dir_input()
    if target == "cancel":
        sys.exit(0)

    resolver = DependencyResolver()
    symlink_mgr = SymlinkManager(script_dir, target)
    validator = Validator(script_dir, target)

    while True:
        action, selected = ui.show_extension_list(extensions)
        if action == "cancel":
            break

        changes = resolver.resolve(selected, extensions)

        if changes["rejected"]:
            for r in changes["rejected"]:
                ui.show_error(
                    f"扩展 {r['name']} 被以下已选择扩展依赖: {', '.join(r.get('dependents', []))}"
                )
            continue

        if not changes["to_enable"] and not changes["to_disable"]:
            ui.show_error("无变更")
            continue

        if not ui.show_change_summary(changes):
            continue

        results = symlink_mgr.apply_changes(
            changes["to_enable"], changes["to_disable"]
        )
        ui.show_results(results)

        for name in changes["to_enable"]:
            if name in extensions:
                extensions[name]["enabled"] = True
        for name in changes["to_disable"]:
            if name in extensions:
                extensions[name]["enabled"] = False

        try:
            config_mgr.save(config)
        except Exception as e:
            ui.show_error(f"配置文件写入失败: {e}")


if __name__ == "__main__":
    main()

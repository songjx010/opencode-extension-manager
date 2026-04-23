# 级联去使能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 禁用扩展时递归级联去使用孤儿子扩展（无其他已启用扩展依赖的子扩展）。

**Architecture:** 在 DependencyResolver.resolve() 后置新增 `_cascade_disable()` 方法，迭代检查 to_disable 扩展的扩展依赖，将孤儿依赖移入 `cascade_disabled` 集合。resolve() 返回值新增 `cascade_disabled` 字段。main() 将 cascade_disabled 合并到 to_disable 执行 symlink 和配置保存。DialogUI.show_change_summary() 区分展示级联禁用项。

**Tech Stack:** Python 3.8+, pytest（测试）, dialog TUI, 无第三方运行时依赖

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `ext_mgr.py` | 修改 | DependencyResolver 新增方法 + resolve() 变更 + show_change_summary() + main() |
| `tests/test_ext_mgr.py` | 修改 | 追加级联去使能测试 |

---

### Task 1: DependencyResolver — 新增级联方法

**Files:**
- Modify: `ext_mgr.py:158-200`（DependencyResolver 类）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写级联去使能测试**

追加到 `tests/test_ext_mgr.py`：

```python
def test_cascade_simple():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
    }
    result = resolver.resolve(["b"], exts)
    assert result["to_enable"] == ["b"]
    assert result["to_disable"] == ["a"]
    assert result["cascade_disabled"] == []
    assert result["rejected"] == []


def test_cascade_recursive():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
    }
    result = resolver.resolve([], exts)
    assert result["to_disable"] == ["a"]
    assert sorted(result["cascade_disabled"]) == ["b", "c"]


def test_cascade_stopped_by_other_dependent():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
        "d": {
            "type": "skill",
            "enabled": True,
            "description": "D",
            "depends": ["c", {"source": "d.md", "target": "d.md"}],
        },
    }
    result = resolver.resolve(["d"], exts)
    assert "a" in result["to_disable"]
    assert "b" in result["cascade_disabled"]
    assert "c" not in result["cascade_disabled"]


def test_cascade_respects_user_selection():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
    }
    result = resolver.resolve(["b"], exts)
    assert result["to_enable"] == ["b"]
    assert "b" not in result["cascade_disabled"]


def test_cascade_shared_dep_disabled_together():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["c", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "skill",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
    }
    result = resolver.resolve([], exts)
    assert sorted(result["to_disable"]) == ["a", "b"]
    assert result["cascade_disabled"] == ["c"]


def test_cascade_no_cascade_when_dep_in_selected():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
        "standalone": {
            "type": "skill",
            "enabled": True,
            "description": "Standalone",
            "depends": [{"source": "s.md", "target": "s.md"}],
        },
    }
    result = resolver.resolve(["standalone"], exts)
    assert "b" in result["cascade_disabled"]
    assert "standalone" in result["to_enable"]


def test_cascade_with_existing_test_data():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve([], exts)
    assert "a" in result["to_disable"]
    assert sorted(result["cascade_disabled"]) == ["b", "c"]
    assert "standalone" in result["to_disable"]


def test_cascade_disabled_in_result_for_no_change():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a", "standalone"], exts)
    assert result["cascade_disabled"] == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_cascade_simple -v`
Expected: FAIL — `resolve()` 返回值中无 `cascade_disabled` 键

- [ ] **Step 3: 实现 `_find_dependents_excluding()` 和 `_cascade_disable()`**

在 `ext_mgr.py` 的 `DependencyResolver` 类中，在 `_find_dependents()` 方法（第191行）之后添加：

```python
    def _find_dependents_excluding(
        self, name: str, extensions: dict, candidates: set, excluded: set
    ) -> list:
        dependents = []
        for ext_name, ext_data in extensions.items():
            if ext_name in excluded:
                continue
            if ext_name not in candidates:
                continue
            ext_deps, _ = parse_depends(ext_data.get("depends", []))
            if name in ext_deps:
                dependents.append(ext_name)
        return sorted(dependents)

    def _cascade_disable(
        self, to_enable: set, to_disable: set, extensions: dict
    ) -> set:
        cascade_disabled = set()
        changed = True
        while changed:
            changed = False
            all_disabled = to_disable | cascade_disabled
            for name in list(all_disabled):
                ext_deps, _ = parse_depends(
                    extensions.get(name, {}).get("depends", [])
                )
                for dep in ext_deps:
                    if dep not in to_enable:
                        continue
                    if dep in to_disable or dep in cascade_disabled:
                        continue
                    remaining = self._find_dependents_excluding(
                        dep, extensions, to_enable, all_disabled
                    )
                    if not remaining:
                        to_enable.discard(dep)
                        cascade_disabled.add(dep)
                        changed = True
        return cascade_disabled
```

- [ ] **Step 4: 修改 `resolve()` 方法**

替换 `ext_mgr.py` 中 `resolve()` 方法（第159-180行）为：

```python
    def resolve(self, selected: list, extensions: dict) -> dict:
        to_enable = set(selected)

        for name in selected:
            self._collect_deps(name, extensions, to_enable)

        to_disable = set(extensions.keys()) - to_enable

        rejected = []
        for name in list(to_disable):
            dependents = self._find_dependents(name, extensions, to_enable)
            if dependents:
                rejected.append(
                    {"name": name, "reason": "被依赖", "dependents": dependents}
                )
                to_disable.discard(name)

        cascade_disabled = self._cascade_disable(
            to_enable, to_disable, extensions
        )

        return {
            "to_enable": sorted(to_enable),
            "to_disable": sorted(to_disable),
            "cascade_disabled": sorted(cascade_disabled),
            "rejected": rejected,
        }
```

- [ ] **Step 5: 运行全部测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS（包括旧的 `test_resolve_disable_no_cascade`，因为 cascade_disabled 是在 to_disable 之外的新字段）

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: add cascade disable for orphaned extension deps"
```

---

### Task 2: DialogUI + main() 适配

**Files:**
- Modify: `ext_mgr.py:646-660`（show_change_summary）
- Modify: `ext_mgr.py:729-765`（main 函数循环体）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 show_change_summary 测试**

追加到 `tests/test_ext_mgr.py`：

```python
from unittest.mock import MagicMock, patch
from ext_mgr import DialogUI


def _make_ui_for_summary():
    adapter = MagicMock()
    config_mgr = MagicMock()
    adapter.run_yesno.return_value = 0
    ui = DialogUI(adapter, config_mgr, "/fake")
    return ui, adapter


def test_show_change_summary_with_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": ["x"],
        "to_disable": ["a"],
        "cascade_disabled": ["b", "c"],
        "rejected": [],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "禁用" in text
    assert "级联禁用" in text
    assert "b" in text
    assert "c" in text


def test_show_change_summary_no_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": ["x"],
        "to_disable": ["a"],
        "cascade_disabled": [],
        "rejected": [],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "级联禁用" not in text


def test_show_change_summary_cascade_before_rejected():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": [],
        "to_disable": ["a"],
        "cascade_disabled": ["b"],
        "rejected": [{"name": "c", "reason": "被依赖", "dependents": ["d"]}],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    lines = text.split("\n")
    cascade_idx = next(i for i, l in enumerate(lines) if "级联禁用" in l)
    rejected_idx = next(i for i, l in enumerate(lines) if "拒绝禁用" in l)
    assert cascade_idx < rejected_idx
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_show_change_summary_with_cascade -v`
Expected: FAIL — show_change_summary 不展示 cascade_disabled

- [ ] **Step 3: 修改 `show_change_summary()`**

替换 `ext_mgr.py` 中 `show_change_summary()` 方法（第646-660行）为：

```python
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
        if changes.get("cascade_disabled"):
            lines.append("")
            lines.append("\\Zb\\Z3级联禁用:\\Zn")
            for n in changes["cascade_disabled"]:
                lines.append(f"  ~ {n}")
        if changes.get("rejected"):
            for r in changes["rejected"]:
                lines.append(
                    f"拒绝禁用 {r['name']}: {r['reason']} "
                    f"({', '.join(r.get('dependents', []))})"
                )
        return self._adapter.run_yesno("确认", "\n".join(lines)) == 0
```

- [ ] **Step 4: 修改 `main()` 函数**

替换 `ext_mgr.py` 中 main() 函数的 while 循环体（第729-765行）为：

```python
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

        all_disable = changes["to_disable"] + changes["cascade_disabled"]

        results = symlink_mgr.apply_changes(
            changes["to_enable"], all_disable, extensions
        )
        ui.show_results(results)

        for name in changes["to_enable"]:
            if name in extensions:
                extensions[name]["enabled"] = True
        for name in all_disable:
            if name in extensions:
                extensions[name]["enabled"] = False

        try:
            config_mgr.save(config)
        except Exception as e:
            ui.show_error(f"配置文件写入失败: {e}")
```

- [ ] **Step 5: 运行全部测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: adapt DialogUI and main() for cascade disable display"
```

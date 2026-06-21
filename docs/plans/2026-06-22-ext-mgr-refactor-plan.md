# ext_mgr.py 架构重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 881 行单文件 `ext_mgr.py` 重构为分层清晰、引入数据模型、消除重复级联逻辑的架构，行为等价、JSON 字节级兼容。

**Architecture:** 单文件内 7 段分区（Constants → Data Models → Exceptions → Domain → I/O → UI → Entry），`ExtensionStore` 作为唯一状态拥有者，`DependencyGraph` 会话单例，UI 层不再越层访问领域。新增 `@dataclass` 数据模型（`Extension`/`Config`/`ChangeSet`/`PathDep`）。

**Tech Stack:** Python 3.8+（仅标准库），dialog TUI，pytest + pytest-cov。

**Spec Reference:** `docs/plans/2026-06-22-ext-mgr-refactor-design.md`

---

## 前置说明（开始前必读）

1. **工作区状态假设**：本计划基于当前工作区 `ext_mgr.py`（881 行，含已有 `DependencyGraph` 类）。若存在未提交改动，**请先提交**，确保起点干净。
2. **覆盖率基线**：`ext_mgr.py` 当前 **65%**（607 stmts, 292 branches, 77 tests 全绿）。重构后不得低于此值。
3. **为何 Task 4 是原子提交**：`extensions` 数据结构类型从 `dict[str, dict]` 变为 `dict[str, Extension]` 是贯穿性的契约变更。`ConfigManager`、`SymlinkManager`、`Validator`、`DialogUI`、`main()` 通过该结构耦合，无法在不破坏运行时的情况下逐个切换。因此 Task 1–3 为增量新增（各自绿、各自提交），Task 4 为一次性切换（内部有多步小颗粒，但仅在全套测试转绿后提交一次）。Task 3 已预先构建并测试新领域逻辑，故 Task 4 主要是机械重接 + 测试迁移，风险可控。
4. **TDD 节奏**：Task 1–3 严格 TDD（先写失败测试 → 实现 → 通过 → 提交）。Task 4 为重构切换，迁移后的测试即规约，全套绿即通过。

---

## 文件结构

| 文件 | 职责 | 本计划动作 |
|------|------|-----------|
| `ext_mgr.py` | 全部源码（单文件） | 重构：新增数据模型/常量/`ExtensionStore`，重写 `ConfigManager`/`SymlinkManager`/`Validator`/`DialogAdapter`/`DialogUI`/`main`，删除 `DependencyResolver` |
| `tests/test_ext_mgr.py` | 77 个测试 + helper | 迁移：6 个 `parse_depends` 测试不动；其余按类别机械迁移到新 API；新增 `make_extensions` / `make_extensions_from_raw` helper |
| `tests/conftest.py` | pytest 路径设置 | 不变 |

---

## Task 1: 新增数据模型与常量（增量，TDD）

**Files:**
- Modify: `ext_mgr.py`（在文件顶部 `GROUP_TO_TYPE`/`TYPE_TO_GROUP` 之后、`parse_depends` 之后新增）
- Test: `tests/test_ext_mgr.py`（新增数据模型测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_ext_mgr.py` 末尾追加：

```python
from ext_mgr import Extension, PathDep, Config, ChangeSet, Status, Format, DEFAULT_TARGET_DIR


def test_pathdep_fields():
    d = PathDep(source="a.md", target="b.md")
    assert d.source == "a.md"
    assert d.target == "b.md"


def test_extension_defaults():
    e = Extension(name="x", type="skill", enabled=True, description="d")
    assert e.ext_deps == []
    assert e.path_deps == []
    assert e.visible is True


def test_extension_with_deps():
    e = Extension(
        name="x", type="skill", enabled=True, description="d",
        ext_deps=["y"], path_deps=[PathDep("s", "t")], visible=False,
    )
    assert e.ext_deps == ["y"]
    assert e.path_deps == [PathDep("s", "t")]
    assert e.visible is False


def test_config_defaults():
    c = Config(version=3, extensions={})
    assert c.warnings == []
    assert c.extra == {}


def test_changeset_is_frozen():
    cs = ChangeSet(to_enable=["a"], to_disable=[], cascade_disabled=[], rejected=[])
    assert cs.to_enable == ["a"]
    import pytest
    with pytest.raises(Exception):
        cs.to_enable = ["b"]   # frozen dataclass 不可赋值


def test_status_constants():
    assert Status.SUCCESS == "success"
    assert Status.MISSING == "missing"
    assert Status.OK == "ok"


def test_format_constants():
    assert Format.BOLD == "\\Zb"
    assert Format.RESET == "\\Zn"


def test_default_target_dir():
    assert DEFAULT_TARGET_DIR == "~/.config/opencode"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ext_mgr.py -k "pathdep_fields or extension_defaults or config_defaults or changeset_is_frozen or status_constants or format_constants or default_target_dir" -v`
Expected: FAIL — `ImportError: cannot import name 'Extension'`

- [ ] **Step 3: 实现数据模型与常量**

在 `ext_mgr.py` 顶部 import 区改为（替换原 `from typing import Dict`）：

```python
from dataclasses import dataclass, field
from typing import Dict, List
```

在 `TYPE_TO_GROUP` 定义之后、`parse_depends` 之前，新增常量：

```python
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
```

在 `parse_depends` 函数之后、`class ConfigError` 之前，新增数据模型：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_ext_mgr.py -k "pathdep_fields or extension_defaults or config_defaults or changeset_is_frozen or status_constants or format_constants or default_target_dir" -v`
Expected: PASS（8 项）

- [ ] **Step 5: 运行全套确认未破坏既有测试**

Run: `pytest tests/ -v`
Expected: PASS（77 + 8 = 85 项）

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "refactor: add dataclass data models and Status/Format constants"
```

---

## Task 2: 新增 `make_extensions` 测试 helper（增量，TDD）

**Files:**
- Modify: `tests/test_ext_mgr.py`（与既有 `_extensions_for_resolver` / `_valid_config` 等模块级 helper 同处一文件）

- [ ] **Step 1: 写失败测试**

在 `tests/test_ext_mgr.py` 末尾追加（`make_extensions` 将在 Step 3 定义于本文件，故无需 import）：

```python
def test_make_extensions_basic():
    exts = make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B", "visible": False},
    })
    assert isinstance(exts["a"], Extension)
    assert exts["a"].ext_deps == ["b"]
    assert exts["a"].path_deps == [PathDep("a.md", "a.md")]
    assert exts["a"].visible is True
    assert exts["b"].type == "agent"
    assert exts["b"].visible is False
    assert exts["b"].ext_deps == []
    assert exts["b"].path_deps == []


def test_make_extensions_defaults():
    exts = make_extensions({"x": {"type": "command", "enabled": True}})
    assert exts["x"].description == ""
    assert exts["x"].visible is True
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ext_mgr.py -k "make_extensions" -v`
Expected: FAIL — `NameError: name 'make_extensions' is not defined`

- [ ] **Step 3: 实现 helper**

在 `tests/test_ext_mgr.py` 顶部 import 区之后（既有 helper 函数附近）新增：

```python
def make_extensions(spec):
    """从紧凑 spec 构造 dict[name -> Extension]。

    spec[name] = {
        "type": str,              # 必填
        "enabled": bool,          # 必填
        "description": str,       # 可选，默认 ""
        "ext_deps": [str, ...],   # 可选，默认 []
        "path_deps": [(source, target), ...],  # 可选，默认 []
        "visible": bool,          # 可选，默认 True
    }
    """
    extensions = {}
    for name, attrs in spec.items():
        extensions[name] = Extension(
            name=name,
            type=attrs["type"],
            enabled=attrs["enabled"],
            description=attrs.get("description", ""),
            ext_deps=list(attrs.get("ext_deps", [])),
            path_deps=[PathDep(s, t) for s, t in attrs.get("path_deps", [])],
            visible=attrs.get("visible", True),
        )
    return extensions
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ext_mgr.py -k "make_extensions" -v`
Expected: PASS（2 项）

- [ ] **Step 5: 运行全套确认未破坏**

Run: `pytest tests/ -v`
Expected: PASS（85 + 2 = 87 项）

- [ ] **Step 6: 提交**

```bash
git add tests/test_ext_mgr.py
git commit -m "test: add make_extensions fixture helper for Extension construction"
```

---

## Task 3: 新增 `ExtensionStore`（增量，TDD，含等价性验证）

**Files:**
- Modify: `ext_mgr.py`（在 `class ConfigError` 之后、`class DependencyGraph` 之前新增 `ExtensionStore`）
- Test: `tests/test_ext_mgr.py`

> 说明：本任务的 `ExtensionStore` 内部内联构建邻接表（`_forward`/`_reverse`）。Task 4 会把这段邻接逻辑提取为最终的 `DependencyGraph` 类并删除旧 `DependencyGraph`。本任务期间，旧 `DependencyGraph` 与 `DependencyResolver` 原样保留、原样工作。

- [ ] **Step 1: 写失败测试 — 基础查询**

在 `tests/test_ext_mgr.py` 末尾追加（复用 Task 2 的 `make_extensions`）：

```python
from ext_mgr import ExtensionStore


def _store_a():
    """a→b→c 链 + standalone，全部 enabled=False。"""
    return ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B",
              "ext_deps": ["c"], "path_deps": [("b.md", "b.md")]},
        "c": {"type": "agent", "enabled": False, "description": "C",
              "path_deps": [("c.md", "c.md")]},
        "standalone": {"type": "skill", "enabled": False, "description": "S",
                       "path_deps": [("s.md", "s.md")]},
    }))


def test_store_get_and_names():
    s = _store_a()
    assert s.get("a").name == "a"
    assert s.get("missing") is None
    assert set(s.names()) == {"a", "b", "c", "standalone"}


def test_store_by_type():
    s = _store_a()
    skills = s.by_type("skill")
    assert {e.name for e in skills} == {"a", "standalone"}


def test_store_adjacency():
    s = _store_a()
    assert s.deps_of("a") == {"b"}
    assert s.deps_of("b") == {"c"}
    assert s.dependents_of("b") == {"a"}
    assert s.dependents_of("c") == {"b"}
    assert s.dependents_of("standalone") == set()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ext_mgr.py -k "store_get_and_names or store_by_type or store_adjacency" -v`
Expected: FAIL — `ImportError: cannot import name 'ExtensionStore'`

- [ ] **Step 3: 实现 `ExtensionStore` 基础（查询部分）**

在 `ext_mgr.py` 的 `class ConfigError` 之后新增：

```python
class ExtensionStore:
    """扩展状态的唯一拥有者：持有 extensions dict 与会话内邻接表。

    封装所有领域操作（toggle / 级联 / 解析 / 可用性检查）。UI 与 I/O 层
    通过本类访问领域状态，不直接操作 extensions dict 或邻接结构。
    """

    def __init__(self, extensions, source_dir=""):
        self._extensions = extensions
        self._source_dir = source_dir
        self._forward = {}
        self._reverse = {}
        for name in extensions:
            self._forward.setdefault(name, set())
            self._reverse.setdefault(name, set())
        for name, ext in extensions.items():
            for dep in ext.ext_deps:
                self._forward[name].add(dep)
                self._reverse.setdefault(dep, set()).add(name)

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
        return set(self._forward.get(name, set()))

    def dependents_of(self, name):
        return set(self._reverse.get(name, set()))

    def has_enabled_dependent(self, name):
        return any(
            self._extensions[d].enabled
            for d in self._reverse.get(name, set())
            if d in self._extensions
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ext_mgr.py -k "store_get_and_names or store_by_type or store_adjacency" -v`
Expected: PASS（3 项）

- [ ] **Step 5: 写失败测试 — `check_availability`**

追加：

```python
def test_store_check_availability_all_present(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("x")
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }), source_dir=str(src))
    assert s.check_availability("a") == []


def test_store_check_availability_ext_dep_missing():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["ghost"]},
    }))
    assert s.check_availability("a") == ["ghost"]


def test_store_check_availability_path_source_missing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "path_deps": [("missing.md", "missing.md")]},
    }), source_dir=str(src))
    assert s.check_availability("a") == ["missing.md"]
```

- [ ] **Step 6: 运行确认失败**

Run: `pytest tests/test_ext_mgr.py -k "store_check_availability" -v`
Expected: FAIL — `AttributeError: 'ExtensionStore' object has no attribute 'check_availability'`

- [ ] **Step 7: 实现 `check_availability`**

在 `ExtensionStore` 内追加方法：

```python
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
```

- [ ] **Step 8: 运行确认通过**

Run: `pytest tests/test_ext_mgr.py -k "store_check_availability" -v`
Expected: PASS（3 项）

- [ ] **Step 9: 写失败测试 — `cascade_disable`（前向级联，等价于旧 `DialogUI._cascade_disable_deps`）**

追加：

```python
def test_store_cascade_simple():
    # a→b，禁 a 后 b 无其他依赖者，应级联禁用 b
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a", "b"}
    assert s.get("a").enabled is False
    assert s.get("b").enabled is False


def test_store_cascade_keeps_if_other_dependent():
    # a→b, c→b；禁 a 时 b 仍被 c 依赖，不应级联
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "c": {"type": "skill", "enabled": True, "description": "C", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a"}
    assert s.get("b").enabled is True


def test_store_cascade_transitive():
    # a→b→c，禁 a 应级联禁 b、c
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": True, "description": "C"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a", "b", "c"}
```

- [ ] **Step 10: 运行确认失败**

Run: `pytest tests/test_ext_mgr.py -k "store_cascade" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'cascade_disable'`

- [ ] **Step 11: 实现 `cascade_disable`**

在 `ExtensionStore` 内追加（算法逐行等价于旧 `DialogUI._cascade_disable_deps`）：

```python
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
            for dep in self._forward.get(cur, set()):
                ext = self._extensions.get(dep)
                if ext is None or not ext.enabled:
                    continue
                if not self.has_enabled_dependent(dep):
                    ext.enabled = False
                    disabled.add(dep)
                    queue.append(dep)
        return disabled
```

- [ ] **Step 12: 运行确认通过**

Run: `pytest tests/test_ext_mgr.py -k "store_cascade" -v`
Expected: PASS（3 项）

- [ ] **Step 13: 写失败测试 — `resolve_changes`（等价于旧 `DependencyResolver.resolve`）**

追加：

```python
def test_store_resolve_single_ext():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B",
              "ext_deps": ["c"], "path_deps": [("b.md", "b.md")]},
        "c": {"type": "agent", "enabled": False, "description": "C",
              "path_deps": [("c.md", "c.md")]},
        "standalone": {"type": "skill", "enabled": False, "description": "S",
                       "path_deps": [("s.md", "s.md")]},
    }))
    cs = s.resolve_changes(["standalone"])
    assert cs.to_enable == ["standalone"]
    assert "standalone" not in cs.to_disable


def test_store_resolve_transitive():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": False, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": False, "description": "C"},
    }))
    cs = s.resolve_changes(["a"])
    assert cs.to_enable == ["a", "b", "c"]


def test_store_resolve_cascade_classification():
    # 全启用 → 选 []：a 显式禁，b/c 因 dependent 全禁而归入 cascade
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": True, "description": "C"},
    }))
    cs = s.resolve_changes([])
    assert cs.to_disable == ["a"]
    assert cs.cascade_disabled == ["b", "c"]
    assert cs.rejected == []


def test_store_resolve_reject_if_depended():
    # b/c 仍被 a 需要（a 在 to_enable）→ 不进入 to_disable
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b", "c"]},
        "b": {"type": "agent", "enabled": False, "description": "B"},
        "c": {"type": "agent", "enabled": False, "description": "C"},
    }))
    cs = s.resolve_changes(["a"])
    assert cs.to_enable == ["a", "b", "c"]
    assert cs.to_disable == []
    assert cs.rejected == []


def test_store_resolve_syncs_state():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A"},
        "b": {"type": "skill", "enabled": False, "description": "B"},
    }))
    s.resolve_changes(["b"])
    assert s.get("a").enabled is False
    assert s.get("b").enabled is True
```

- [ ] **Step 14: 运行确认失败**

Run: `pytest tests/test_ext_mgr.py -k "store_resolve" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'resolve_changes'`

- [ ] **Step 15: 实现 `resolve_changes` 与 `_classify_for_display`**

在 `ExtensionStore` 内追加（算法逐行等价于旧 `DependencyResolver.resolve` + `_cascade_disable`，并在末尾同步状态）：

```python
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
                d for d in self._reverse.get(name, set()) if d in to_enable
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
        for dep in self._forward.get(name, set()):
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
                deps = self._reverse.get(name, set())
                if deps and all(d in actual_disable for d in deps):
                    cascade.add(name)
                    changed = True
        return cascade
```

- [ ] **Step 16: 运行确认通过**

Run: `pytest tests/test_ext_mgr.py -k "store_resolve" -v`
Expected: PASS（5 项）

- [ ] **Step 17: 运行全套确认未破坏既有测试**

Run: `pytest tests/ -v`
Expected: PASS（87 + 3 + 3 + 3 + 5 = 101 项）

- [ ] **Step 18: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "refactor: add ExtensionStore as sole domain state owner (cascade/resolve/availability)"
```

---

## Task 4: 原子切换 — 重接全部消费者 + 提取 `DependencyGraph` + 删除 `DependencyResolver` + 迁移测试

> **本任务为单次提交。** 内部步骤有序，但仅在 Step 末尾全套测试转绿后提交一次。理由见计划开头「前置说明 第 3 条」。

**Files:**
- Modify: `ext_mgr.py`（重写 `ConfigManager`/`SymlinkManager`/`Validator`/`DialogAdapter`/`DialogUI`/`main`，删除 `DependencyResolver`，用 `Dict[str, Extension]` 版重写 `DependencyGraph`，让 `ExtensionStore` 委托给它）
- Modify: `tests/test_ext_mgr.py`（迁移 ~71 个测试，新增 `make_extensions_from_raw` helper）

### 测试迁移规则（先读）

**类别 A — `parse_depends` 测试（6 个，不动）**：`test_parse_depends_*` 保持原样。

**类别 B — `ConfigManager` 测试（约 28 个）**，迁移规则：
- `config["extensions"][n]["enabled"]` → `config.extensions[n].enabled`
- `config["extensions"][n]["type"]` → `config.extensions[n].type`
- `config["extensions"]` → `config.extensions`
- `config["warnings"]` → `config.warnings`
- `config.get("extensions", {})` → `config.extensions`
- 既有 `_valid_config()` / `_write_config()` 仍返回原始 JSON dict（它们是 JSON 级别的），**不变**；变化只在断言从「load 返回的 dict」改为「load 返回的 `Config` 对象」。

代表性 before/after（`test_validate_version3_ok`）：
```python
# before
def test_validate_version3_ok(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    config = ConfigManager(str(p)).load()
    assert "brainstorming" in config["extensions"]

# after
def test_validate_version3_ok(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    config = ConfigManager(str(p)).load()
    assert "brainstorming" in config.extensions
```

断言字段示例（`test_validate_type_derived_from_group`）：
```python
# before
    assert config["extensions"]["brainstorming"]["type"] == "skill"
# after
    assert config.extensions["brainstorming"].type == "skill"
```

`enabled` 断言示例：
```python
# before
    assert config["extensions"]["brainstorming"]["enabled"] is True
# after
    assert config.extensions["brainstorming"].enabled is True
```

`warnings` 断言示例（`test_validate_ext_dep_not_exist_warning`）：
```python
# before
    assert config["warnings"] == ["扩展 'kernel-dev' 的依赖 'ghost' 不存在"]
# after
    assert config.warnings == ["扩展 'kernel-dev' 的依赖 'ghost' 不存在"]
```

**类别 C — `DependencyResolver` 测试（约 7 个，迁到 `ExtensionStore.resolve_changes`）**，迁移规则：
- `resolver = DependencyResolver()` + `resolver.resolve(sel, exts)` → `store = ExtensionStore(make_extensions_from_raw(exts))` + `cs = store.resolve_changes(sel)`
- 其中 `make_extensions_from_raw` 见下方 Step 实现（把旧 raw dict spec 转 Extension）
- `result["to_enable"]` → `cs.to_enable`，其余同理

> **策略简化**：为减少机械转换错误，在 `tests/test_ext_mgr.py` 新增 `make_extensions_from_raw(raw_dict)` helper，把旧测试里的 raw dict（`{"type":...,"enabled":...,"depends":[...]}`）原地转成 `dict[str, Extension]`。这样旧测试只需把 fixture 喂给该 helper + 改断言访问。

代表性 before/after（`test_resolve_single_ext`）：
```python
# before
def test_resolve_single_ext():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["standalone"], exts)
    assert result["to_enable"] == ["standalone"]

# after
def test_resolve_single_ext():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["standalone"])
    assert cs.to_enable == ["standalone"]
```

**类别 D — `SymlinkManager` / `Validator` 测试（约 16 个）**，迁移规则：
- 把测试内构造的 raw `exts` dict 用 `make_extensions_from_raw(exts)` 包装
- 断言中状态字符串如有裸值，可保留（仍与 `Status.*` 等值）；本任务不强制改用常量

代表性 before/after（`test_apply_changes_with_extensions` 片段）：
```python
# before
    exts = {"a": {"type": "skill", "enabled": True, "description": "A",
                   "depends": [{"source": "a.md", "target": "a.md"}]}}
    mgr = SymlinkManager(source, target)
    results = mgr.apply_changes(["a"], [], exts)

# after
    exts = make_extensions_from_raw({"a": {"type": "skill", "enabled": True,
            "description": "A", "depends": [{"source": "a.md", "target": "a.md"}]}})
    mgr = SymlinkManager(source, target)
    results = mgr.apply_changes(["a"], [], exts)
```

**类别 E — `DialogUI` 测试（约 14 个）**，迁移规则：
- 构造：`DialogUI(adapter, config_mgr, source_dir)` → `DialogUI(adapter, store, config_mgr)`
- 原 `_cascade_disable_deps(disabled, exts)` → `store.cascade_disable(disabled)`
- 原 `_check_availability(name, exts)` → `store.check_availability(name)`
- 原 `_build_checklist_items(exts, t)` / `_count_stats(exts, t)` → 仍调用同名方法但只传 `(t,)`（内部用 store）
- `_show_type_checklist` / `show_change_summary` 等行为测试：构造 `store` 后传入，断言中 `changes["to_enable"]` → `changes.to_enable`

代表性 before/after（`test_cascade_disable_deps_disables_child`）：
```python
# before
def test_cascade_disable_deps_disables_child():
    adapter = DialogAdapter()
    ui = DialogUI(adapter, _make_config_mgr(), str(tmp))
    exts = {"parent": {...}, "child": {...}}
    ui._cascade_disable_deps({"parent"}, exts)
    assert exts["child"]["enabled"] is False

# after
def test_cascade_disable_deps_disables_child():
    exts = make_extensions_from_raw({"parent": {...}, "child": {...}})
    store = ExtensionStore(exts)
    store.cascade_disable({"parent"})
    assert exts["child"].enabled is False
```

---

- [ ] **Step 1: 在 `tests/test_ext_mgr.py` 新增 `make_extensions_from_raw`**

在 `tests/test_ext_mgr.py` 的 `make_extensions` 之后追加：

```python
def make_extensions_from_raw(raw):
    """把旧式 raw dict[name -> {type,enabled,description,depends,visible}]
    转为 dict[name -> Extension]。depends 用 parse_depends 拆分。
    用于把既有测试的 raw fixture 喂给新 API。"""
    extensions = {}
    for name, attrs in raw.items():
        ext_deps, path_deps = parse_depends(attrs.get("depends", []))
        extensions[name] = Extension(
            name=name,
            type=attrs.get("type", "skill"),
            enabled=attrs.get("enabled", False),
            description=attrs.get("description", ""),
            ext_deps=ext_deps,
            path_deps=[PathDep(p["source"], p["target"]) for p in path_deps],
            visible=attrs.get("visible", True),
        )
    return extensions
```

- [ ] **Step 2: 重写 `ext_mgr.py` 顶部 import**

将 import 区改为：

```python
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List
```

- [ ] **Step 3: 重写 `DependencyGraph`（接受 `Dict[str, Extension]`，委托给 `ExtensionStore` 使用）**

删除旧 `DependencyGraph` 类（读取 raw dict + `parse_depends` 的版本），替换为：

```python
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
```

- [ ] **Step 4: 删除 `DependencyResolver` 类**

整体删除 `class DependencyResolver`（含 `resolve` / `_collect_deps` / `_cascade_disable`）。

- [ ] **Step 5: 让 `ExtensionStore` 委托给 `DependencyGraph`**

将 Task 3 中 `ExtensionStore.__init__` 的内联邻接构建替换为持有 `DependencyGraph` 实例，并把 `deps_of` / `dependents_of` / `has_enabled_dependent` 改为委托。改后 `ExtensionStore` 顶部为：

```python
class ExtensionStore:
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
```

`check_availability`、`cascade_disable`、`resolve_changes`、`_collect_forward`、`_classify_for_display` 方法体保持 Task 3 实现不变，但其中所有 `self._forward.get(...)` 改为 `self._graph.deps_of(...)`，`self._reverse.get(...)` 改为 `self._graph.dependents_of(...)`，`self.has_enabled_dependent(dep)` 改为 `self._graph.has_enabled_dependent(dep, self._extensions)`。

具体替换后 `cascade_disable` 为：

```python
    def cascade_disable(self, seed):
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
```

`resolve_changes` 中：
- `for dep in self._forward.get(name, set())` → `for dep in self._graph.deps_of(name)`
- `self._reverse.get(name, set())` → `self._graph.dependents_of(name)`
- `_collect_forward` 内 `self._forward.get(name, set())` → `self._graph.deps_of(name)`
- `_classify_for_display` 内 `self._reverse.get(name, set())` → `self._graph.dependents_of(name)`

- [ ] **Step 6: 重写 `ConfigManager`**

整体替换 `class ConfigManager`：

```python
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
            ext_data = {
                "enabled": ext.enabled,
                "description": ext.description,
            }
            deps = list(ext.ext_deps) + [
                {"source": p.source, "target": p.target} for p in ext.path_deps
            ]
            if deps:
                ext_data["depends"] = deps
            if not ext.visible:
                ext_data["visible"] = False
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

        # pass 1：收集所有扩展名 + 结构校验（跨组查重需要全部名字）
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

        # pass 2：字段 / 格式 / 依赖存在性（用 all_names 判存在，覆盖跨组依赖）
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
```

> 说明：`_validate` 不再构建 `Extension`（只校验 raw 结构 + 返回 warnings）。`load()` 调用顺序为 `_validate(raw)` → `_build_extensions(raw["extensions"])` → `_check_circular_deps(extensions)`。字符串依赖存在性用 pass 1 收集的 `all_names` 判定，正确覆盖跨组依赖（与旧版行为一致）。

- [ ] **Step 7: 重写 `SymlinkManager`（仅改读取方式）**

把 `apply_for_extension` 内 `parse_depends(...)` 调用改为直接读字段：

```python
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
```

- [ ] **Step 8: 重写 `Validator`（仅改读取方式）**

```python
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
```

- [ ] **Step 9: 重写 `DialogAdapter`（静态 → 实例 + `check_available`）**

```python
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
```

> 行为不变：只是去掉 `@staticmethod`（除 `check_available`/`_term_size` 保留静态），调用处由 `DialogAdapter.run_menu(...)` 改为 `self._adapter.run_menu(...)` 或 `adapter.run_menu(...)`。

- [ ] **Step 10: 重写 `DialogUI`（构造改 `(adapter, store, config_manager)`，领域操作委托 store）**

```python
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
                ext.enabled = now_enabled
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
```

- [ ] **Step 11: 重写 `main()`**

```python
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
```

- [ ] **Step 12: 迁移测试 — 按类别 A–E 执行**

按「测试迁移规则」逐类别改 `tests/test_ext_mgr.py`：
- A（`parse_depends` 6 个）：不动。
- B（`ConfigManager` 约 28 个）：断言 `config[...]` → `config.<field>`。注意 `_valid_config()`/`_write_config()` 不变。
- C（`DependencyResolver` 约 7 个）：删除 `from ext_mgr import DependencyResolver` 行，改为 `from ext_mgr import ExtensionStore`，每个用例用 `make_extensions_from_raw(_extensions_for_resolver())` + `ExtensionStore(...).resolve_changes(sel)`，断言 `result["x"]` → `cs.x`。删除 `_extensions_for_resolver` 内不影响（它返回 raw dict，正好喂给 helper）。
- D（`SymlinkManager`/`Validator` 约 16 个）：每个构造 exts 处用 `make_extensions_from_raw(...)` 包装。
- E（`DialogUI` 约 14 个）：构造改 `DialogUI(adapter, store, config_mgr)`；`_cascade_disable_deps` 调用改 `store.cascade_disable`；`_check_availability` 改 `store.check_availability`；`_build_checklist_items(exts, t)` 改 `_build_checklist_items(t)`；`_count_stats(exts, t)` 改 `_count_stats(t)`；`show_change_summary` 等的 `changes["x"]` 改 `changes.x`，构造 ChangeSet 处用 `ChangeSet(to_enable=[...], ...)`。
- 更新 import 行：删除 `DependencyResolver`；新增 `ExtensionStore, ChangeSet`（如未在 Task 1/3 导入）。

- [ ] **Step 13: 运行全套测试**

Run: `pytest tests/ -v`
Expected: PASS（全部测试，含 Task 1–3 新增 + 迁移后的旧测试）。若失败，按报错定位修正（常见：漏改的 `config["..."]`、漏包装的 raw dict、`_build_checklist_items` 多传了参数）。

- [ ] **Step 14: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "refactor: atomic cutover to dataclass architecture

- ConfigManager.load returns Config; save takes Config
- SymlinkManager/Validator consume Dict[str, Extension]
- DialogAdapter: instance methods + check_available
- DialogUI: depends on ExtensionStore, no domain leakage
- DependencyGraph: accepts Dict[str, Extension], session singleton
- ExtensionStore delegates to DependencyGraph
- Remove DependencyResolver
- Migrate all tests to new API"
```

---

## Task 5: 最终验证

**Files:** 无修改（仅验证）

- [ ] **Step 1: 全套测试通过**

Run: `pytest tests/ -v`
Expected: PASS（全部）

- [ ] **Step 2: 覆盖率不低于基线 65%**

Run: `pytest --cov=ext_mgr --cov-branch tests/`
Expected: `Cover` 列 ≥ 65%（目标：持平或更高）

- [ ] **Step 3: JSON 往返字节级兼容**

Run:
```bash
python3 -c "
from ext_mgr import ConfigManager
import json
m = ConfigManager('extensions.json')
cfg = m.load()
m.save(cfg)
print('roundtrip ok')
"
git diff --exit-code extensions.json && echo "extensions.json 未变化（字节级兼容）"
```
Expected: `roundtrip ok` + `extensions.json 未变化`（git diff 退出码 0）

> 若 `git diff` 显示变化：检查 `Config.extra` 是否正确承载未知字段、`save` 是否多写/少写键、`visible` 默认值是否被错误写入。修正后重跑。

- [ ] **Step 4: 冒烟运行（可选，需 dialog 环境）**

Run: `python3 ext_mgr.py`
Expected: 正常弹出目标目录输入框（手动 Cancel 退出即可）。

- [ ] **Step 5: 检查无残留旧 API**

Run:
```bash
grep -nE "DependencyResolver|check_dialog_available|ext\[.enabled.\]|result\[.to_enable.\]" ext_mgr.py
```
Expected: 无输出（旧 API 全部清除）

- [ ] **Step 6: 计划完成标记**

全部 Step 通过后，重构完成。如需提交验证记录：
```bash
git log --oneline -5
```
确认提交链：数据模型 → helper → ExtensionStore → 原子切换 → （验证无新提交，或仅文档）。

---

## Self-Review 已完成

- **Spec 覆盖**：设计文档第 2 节（分区）→ Task 4 重接全部层；第 3 节（数据模型）→ Task 1；第 4 节（领域层/级联统一）→ Task 3 + Task 4 Step 3–5；第 5 节（I/O 层）→ Task 4 Step 6–8；第 6 节（UI 层）→ Task 4 Step 9–10；第 7 节（常量）→ Task 1；第 8 节（main）→ Task 4 Step 11；第 9 节（API 迁移）→ Task 4 Step 12；第 10 节（测试）→ Task 1–4；第 11 节（验证基线）→ Task 5。无遗漏。
- **占位符**：无 TODO/TBD/占位代码。Task 4 Step 6 的 `_validate` 已给出干净正确的两遍扫描版本（pass 1 收集 `all_names`，pass 2 用之判存在性，正确覆盖跨组依赖）。
- **类型一致性**：`ExtensionStore.cascade_disable(seed: set) -> set`、`resolve_changes(selected) -> ChangeSet`、`check_availability(name) -> list`、`DialogUI.__init__(adapter, store, config_manager)`、`DialogAdapter.check_available()`、`ConfigManager.load() -> Config` / `save(Config)` 在各 Task 间一致。`Status.SKIPPED`/`Status.SUCCESS` 等常量在 SymlinkManager/Validator/DialogUI 中使用一致。

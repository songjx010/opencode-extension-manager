# Extension Schema Refactor 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 extensions.json 数据结构和 ext_mgr.py 处理逻辑，使 key 为纯扩展名，类型通过 type 字段声明，路径映射显式写在 depends 中。

**Architecture:** 保持单文件 ext_mgr.py 架构不变。新增模块级 parse_depends() 辅助函数统一解析混合格式 depends。ConfigManager 校验新 schema（version=2, type 字段, 纯名称 key）。SymlinkManager 从"按路径名操作"改为"按扩展操作"，路径从 depends 路径依赖项获取。DependencyResolver 使用 parse_depends 分离扩展依赖和路径依赖。

**Tech Stack:** Python 3.8+, pytest（测试）, dialog TUI, 无第三方运行时依赖

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `ext_mgr.py` | 修改 | 全部实现代码：parse_depends, ConfigManager, DependencyResolver, SymlinkManager, Validator, DialogUI, main |
| `tests/conftest.py` | 创建 | 测试路径配置，将项目根目录加入 sys.path |
| `tests/test_ext_mgr.py` | 创建 | 所有单元测试 |
| `extensions.json` | 修改 | 手动迁移到 version=2 新格式 |

---

### Task 1: 测试基础设施 + parse_depends()

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_ext_mgr.py`
- Modify: `ext_mgr.py:13`（VALID_CATEGORIES 行之后插入函数）

- [ ] **Step 1: 安装 pytest**

```bash
pip3 install pytest
```

Run: `python3 -c "import pytest; print(pytest.__version__)"`
Expected: 输出版本号，无报错

- [ ] **Step 2: 创建 tests 目录**

```bash
mkdir -p tests
```

- [ ] **Step 3: 创建 tests/conftest.py**

```python
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 4: 写 parse_depends 测试**

```python
from ext_mgr import parse_depends


def test_parse_depends_empty():
    ext_deps, path_deps = parse_depends([])
    assert ext_deps == []
    assert path_deps == []


def test_parse_depends_strings_only():
    ext_deps, path_deps = parse_depends(["ext-a", "ext-b"])
    assert ext_deps == ["ext-a", "ext-b"]
    assert path_deps == []


def test_parse_depends_dicts_only():
    ext_deps, path_deps = parse_depends([
        {"source": "a.md", "target": "b.md"},
        {"source": "c.md", "target": "d.md"},
    ])
    assert ext_deps == []
    assert path_deps == [
        {"source": "a.md", "target": "b.md"},
        {"source": "c.md", "target": "d.md"},
    ]


def test_parse_depends_mixed():
    ext_deps, path_deps = parse_depends([
        "ext-a",
        {"source": "a.md", "target": "a.md"},
        "ext-b",
        {"source": "b.md", "target": "c.md"},
    ])
    assert ext_deps == ["ext-a", "ext-b"]
    assert path_deps == [
        {"source": "a.md", "target": "a.md"},
        {"source": "b.md", "target": "c.md"},
    ]


def test_parse_depends_ignores_unknown_types():
    ext_deps, path_deps = parse_depends([123, True])
    assert ext_deps == []
    assert path_deps == []
```

- [ ] **Step 5: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_depends' from 'ext_mgr'`

- [ ] **Step 6: 实现 parse_depends()**

在 `ext_mgr.py` 中 `VALID_CATEGORIES` 行（第13行）之后添加：

```python
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
```

注意：`VALID_CATEGORIES` 保留不删，在 Task 2 中统一替换。

- [ ] **Step 7: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add tests/ ext_mgr.py
git commit -m "feat: add parse_depends() helper and test infrastructure"
```

---

### Task 2: ConfigManager._validate() 重构

**Files:**
- Modify: `ext_mgr.py:13`（删除 VALID_CATEGORIES）
- Modify: `ext_mgr.py:57-105`（_validate 方法）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 ConfigManager._validate 测试**

追加到 `tests/test_ext_mgr.py`：

```python
import json
import os
import pytest
from ext_mgr import ConfigManager, ConfigError


def _write_config(tmp_path, config_dict):
    p = tmp_path / "extensions.json"
    p.write_text(json.dumps(config_dict, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _valid_config():
    return {
        "version": 2,
        "extensions": {
            "brainstorming": {
                "type": "skill",
                "enabled": True,
                "description": "头脑风暴",
                "depends": [
                    {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
                ],
            },
            "kernel-dev": {
                "type": "agent",
                "enabled": False,
                "description": "Kernel开发",
                "depends": [
                    "brainstorming",
                    {"source": "agents/kernel.md", "target": "agents/kernel.md"},
                ],
            },
        },
    }


def test_validate_version2_ok(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    mgr = ConfigManager(p)
    config = mgr.load()
    assert config["version"] == 2
    assert config["warnings"] == []


def test_validate_version1_rejected(tmp_path):
    cfg = _valid_config()
    cfg["version"] = 1
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="不支持的 version: 1"):
        ConfigManager(p).load()


def test_validate_missing_version(tmp_path):
    cfg = _valid_config()
    del cfg["version"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 version 字段"):
        ConfigManager(p).load()


def test_validate_type_skill_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "skill"
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"]["brainstorming"]["type"] == "skill"


def test_validate_type_plugin_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "plugin"
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"]["brainstorming"]["type"] == "plugin"


def test_validate_type_unknown_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "unknown"
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="type 'unknown' 不合法"):
        ConfigManager(p).load()


def test_validate_missing_type(tmp_path):
    cfg = _valid_config()
    del cfg["extensions"]["brainstorming"]["type"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 type 字段"):
        ConfigManager(p).load()


def test_validate_key_with_slash_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"].pop("brainstorming")
    cfg["extensions"]["skills/brainstorming"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="格式错误"):
        ConfigManager(p).load()


def test_validate_key_with_dotdot_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"].pop("brainstorming")
    cfg["extensions"]["../evil"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="非法字符"):
        ConfigManager(p).load()


def test_validate_depends_path_dep_missing_source(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [
        {"target": "skills/brainstorming.md"}
    ]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 source 或 target 字段"):
        ConfigManager(p).load()


def test_validate_depends_invalid_type(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [123]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="依赖类型不合法"):
        ConfigManager(p).load()


def test_validate_ext_dep_not_exist_warning(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = ["nonexistent"]
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert any("nonexistent" in w for w in config["warnings"])


def test_validate_ext_dep_with_slash_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = ["skills/other"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="格式错误"):
        ConfigManager(p).load()


def test_validate_ext_dep_empty_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [""]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="扩展依赖名称不能为空"):
        ConfigManager(p).load()


def test_validate_empty_extensions_ok(tmp_path):
    cfg = {"version": 2, "extensions": {}}
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"] == {}


def test_validate_empty_depends_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = []
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_validate_version1_rejected -v`
Expected: FAIL — 旧代码接受 version=1 而非拒绝

- [ ] **Step 3: 重写 _validate()**

替换 `ext_mgr.py` 中的 `VALID_CATEGORIES` 行（第13行）：

将第13行 `VALID_CATEGORIES = {"skills", "agents", "commands"}` 删除（`VALID_TYPES` 已在 Task 1 添加）。

替换 `ext_mgr.py` 中 `_validate` 方法（第57-105行）为：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: rewrite ConfigManager._validate for version=2 schema"
```

---

### Task 3: ConfigManager._check_circular_deps() 适配

**Files:**
- Modify: `ext_mgr.py:107-128`（_check_circular_deps 方法）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写循环依赖测试**

追加到 `tests/test_ext_mgr.py`：

```python
def test_no_cycle(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []


def test_simple_cycle(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": ["b"],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": ["a"],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="循环依赖"):
        ConfigManager(p).load()


def test_three_node_cycle(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": ["b"],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": ["c"],
            },
            "c": {
                "type": "command",
                "enabled": True,
                "description": "C",
                "depends": [
                    "a",
                    {"source": "c.md", "target": "c.md"},
                ],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="循环依赖"):
        ConfigManager(p).load()


def test_cycle_with_path_deps_no_false_positive(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": [
                    {"source": "a.md", "target": "a.md"},
                ],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": [
                    "a",
                    {"source": "b.md", "target": "b.md"},
                ],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_three_node_cycle -v`
Expected: FAIL — 旧代码将路径依赖对象当作字符串处理，不会正确检测仅含字符串依赖的循环

- [ ] **Step 3: 重写 _check_circular_deps()**

替换 `ext_mgr.py` 中 `_check_circular_deps` 方法为：

```python
    def _check_circular_deps(self, exts: dict) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in exts}

        def dfs(name: str, path: list) -> None:
            color[name] = GRAY
            path.append(name)
            ext_deps, _ = parse_depends(exts[name].get("depends", []))
            for dep in ext_deps:
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

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: adapt _check_circular_deps to use parse_depends"
```

---

### Task 4: DependencyResolver 重构

**Files:**
- Modify: `ext_mgr.py:131-172`（DependencyResolver 类）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 DependencyResolver 测试**

追加到 `tests/test_ext_mgr.py`：

```python
from ext_mgr import DependencyResolver


def _extensions_for_resolver():
    return {
        "a": {
            "type": "skill",
            "enabled": False,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": False,
            "description": "B",
            "depends": [
                "c",
                {"source": "b.md", "target": "b.md"},
            ],
        },
        "c": {
            "type": "agent",
            "enabled": False,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
        "standalone": {
            "type": "skill",
            "enabled": False,
            "description": "Standalone",
            "depends": [{"source": "s.md", "target": "s.md"}],
        },
    }


def test_resolve_single_ext():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["standalone"], exts)
    assert result["to_enable"] == ["standalone"]
    assert "standalone" not in result["to_disable"]


def test_resolve_with_ext_dep():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert "a" in result["to_enable"]
    assert "b" in result["to_enable"]


def test_resolve_transitive_deps():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert sorted(result["to_enable"]) == ["a", "b", "c"]


def test_resolve_disable_no_cascade():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert "standalone" in result["to_disable"]


def test_resolve_reject_if_depended():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    exts["a"]["enabled"] = True
    result = resolver.resolve(["a"], exts)
    rejected_names = [r["name"] for r in result["rejected"]]
    assert "b" not in rejected_names
    assert "c" not in rejected_names


def test_resolve_ext_dep_not_in_extensions():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": False,
            "description": "A",
            "depends": ["nonexistent"],
        }
    }
    result = resolver.resolve(["a"], exts)
    assert result["to_enable"] == ["a"]


def test_resolve_all_enabled_no_disable():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a", "standalone"], exts)
    assert result["to_enable"] == ["a", "b", "c", "standalone"]
    assert result["to_disable"] == []
    assert result["rejected"] == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_resolve_with_ext_dep -v`
Expected: FAIL — 旧 _collect_deps 把路径依赖对象当字符串处理，无法正确匹配扩展名

- [ ] **Step 3: 重写 DependencyResolver**

替换 `ext_mgr.py` 中整个 `DependencyResolver` 类（第131-172行）为：

```python
class DependencyResolver:
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

        return {
            "to_enable": sorted(to_enable),
            "to_disable": sorted(to_disable),
            "rejected": rejected,
        }

    def _collect_deps(self, name: str, extensions: dict, collected: set) -> None:
        if name not in extensions:
            return
        ext_deps, _ = parse_depends(extensions[name].get("depends", []))
        for dep in ext_deps:
            if dep not in collected and dep in extensions:
                collected.add(dep)
                self._collect_deps(dep, extensions, collected)

    def _find_dependents(
        self, name: str, extensions: dict, selected: set
    ) -> list:
        dependents = []
        for ext_name, ext_data in extensions.items():
            if ext_name in selected:
                ext_deps, _ = parse_depends(ext_data.get("depends", []))
                if name in ext_deps:
                    dependents.append(ext_name)
        return sorted(dependents)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: rewrite DependencyResolver to use parse_depends"
```

---

### Task 5: SymlinkManager 重构

**Files:**
- Modify: `ext_mgr.py:175-249`（SymlinkManager 类）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 SymlinkManager 测试**

追加到 `tests/test_ext_mgr.py`：

```python
from ext_mgr import SymlinkManager


def _setup_dirs(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    return str(source), str(target)


def test_create_symlink_success(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "brainstorming.md").write_text("skill")
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert len(results) == 1
    assert results[0]["status"] == "success"
    link = os.path.join(target, "skills", "brainstorming.md")
    assert os.path.islink(link)


def test_create_symlink_already_exists_correct(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert results[0]["status"] == "skipped"


def test_create_symlink_conflict(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "target" / "skills").mkdir()
    conflict = tmp_path / "target" / "skills" / "brainstorming.md"
    conflict.write_text("other")
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert results[0]["status"] == "conflict"


def test_remove_symlink_success(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "success"
    assert not os.path.exists(os.path.join(target, "skills", "brainstorming.md"))


def test_remove_symlink_not_exist(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "skipped"


def test_apply_for_extension_multiple_paths(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "main.md").write_text("main")
    (tmp_path / "source" / "skills" / "helper.md").write_text("helper")
    mgr = SymlinkManager(source, target)
    exts = {
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    }
    results = mgr.apply_for_extension("multi", exts, "create")
    assert len(results) == 2
    assert all(r["status"] == "success" for r in results)


def test_apply_for_extension_no_path_deps(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = {
        "pure-dep": {
            "type": "skill",
            "enabled": True,
            "description": "PureDep",
            "depends": ["other-ext"],
        }
    }
    results = mgr.apply_for_extension("pure-dep", exts, "create")
    assert len(results) == 1
    assert results[0]["status"] == "skipped"


def test_apply_changes_with_extensions(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "a.md").write_text("a")
    (tmp_path / "source" / "skills" / "b.md").write_text("b")
    mgr = SymlinkManager(source, target)
    exts = {
        "ext-a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": [{"source": "skills/a.md", "target": "skills/a.md"}],
        },
        "ext-b": {
            "type": "skill",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "skills/b.md", "target": "skills/b.md"}],
        },
    }
    results = mgr.apply_changes(["ext-a"], ["ext-b"], exts)
    success_names = [r["name"] for r in results if r["status"] == "success"]
    assert "skills/a.md" in success_names
    assert "skills/b.md" in success_names
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_create_symlink_success -v`
Expected: FAIL — 旧 apply_for_extension 方法不存在

- [ ] **Step 3: 重写 SymlinkManager**

替换 `ext_mgr.py` 中整个 `SymlinkManager` 类（第175-249行）为：

```python
class SymlinkManager:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def apply_changes(
        self, to_enable: list, to_disable: list, extensions: dict
    ) -> list:
        results = []
        for name in to_enable:
            results.extend(self.apply_for_extension(name, extensions, "create"))
        for name in to_disable:
            results.extend(self.apply_for_extension(name, extensions, "remove"))
        return results

    def apply_for_extension(
        self, ext_name: str, extensions: dict, action: str
    ) -> list:
        _, path_deps = parse_depends(extensions[ext_name].get("depends", []))
        results = []
        for dep in path_deps:
            if action == "create":
                results.append(
                    self._create_symlink(dep["source"], dep["target"])
                )
            else:
                results.append(
                    self._remove_symlink(dep["source"], dep["target"])
                )
        if not path_deps:
            results.append(
                {"name": ext_name, "status": "skipped", "detail": "无路径依赖"}
            )
        return results

    def _create_symlink(self, source_rel: str, target_rel: str) -> dict:
        source = os.path.join(self._source_dir, source_rel)
        target = os.path.join(self._target_dir, target_rel)
        self._ensure_subdir(os.path.dirname(target))

        if os.path.islink(target):
            existing = os.readlink(target)
            if os.path.abspath(existing) == os.path.abspath(source):
                return {"name": target_rel, "status": "skipped", "detail": ""}
            return {
                "name": target_rel,
                "status": "conflict",
                "detail": f"符号链接已指向 {existing}",
            }

        if os.path.exists(target):
            return {
                "name": target_rel,
                "status": "conflict",
                "detail": f"目标路径 {target} 已存在",
            }

        try:
            os.symlink(source, target)
            return {"name": target_rel, "status": "success", "detail": ""}
        except OSError as e:
            return {"name": target_rel, "status": "error", "detail": str(e)}

    def _remove_symlink(self, source_rel: str, target_rel: str) -> dict:
        source = os.path.join(self._source_dir, source_rel)
        target = os.path.join(self._target_dir, target_rel)

        if not os.path.islink(target):
            if not os.path.exists(target):
                return {"name": target_rel, "status": "skipped", "detail": ""}
            return {
                "name": target_rel,
                "status": "conflict",
                "detail": f"目标路径 {target} 存在但非符号链接",
            }

        existing = os.readlink(target)
        if os.path.abspath(existing) != os.path.abspath(source):
            return {
                "name": target_rel,
                "status": "conflict",
                "detail": f"符号链接指向 {existing}，非预期目标",
            }

        try:
            os.unlink(target)
            return {"name": target_rel, "status": "success", "detail": ""}
        except OSError as e:
            return {"name": target_rel, "status": "error", "detail": str(e)}

    def _ensure_subdir(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: rewrite SymlinkManager to use depends path items"
```

---

### Task 6: Validator 重构

**Files:**
- Modify: `ext_mgr.py`（Validator 类，原第252-304行）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 Validator 测试**

追加到 `tests/test_ext_mgr.py`：

```python
from ext_mgr import Validator


def test_validate_enabled_ok(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert len(results) == 1
    assert results[0]["status"] == "ok"


def test_validate_enabled_missing(tmp_path):
    source, target = _setup_dirs(tmp_path)
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert any(r["status"] == "missing" for r in results)


def test_validate_disabled_unexpected(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": False,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert any(r["status"] == "unexpected" for r in results)


def test_validate_enabled_broken_link(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink("/nonexistent/path", os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert any(r["status"] == "broken" for r in results)


def test_validate_no_target_dir(tmp_path):
    source = str(tmp_path / "source")
    target = str(tmp_path / "nonexistent_target")
    os.makedirs(source)
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert any(r["status"] == "missing" for r in results)


def test_validate_multiple_paths_per_extension(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "main.md").write_text("main")
    (tmp_path / "target" / "skills").mkdir()
    src_main = tmp_path / "source" / "skills" / "main.md"
    os.symlink(str(src_main), os.path.join(target, "skills", "main.md"))
    validator = Validator(source, target)
    exts = {
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    }
    results = validator.validate(exts)
    statuses = [r["status"] for r in results]
    assert "ok" not in statuses
    assert any(s == "missing" for s in statuses)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_validate_enabled_ok -v`
Expected: FAIL — 旧 Validator 通过 `category/name` 拼接路径，新 key 不含 `/`

- [ ] **Step 3: 重写 Validator**

替换 `ext_mgr.py` 中整个 `Validator` 类为：

```python
class Validator:
    def __init__(self, source_dir: str, target_dir: str):
        self._source_dir = os.path.abspath(source_dir)
        self._target_dir = os.path.abspath(target_dir)

    def validate(self, extensions: dict) -> list:
        results = []
        if not os.path.isdir(self._target_dir):
            for name, ext in extensions.items():
                if ext.get("enabled", False):
                    _, path_deps = parse_depends(ext.get("depends", []))
                    if path_deps:
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
            _, path_deps = parse_depends(ext.get("depends", []))

            if enabled:
                for dep in path_deps:
                    target = os.path.join(self._target_dir, dep["target"])
                    source = os.path.join(self._source_dir, dep["source"])
                    if not os.path.islink(target):
                        results.append(
                            {
                                "name": f"{name}:{dep['target']}",
                                "status": "missing",
                                "detail": "符号链接缺失",
                            }
                        )
                    else:
                        actual = os.readlink(target)
                        if os.path.abspath(actual) != os.path.abspath(source):
                            results.append(
                                {
                                    "name": f"{name}:{dep['target']}",
                                    "status": "broken",
                                    "detail": f"指向错误目标: {actual}",
                                }
                            )
            else:
                for dep in path_deps:
                    target = os.path.join(self._target_dir, dep["target"])
                    if os.path.islink(target):
                        results.append(
                            {
                                "name": f"{name}:{dep['target']}",
                                "status": "unexpected",
                                "detail": "已禁用但符号链接仍存在",
                            }
                        )

        if not results:
            results.append(
                {"name": "", "status": "ok", "detail": "所有扩展状态正常"}
            )

        return results
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: rewrite Validator to use depends path items"
```

---

### Task 7: DialogUI 适配

**Files:**
- Modify: `ext_mgr.py`（DialogUI 类，原第432-638行）
- Modify: `tests/test_ext_mgr.py`（追加测试）

- [ ] **Step 1: 写 DialogUI 核心逻辑测试**

追加到 `tests/test_ext_mgr.py`：

```python
from unittest.mock import MagicMock
from ext_mgr import DialogUI


def _make_ui(source_dir="/fake"):
    adapter = MagicMock()
    config_mgr = MagicMock()
    ui = DialogUI(adapter, config_mgr, source_dir)
    return ui


def test_check_availability_all_present(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert missing == []


def test_check_availability_ext_dep_missing(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": ["nonexistent-ext"],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert "nonexistent-ext" in missing


def test_check_availability_path_dep_source_missing(tmp_path):
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert "skills/brainstorming.md" in missing


def test_build_checklist_items_filters_by_type():
    ui = _make_ui()
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": False,
            "description": "Kernel Dev",
        },
    }
    items, unavailable = ui._build_checklist_items(exts, "skill")
    names = [i[0] for i in items]
    assert "brainstorming" in names
    assert "kernel-dev" not in names


def test_build_checklist_items_filters_agent():
    ui = _make_ui()
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": False,
            "description": "Kernel Dev",
        },
    }
    items, unavailable = ui._build_checklist_items(exts, "agent")
    names = [i[0] for i in items]
    assert "kernel-dev" in names
    assert "brainstorming" not in names


def test_count_stats_by_type():
    ui = _make_ui()
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "code-review": {
            "type": "skill",
            "enabled": False,
            "description": "Code Review",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": True,
            "description": "Kernel Dev",
        },
    }
    total, enabled, ok = ui._count_stats(exts, "skill")
    assert total == 2
    assert enabled == 1


def test_count_stats_empty_type():
    ui = _make_ui()
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
    }
    total, enabled, ok = ui._count_stats(exts, "plugin")
    assert total == 0
    assert enabled == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_ext_mgr.py::test_build_checklist_items_filters_by_type -v`
Expected: FAIL — 旧代码按 key 前缀 `category + "/"` 过滤，新 key 不含前缀

- [ ] **Step 3: 重写 DialogUI**

替换 `ext_mgr.py` 中 `DialogUI` 类的以下部分：

**常量**：将 `CATEGORY_LABELS` 和 `CATEGORY_ORDER`（原第433-438行）替换为：

```python
    TYPES_LABELS = {
        "skill": "Skills  — 技能扩展",
        "agent": "Agents — 智能体",
        "command": "Commands — 命令编排",
        "plugin": "Plugins — 插件扩展",
    }
    TYPES_ORDER = ["skill", "agent", "command", "plugin"]
```

**_check_availability 方法**：替换为：

```python
    def _check_availability(self, name: str, extensions: dict) -> list:
        missing = []
        ext_deps, path_deps = parse_depends(
            extensions.get(name, {}).get("depends", [])
        )
        for dep in ext_deps:
            if dep not in extensions:
                missing.append(dep)
        for dep in path_deps:
            source_path = os.path.join(self._source_dir, dep["source"])
            if not os.path.exists(source_path):
                missing.append(dep["source"])
        return missing
```

**_build_checklist_items 方法**：替换为：

```python
    def _build_checklist_items(self, extensions: dict, ext_type: str) -> tuple:
        items = []
        unavailable = set()
        for name, ext in extensions.items():
            if ext.get("type") != ext_type:
                continue
            missing = self._check_availability(name, extensions)
            if missing:
                unavailable.add(name)
                mark = "\\Zr !! \\ZR"
                help_text = "缺失依赖: " + ", ".join(missing)
            else:
                mark = "\\Zb\\Z2 OK \\Zn"
                help_text = ext.get("description", "")
            text = f"{mark} {ext.get('description', '')}"
            items.append((name, ext.get("enabled", False), text, help_text))
        return items, unavailable
```

**_count_stats 方法**：替换为：

```python
    def _count_stats(self, extensions: dict, ext_type: str) -> tuple:
        total = 0
        ok = 0
        enabled = 0
        for name, ext in extensions.items():
            if ext.get("type") != ext_type:
                continue
            total += 1
            if ext.get("enabled", False):
                enabled += 1
            if not self._check_availability(name, extensions):
                ok += 1
        return total, enabled, ok
```

**show_extension_list 方法**：将所有 `CATEGORY_ORDER` 替换为 `TYPES_ORDER`，所有 `CATEGORY_LABELS` 替换为 `TYPES_LABELS`。完整方法：

```python
    def show_extension_list(self, extensions: dict) -> tuple:
        while True:
            menu_items = []
            max_label_w = 0
            for t in self.TYPES_ORDER:
                total, _, _ = self._count_stats(extensions, t)
                if total > 0:
                    max_label_w = max(
                        max_label_w,
                        self._visible_len(self.TYPES_LABELS.get(t, t)),
                    )
            for t in self.TYPES_ORDER:
                total, enabled, ok = self._count_stats(extensions, t)
                if total == 0:
                    continue
                label = self._pad_label(
                    self.TYPES_LABELS.get(t, t), max_label_w
                )
                stats = (
                    f"\t\\Zb\\Z1{enabled}/{total} 启用\\Zn"
                    f"\t\\Zb\\Z5{ok}/{total} 可用\\Zn"
                )
                menu_items.append((t, label + stats))
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

            if choice in self.TYPES_ORDER:
                action = self._show_type_checklist(extensions, choice)
                if action == "apply":
                    return "ok", [
                        name for name, ext in extensions.items()
                        if ext.get("enabled", False)
                    ]
```

**_show_category_checklist → _show_type_checklist**：替换为：

```python
    def _show_type_checklist(self, extensions: dict, ext_type: str) -> str:
        items, unavailable = self._build_checklist_items(extensions, ext_type)
        if not items:
            self._adapter.run_msgbox("提示", "该分类下没有扩展")
            return "back"

        while True:
            label = self.TYPES_LABELS.get(ext_type, ext_type)
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
                if ext.get("type") == ext_type:
                    ext["enabled"] = name in selected

            items, unavailable = self._build_checklist_items(
                extensions, ext_type
            )
            return "back"
```

注意：`show_change_summary`, `show_results`, `show_validation_results`, `show_error`, `show_target_dir_input` 方法无需修改。

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: adapt DialogUI to use type field instead of key prefix"
```

---

### Task 8: main() 入口适配

**Files:**
- Modify: `ext_mgr.py`（main 函数，原第640-709行）

- [ ] **Step 1: 修改 main() 中的 apply_changes 调用**

将 main() 中第690-692行：

```python
        results = symlink_mgr.apply_changes(
            changes["to_enable"], changes["to_disable"]
        )
```

替换为：

```python
        results = symlink_mgr.apply_changes(
            changes["to_enable"], changes["to_disable"], extensions
        )
```

这是唯一的 main() 变更。其余逻辑（ConfigManager, DependencyResolver, Validator 初始化、循环结构、config save）保持不变。

- [ ] **Step 2: 验证语法正确**

Run: `python3 -c "import ext_mgr; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: 运行全部测试**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add ext_mgr.py
git commit -m "feat: update main() to pass extensions to SymlinkManager"
```

---

### Task 9: extensions.json 数据迁移

**Files:**
- Modify: `extensions.json`

- [ ] **Step 1: 将 extensions.json 迁移到 version=2 新格式**

将 `extensions.json` 整体替换为：

```json
{
  "version": 2,
  "extensions": {
    "ascend-c-integrated-development": {
      "type": "skill",
      "enabled": true,
      "description": "Ascend C自定义算子全流程开发（kernel/host/ONNX插件）",
      "depends": [
        "kernel-side-code-developer",
        "host-side-code-developer",
        "onnx-plugin-developer",
        {"source": "skills/ascend-c-integrated-development.md", "target": "skills/ascend-c-integrated-development.md"}
      ]
    },
    "kernel-side-code-developer": {
      "type": "agent",
      "enabled": true,
      "description": "Kernel侧代码开发",
      "depends": [
        {"source": "agents/kernel-side-code-developer.md", "target": "agents/kernel-side-code-developer.md"}
      ]
    },
    "host-side-code-developer": {
      "type": "agent",
      "enabled": true,
      "description": "Host侧代码开发",
      "depends": [
        {"source": "agents/host-side-code-developer.md", "target": "agents/host-side-code-developer.md"}
      ]
    },
    "onnx-plugin-developer": {
      "type": "agent",
      "enabled": true,
      "description": "ONNX插件开发",
      "depends": [
        {"source": "agents/onnx-plugin-developer.md", "target": "agents/onnx-plugin-developer.md"}
      ]
    },
    "brainstorming": {
      "type": "skill",
      "enabled": true,
      "description": "结构化头脑风暴，创意工作前必用",
      "depends": [
        {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
      ]
    },
    "code-review-cpp-enhanced": {
      "type": "skill",
      "enabled": false,
      "description": "深度C++代码检视（L1-L5五层金字塔），支持MR审查和本地审查",
      "depends": [
        "code-review-cpp-guidelines",
        {"source": "skills/code-review-cpp-enhanced.md", "target": "skills/code-review-cpp-enhanced.md"}
      ]
    },
    "code-review-cpp-guidelines": {
      "type": "skill",
      "enabled": false,
      "description": "基于华为C++编程规范的代码审查指南",
      "depends": [
        {"source": "skills/code-review-cpp-guidelines.md", "target": "skills/code-review-cpp-guidelines.md"}
      ]
    },
    "cpp-interface-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++接口设计检视（参数安全/异常处理/移动语义）",
      "depends": [
        {"source": "agents/cpp-interface-reviewer.md", "target": "agents/cpp-interface-reviewer.md"}
      ]
    },
    "cpp-memory-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++内存安全审查（泄漏/悬垂/越界）",
      "depends": [
        {"source": "agents/cpp-memory-reviewer.md", "target": "agents/cpp-memory-reviewer.md"}
      ]
    },
    "cpp-concurrency-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++并发安全审查（竞态/死锁/数据竞争）",
      "depends": [
        {"source": "agents/cpp-concurrency-reviewer.md", "target": "agents/cpp-concurrency-reviewer.md"}
      ]
    },
    "cpp-logic-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++逻辑缺陷检测（控制流/边界/空指针）",
      "depends": [
        {"source": "agents/cpp-logic-reviewer.md", "target": "agents/cpp-logic-reviewer.md"}
      ]
    },
    "cpp-bug-scorer": {
      "type": "agent",
      "enabled": false,
      "description": "C++缺陷严重度评分",
      "depends": [
        {"source": "agents/cpp-bug-scorer.md", "target": "agents/cpp-bug-scorer.md"}
      ]
    },
    "cpp-idiom-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++惯用法审查（RAII/Pimpl/CRTP）",
      "depends": [
        {"source": "agents/cpp-idiom-reviewer.md", "target": "agents/cpp-idiom-reviewer.md"}
      ]
    },
    "cpp-stl-reviewer": {
      "type": "agent",
      "enabled": false,
      "description": "C++ STL使用审查（容器选择/算法/迭代器）",
      "depends": [
        {"source": "agents/cpp-stl-reviewer.md", "target": "agents/cpp-stl-reviewer.md"}
      ]
    },
    "cpp-standards-scorer": {
      "type": "agent",
      "enabled": false,
      "description": "C++编码规范评分（按华为标准）",
      "depends": [
        {"source": "agents/cpp-standards-scorer.md", "target": "agents/cpp-standards-scorer.md"}
      ]
    },
    "complex-task": {
      "type": "command",
      "enabled": false,
      "description": "编排复杂多步骤任务",
      "depends": [
        "brainstorming",
        "diagram-generator",
        "cpp-code-review",
        "cpp-guideline-review",
        {"source": "commands/complex-task.md", "target": "commands/complex-task.md"}
      ]
    },
    "diagram-generator": {
      "type": "skill",
      "enabled": false,
      "description": "生成架构图和流程图",
      "depends": [
        {"source": "skills/diagram-generator.md", "target": "skills/diagram-generator.md"}
      ]
    },
    "cpp-code-review": {
      "type": "command",
      "enabled": false,
      "description": "C++逻辑缺陷检测（内存/并发/控制流）",
      "depends": [
        "cpp-memory-reviewer",
        "cpp-concurrency-reviewer",
        "cpp-logic-reviewer",
        "cpp-bug-scorer",
        {"source": "commands/cpp-code-review.md", "target": "commands/cpp-code-review.md"}
      ]
    },
    "cpp-guideline-review": {
      "type": "command",
      "enabled": false,
      "description": "C++编码规范审查（惯用法/接口/STL/规范评分）",
      "depends": [
        "cpp-idiom-reviewer",
        "cpp-interface-reviewer",
        "cpp-stl-reviewer",
        "cpp-standards-scorer",
        {"source": "commands/cpp-guideline-review.md", "target": "commands/cpp-guideline-review.md"},
        {"source": "references/cpp-coding-standards", "target": "references/cpp-coding-standards"}
      ]
    }
  }
}
```

- [ ] **Step 2: 验证 JSON 格式正确**

Run: `python3 -c "import json; json.load(open('extensions.json')); print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: 验证 ConfigManager 可加载**

Run: `python3 -c "from ext_mgr import ConfigManager; c = ConfigManager('extensions.json'); config = c.load(); print(f'Loaded {len(config[\"extensions\"])} extensions'); print(f'Warnings: {config[\"warnings\"]}')" `
Expected: 输出扩展数量和空的警告列表

- [ ] **Step 4: 运行全部测试**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add extensions.json
git commit -m "feat: migrate extensions.json to version=2 schema"
```

---

### Task 10: 清理遗留代码

**Files:**
- Modify: `ext_mgr.py`

- [ ] **Step 1: 删除 VALID_CATEGORIES**

在 `ext_mgr.py` 中找到 `VALID_CATEGORIES = {"skills", "agents", "commands"}` 行，删除整行。此时所有代码已不再引用此常量。

- [ ] **Step 2: 验证无 VALID_CATEGORIES 引用**

Run: `python3 -c "import ext_mgr; print('VALID_TYPES' in dir(ext_mgr)); print('VALID_CATEGORIES' in dir(ext_mgr))"`
Expected: 输出 `True` 然后 `False`

- [ ] **Step 3: 运行全部测试**

Run: `python3 -m pytest tests/test_ext_mgr.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add ext_mgr.py
git commit -m "chore: remove deprecated VALID_CATEGORIES constant"
```

---

## 自审检查

### 1. Spec 覆盖度

| SRS 需求 | 对应 Task |
|----------|-----------|
| FR-001 新数据结构 (version=2, type, depends混合) | Task 2 |
| FR-002 扩展类型校验 (VALID_TYPES) | Task 2 |
| FR-003 key 格式校验 (纯名称) | Task 2 |
| FR-004 depends 混合格式解析 | Task 1 |
| FR-005 依赖解析与展开（使能） | Task 4 |
| FR-006 依赖清理（去使能） | Task 4 |
| FR-007 依赖缺失批量报告 | Task 5 (SymlinkManager 返回所有结果) |
| FR-008 SymlinkManager 路径解析重构 | Task 5 |
| FR-009 DependencyResolver 重构 | Task 4 |
| FR-010 version 字段更新 | Task 2 |
| FR-011 DialogUI 分类展示适配 | Task 7 |
| FR-012 Validator 校验逻辑适配 | Task 6 |
| NFR-001 错误信息可读性 | Task 2 (所有错误包含扩展名) |
| NFR-002 数据完整性 | 保持不变 (save 方法未改) |

### 2. 占位符扫描

- 无 TBD / TODO / "implement later"
- 无 "add appropriate error handling" / "add validation"
- 无 "write tests for the above" (所有测试代码完整)
- 无 "similar to Task N" (代码均已完整展示)
- 所有步骤均含代码块或精确命令

### 3. 类型一致性

- `parse_depends()` 返回 `(list[str], list[dict])` — 所有调用处一致
- `SymlinkManager._create_symlink(source_rel, target_rel)` — 与 `apply_for_extension` 中的 `dep["source"]`/`dep["target"]` 类型一致（均为 str）
- `DependencyResolver.resolve()` 返回 `{"to_enable": list, "to_disable": list, "rejected": list}` — 与 main() 中消费方式一致
- `Validator.validate()` 返回 `list[dict]` — 每个 dict 包含 `name`/`status`/`detail` — 与 `show_validation_results` 消费方式一致
- `DialogUI.TYPES_ORDER` 列表元素 (str) — 与 `_build_checklist_items(ext_type: str)` 参数类型一致

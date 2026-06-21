# 插件使能时自动执行 npm install 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使能 plugin 类型扩展后，自动在其 source 目录的 package.json 所在处执行 `npm install`，结果汇入现有操作结果界面。

**Architecture:** 新增 `NpmDependencyManager`（I/O 层，与 `SymlinkManager` 平级），`main()` 在符号链接创建后调用它，结果追加到同一结果列表。失败非阻断，仅以 ERROR 结果提示。新增 `DialogAdapter.run_infobox` + `DialogUI.show_installing_progress` 提供安装过程提示。

**Tech Stack:** Python 3.8+ 标准库（`subprocess`/`shutil`/`os`），dialog TUI，pytest（mock `subprocess.run`/`shutil.which`）。

**关联设计文档：** `docs/plans/2026-06-22-plugin-npm-install-design.md`

---

## 文件结构

| 文件 | 责任 | 改动 |
|------|------|------|
| `ext_mgr.py` | 主程序（单文件） | 新增 `NpmDependencyManager` 类（I/O 层，`SymlinkManager` 之后）；`DialogAdapter` 新增 `run_infobox`；`DialogUI` 新增 `show_installing_progress`；`main()` 构造与编排 |
| `tests/test_ext_mgr.py` | 测试 | 新增 import（`patch`/`call`/`subprocess`/`NpmDependencyManager`）；新增约 11 个测试用例 |
| `README.md` | 文档 | 架构表新增 `NpmDependencyManager`；符号链接/依赖安装章节补充说明 |

---

## Task 1: NpmDependencyManager 核心与成功路径

**Files:**
- Create: `ext_mgr.py`（在 `SymlinkManager` 类之后、`Validator` 类之前插入新类，约第 522 行）
- Test: `tests/test_ext_mgr.py`

- [ ] **Step 1: 更新测试 import**

修改 `tests/test_ext_mgr.py:1-20`，增加 `patch`、`call`、`subprocess`、`NpmDependencyManager`：

```python
import json
import os
import subprocess
import pytest
from unittest.mock import MagicMock, patch, call
from ext_mgr import (
    parse_depends,
    ConfigManager,
    ConfigError,
    ExtensionStore,
    ChangeSet,
    SymlinkManager,
    NpmDependencyManager,
    Validator,
    DialogUI,
    Extension,
    PathDep,
    Config,
    Status,
    Format,
    DEFAULT_TARGET_DIR,
)
```

- [ ] **Step 2: 写失败测试（成功路径 + 文件 source + 无 package.json 跳过）**

在 `tests/test_ext_mgr.py` 文件末尾追加：

```python
# ---------- NpmDependencyManager ----------

def test_npm_install_dir_source_success(tmp_path):
    pkg_dir = tmp_path / "myplugin"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text('{"name":"myplugin"}')
    exts = make_extensions({
        "myplugin": {"type": "plugin", "enabled": False,
                     "path_deps": [(str(pkg_dir), "plugins/myplugin")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake_proc = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake_proc) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["myplugin"], exts)
    assert len(results) == 1
    assert results[0]["status"] == Status.SUCCESS
    assert results[0]["name"] == "myplugin"
    assert mock_run.call_count == 1
    assert mock_run.call_args.kwargs["cwd"] == str(pkg_dir)


def test_npm_install_file_source_uses_dirname(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    (pkg_dir / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(pkg_dir)


def test_npm_install_no_package_json_skipped(tmp_path):
    pkg_dir = tmp_path / "nopkg"
    pkg_dir.mkdir()
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results == []
    assert mock_run.call_count == 0
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_ext_mgr.py -k npm_install_dir_source_success -v`
Expected: FAIL — `ImportError: cannot import name 'NpmDependencyManager'`

- [ ] **Step 4: 实现 NpmDependencyManager（成功路径）**

在 `ext_mgr.py` 的 `SymlinkManager` 类之后（`Validator` 类之前，约第 522 行）插入：

```python
class NpmDependencyManager:
    """使能 plugin 扩展时，在其 source 目录的 package.json 所在处执行 npm install。"""

    NPM_INSTALL_TIMEOUT = 300

    def __init__(self, source_dir: str):
        self._source_dir = os.path.abspath(source_dir)

    def install_for(self, to_enable, extensions):
        """对 to_enable 中的 plugin 扩展，定位 source 目录下的 package.json，
        在该目录执行 npm install。返回 [{name, status, detail}] 结果列表。"""
        install_dirs = self._collect_install_dirs(to_enable, extensions)
        results = []
        for pkg_dir in install_dirs:
            disp = self._display_path(pkg_dir)
            proc = subprocess.run(
                ["npm", "install"], cwd=pkg_dir,
                capture_output=True, text=True,
                timeout=self.NPM_INSTALL_TIMEOUT,
            )
            results.append({"name": disp, "status": Status.SUCCESS,
                            "detail": "npm install"})
        return results

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
        """source 为目录取自身，为文件取 dirname；无 package.json 返回 None。"""
        abs_source = os.path.normpath(os.path.join(self._source_dir, source))
        pkg_dir = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
        if os.path.isfile(os.path.join(pkg_dir, "package.json")):
            return pkg_dir
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


```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_ext_mgr.py -k "npm_install_dir_source_success or npm_install_file_source_uses_dirname or npm_install_no_package_json_skipped" -v`
Expected: PASS（3 个测试通过）

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: add NpmDependencyManager core (package.json detection + success path)"
```

---

## Task 2: plugin 类型过滤与目录去重（特征化测试）

本任务不改实现——Task 1 的 `_collect_install_dirs` 已内置 plugin 过滤与去重。此处补齐特征化测试锁定行为。

**Files:**
- Test: `tests/test_ext_mgr.py`

- [ ] **Step 1: 追加特征化测试**

在 `tests/test_ext_mgr.py` 的 `test_npm_install_no_package_json_skipped` 之后追加：

```python
def test_npm_install_filters_non_plugin_types(tmp_path):
    pkg_dir = tmp_path / "sk"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "sk": {"type": "skill", "enabled": False,
               "path_deps": [(str(pkg_dir), "skills/sk")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["sk"], exts)
    assert results == []
    assert mock_run.call_count == 0


def test_npm_install_dedup_single_plugin_two_deps(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    (pkg_dir / "index.js").write_text("")
    (pkg_dir / "extra.js").write_text("")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir / "index.js"), "a.js"),
                            (str(pkg_dir / "extra.js"), "b.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_count == 1


def test_npm_install_dedup_two_plugins_shared_dir(tmp_path):
    pkg_dir = tmp_path / "shared"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p1": {"type": "plugin", "enabled": False,
               "path_deps": [(str(pkg_dir), "plugins/p1")]},
        "p2": {"type": "plugin", "enabled": False,
               "path_deps": [(str(pkg_dir), "plugins/p2")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p1", "p2"], exts)
    assert mock_run.call_count == 1
```

- [ ] **Step 2: 运行测试确认通过**

Run: `pytest tests/test_ext_mgr.py -k "npm_install_filters_non_plugin_types or npm_install_dedup" -v`
Expected: PASS（3 个测试通过；行为已由 Task 1 实现覆盖）

- [ ] **Step 3: 提交**

```bash
git add tests/test_ext_mgr.py
git commit -m "test: add characterization tests for plugin filter and dir dedup"
```

---

## Task 3: 失败、超时、npm 未安装与外部路径处理

Task 1 的 `install_for` 仅处理成功分支。本任务以失败优先的测试驱动补齐错误分支。

**Files:**
- Modify: `ext_mgr.py`（`NpmDependencyManager.install_for` 方法）
- Test: `tests/test_ext_mgr.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_ext_mgr.py` 的 `test_npm_install_dedup_two_plugins_shared_dir` 之后追加：

```python
def test_npm_install_failure_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=1, stderr="some install error", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake), \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "some install error" in results[0]["detail"]


def test_npm_install_timeout_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=300)), \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "超时" in results[0]["detail"]


def test_npm_install_npm_missing_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value=None):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "npm 未安装" in results[0]["detail"]
    assert mock_run.call_count == 0


def test_npm_install_external_absolute_path(tmp_path):
    ext_dir = tmp_path / "external" / "dist"
    ext_dir.mkdir(parents=True)
    (ext_dir / "package.json").write_text("{}")
    (ext_dir / "index.js").write_text("")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(ext_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.SUCCESS
    assert mock_run.call_args.kwargs["cwd"] == str(ext_dir)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ext_mgr.py -k "npm_install_failure_returns_error or npm_install_timeout_returns_error or npm_install_npm_missing_returns_error" -v`
Expected: FAIL — Task 1 的 `install_for` 对 returncode!=0 返回 SUCCESS、未捕获 TimeoutExpired、未检查 npm 可用性

（`test_npm_install_external_absolute_path` 应已通过，因 `os.path.join` 对绝对路径透传。）

- [ ] **Step 3: 实现错误分支**

将 `ext_mgr.py` 中 `NpmDependencyManager.install_for` 方法整体替换为：

```python
    def install_for(self, to_enable, extensions):
        """对 to_enable 中的 plugin 扩展，定位 source 目录下的 package.json，
        在该目录执行 npm install。返回 [{name, status, detail}] 结果列表。"""
        install_dirs = self._collect_install_dirs(to_enable, extensions)
        if not install_dirs:
            return []
        npm_available = shutil.which("npm") is not None
        results = []
        for pkg_dir in install_dirs:
            disp = self._display_path(pkg_dir)
            if not npm_available:
                results.append({"name": disp, "status": Status.ERROR,
                                "detail": "npm 未安装，跳过依赖安装"})
                continue
            try:
                proc = subprocess.run(
                    ["npm", "install"], cwd=pkg_dir,
                    capture_output=True, text=True,
                    timeout=self.NPM_INSTALL_TIMEOUT,
                )
                if proc.returncode == 0:
                    results.append({"name": disp, "status": Status.SUCCESS,
                                    "detail": "npm install"})
                else:
                    results.append({"name": disp, "status": Status.ERROR,
                                    "detail": (proc.stderr or "")[-500:]})
            except subprocess.TimeoutExpired:
                results.append({"name": disp, "status": Status.ERROR,
                                "detail": "npm install 超时"})
        return results
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_ext_mgr.py -k "npm_install" -v`
Expected: PASS（全部 npm_install 测试通过）

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `pytest tests/test_ext_mgr.py -q`
Expected: 全部通过（原有 102 + 新增 10 = 112）

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: handle npm install failure, timeout, and missing npm"
```

---

## Task 4: 安装进度提示（run_infobox + show_installing_progress）

**Files:**
- Modify: `ext_mgr.py`（`DialogAdapter` 新增 `run_infobox`，约第 662 行；`DialogUI` 新增 `show_installing_progress`，约第 843 行）
- Test: `tests/test_ext_mgr.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_ext_mgr.py` 的最后一个 `test_npm_install_external_absolute_path` 之后追加：

```python
# ---------- 安装进度提示 ----------

def test_show_installing_progress_calls_infobox():
    adapter = MagicMock()
    store = ExtensionStore({}, source_dir="/fake")
    ui = DialogUI(adapter, store, MagicMock())
    ui.show_installing_progress()
    adapter.run_infobox.assert_called_once()
    args, _ = adapter.run_infobox.call_args
    assert "安装" in args[0] and "依赖" in args[0]


def test_run_infobox_builds_dialog_args(tmp_path):
    adapter = DialogAdapter()
    with patch("ext_mgr.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        adapter.run_infobox("正在安装插件依赖，请稍候...")
    cmd = mock_run.call_args.args[0]
    assert "--infobox" in cmd
    assert "正在安装插件依赖，请稍候..." in cmd
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ext_mgr.py -k "show_installing_progress_calls_infobox or run_infobox_builds_dialog_args" -v`
Expected: FAIL — `AttributeError: 'DialogUI' object has no attribute 'show_installing_progress'` / `'DialogAdapter' object has no attribute 'run_infobox'`

- [ ] **Step 3: 实现 run_infobox**

在 `ext_mgr.py` 的 `DialogAdapter.run_yesno` 方法之后（`DialogUI` 类之前，约第 662 行）插入：

```python
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

```

- [ ] **Step 4: 实现 show_installing_progress**

在 `ext_mgr.py` 的 `DialogUI.show_error` 方法之后（`main` 函数之前，约第 843 行）插入：

```python
    def show_installing_progress(self):
        self._adapter.run_infobox("正在安装插件依赖，请稍候...")

```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_ext_mgr.py -k "show_installing_progress_calls_infobox or run_infobox_builds_dialog_args" -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: add install progress infobox for plugin dependency installation"
```

---

## Task 5: main() 编排集成

将 `NpmDependencyManager` 接入 `main()`：构造实例、在符号链接创建后按需调用并合并结果。

**Files:**
- Modify: `ext_mgr.py`（`main()` 函数，第 867 行与第 891-895 行区域）
- Test: 全量回归

- [ ] **Step 1: 构造 NpmDependencyManager**

在 `ext_mgr.py` 的 `main()` 中，找到（约第 867 行）：

```python
    symlink_mgr = SymlinkManager(script_dir, target)
```

在其后新增一行：

```python
    symlink_mgr = SymlinkManager(script_dir, target)
    npm_mgr = NpmDependencyManager(script_dir)
```

- [ ] **Step 2: 接入 install_for 与结果合并**

在 `main()` 中，找到（约第 891-895 行）：

```python
        all_disable = changes.to_disable + changes.cascade_disabled
        results = symlink_mgr.apply_changes(
            changes.to_enable, all_disable, store.extensions
        )
        ui.show_results(results)
```

替换为：

```python
        all_disable = changes.to_disable + changes.cascade_disabled
        results = symlink_mgr.apply_changes(
            changes.to_enable, all_disable, store.extensions
        )
        has_plugin = any(
            store.extensions[n].type == "plugin"
            for n in changes.to_enable if n in store.extensions
        )
        if has_plugin:
            ui.show_installing_progress()
            results += npm_mgr.install_for(changes.to_enable, store.extensions)
        ui.show_results(results)
```

- [ ] **Step 3: 运行全量测试确认无回归**

Run: `pytest tests/test_ext_mgr.py -q`
Expected: 全部通过

- [ ] **Step 4: 类型/语法自检**

Run: `python3 -c "import ext_mgr; print('OK')"`
Expected: 输出 `OK`（无语法错误、import 正常）

- [ ] **Step 5: 提交**

```bash
git add ext_mgr.py
git commit -m "feat: wire NpmDependencyManager into main() apply flow"
```

---

## Task 6: README.md 更新

**Files:**
- Modify: `README.md`（架构表 + 特性/符号链接章节）

- [ ] **Step 1: 特性列表补充依赖安装**

在 `README.md` 的「特性」章节，找到：

```
- **隐藏扩展**：`visible: false` 的扩展不进入勾选列表，但仍参与依赖管理
```

在其后新增一条：

```
- **插件依赖安装**：使能 plugin 类型扩展时，自动在其 source 目录的 `package.json` 所在处执行 `npm install`
```

- [ ] **Step 2: 架构表 I/O 层补充 NpmDependencyManager**

在 `README.md` 的「架构 → I/O 层」表格中，找到 `SymlinkManager` 行：

```
| `SymlinkManager` | 创建 / 删除符号链接，返回带状态的结果列表 |
```

在其后新增一行：

```
| `NpmDependencyManager` | 使能 plugin 扩展时在其 source 目录的 package.json 所在处执行 `npm install` |
```

- [ ] **Step 3: 符号链接规则章节补充依赖安装说明**

在 `README.md` 的「符号链接规则」章节末尾（「禁用扩展」小节之后）新增小节：

```
### 插件依赖安装

使能 `plugin` 类型的扩展后，系统会检查其 `depends` 中每个路径依赖项的 `source` 所在目录（`source` 为目录取自身，为文件取其所在目录）。若该目录含 `package.json`，则在其中执行 `npm install` 安装依赖。

- 同一目录只安装一次（多个 `source` 指向同一目录时自动去重）
- 仅 `plugin` 类型触发；其他类型即使存在 `package.json` 也不执行
- 安装失败（非零退出、超时、npm 未安装）非阻断：符号链接照常创建、`enabled` 照常写入，仅在结果界面以 `ERROR` 呈现
- 禁用插件时**不删除**已安装的 `node_modules`，保留依赖
```

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: document plugin npm install behavior in README"
```

---

## 验收检查

完成全部任务后执行：

```bash
pytest tests/test_ext_mgr.py -v          # 全部测试通过
python3 -c "import ext_mgr"              # 无 import 错误
```

确认：
- 使能 plugin 扩展时，其 source 目录有 `package.json` 则执行一次 `npm install`
- 结果在操作结果界面与符号链接结果一同展示
- 失败、超时、npm 未安装均为非阻断 ERROR
- 禁用插件不清理依赖
- skills/agents/commands 类型不触发 npm install

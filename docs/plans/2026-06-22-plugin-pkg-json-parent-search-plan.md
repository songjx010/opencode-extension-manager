# plugin 依赖 package.json 向上查找增强 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使能 plugin 扩展定位 `npm install` 目录时，当前目录无 `package.json` 则依次向上查找父目录、祖父目录，在首个命中的目录执行安装。

**Architecture:** 仅增强 `NpmDependencyManager._resolve_pkg_dir`（ext_mgr.py:616）的目录探测逻辑——确定起始目录后向上循环最多 3 次。方法签名、去重逻辑、`install_for` 编排、UI 全部不变。

**Tech Stack:** Python 3 / 标准库 os / pytest / unittest.mock

---

## 文件结构

| 文件 | 责任 | 改动 |
|------|------|------|
| `ext_mgr.py` | 主程序（单文件分层） | 修改 `_resolve_pkg_dir`（约 +5 行）：起始目录无 package.json 时向上查父、祖父目录 |
| `tests/test_ext_mgr.py` | 测试 | 在 `NpmDependencyManager` 测试段末尾（line ~1896 附近）追加 2 个向上查找测试 |
| `README.md` | 文档 | 依赖安装章节（line 285）补充向上查找说明 |

---

## Task 1: `_resolve_pkg_dir` 向上查找（TDD）

**Files:**
- Modify: `ext_mgr.py:616-622`（`_resolve_pkg_dir` 方法）
- Test: `tests/test_ext_mgr.py`（`NpmDependencyManager` 测试段，在 `test_npm_install_plugin_with_only_ext_deps_is_noop` 之前追加）

- [ ] **Step 1: 写两个失败测试（父目录命中、祖父目录命中）**

在 `tests/test_ext_mgr.py` 的 `test_npm_install_external_absolute_path`（line 1835）之后、`test_npm_install_invokes_progress_callback_per_dir`（line 1866）之前，插入以下两个测试：

```python
def test_npm_install_finds_package_json_in_parent_dir(tmp_path):
    # source 直接所在目录无 package.json，父目录有 -> 在父目录安装
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "package.json").write_text("{}")
    dist_dir = project_root / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(dist_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(project_root)


def test_npm_install_finds_package_json_in_grandparent_dir(tmp_path):
    # source 在 project/a/b/index.js，package.json 在 project（祖父目录）
    project_root = tmp_path / "projroot"
    project_root.mkdir()
    (project_root / "package.json").write_text("{}")
    sub = project_root / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(sub / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(project_root)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_ext_mgr.py -k "finds_package_json_in_parent_dir or finds_package_json_in_grandparent_dir" -v`
Expected: FAIL —— 当前 `_resolve_pkg_dir` 只检查起始目录一层，父/祖父有 package.json 时返回 None，`mock_run` 未被调用，`mock_run.call_args` 为 None，`None.kwargs` 抛 `AttributeError`。

- [ ] **Step 3: 实现 `_resolve_pkg_dir` 向上查找**

将 `ext_mgr.py:616-622` 当前实现：

```python
    def _resolve_pkg_dir(self, source):
        """source 为目录取自身，为文件取 dirname；无 package.json 返回 None。"""
        abs_source = os.path.normpath(os.path.join(self._source_dir, source))
        pkg_dir = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
        if os.path.isfile(os.path.join(pkg_dir, "package.json")):
            return pkg_dir
        return None
```

替换为：

```python
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
```

- [ ] **Step 4: 运行新测试 + 全量 npm 测试验证通过**

Run: `pytest tests/test_ext_mgr.py -k "npm_install" -v`
Expected: PASS —— 新增 2 个测试通过；既有测试（含 `test_npm_install_dir_source_success`、`test_npm_install_file_source_uses_dirname`、`test_npm_install_no_package_json_skipped` 等）全部不回归。

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `pytest tests/test_ext_mgr.py -q`
Expected: 全部通过（原 120 测试 + 新增 2 = 122）。

- [ ] **Step 6: 提交**

```bash
git add ext_mgr.py tests/test_ext_mgr.py
git commit -m "feat: plugin 依赖向上查找 package.json（父/祖父目录）"
```

---

## Task 2: README 文档更新

**Files:**
- Modify: `README.md:285`（插件依赖安装章节首段）

- [ ] **Step 1: 更新依赖安装章节说明**

将 `README.md:285` 当前文本：

```
使能 `plugin` 类型的扩展后，系统会检查其 `depends` 中每个路径依赖项的 `source` 所在目录（`source` 为目录取自身，为文件取其所在目录）。若该目录含 `package.json`，则在其中执行 `npm install` 安装依赖。
```

替换为：

```
使能 `plugin` 类型的扩展后，系统会检查其 `depends` 中每个路径依赖项的 `source` 所在目录（`source` 为目录取自身，为文件取其所在目录）。若该目录含 `package.json`，则在其中执行 `npm install` 安装依赖；若不含，则依次向上查找父目录、祖父目录，在首个含 `package.json` 的目录执行安装。
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: 补充 plugin 依赖向上查找 package.json 说明"
```

---

## Self-Review

**1. Spec coverage:**
- 固定 3 候选目录（当前/父/祖父）→ Task 1 Step 3 的 `range(3)` 循环 ✓
- 就近优先（第一个命中即返回）→ 循环 `return cur` 即返回 ✓
- 均无则返回 None（静默跳过）→ 循环结束 `return None` ✓
- 父目录命中测试 → `test_npm_install_finds_package_json_in_parent_dir` ✓
- 祖父目录命中测试 → `test_npm_install_finds_package_json_in_grandparent_dir` ✓
- 当前目录命中不回归 → 既有 `test_npm_install_dir_source_success` / `test_npm_install_file_source_uses_dirname` ✓
- 三层都无不回归 → 既有 `test_npm_install_no_package_json_skipped`（tmp_path 环境下父/祖父也无 package.json）✓
- README 更新 → Task 2 ✓

**2. Placeholder scan:** 无 TBD/TODO；所有代码块均含完整实现，无"类似 Task N"引用。

**3. Type/signature consistency:** `_resolve_pkg_dir(self, source)` 签名不变；返回值语义（目录绝对路径 str 或 None）不变；`install_for`/`_collect_install_dirs` 调用方不变。`mock_run.call_args.kwargs["cwd"]` 与既有测试 `test_npm_install_file_source_uses_dirname:1713` 用法一致。

# 设计文档：plugin 依赖 package.json 向上查找增强

| 项 | 内容 |
|---|---|
| 日期 | 2026-06-22 |
| Scope | 使能 plugin 扩展定位 `npm install` 目录时，当前目录无 `package.json` 则继续向上查找父目录、祖父目录 |
| 动机 | 真实插件 source 是 `dist/index.js`，`package.json` 在项目根（`dist/` 的父目录）。当前实现只在 source 直接所在目录查找，找不到即静默跳过，导致依赖不安装 |
| 兼容性 | 向后兼容：当前目录有 `package.json` 时行为不变；仅在原先会"静默跳过"的场景扩展查找范围 |

## 背景

`NpmDependencyManager` (ext_mgr.py:558) 在使能 plugin 扩展时，对每个 path 依赖的 `source` 定位目录并执行 `npm install`。定位逻辑在 `_resolve_pkg_dir` (ext_mgr.py:616)：

```python
def _resolve_pkg_dir(self, source):
    abs_source = os.path.normpath(os.path.join(self._source_dir, source))
    pkg_dir = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
    if os.path.isfile(os.path.join(pkg_dir, "package.json")):
        return pkg_dir
    return None
```

仅检查起始目录（source 为目录取自身，为文件取 dirname）一层。真实场景中 `dist/` 下无 `package.json`（构建产物），但项目根有——恰好是父目录。当前会返回 None 静默跳过，依赖不安装。

## 设计决策

### 查找范围：固定 3 个候选目录

从起始目录开始，依次向上检查：**起始目录 → 其父目录 → 其祖父目录**，返回第一个含 `package.json` 的目录。三个都无则返回 None（静默跳过，与现状一致）。

**为何固定 3 层而非无限向上：**
- 匹配用户明确的 `../` 与 `../../` 需求
- 避免无限向上误命中祖先目录中无关的 `package.json`（如用户 home 目录偶发的 package.json）
- 固定次数天然规避"到达文件系统根死循环"问题，无需额外边界判断

**查找顺序：就近原则**——从起始目录向上，最近者优先。这保证命中离 source 最近的 `package.json`，符合 Node 模块解析直觉。

### 实现位置：在 `_resolve_pkg_dir` 内部扩展

不改方法签名、不改调用方。向上查找是对"定位 package.json 所在目录"这一既有职责的自然扩展。

```python
def _resolve_pkg_dir(self, source):
    abs_source = os.path.normpath(os.path.join(self._source_dir, source))
    start_dir = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
    cur = start_dir
    for _ in range(3):
        if os.path.isfile(os.path.join(cur, "package.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None
```

`parent == cur` 的 break 是对文件系统根的防御（理论上固定 3 次已够，此处双重保险，到达根 `/` 时 dirname 返回自身即停止）。

## 不变的部分

- **`_collect_install_dirs` 的 pkg_dir 去重**：向上查找后，多个 source 仍可能解析到同一 pkg_dir，现有按发现顺序去重逻辑依然正确有效。
- **`install_for` 的执行流程**：npm 可用性检查、进度提示、subprocess 调用、错误处理、超时处理全部不变。
- **非 plugin 类型不触发**：类型过滤在 `_collect_install_dirs` 完成，与本次改动无关。
- **找不到时静默跳过**：不安装、不报错，插件仍通过符号链接正常使能。

## 测试计划

新增测试加入 `tests/test_ext_mgr.py`，遵循现有 mock 模式（mock `subprocess.run` 与 `shutil.which`，用 `tmp_path` 构造真实目录，不联网）。

| 场景 | 预期 |
|---|---|
| 起始目录有 `package.json` | 用起始目录（不回归，现有行为） |
| 起始目录无、父目录有 | 用父目录（新增） |
| 起始目录无、父目录无、祖父目录有 | 用祖父目录（新增） |
| 三层都无 `package.json` | 返回 None，`subprocess.run` 未调用（不回归） |

## 文档更新

`README.md` 依赖安装章节补充一句：当前目录无 `package.json` 时，继续在父目录、祖父目录查找，在命中的目录执行 `npm install`。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `ext_mgr.py` | `_resolve_pkg_dir` 方法内部增加向上查找循环（约 +5 行） |
| `tests/test_ext_mgr.py` | 新增 2 个向上查找测试用例 |
| `README.md` | 依赖安装章节一句话补充 |

# 设计文档：插件使能时自动执行 npm install

| 字段 | 值 |
|------|------|
| Date | 2026-06-22 |
| Status | Approved |
| Project | opencode-extension-manager 插件依赖管理 |
| Scope | 使能 plugin 类型扩展时，自动在其 source 目录执行 `npm install`；禁用时不清理依赖 |

---

## 0. 目标与约束

### 0.1 目标

- 使能 plugin 类型扩展后，若其 `source` 定位到的目录含 `package.json`，则在该目录执行 `npm install` 安装依赖
- 安装结果汇入现有操作结果界面，与其他符号链接操作统一展示
- 失败不阻断使能流程，仅以错误结果提示用户

### 0.2 约束

- **单文件**：实现加入 `ext_mgr.py`，不拆分文件
- **仅标准库**：通过 `subprocess` 调用 `npm`，不引入 npm 客户端库
- **触发范围**：仅 `plugin` 类型扩展触发；skills/agents/commands 即使 source 目录有 `package.json` 也不执行
- **禁用不清理**：去使能插件时不删除已安装的 `node_modules`，保留依赖
- **行为等价**：不改变现有符号链接、级联、配置回写逻辑，仅在其后追加依赖安装步骤

### 0.3 不在范围内（YAGNI）

- 不支持 `yarn` / `pnpm` 等其他包管理器（仅 `npm`）
- 不支持自定义安装命令或参数（固定 `npm install`）
- 不在禁用时清理 `node_modules`
- 不解析 `package-lock.json` / 不校验依赖完整性
- 不做安装缓存或增量判断（每次使能即执行一次完整 `npm install`）

---

## 1. 背景与现状

### 1.1 真实插件结构

当前 `extensions.json` 中的插件 `opencode-context-compress`：

```json
"plugins": {
  "opencode-context-compress": {
    "enabled": false,
    "depends": [
      {"source": "/home/.../opencode-context-compress-sync/dist/index.js", "target": "plugins/occ.js"},
      {"source": "/home/.../opencode-context-compress-sync/dist/occ.example.jsonc", "target": "occ.jsonc"}
    ]
  }
}
```

其 source 指向外部项目 `dist/` 目录下的文件，该目录经构建已包含 `package.json`（从项目根拷贝）。插件运行时通过符号链接 `~/.config/opencode/plugins/occ.js → dist/index.js`，Node 模块解析会沿真实路径在 `dist/` 下查找 `node_modules`。因此依赖必须安装在 source 的 package.json 所在目录，而非 target 目录。

### 1.2 现 apply 流程

`main()` 中应用变更的流程（`ext_mgr.py:891-895`）：

```python
results = symlink_mgr.apply_changes(changes.to_enable, all_disable, store.extensions)
ui.show_results(results)
```

`SymlinkManager.apply_changes` 创建/删除符号链接，返回 `[{name, status, detail}]`，UI 统一展示。npm install 需插入在符号链接创建**之后**、结果展示**之前**。

---

## 2. 架构设计

### 2.1 新增组件：NpmDependencyManager

放在 I/O 层，与 `SymlinkManager`、`Validator` 平级，职责单一——只为 plugin 类型扩展执行 `npm install`：

```python
class NpmDependencyManager:
    def __init__(self, source_dir: str):
        self._source_dir = os.path.abspath(source_dir)

    def install_for(self, to_enable, extensions) -> list:
        """对 to_enable 中的 plugin 扩展，定位其 source 目录下的 package.json，
        在该目录执行 npm install。返回 [{name, status, detail}] 结果列表。"""
```

**分层不变**：`main()` 编排 → I/O 层（`SymlinkManager` + `NpmDependencyManager`）→ 返回结果 → TUI 层展示。`NpmDependencyManager` 不依赖 UI，纯 I/O 组件，可独立单测。

### 2.2 数据流

`main()` 编排改动仅 2 行：

```python
results = symlink_mgr.apply_changes(changes.to_enable, all_disable, store.extensions)
results += npm_mgr.install_for(changes.to_enable, store.extensions)   # 新增
ui.show_results(results)
```

- `install_for` 只处理 `to_enable`（本次新启用的扩展）；已启用再应用不会重装
- 禁用时 `NpmDependencyManager` 不参与，依赖保留
- npm 结果追加在符号链接结果之后，`show_results` 现有分组逻辑（ok/fail/skip）无需改动即可正确归类

---

## 3. 算法设计

### 3.1 package.json 智能定位

对 `PathDep.source`（记为 `s`）：

```
abs_source = os.path.join(source_dir, s)      # join 遇绝对路径自动透传
pkg_dir    = abs_source if os.path.isdir(abs_source) else os.path.dirname(abs_source)
has_pkg    = os.path.isfile(os.path.join(pkg_dir, "package.json"))
```

- `source` 为文件（如 `dist/index.js`）→ 取其所在目录 `dist/`
- `source` 为目录（如 `plugins/my-plugin`）→ 取目录自身
- 绝对外部路径（如 `/home/.../dist/index.js`）→ `os.path.join` 透传，正确定位

### 3.2 install_for 流程

```
install_for(to_enable, extensions):
    install_dirs = OrderedDict()   # {abs_pkg_dir: None}，按发现顺序去重

    # 阶段 1：收集需安装的目录
    for name in to_enable:
        ext = extensions.get(name)
        if ext is None or ext.type != "plugin":
            continue
        for dep in ext.path_deps:
            abs_source = os.path.join(self._source_dir, dep.source)
            pkg_dir = abs_source if isdir(abs_source) else dirname(abs_source)
            if isfile(join(pkg_dir, "package.json")):
                install_dirs.setdefault(normpath(pkg_dir), None)   # 去重

    # 阶段 2：执行安装
    npm_available = shutil.which("npm") is not None
    results = []
    for pkg_dir in install_dirs:
        disp = _display_path(pkg_dir, self._source_dir)   # 相对 source_dir，否则绝对
        if not npm_available:
            results.append({"name": disp, "status": Status.ERROR,
                            "detail": "npm 未安装，跳过依赖安装"})
            continue
        try:
            proc = subprocess.run(["npm", "install"], cwd=pkg_dir,
                                  capture_output=True, text=True, timeout=300)
            if proc.returncode == 0:
                results.append({"name": disp, "status": Status.SUCCESS,
                                "detail": "npm install"})
            else:
                results.append({"name": disp, "status": Status.ERROR,
                                "detail": (proc.stderr or "")[-500:]})
        except TimeoutExpired:
            results.append({"name": disp, "status": Status.ERROR,
                            "detail": "npm install 超时"})
    return results
```

**关键点：**

- **目录去重**：用 `OrderedDict` 键去重，同一 `pkg_dir` 只装一次。真实插件 2 个 source 都在 `dist/`，仅触发 1 次 `npm install`；多个插件共享目录同理
- **路径规范化**：`os.path.normpath` 消除 `..`/`.`/重复斜杠，确保去重可靠
- **超时**：默认 300s（常量 `NPM_INSTALL_TIMEOUT`），防止网络问题挂死 TUI
- **无 package.json**：不产生结果（静默跳过），插件仍通过符号链接正常使能

---

## 4. 结果格式

复用现有 `{name, status, detail}` 字典与 `Status` 常量，**不新增 Status**：

| 场景 | name | status | detail |
|------|------|--------|--------|
| 安装成功 | 包目录显示路径 | `Status.SUCCESS` | `npm install` |
| 安装失败 | 包目录显示路径 | `Status.ERROR` | stderr 末尾 500 字符 |
| 超时 | 包目录显示路径 | `Status.ERROR` | `npm install 超时` |
| npm 未安装 | 包目录显示路径 | `Status.ERROR` | `npm 未安装，跳过依赖安装` |
| 无 package.json | — | 不产生结果 | — |

- `name` 取包目录路径：在 `source_dir` 下时显示相对路径，外部路径显示绝对路径
- npm 结果追加在符号链接结果之后，`show_results` 现有 ok/fail/skip 分组逻辑无需改动

---

## 5. 错误处理

所有 npm 相关失败均为**非阻断**：

| 失败场景 | 处理 |
|---------|------|
| `npm install` 非零退出 | 产生 `ERROR` 结果（附 stderr 摘要），继续下一个目录 |
| `npm install` 超时 | 产生 `ERROR` 结果，继续下一个目录 |
| npm 未安装 | 对每个待装目录产生 `ERROR` 结果，subprocess 不调用 |
| source 路径不存在 | `isfile(package.json)` 为假，静默跳过（不安装、不报错） |

**绝不**因依赖安装失败而：回滚已创建的符号链接、阻止 `enabled` 写入、中止其他插件或整体流程。符号链接已创建、`enabled` 照常回写，依赖问题仅作为结果界面的 `ERROR` 行呈现。

---

## 6. TUI 集成

### 6.1 进度提示

`npm install` 是阻塞调用，期间 dialog 界面冻结。真实插件安装可能耗时十几秒到一分钟，冻结画面会让用户困惑。

**方案**：给 `DialogAdapter` 新增 `run_infobox(title, text)` 方法（`dialog --infobox` 非阻塞，立即返回后文字停留屏幕）。`main()` 在调用 `install_for` 前，若 `to_enable` 含 plugin 类型扩展，先弹出：

```
正在安装插件依赖，请稍候...
```

随后阻塞执行 `npm install`，期间该提示可见。完成后 `show_results` 的 msgbox 自动覆盖画面，无需显式清除。

- `NpmDependencyManager` 保持纯 I/O，不碰 UI
- 无 plugin 时跳过 infobox，避免无意义闪烁

### 6.2 main() 编排

```python
npm_mgr = NpmDependencyManager(script_dir)            # 与 symlink_mgr 同级构造

# apply 流程
results = symlink_mgr.apply_changes(changes.to_enable, all_disable, store.extensions)

has_plugin = any(
    store.extensions[n].type == "plugin"
    for n in changes.to_enable if n in store.extensions
)
if has_plugin:
    ui.show_installing_progress()                      # infobox
    results += npm_mgr.install_for(changes.to_enable, store.extensions)

ui.show_results(results)
```

`DialogUI.show_installing_progress()` 调用 `adapter.run_infobox`，封装 UI 细节。

---

## 7. 测试策略

新增测试加入 `tests/test_ext_mgr.py`，遵循现有模式。全部 mock `subprocess.run` 与 `shutil.which`，**不执行真实 npm、不联网**；用 `tmp_path` 构造带 `package.json` 的真实目录验证文件探测逻辑。复用 `make_extensions_from_raw` fixture 构造 `Extension` 对象。

| 测试用例 | 验证点 |
|---------|--------|
| source 为文件、目录有 `package.json` | 收集 dirname，触发安装 |
| source 为目录、目录有 `package.json` | 收集目录自身 |
| 无 `package.json` | 结果为空，`subprocess.run` 未调用 |
| 非 plugin 类型（skill）有 `package.json` | 被过滤，不安装 |
| 单插件 2 个 source 同目录 | 去重，`subprocess.run` 只调用 1 次 |
| 2 插件共享同一目录 | 去重，只调用 1 次 |
| `npm install` 成功（returncode 0） | `SUCCESS` 结果，`cwd` 正确 |
| `npm install` 失败（returncode 1） | `ERROR` 结果含 stderr |
| `npm install` 超时（`TimeoutExpired`） | `ERROR` 结果 |
| npm 未安装（`which` 返回 `None`） | `ERROR` 结果，`subprocess.run` 未调用 |
| 外部绝对路径 source | `os.path.join` 透传，正确定位 |

---

## 8. 实现影响清单

| 文件 | 改动 |
|------|------|
| `ext_mgr.py` | 新增 `NpmDependencyManager` 类；`DialogAdapter` 新增 `run_infobox`；`DialogUI` 新增 `show_installing_progress`；`main()` 增加构造与编排（约 10 行） |
| `tests/test_ext_mgr.py` | 新增约 11 个测试用例（见第 7 节） |
| `README.md` | 架构图与符号链接规则章节补充插件依赖安装说明 |

对外 JSON 格式、命令行接口、其他类型扩展行为均不变。

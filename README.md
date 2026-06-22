# opencode 扩展管理器

一个 Linux 终端下的交互式扩展管理器，通过 `dialog` TUI 界面管理 opencode 扩展的启用/禁用。扩展以**符号链接**的形式安装到目标目录（默认 `~/.config/opencode`），无需复制文件，干净可逆。

## 特性

- **TUI 勾选界面**：基于 `dialog` 的 checklist，按扩展分类（技能 / 智能体 / 命令编排 / 插件）分组管理
- **依赖自动管理**：启用时递归展开扩展依赖，禁用时级联清理孤儿依赖
- **符号链接安装**：以符号链接方式安装/卸载，不复制文件，支持冲突检测
- **原子配置回写**：配置变更通过临时文件 + `os.replace` 原子写入，保证数据安全
- **可用性检查**：实时检测缺失依赖，缺失的扩展在界面中标记并禁止勾选
- **隐藏扩展**：`visible: false` 的扩展不进入勾选列表，但仍参与依赖管理
- **插件依赖安装**：使能 plugin 类型扩展时，自动在其 source 目录的 `package.json` 所在处执行 `npm install`

## 前置条件

| 依赖 | 版本 | 安装方式 |
|------|------|----------|
| Python | 3.8+ | 系统自带或 `sudo apt install python3` |
| dialog | 任意版本 | `sudo apt install dialog`（Debian/Ubuntu）或 `sudo yum install dialog`（RHEL/CentOS） |

## 快速开始

```bash
# 1. 安装系统依赖
sudo apt install python3 dialog          # Debian/Ubuntu

# 2. 进入仓库目录运行
python3 ext_mgr.py
```

启动后输入目标目录（默认 `~/.config/opencode`），即可在 TUI 中勾选扩展并应用变更。

## 目录结构

```
opencode-extension-manager/
├── ext_mgr.py              # 主程序（单文件，运行此文件）
├── extensions.json          # 扩展配置文件（version 4 格式）
├── tests/
│   ├── test_ext_mgr.py      # 测试用例
│   └── conftest.py          # 测试路径 fixtures
├── docs/plans/              # 设计文档与需求文档（SRS / 设计 / 计划）
├── LICENSE                  # Apache License 2.0
└── README.md
```

> `ext_mgr.py` 为单文件架构，内部按职责划分为数据模型、配置管理、领域状态、符号链接、校验、TUI 适配等多个模块（详见下文[架构](#架构)）。
>
> 扩展源文件（`skills/`、`agents/`、`commands/` 等目录）由用户自行放入仓库根目录，其路径在 `extensions.json` 的 `depends` 中以 `source` 引用。

## 配置文件格式

在 `ext_mgr.py` 同级目录下创建 `extensions.json`，格式如下：

```json
{
  "version": 4,
  "extensions": {
    "skills": {
      "<extension-name>": {
        "enabled": true,
        "visible": true,
        "description": "扩展的描述信息",
        "depends": [
          "agents/<other-extension-name>",
          {"source": "skills/example", "target": "skills/example"}
        ]
      }
    },
    "agents": {},
    "commands": {},
    "plugins": {}
  }
}
```

`extensions` 下按扩展类型分为 4 个分类组，每个扩展放在对应的分类组下，扩展本身**不再需要** `type` 字段——类型由其所属分类组决定。

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | integer | 是 | 必须为 `4`（不支持旧版 `1`/`2`/`3`） |
| `extensions` | object | 是 | 包含 4 个分类组的对象 |
| `extensions.skills` | object | 否 | 技能扩展组（键为扩展名） |
| `extensions.agents` | object | 否 | 智能体扩展组 |
| `extensions.commands` | object | 否 | 命令编排扩展组 |
| `extensions.plugins` | object | 否 | 插件扩展组 |
| `enabled` | boolean | 是 | 初始启用状态 |
| `visible` | boolean | 是 | 是否在 TUI 管理界面显示此扩展。设为 `false` 的扩展仅作为其他扩展的依赖被自动管理，不出现在勾选列表中。保存时无论取值均会写回配置文件 |
| `description` | string | 是 | 扩展描述（在 TUI 中显示） |
| `depends` | array | 否 | 依赖列表，支持扩展依赖（字符串，`分类/名称` 格式）和路径依赖（对象）混合 |

### 分类组

| 分类组 | 说明 |
|------|------|
| `skills` | 技能扩展 |
| `agents` | 智能体扩展 |
| `commands` | 命令编排扩展 |
| `plugins` | 插件扩展 |

分类组可省略（省略时视为空组），但 `extensions` 下不允许出现这 4 个以外的键。同一个扩展名不可出现在多个分类组中。

### depends 混合格式

`depends` 列表支持两种条目：

**扩展依赖**（字符串）：以 `分类/名称` 格式引用另一个扩展，其中分类为该扩展实际所属的分组（`skills`/`agents`/`commands`/`plugins`）。启用时递归展开，自动将依赖扩展也标记为启用。禁用时若依赖扩展成为孤儿（无其他已启用扩展依赖它），则自动级联禁用。

```json
"depends": ["agents/other-extension"]
```

> 前缀分类必须与目标扩展实际所在的分组一致，否则校验报错；目标扩展不存在同样报错。保存时自动按目标扩展的类型还原 `分类/名称` 前缀写回。

**路径依赖**（对象）：指定 `source`（源路径）和 `target`（目标路径）的映射。启用时在目标目录创建符号链接。

```json
"depends": [{"source": "skills/brainstorming", "target": "skills/brainstorming"}]
```

- `source`：相对于本仓库根目录的文件/目录路径，也支持外部路径（如 `../../other/file.md`）
- `target`：符号链接在目标目录下的相对路径

### 完整示例

```json
{
  "version": 4,
  "extensions": {
    "skills": {
      "brainstorming": {
        "enabled": true,
        "visible": true,
        "description": "结构化头脑风暴",
        "depends": [
          {"source": "skills/brainstorming", "target": "skills/brainstorming"}
        ]
      },
      "diagram-generator": {
        "enabled": false,
        "visible": true,
        "description": "生成架构图和流程图",
        "depends": [
          {"source": "skills/diagram-generator", "target": "skills/diagram-generator"}
        ]
      },
      "ascend-c-integrated-development": {
        "enabled": true,
        "visible": true,
        "description": "Ascend C自定义算子全流程开发",
        "depends": [
          "agents/kernel-side-code-developer",
          "agents/host-side-code-developer",
          "agents/onnx-plugin-developer",
          {"source": "skills/ascend-c-integrated-development", "target": "skills/ascend-c-integrated-development"}
        ]
      }
    },
    "agents": {
      "kernel-side-code-developer": {
        "enabled": true,
        "visible": false,
        "description": "Kernel侧代码开发",
        "depends": [
          {"source": "agents/kernel-side-code-developer.md", "target": "agents/kernel-side-code-developer.md"}
        ]
      }
    },
    "commands": {
      "cpp-code-review": {
        "enabled": false,
        "visible": true,
        "description": "C++逻辑缺陷检测",
        "depends": [
          "agents/cpp-memory-reviewer",
          "agents/cpp-concurrency-reviewer",
          "agents/cpp-logic-reviewer",
          "agents/cpp-bug-scorer",
          {"source": "commands/cpp-code-review.md", "target": "commands/cpp-code-review.md"}
        ]
      }
    },
    "plugins": {}
  }
}
```

上例中 `kernel-side-code-developer` 设为 `visible: false`，它仅作为 `ascend-c-integrated-development` 的依赖被自动启用/级联禁用，不会出现在 TUI 勾选界面中。

> **保存行为**：配置回写时每个扩展的键顺序固定为 `enabled` → `visible` → `description` → `depends`，其中 `visible` 无论取值均会写入。

## 运行

```bash
python3 ext_mgr.py
```

## TUI 操作流程

### 1. 设置目标目录

启动后首先弹出输入框，默认值为 `~/.config/opencode`：

- 输入自定义路径后按 **OK** 确认
- 按 **Cancel** 退出脚本
- 输入为空时会提示「目标目录不能为空」并重新输入

### 2. 扩展分类主界面

主界面按扩展分类（`skills`/`agents`/`commands`/`plugins`）分组显示。设为 `visible: false` 的扩展不出现在列表中，但仍参与依赖管理：

- **Skills — 技能扩展**
- **Agents — 智能体**
- **Commands — 命令编排**
- **Plugins — 插件扩展**

每个分类显示 `启用数/总数` 与 `可用数/总数`。选择分类进入对应的 checklist 界面，或直接选择「确认并应用变更」/「退出」。

### 3. Checklist 界面

进入某一类型的 checklist 界面，显示该类型下所有可见扩展：

- 已启用的扩展（`enabled: true`）默认被选中（带 `*` 标记）
- 状态标记：`OK` 表示依赖齐全，`!!` 表示缺失依赖（不可选，强制取消勾选）
- 用方向键移动光标，**空格键** 切换选中/取消选中
- 选择完成后按 **OK** 提交（即时触发当前分类内的级联禁用并刷新列表），按 **Cancel** 返回主界面
- 若勾选了缺失依赖的扩展，会弹出错误提示要求取消勾选后重试

### 4. 确认变更

在主界面选择「确认并应用变更」后，系统计算完整变更集：

- 若存在被已选择扩展依赖、无法禁用的扩展，则弹出错误提示并返回主界面，**不应用任何变更**
- 否则弹出变更摘要对话框：
  - 列出将要**启用**的扩展（`+` 标记）
  - 列出将要**禁用**的扩展（`-` 标记，用户明确取消选中的扩展）
  - 列出将要**级联禁用**的扩展（`~` 标记，因禁用扩展而自动清理的孤儿依赖扩展）

按 **Yes** 确认执行，按 **No** 返回主界面。

### 5. 依赖自动处理

应用变更时，系统按以下规则处理依赖关系：

- **启用扩展时**：递归展开 `depends` 中的扩展依赖，自动将所有被依赖的扩展也标记为 `enabled=true`
- **禁用扩展时**：递归级联清理孤儿依赖——如果被禁用扩展的子依赖不再被任何其他已启用扩展依赖，则自动级联禁用该依赖
- **拒绝禁用**：若被禁用的扩展仍被其他已启用扩展依赖，则拒绝禁用并提示

### 6. 查看结果

执行完成后弹出操作结果，按状态分组显示：

- `success`（成功创建/删除符号链接）
- `skipped`（无需操作，如无路径依赖或链接已正确）
- `conflict`（目标路径冲突）
- `error`（系统错误）

### 7. 配置回写

操作完成后，`extensions.json` 中对应扩展的 `enabled`、`visible` 字段更新为实际状态（含级联禁用的扩展），配置通过原子写入（先写临时文件再 `os.replace`）确保数据安全。

## 符号链接规则

### 启用扩展

对扩展 `depends` 中的每个路径依赖项，在目标目录下创建符号链接：

```
~/.config/opencode/skills/brainstorming → /源目录/skills/brainstorming
```

- 目标路径的子目录不存在时自动创建
- 如果目标路径已存在且指向正确源文件，状态为 `skipped`（跳过）
- 如果目标路径已存在但指向错误目标或为普通文件，状态为 `conflict`（冲突）

### 禁用扩展

仅删除该扩展自身 `depends` 中路径依赖对应的符号链接。级联禁用的扩展同样会删除其路径依赖的符号链接。若链接指向非预期目标则报告 `conflict`，不会误删。

### 插件依赖安装

使能 `plugin` 类型的扩展后，系统会检查其 `depends` 中每个路径依赖项的 `source` 所在目录（`source` 为目录取自身，为文件取其所在目录）。若该目录含 `package.json`，则在其中执行 `npm install` 安装依赖。

- 同一目录只安装一次（多个 `source` 指向同一目录时自动去重）
- 仅 `plugin` 类型触发；其他类型即使存在 `package.json` 也不执行
- 安装失败（非零退出、超时、npm 未安装）非阻断：符号链接照常创建、`enabled` 照常写入，仅在结果界面以 `ERROR` 呈现
- 禁用插件时**不删除**已安装的 `node_modules`，保留依赖

## 架构

`ext_mgr.py` 采用单文件分层架构，各组件职责清晰、单向依赖（UI / I/O 层 → 领域层 → 数据模型）：

### 数据模型

| 组件 | 职责 |
|------|------|
| `Extension` | 单个扩展的领域模型（name / type / enabled / visible / ext_deps / path_deps） |
| `PathDep` | 路径依赖（source → target 符号链接映射） |
| `Config` | 整体配置，`extensions` 为扁平 `dict[name -> Extension]` |
| `ChangeSet` | 变更解析的不可变返回值（to_enable / to_disable / cascade_disabled / rejected） |

### 领域层

| 组件 | 职责 |
|------|------|
| `ExtensionStore` | 扩展状态的唯一拥有者，封装 toggle / 级联 / 解析 / 可用性检查等所有领域操作。UI 与 I/O 层通过它访问状态 |
| `DependencyGraph` | 会话内单例的依赖邻接表（forward / reverse），由 `ExtensionStore` 构造一次 |

### I/O 层

| 组件 | 职责 |
|------|------|
| `ConfigManager` | 加载 / 校验 / 保存 `extensions.json`，处理原子写入与 `extra` 未知字段保留 |
| `SymlinkManager` | 创建 / 删除符号链接，返回带状态的结果列表 |
| `NpmDependencyManager` | 使能 plugin 扩展时在其 source 目录的 package.json 所在处执行 `npm install` |
| `Validator` | 编程式符号链接状态校验（独立工具，未接入 `main()` 运行时主循环） |

### TUI 层

| 组件 | 职责 |
|------|------|
| `DialogAdapter` | `dialog` 命令的薄封装（menu / checklist / inputbox / msgbox / yesno），自适应终端尺寸 |
| `DialogUI` | 应用 TUI 编排：目标目录询问、分类主界面、checklist、变更摘要、结果展示 |

### 常量

| 组件 | 职责 |
|------|------|
| `Status` | 操作结果状态枚举（success / skipped / conflict / error / missing / broken / unexpected / ok） |
| `Format` | `dialog` 颜色/样式转义常量 |
| `GROUP_TO_TYPE` / `TYPE_TO_GROUP` | 分类组与扩展类型的双向映射 |

## 常见问题

### Q: dialog 工具未安装

```
错误: dialog 工具未安装，请先安装 dialog
```

**解决**：`sudo apt install dialog`

### Q: 配置文件不存在

```
错误: 配置文件 /path/to/extensions.json 不存在
```

**解决**：确保 `extensions.json` 与 `ext_mgr.py` 在同一目录下。

### Q: version 不支持

```
错误: 不支持的 version: 3
```

**解决**：将 `extensions.json` 中的 `version` 改为 `4`，并按 version 4 的格式更新扩展配置：扩展放在 `skills`/`agents`/`commands`/`plugins` 分类组下（移除 `type` 字段），扩展依赖（`depends` 中的字符串）须为 `分类/名称` 格式（如 `"agents/foo"`）。

### Q: 扩展依赖格式错误

```
扩展 'xxx' 的扩展依赖 'foo' 格式错误，应为 '分类/名称'（如 agents/foo）
```

**解决**：`depends` 中的扩展依赖字符串必须带分类前缀，且分类须与目标扩展实际所属分组一致。例如目标扩展 `foo` 在 `agents` 组下，则写作 `"agents/foo"`。

### Q: 扩展依赖分类不匹配

```
扩展 'xxx' 的依赖 'skills/foo' 分类不匹配，'foo' 实际属于 'agents'
```

**解决**：依赖前缀声明的分类与目标扩展实际所在分组不一致。按提示将前缀改为目标扩展真正所属的分类。

### Q: 扩展键名格式错误

```
扩展键名 'skills/xxx' 格式错误，应为纯名称（不含 /）
```

**解决**：扩展的**键名**（分类组下的键）须为纯名称（如 `"brainstorming"`），类型通过所属分类组指定。`分类/名称` 前缀仅用于 `depends` 中的扩展依赖，不可用于键名。

### Q: 未知的扩展分类

```
未知的扩展分类 'xxx'，必须为 skills, agents, commands, plugins
```

**解决**：`extensions` 下只允许 `skills`、`agents`、`commands`、`plugins` 四个键，将扩展移入对应分类组。

### Q: 扩展安装失败（冲突）

```
目标路径 ~/.config/opencode/skills/xxx 已存在
```

**解决**：手动检查目标路径，移除已有文件后重新运行。

### Q: 循环依赖

```
循环依赖: a → b → a
```

**解决**：修改 `extensions.json` 中的 `depends` 字段，消除循环引用。

### Q: 缺少 enabled / visible / description 字段

```
扩展 'xxx' 缺少 enabled 字段
```

**解决**：`enabled`、`visible`、`description` 均为必填字段，为该扩展补齐缺少的字段。

### Q: visible 字段类型错误

```
扩展 'xxx' 的 visible 必须为布尔值
```

**解决**：`visible` 字段只能为 `true` 或 `false`。

### Q: 路径依赖缺少字段

```
扩展 'xxx' 的路径依赖缺少 source 或 target 字段
```

**解决**：确保路径依赖对象同时包含 `source` 和 `target` 字段。

## 运行测试

```bash
pytest tests/ -v                              # 运行全部测试
pytest --cov=ext_mgr --cov-branch tests/      # 带覆盖率
```

> 仅运行 `ext_mgr.py` 本身只需要 Python 3.8+ 和 `dialog`；`pytest`/`pytest-cov` 仅开发和测试时需要：
> ```bash
> pip3 install pytest pytest-cov
> ```

## 许可证

[Apache License 2.0](LICENSE)

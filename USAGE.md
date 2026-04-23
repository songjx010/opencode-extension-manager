# opencode 扩展管理器 — 使用指导

## 概述

本工具是一个 Linux 终端下的交互式扩展管理器，通过 `dialog` TUI 界面管理 opencode 扩展的启用/禁用。扩展以**符号链接**的形式安装到目标目录（默认 `~/.config/opencode`），无需复制文件。

## 前置条件

| 依赖 | 版本 | 安装方式 |
|------|------|----------|
| Python | 3.8+ | 系统自带或 `sudo apt install python3` |
| dialog | 任意版本 | `sudo apt install dialog`（Debian/Ubuntu）或 `sudo yum install dialog`（RHEL/CentOS） |

## 环境初始化

### 方式一：自动初始化（推荐）

```bash
cd /path/to/opencode-extension-manager
bash init.sh
```

此脚本会创建 Python 虚拟环境并安装测试依赖（pytest、pytest-cov、mutmut）。

### 方式二：手动安装依赖

如果 `init.sh` 执行失败，可按以下步骤手动操作：

#### 1. 安装系统依赖

```bash
# Debian / Ubuntu
sudo apt install python3 python3-pip python3-venv dialog

# RHEL / CentOS
sudo yum install python3 python3-pip dialog

# Arch Linux
sudo pacman -S python python-pip dialog
```

#### 2. 创建虚拟环境

```bash
python3 -m venv .venv
```

如果报错 `No module named '_ssl'` 或 `ensurepip`，先安装缺失的系统包：

```bash
# Debian / Ubuntu
sudo apt install python3-distutils python3-venv

# 如果 pip 模块缺失
python3 -m ensurepip --upgrade
```

#### 3. 激活虚拟环境并安装 Python 依赖

```bash
source .venv/bin/activate
pip install --upgrade pip
pip install pytest pytest-cov mutmut
```

如果默认 pip 源速度慢，可使用国内镜像：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pytest pytest-cov mutmut
```

#### 4. 验证安装

```bash
python3 --version          # 应为 3.8+
dialog --version           # 应有输出
pytest --version           # 应有输出
```

> 注意：运行 `ext_mgr.py` 本身只需要 Python 3.8+ 和 `dialog`，无需虚拟环境。`pytest`/`pytest-cov`/`mutmut` 仅开发和测试时需要。

## 目录结构

```
opencode-extension-manager/
├── ext_mgr.py              # 主脚本（运行此文件）
├── extensions.json          # 扩展配置文件（version 2 格式）
├── init.sh                  # 环境初始化脚本
├── tests/                   # 测试文件
├── docs/                    # 文档
└── ...                      # 扩展源文件（skills/, agents/, commands/ 等目录）
```

## 配置文件格式

在 `ext_mgr.py` 同级目录下创建 `extensions.json`，格式如下：

```json
{
  "version": 2,
  "extensions": {
    "<extension-name>": {
      "type": "skill",
      "enabled": true,
      "description": "扩展的描述信息",
      "depends": [
        "<other-extension-name>",
        {"source": "skills/example", "target": "skills/example"}
      ]
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | integer | 是 | 必须为 `2`（不支持旧版 `1`） |
| `extensions` | object | 是 | 以纯扩展名为键的扩展字典 |
| `type` | string | 是 | 扩展类型，必须为 `skill`、`agent`、`command`、`plugin` 之一 |
| `enabled` | boolean | 是 | 初始启用状态 |
| `description` | string | 是 | 扩展描述（在 TUI 中显示） |
| `depends` | array | 否 | 依赖列表，支持扩展依赖（字符串）和路径依赖（对象）混合 |

### 扩展键名规则

扩展的键名为纯名称，**不允许**包含 `/`、`..`、不以 `/` 开头：

- 正确：`"brainstorming"`、`"kernel-side-code-developer"`
- 错误：`"skills/brainstorming"`、`"../evil"`

### type 取值

| 类型 | 说明 |
|------|------|
| `skill` | 技能扩展 |
| `agent` | 智能体扩展 |
| `command` | 命令编排扩展 |
| `plugin` | 插件扩展 |

### depends 混合格式

`depends` 列表支持两种条目：

**扩展依赖**（字符串）：引用另一个扩展的键名。启用时递归展开，自动将依赖扩展也标记为启用。

```json
"depends": ["other-extension"]
```

**路径依赖**（对象）：指定 `source`（源路径）和 `target`（目标路径）的映射。启用时在目标目录创建符号链接。

```json
"depends": [{"source": "skills/brainstorming", "target": "skills/brainstorming"}]
```

- `source`：相对于本仓库根目录的文件/目录路径，也支持外部路径（如 `../../other/file.md`）
- `target`：符号链接在目标目录下的相对路径

### 完整示例

```json
{
  "version": 2,
  "extensions": {
    "brainstorming": {
      "type": "skill",
      "enabled": false,
      "description": "结构化头脑风暴",
      "depends": [
        {"source": "skills/brainstorming", "target": "skills/brainstorming"}
      ]
    },
    "code-review-enhanced": {
      "type": "skill",
      "enabled": false,
      "description": "深度代码审查",
      "depends": [
        "brainstorming",
        {"source": "skills/code-review-enhanced", "target": "skills/code-review-enhanced"}
      ]
    },
    "cpp-reviewer": {
      "type": "agent",
      "enabled": true,
      "description": "C++代码审查代理",
      "depends": [
        {"source": "agents/cpp-reviewer.md", "target": "agents/cpp-reviewer.md"}
      ]
    },
    "complex-task": {
      "type": "command",
      "enabled": false,
      "description": "编排复杂多步骤任务",
      "depends": [
        "brainstorming",
        {"source": "commands/complex-task.md", "target": "commands/complex-task.md"}
      ]
    }
  }
}
```

## 运行

```bash
source .venv/bin/activate
python3 ext_mgr.py
```

## TUI 操作流程

### 1. 设置目标目录

启动后首先弹出输入框，默认值为 `~/.config/opencode`：

- 输入自定义路径后按 **OK** 确认
- 按 **Cancel** 退出脚本

### 2. 扩展分类主界面

主界面按扩展类型（`type` 字段）分组显示：

- **Skills — 技能扩展**
- **Agents — 智能体**
- **Commands — 命令编排**
- **Plugins — 插件扩展**

每个分类显示启用数/总数和可用数/总数。选择分类进入对应的 checklist 界面。

### 3. Checklist 界面

进入某一类型的 checklist 界面，显示该类型下所有扩展：

- 已启用的扩展（`enabled: true`）默认被选中（带 `*` 标记）
- 状态标记：`OK` 表示依赖齐全，`!!` 表示缺失依赖（不可选）
- 用方向键移动光标，**空格键** 切换选中/取消选中
- 选择完成后按 **OK** 提交

### 4. 依赖自动处理

提交选择后，系统自动处理依赖关系：

- **启用扩展时**：递归展开 `depends` 中的扩展依赖，自动将所有被依赖的扩展也标记为 `enabled=true`
- **禁用扩展时**：递归级联清理孤儿依赖——如果被禁用扩展的子扩展不再被任何其他已启用扩展依赖，则自动级联禁用该子扩展。如果被禁用的扩展被其他已启用的扩展依赖，则拒绝禁用

### 5. 确认变更

弹出变更摘要对话框：

- 列出将要启用、禁用和级联禁用的扩展
- **禁用**：用户明确取消选中的扩展
- **级联禁用**：因禁用扩展而自动清理的孤儿依赖扩展
- 按 **Yes** 确认执行，按 **No** 返回重新选择

### 6. 查看结果

执行完成后弹出操作结果：

- 每个路径依赖显示操作状态：`success`（成功）、`conflict`（目标路径冲突）、`skipped`（无需操作）、`error`（系统错误）

## 符号链接规则

### 启用扩展

对扩展 `depends` 中的每个路径依赖项，在目标目录下创建符号链接：

```
~/.config/opencode/skills/brainstorming.md → /源目录/skills/brainstorming.md
```

- 目标路径的子目录不存在时自动创建
- 如果目标路径已存在且指向正确源文件，状态为 `skipped`（跳过）
- 如果目标路径已存在但指向错误目标或为普通文件，状态为 `conflict`（冲突）

### 禁用扩展

仅删除该扩展自身 `depends` 中路径依赖对应的符号链接。不删除其依赖扩展的符号链接。

### 配置回写

操作完成后，`extensions.json` 中对应扩展的 `enabled` 字段更新为用户选择的状态。

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
错误: 不支持的 version: 1
```

**解决**：将 `extensions.json` 中的 `version` 改为 `2`，并按新格式更新扩展配置。

### Q: 扩展键名格式错误

```
扩展键名 'skills/xxx' 格式错误，应为纯名称（不含 /）
```

**解决**：将键名改为纯名称（如 `"brainstorming"`），类型通过 `type` 字段指定。

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

### Q: 缺少 type 字段

```
扩展 'xxx' 缺少 type 字段
```

**解决**：为该扩展添加 `"type"` 字段，值为 `skill`、`agent`、`command` 或 `plugin`。

### Q: 路径依赖缺少字段

```
扩展 'xxx' 的路径依赖缺少 source 或 target 字段
```

**解决**：确保路径依赖对象同时包含 `source` 和 `target` 字段。

## 运行测试

```bash
source .venv/bin/activate
pytest tests/ -v                              # 运行全部测试
pytest --cov=ext_mgr --cov-branch tests/      # 带覆盖率
```

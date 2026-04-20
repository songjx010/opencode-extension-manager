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
├── extensions.json          # 扩展配置文件（需按格式创建）
├── init.sh                  # 环境初始化脚本
├── tests/                   # 测试文件
├── docs/                    # 文档
└── ...
```

## 配置文件格式

在 `ext_mgr.py` 同级目录下创建 `extensions.json`，格式如下：

```json
{
  "version": 1,
  "extensions": {
    "<category>/<name>": {
      "enabled": true,
      "description": "扩展的描述信息",
      "depends": ["<category>/<dependency-name>"]
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | integer | 是 | 必须为 `1` |
| `extensions` | object | 是 | 以 `category/name` 为键的扩展字典 |
| `enabled` | boolean | 是 | 初始启用状态 |
| `description` | string | 是 | 扩展描述（在 TUI 中显示） |
| `depends` | array | 否 | 依赖的扩展键名列表 |

### category 取值

category 只能为以下三种之一：

- `skills` — 技能扩展
- `agents` — 代理扩展
- `commands` — 命令扩展

### 完整示例

```json
{
  "version": 1,
  "extensions": {
    "skills/brainstorming": {
      "enabled": false,
      "description": "结构化头脑风暴"
    },
    "skills/code-review-enhanced": {
      "enabled": false,
      "description": "深度代码审查",
      "depends": ["skills/brainstorming"]
    },
    "agents/cpp-reviewer": {
      "enabled": true,
      "description": "C++代码审查代理"
    },
    "commands/complex-task": {
      "enabled": false,
      "description": "编排复杂多步骤任务",
      "depends": ["skills/brainstorming"]
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

### 2. 扩展列表主界面

进入 checklist 界面，显示所有扩展：

- 已启用的扩展（`enabled: true`）默认被选中（带 `*` 标记）
- 用方向键移动光标，**空格键** 切换选中/取消选中
- 选择完成后按 **OK** 提交

### 3. 依赖自动处理

提交选择后，系统自动处理依赖关系：

- **启用扩展时**：自动启用其所有依赖项
- **禁用扩展时**：如果其他已选择的扩展依赖它，则拒绝禁用并提示

### 4. 确认变更

弹出变更摘要对话框：

- 列出将要启用和禁用的扩展
- 按 **Yes** 确认执行，按 **No** 返回重新选择

### 5. 查看结果

执行完成后弹出操作结果：

- 每个扩展显示操作状态：`success`（成功）、`conflict`（目标路径冲突）、`skipped`（无需操作）

## 符号链接规则

### 启用扩展

在目标目录下创建符号链接：

```
~/.config/opencode/skills/brainstorming → /源目录/skills/brainstorming
```

- 子目录（`skills/`、`agents/`、`commands/`）不存在时自动创建
- 如果目标路径已存在**任何文件**（文件、目录、符号链接），则该扩展安装失败，报告冲突

### 禁用扩展

删除目标目录下对应的符号链接。

### 配置回写

操作完成后，`extensions.json` 中的 `enabled` 字段更新为**用户选择的状态**（不受单个扩展操作失败影响）。

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

### Q: 扩展安装失败（冲突）

```
目标路径 ~/.config/opencode/skills/xxx 已存在
```

**解决**：手动检查目标路径，移除已有文件后重新运行。

### Q: 扩展键名格式错误

```
扩展键名 'xxx' 格式错误，应为 <category>/<name>
```

**解决**：确保键名格式为 `skills/xxx`、`agents/xxx` 或 `commands/xxx`。

### Q: 循环依赖

```
循环依赖: skills/a → skills/b → skills/a
```

**解决**：修改 `extensions.json` 中的 `depends` 字段，消除循环引用。

## 运行测试

```bash
source .venv/bin/activate
pytest tests/ -v                              # 运行全部测试
pytest --cov=ext_mgr --cov-branch tests/      # 带覆盖率
```

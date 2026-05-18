# MiniCode Python 中文说明

MiniCode Python 是对原 TypeScript 版 MiniCode 项目的 Python 重写版本。它保留了原项目的核心运行模型、工具调用流程、权限控制、会话管理、MCP 集成、Skills 机制和终端交互能力，并用 Python 生态重新实现 CLI、Agent Loop、模型适配器和本地工具系统。

> 说明：本项目是功能等价迁移版本，不是 React/Ink 终端 UI 的逐像素复刻。Python 版采用原生终端 REPL/TUI 实现，重点保证核心能力、命令行为、配置路径和扩展机制可用。

## 一、核心能力

### 1. Coding Agent 主循环

项目实现了典型的 Agent 执行链路：

```text
用户输入 -> 模型推理 -> 工具调用 -> 工具结果回传 -> 模型继续推理 -> 最终回答
```

主要能力包括：

- Anthropic Messages API 兼容适配
- Tool Use / Tool Result 消息转换
- Thinking block 保留
- progress / final markers 输出处理
- 多轮工具调用
- 上下文压缩、裁剪和折叠
- 会话保存、恢复、分叉和重命名

### 2. 内置开发工具

Python 版内置了常用 coding agent 工具：

| 工具 | 作用 |
|---|---|
| `list_files` | 列出目录文件 |
| `read_file` | 读取文件 |
| `write_file` | 写入文件 |
| `modify_file` | 替换文件内容并支持审查 |
| `edit_file` | 精确搜索替换 |
| `patch_file` | 批量 patch 文件 |
| `grep_files` | 在项目内搜索文本 |
| `run_command` | 执行开发命令 |
| `web_fetch` | 抓取网页内容 |
| `web_search` | 搜索网页内容 |
| `ask_user` | 需要用户决策时发起询问 |
| `load_skill` | 加载指定 Skill 工作流 |

### 3. 权限与安全控制

项目包含权限管理模块，重点控制：

- 文件读取路径
- 文件写入路径
- 文件修改审批
- Shell 命令执行
- 已审查文件记录
- 权限持久化

默认权限文件路径：

```bash
~/.mini-code/permissions.json
```

### 4. 会话管理

会话会持久化到本地，支持：

- 新建会话
- 恢复历史会话
- 分叉会话
- 重命名会话
- 清理会话
- transcript 渲染

默认会话目录：

```bash
~/.mini-code/projects/
```

### 5. MCP 支持

项目支持 MCP server 配置和加载，包括：

- stdio MCP server
- streamable-http MCP server
- MCP tools
- MCP resources
- MCP prompts
- token 登录与登出
- 用户级和项目级 MCP 配置

配置位置：

```bash
~/.mini-code/mcp.json
.mcp.json
~/.mini-code/settings.json
```

### 6. Skills 支持

项目支持从多个路径发现和加载 `SKILL.md`：

```bash
~/.mini-code/skills/<skill-name>/SKILL.md
.mini-code/skills/<skill-name>/SKILL.md
~/.claude/skills/<skill-name>/SKILL.md
.claude/skills/<skill-name>/SKILL.md
```

Skills 可用于给 Agent 注入特定工作流、项目规范、工具使用方式或领域知识。

## 二、项目结构

```text
MiniCode-python/
├── minicode/
│   ├── __main__.py                 # python -m minicode 入口
│   ├── index.py                    # 主 CLI 入口
│   ├── agent_loop.py               # Agent 主循环
│   ├── anthropic_adapter.py        # Anthropic 模型适配器
│   ├── mock_model.py               # 本地 mock 模型
│   ├── config.py                   # 配置加载与合并
│   ├── permissions.py              # 权限管理
│   ├── session.py                  # 会话保存/恢复/分叉
│   ├── skills.py                   # Skill 发现、安装、移除
│   ├── mcp.py                      # MCP 客户端与工具包装
│   ├── prompt.py                   # 系统提示词构建
│   ├── manage_cli.py               # MCP / Skills 管理命令
│   ├── tty_app.py                  # Python 原生终端交互
│   ├── cli_commands.py             # 斜杠命令
│   ├── tools/                      # 内置工具
│   ├── compact/                    # 上下文压缩、裁剪、折叠
│   ├── tui/                        # 终端渲染、输入解析、transcript
│   └── utils/                      # 通用工具函数
├── bin/
│   └── minicode                    # 可执行脚本
├── docs/                           # 静态文档资源
├── tests/                          # 测试目录
├── pyproject.toml                  # Python 包配置
├── LICENSE
└── README.md
```

## 三、环境要求

- Python 3.10 或更高版本
- macOS / Linux / Windows 均可运行
- 使用真实模型时需要 Anthropic API Key 或 Auth Token

检查 Python 版本：

```bash
python3 --version
```

## 四、安装方式

进入项目目录：

```bash
cd MiniCode-python
```

建议创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

安装项目：

```bash
python -m pip install -e .
```

安装完成后可以使用：

```bash
minicode --help
```

也可以不安装，直接运行：

```bash
python -m minicode --help
```

## 五、快速启动

### 1. Mock 模式启动

Mock 模式不需要 API Key，适合验证项目是否能启动、CLI 是否正常、工具调用链路是否可用。

```bash
MINI_CODE_MODEL_MODE=mock python -m minicode
```

或安装后运行：

```bash
MINI_CODE_MODEL_MODE=mock minicode
```

Windows PowerShell：

```powershell
$env:MINI_CODE_MODEL_MODE="mock"
python -m minicode
```

### 2. 使用 Anthropic API 启动

设置环境变量：

```bash
export ANTHROPIC_API_KEY="你的 Anthropic API Key"
export ANTHROPIC_MODEL="claude-3-5-sonnet-latest"
```

启动：

```bash
python -m minicode
```

或：

```bash
minicode
```

也可以使用 `ANTHROPIC_AUTH_TOKEN`：

```bash
export ANTHROPIC_AUTH_TOKEN="你的 Auth Token"
export ANTHROPIC_MODEL="claude-3-5-sonnet-latest"
minicode
```

## 六、配置说明

MiniCode Python 会合并读取以下配置：

```bash
~/.mini-code/settings.json
~/.mini-code/mcp.json
当前项目/.mcp.json
~/.claude/settings.json
```

推荐配置文件：

```bash
~/.mini-code/settings.json
```

示例：

```json
{
  "model": "claude-3-5-sonnet-latest",
  "maxOutputTokens": 4096,
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key"
  },
  "mcpServers": {}
}
```

常用环境变量：

| 环境变量 | 作用 |
|---|---|
| `MINI_CODE_HOME` | 自定义 MiniCode 数据目录，默认 `~/.mini-code` |
| `MINI_CODE_MODEL` | 指定模型名，优先级高于配置文件 |
| `MINI_CODE_MODEL_MODE=mock` | 使用本地 mock 模型 |
| `MINI_CODE_MAX_OUTPUT_TOKENS` | 指定最大输出 token |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `ANTHROPIC_AUTH_TOKEN` | Anthropic Auth Token |
| `ANTHROPIC_MODEL` | Anthropic 模型名 |
| `ANTHROPIC_BASE_URL` | 自定义 Anthropic API Base URL |

## 七、交互命令

启动后可在终端中直接输入自然语言任务，也可以使用斜杠命令。

### 常用命令

| 命令 | 作用 |
|---|---|
| `/help` | 查看可用命令 |
| `/tools` | 查看可用工具 |
| `/status` | 查看当前模型和配置来源 |
| `/model` | 查看当前模型 |
| `/model <model-name>` | 保存模型覆盖配置 |
| `/config-paths` | 查看配置文件路径 |
| `/skills` | 查看发现的 Skills |
| `/mcp` | 查看 MCP server 状态 |
| `/permissions` | 查看权限存储路径 |
| `/exit` | 退出程序 |

### 文件和命令快捷操作

| 命令 | 示例 |
|---|---|
| `/ls [path]` | `/ls .` |
| `/grep <pattern>::[path]` | `/grep Agent::minicode` |
| `/read <path>` | `/read README.md` |
| `/write <path>::<content>` | `/write demo.txt::hello` |
| `/modify <path>::<content>` | `/modify demo.txt::new content` |
| `/edit <path>::<search>::<replace>` | `/edit demo.txt::hello::hi` |
| `/patch <path>::<search1>::<replace1>...` | `/patch demo.txt::old::new` |
| `/cmd [cwd::]<command>` | `/cmd .::python -m compileall -q minicode` |

### 会话命令

| 命令 | 作用 |
|---|---|
| `/new` | 新建会话 |
| `/resume` | 选择并恢复历史会话 |
| `/resume <id>` | 恢复指定会话 |
| `/rename <name>` | 重命名当前会话 |
| `/fork` | 基于当前会话创建分叉 |
| `/compact` | 压缩当前上下文 |
| `/collapse` | 将旧上下文折叠成摘要 |
| `/snip` | 删除安全的中间上下文片段 |

## 八、管理命令

管理命令在普通终端中执行，不是在 Agent 交互界面内执行。

### 1. MCP 管理

查看 MCP server：

```bash
minicode mcp list
```

查看项目级 MCP server：

```bash
minicode mcp list --project
```

添加 stdio MCP server：

```bash
minicode mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem .
```

添加项目级 MCP server：

```bash
minicode mcp add filesystem --project -- npx -y @modelcontextprotocol/server-filesystem .
```

添加 streamable-http MCP server：

```bash
minicode mcp add remote-server --protocol streamable-http --url https://example.com/mcp
```

登录 MCP token：

```bash
minicode mcp login remote-server --token your-token
```

登出 MCP token：

```bash
minicode mcp logout remote-server
```

删除 MCP server：

```bash
minicode mcp remove remote-server
```

### 2. Skills 管理

查看 Skills：

```bash
minicode skills list
```

安装 Skill 到用户目录：

```bash
minicode skills add ./my-skill --name my-skill
```

安装 Skill 到当前项目：

```bash
minicode skills add ./my-skill --name my-skill --project
```

移除 Skill：

```bash
minicode skills remove my-skill
```

移除项目级 Skill：

```bash
minicode skills remove my-skill --project
```

## 九、运行示例

### 示例 1：分析项目

```text
请分析当前项目结构，说明每个核心模块的职责。
```

### 示例 2：读取并修改文件

```text
读取 minicode/agent_loop.py，帮我解释 Agent 主循环是如何执行工具调用的。
```

```text
把 README.md 中的安装说明改得更适合新手。
```

### 示例 3：执行本地命令

```text
帮我运行 Python 编译检查，并修复发现的问题。
```

等价快捷命令：

```bash
/cmd .::python -m compileall -q minicode
```

### 示例 4：使用上下文压缩

```bash
/compact
```

或：

```bash
/collapse
```

## 十、开发与验证

### 1. 编译检查

```bash
python -m compileall -q minicode
```

### 2. 本地启动检查

```bash
MINI_CODE_MODEL_MODE=mock python -m minicode --help
```

### 3. 可编辑安装

```bash
python -m pip install -e .
```

### 4. 包入口检查

```bash
python -m minicode --help
minicode --help
```

## 十一、与原 TypeScript 项目的对应关系

| 原 TypeScript 能力 | Python 版对应实现 |
|---|---|
| Agent loop | `minicode/agent_loop.py` |
| Anthropic adapter | `minicode/anthropic_adapter.py` |
| Tool registry | `minicode/tools/index.py` |
| File tools | `minicode/tools/*.py` |
| Permission manager | `minicode/permissions.py` |
| Session persistence | `minicode/session.py` |
| MCP integration | `minicode/mcp.py` |
| Skills discovery | `minicode/skills.py` |
| Context compaction | `minicode/compact/` |
| TUI / REPL | `minicode/tty_app.py`, `minicode/tui/` |
| CLI management commands | `minicode/manage_cli.py` |

## 十二、注意事项

1. Python 版默认依赖较少，优先使用标准库实现。
2. Web 搜索和网页抓取能力依赖当前运行环境的网络访问。
3. Shell 命令执行受权限系统约束，不建议直接放开所有命令。
4. 使用真实模型时必须配置模型名和认证信息。
5. 若没有配置模型或认证信息，建议先用 `MINI_CODE_MODEL_MODE=mock` 验证本地流程。
6. 终端 UI 是 Python 原生实现，不追求和 TypeScript 版 React/Ink 完全一致。
7. MCP server 是否可用取决于本地是否安装对应命令或远端服务是否可访问。

## 十三、常见问题

### 1. 启动时报 No model configured

原因：没有设置模型名。

解决：

```bash
export ANTHROPIC_MODEL="claude-3-5-sonnet-latest"
```

或写入：

```json
{
  "model": "claude-3-5-sonnet-latest"
}
```

### 2. 启动时报 No auth configured

原因：没有配置 API Key 或 Auth Token。

解决：

```bash
export ANTHROPIC_API_KEY="your-api-key"
```

或：

```bash
export ANTHROPIC_AUTH_TOKEN="your-token"
```

### 3. 只想本地测试，不想配置 API Key

使用 mock 模式：

```bash
MINI_CODE_MODEL_MODE=mock python -m minicode
```

### 4. MCP server 不显示或不可用

检查配置：

```bash
minicode mcp list
```

检查项目级配置：

```bash
minicode mcp list --project
```

检查 `.mcp.json` 或 `~/.mini-code/mcp.json` 是否包含：

```json
{
  "mcpServers": {
    "server-name": {
      "command": "command",
      "args": ["arg1", "arg2"]
    }
  }
}
```

### 5. Skill 没有被发现

检查目录结构是否符合：

```text
my-skill/
└── SKILL.md
```

然后运行：

```bash
minicode skills list
```

## 十四、许可证

本项目沿用原项目许可证，详见 `LICENSE`。

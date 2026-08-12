# Sensors 边车 CLI

一个**实验性**的小型“边车”系统，可在编码智能体旁边运行一系列代码质量传感器。它可以按计划或监视模式执行代码检查、测试及其他检查，并在目标代码库的 `.sensors/` 目录下持久化结构化状态，同时提供一个 **`sensors`** CLI，用于运行服务、查看传感器状态或以人类可读的格式展示状态。

本文的配套仓库：[面向编码智能体的可维护性传感器]

使用 `/_local-setup` 技能在你的机器上进行设置（若希望手动操作，也可将 `SKILL.md` 文件用作文档）。

**平台说明：** 控制平面使用 **Unix 域套接字**，仅在 macOS 上测试过。

这个工具基本上是“即兴编码”（Vibe Coding）完成的，不过我也用 AI 做了定期重构，并且在这个代码库上使用 CLI 运行传感器（“吃自己的狗粮”）。查看 [`.sensors/sensors-cli.sensors.yaml`](./.sensors/sensors-cli.sensors.yaml) 了解此处使用的传感器。并查看 [docs/reports/2026-06-15_modularity-review.md](./docs/reports/2026-06-15_modularity-review.md)，了解类似这种快速传感器为何有助于可维护性，但在我们没有花足够时间关注整体代码结构时，它们的作用也仅此而已……

## 命令

（CLI 需要通过 `uv tool install` 安装，参见 `/_local-setup` 技能）

```bash
# 传感器服务是否正在运行？（退出码 0 = 是, 1 = 否）
sensors status .

# 所有传感器都在本主机上启动进程（通过 /proc 或 ps 查看）
sensors status --all

# 启动传感器
sensors start .

# 显示状态
sensors show .

# 启动传感器并立即进入显示模式
sensors show --start .

# 面向智能体优化的运行器结果（包含每个运行器的失败项）；退出码 0/1/2
sensors check .

sensors check . --runner eslint

# 通过 RPC 保存评分快照（需要进程正在运行）
sensors snapshot .
```

## 配置

CLI 会在 `.sensors/` 目录下查找 `*.sensors.yaml` 文件。

本仓库中有一些技能文档，更详细地记录了配置方式，你可以复用：
- `.claude/skills/sensors_config-default` —— 一个极简的默认配置，会尝试从你的代码库中推断出一个传感器示例。用它来快速体验。
- `.claude/skills/sensors_config-typescript` —— 我完整的 TypeScript 传感器配置
- `.claude/skills/sensors_config-python` —— 我完整的 Python 传感器配置

## 解析器

项目附带了许多常用工具的输出解析器，例如 `eslint` 或 `ruff`。如果你想将一个尚不支持的工作用作传感器，你需要向代码中添加一个新的解析器（并重新安装 CLI），或者也可以使用默认解析器。

### 添加新的解析器

本仓库包含一个技能文档，指导如何添加新的解析器：[`.claude/skills/_new-parser/SKILL.md`](/.claude/skills/_new-parser/SKILL.md)，其中提供了引导模板。

## 默认解析器：期望的输出格式

在你的运行器配置中使用 `parser: default`，即可接入任何能输出符合指定模式的 JSON 对象的工具。你需要为你的工具构建一个脚本，将工具的输出转换为该模式，并在传感器配置中使用该脚本。

本仓库包含一个技能，可以帮助你围绕工具编写包装脚本，将工具数据转换为该 JSON 模式：[`.claude/skills/sensors_wrap-tool/SKILL.md`](/.claude/skills/sensors_wrap-tool/SKILL.md)

### 模式

```json
{
  "findings": [
    {
      "message": "未使用的变量 'x'",
      "severity": "error",
      "file": "src/foo.py",
      "line": 42,
      "column": 9,
      "rule": "F841",
      "context": "x 已赋值但从未使用"
    }
  ],
  "metrics": [
    {
      "key": "errorCount",
      "label": "错误",
      "value": 1,
      "direction": "less"
    }
  ],
  "guidance": [
    {
      "rule": "F841",
      "body": "删除变量或使用它。"
    }
  ],
  "score": {
    "value": 1,
    "direction": "less",
    "description": "工具报告的问题"
  },
  "success": false,
  "summary": "1 个问题",
  "extra": {
    "any": "解析器特定的负载"
  }
}
```

此模式与内置解析器使用的 `SensorReading` 模型一致。所有字段都是可选的；缺失值的派生规则如下：

| 字段 | 若缺失或为 null |
|---|---|
| `findings` | 视为 `[]` |
| `metrics` | 视为 `[]` |
| `guidance` | 视为 `[]` |
| `extra` | 视为 `{}` |
| `success` | `findings` 为空时为 `true`，否则为 `false` |
| `summary` | 根据 findings 数量生成：`"N issue(s)"` / `"No issues"` |
| `score.value` | `len(findings)` |
| `score.direction` | `"less"` （越小越好） |
| `score.description` | `"Issues reported by tool"` |

`success`、`summary` 和 `score` 可以被显式设置，设置后将直接使用。这样，不产生逐项发现条目的工具（例如覆盖率检查）也能直接报告评分。

### 配置示例

```yaml
runners:
  - name: my-custom-check
    parser: default
    enabled: true
    mode: interval
    command: some-tool | ./scripts/to-parser-default-format.sh
    interval: 10000
```

### 最低限度的有效输出

一个仅报告计数而不提供单独违规项的工具：

```json
{"success": false, "summary": "Coverage 72% (threshold 80%)", "score": {"value": 72, "direction": "more"}}
```

一个没有问题的工具：

```json
{"findings": []}
```
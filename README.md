# Codex 隐私过滤插件

这是一个面向 Codex 的隐私过滤与敏感信息脱敏插件项目。

## 项目概述

本项目旨在开发一个 Codex 插件，用于在 AI 辅助开发流程中减少隐私泄露和敏感信息暴露的风险。插件将重点识别、屏蔽和控制 API Key、Token、密码、个人身份信息以及其他机密文本在工具输入、工具输出、日志和用户可见内容中的传播。

## 项目目标

- 检测常见敏感信息模式，例如 API Key、Bearer Token、密码和长凭证字符串。
- 在敏感内容被进一步处理或展示之前完成脱敏。
- 为 Codex 工作流提供可复用的插件结构。
- 让过滤规则具备可配置性和可扩展性。
- 支持课程项目、实验演示和报告撰写中的安全展示需求。

## 初始范围

第一版预计包含以下内容：

- 一个用于 Codex 的插件清单文件。
- 一个基于 Python 的脱敏脚本。
- 一组基于正则表达式的基础规则。
- 本地安装与测试说明文档。
- 示例输入与预期脱敏输出。

## 设计思路

这个插件的目标是在 Codex 工作流前后增加一层隐私保护。项目不默认假设所有输入和输出都是安全的，而是把工具输入、工具输出和中间文本都视为可能包含敏感信息的对象，并在再次使用、展示或记录之前进行检查和过滤。

一个典型流程如下：

1. 接收文本、命令内容或文件内容。
2. 扫描其中的敏感模式。
3. 将命中的内容替换为带类型的 token，例如 `[USER_EMAIL_xxxxxx]`、`[SENSITIVE_SECRET_xxxxxx]`。
4. 可选保存 `token -> 原值` 的映射文件，便于后续恢复。
5. 输出清洗后的安全结果供后续处理。

## 可能处理的敏感信息类别

- API 密钥
- 访问令牌
- 密码
- Authorization 请求头
- 邮箱地址
- 手机号码
- 内部标识符
- 项目中的机密字符串

## 建议目录结构

```text
summer_projection/
  README.md
  codex-privacy-filter/
    .codex-plugin/
      plugin.json
    scripts/
      redact.py
    skills/
      SKILL.md
  examples/
  tests/
  docs/
```

## 使用说明

当前核心脚本位于 `codex-privacy-filter/scripts/redact.py`，支持普通文本和 JSON 两种输入形式。

### 1. 脱敏普通文本

```bash
python codex-privacy-filter/scripts/redact.py
```

然后通过标准输入传入文本，脚本会输出脱敏后的结果。

### 2. 脱敏文件内容

```bash
python codex-privacy-filter/scripts/redact.py input.txt
```

如果传入文件路径，脚本会读取该文件并输出脱敏后的内容。

### 3. 保存 token 映射

```bash
python codex-privacy-filter/scripts/redact.py input.txt --map-out examples/token-map.json
```

这会：

- 输出脱敏后的文本
- 同时将 `token -> 原值` 映射保存到 `examples/token-map.json`

### 4. 恢复原文

```bash
python codex-privacy-filter/scripts/redact.py redacted.txt --restore-map examples/token-map.json
```

这会根据保存下来的映射文件，将脱敏文本中的 token 恢复成原始值。

### 5. 处理 JSON / 嵌套对象

如果输入内容本身是合法 JSON，脚本会自动切换到递归处理模式：

- 递归检查对象中的每一层字段
- 递归检查数组中的每一项
- 对字符串内容进行脱敏
- 对字段名本身带有 `token`、`password`、`secret`、`auth` 等含义的值优先处理

如果输入不是 JSON，脚本会自动退回到普通文本处理模式。

### 6. 运行测试

```bash
python tests/test_redact.py
```

当前测试覆盖了：

- 普通文本脱敏
- JSON 递归脱敏
- `--map-out` 与 `--restore-map` 的往返恢复

## 代码结构说明

当前项目已经把核心文本处理逻辑拆分为几个职责更清晰的模块：

- `codex-privacy-filter/core/redactor.py`
  负责核心脱敏逻辑。主要功能包括：
  - 定义敏感信息识别规则
  - 扫描普通文本中的敏感内容
  - 递归处理 JSON、列表和嵌套对象
  - 生成类型化 token，例如 `[USER_EMAIL_xxxxxx]`
- `codex-privacy-filter/core/vault.py`
  负责 token 映射的保存、读取和恢复。主要功能包括：
  - 将 `token -> 原值` 保存为 JSON 文件
  - 从 JSON 文件中重新读取映射
  - 根据映射将脱敏文本恢复为原始文本
- `codex-privacy-filter/core/utils.py`
  负责通用辅助函数。主要功能包括：
  - 生成 token 哈希值
  - 生成标准 token 格式
  - 判断字段名是否属于敏感字段
- `codex-privacy-filter/scripts/redact.py`
  负责命令行入口。主要功能包括：
  - 接收文件输入或标准输入
  - 调用 `redactor` 完成脱敏
  - 调用 `vault` 保存映射或恢复原文
  - 作为当前项目最直接的运行入口
- `tests/test_redact.py`
  负责基础测试。主要功能包括：
  - 验证普通文本脱敏是否正确
  - 验证 JSON 递归脱敏是否正确
  - 验证映射保存与恢复是否能形成闭环

这种拆分方式的好处是：

- 脱敏逻辑和文件读写逻辑分开，后续更容易维护
- 映射保存与恢复逻辑独立，后面更容易升级成真正的 vault
- 工具函数集中管理，避免核心逻辑里堆太多杂项代码
- 测试可以更容易按模块扩展

## 可修改部分

在后续开发和迭代中，最常需要修改的部分主要有以下几类：

- `codex-privacy-filter/.codex-plugin/plugin.json`
  用于修改插件名称、版本号、简介、界面展示信息和默认提示词。
- `codex-privacy-filter/scripts/redact.py`
  这是核心脱敏逻辑所在文件，可以在这里增删正则规则、调整敏感信息类型、优化 token 命名方式，或者进一步加入 JSON 递归处理与映射保存能力。
- `codex-privacy-filter/skills/SKILL.md`
  用于修改插件在 Codex 中的使用说明，例如适用场景、调用方式和注意事项。
- `examples/`
  用于补充示例输入和示例输出，便于演示项目效果。
- `tests/`
  用于增加测试样例，验证不同敏感信息类型是否能被正确识别和替换。
- `docs/`
  用于补充更详细的设计文档、实验记录、对比分析和后续扩展计划。

如果后面项目继续升级，还可能新增这些可扩展部分：

- 本地代理服务代码，例如 `server.py` 或 `server.ts`
- token 映射与恢复模块，例如 `vault.py`
- 项目级 `.codex/config.toml` 示例配置
- 更完整的敏感信息规则库

## 计划里程碑

1. 完成插件目录结构和清单文件设计。
2. 实现第一版脱敏脚本和基础正则规则。
3. 为常见敏感信息格式补充测试样例。
4. 编写 Codex 中的安装、调用和使用说明。
5. 优化识别准确率，减少误报和漏报。

## 风险与挑战

- 过度脱敏可能误伤正常文本。
- 脱敏不足可能漏掉真实敏感信息。
- Codex 插件机制与 Claude Code 的 Hook 机制不同，接入方式需要单独设计。
- 真实世界中的密钥格式很多，需要持续迭代规则。

## 当前状态

当前项目已经完成了核心文本脱敏原型的第一阶段实现，主要包括：

- 已完成普通文本输入的敏感信息识别与脱敏
- 已完成 JSON / 嵌套对象输入的递归脱敏处理
- 已支持类型化 token 替换，而不再只是统一输出 `REDACTED`
- 已支持 `token -> 原值` 映射保存
- 已支持根据映射文件恢复原文
- 已完成 `redactor / vault / utils / scripts` 的基础模块拆分
- 已补充基础测试，并验证普通文本、JSON 递归处理以及恢复链路可以正常工作
- 已补充 `examples/` 样例文件，能够展示输入、脱敏输出和 token 映射之间的对应关系

目前项目已经从“单一脚本演示”推进到了“具备初步工程结构的核心脱敏原型”阶段。

## 下一步工作

- 创建插件清单文件。
- 编写第一版 Python 脱敏脚本。
- 明确 Codex 调用过滤逻辑的方式。
- 补充示例数据和验证用例。

## 说明

本项目的初始目标是构建一个适合学习、演示和扩展的 Codex 隐私保护插件原型，而不是一开始就提供完整的生产级安全系统。

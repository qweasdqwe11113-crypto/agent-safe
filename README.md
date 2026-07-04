# Agent Privacy Guard 原型

这是一个面向 Agent 工作流的本地隐私过滤原型项目。当前仓库同时包含两条实现路线：

- `guard.py`：当前主线方案，采用 CLI wrapper 方式实现“发送前预览、脱敏、阻断与用户确认”。
- `codex-privacy-filter/`：早期 Codex 插件实验，实现了本地技能调用与脱敏脚本封装。

## 项目概述

本项目旨在开发一个本地隐私保护层，用于在 Agent 读取文本、日志、配置、JSON 和其他上下文之后、内容离开用户机器之前，先完成敏感信息检测、预览、脱敏、阻断和人工确认。项目重点识别、屏蔽和控制 API Key、Token、密码、个人身份信息以及其他机密文本在 Agent 工作流中的传播。

## 项目目标

- 检测常见敏感信息模式，例如 API Key、Bearer Token、密码和长凭证字符串。
- 在敏感内容被进一步处理、展示或发送之前完成预览和脱敏。
- 提供 `allow / mask / block` 三类策略动作。
- 支持“代码先判定，用户可 override”的发送前确认流程。
- 为 Agent 工作流提供可复用的本地保护层。
- 让过滤规则具备可配置性和可扩展性。
- 支持课程项目、实验演示和报告撰写中的安全展示需求。

## 当前实现范围

当前第一版主要包含以下内容：

- 一个基于 Python 的 CLI wrapper 入口 `guard.py`
- 一个基于 Python 的脱敏脚本 `codex-privacy-filter/scripts/redact.py`
- 一组基于正则表达式的基础检测规则
- review / override 的终端预览流程
- `--out` 输出安全文件
- `--codex` 自动调用 `codex exec`
- 本地测试与示例文件

## 设计思路

当前主线方案是在 Agent 工作流前增加一层本地包装器。项目不默认假设所有输入和输出都是安全的，而是把待发送文本、日志、配置片段和中间上下文都视为可能包含敏感信息的对象，并在再次使用、展示或发送之前进行检查和过滤。

一个典型流程如下：

1. 接收文本、命令内容或文件内容。
2. 扫描其中的敏感模式。
3. 根据 profile 自动生成建议动作 `allow / mask / block`。
4. 展示发送前预览，包括原始内容、命中项和脱敏后内容。
5. 用户接受建议，或通过 override 修改最终动作。
6. 输出最终决策结果和安全版本内容，供后续 Agent 或 Codex 使用。

## 可能处理的敏感信息类别

- API 密钥
- 访问令牌
- 密码
- Authorization 请求头
- 邮箱地址
- 手机号码
- 内部标识符
- 项目中的机密字符串
- 支付卡号
- IP 地址

## 建议目录结构

```text
summer_projection/
  README.md
  guard.py
  outputs/
  codex-privacy-filter/
    .codex-plugin/
      plugin.json
    core/
      redactor.py
      utils.py
      vault.py
    scripts/
      redact.py
    skills/
      SKILL.md
  examples/
  tests/
  docs/
```

## CLI Wrapper 使用说明

当前主线入口是 `guard.py`。它实现的是“发送前预览”式保护，而不是直接把内容无条件转发给 Agent。

### 0. 手动运行前准备

先进入项目根目录：

```bash
cd C:\Users\jiahjq\Desktop\summer_projection
```

确认本机可以直接使用 Python：

```bash
python --version
```

### 1. 处理文件输入

```bash
python guard.py examples/plain-input.txt --profile coding
```

这会输出：

- `Detection Results`
- `Risk Level`
- `Suggested Action`
- `Final Action`
- `Override`
- `Original Content`
- `Redacted Content`

### 2. 处理标准输入

```bash
python guard.py --stdin --profile coding
```

在 Windows PowerShell 下，可以这样传多行文本：

```powershell
@"
Authorization: Bearer abcdefghijklmnopqrstuvwxyz
email: test@example.com
password=MySecret123
"@ | python guard.py --stdin --profile coding
```

### 3. 交互式 review

如果希望先看预览，再决定最终动作，可以使用：

```bash
python guard.py examples/plain-input.txt --profile coding --review
```

交互逻辑如下：

- 直接按回车：接受系统建议动作
- 输入 `allow`：允许发送原始内容
- 输入 `mask`：发送脱敏后的内容
- 输入 `block`：阻断发送
- 如果用户修改了系统建议动作，还需要输入 `override reason`

### 4. 非交互 override

如果不走交互 review，也可以直接在命令行里覆盖建议动作：

```bash
python guard.py examples/plain-input.txt --profile coding --override mask --override-reason "demo approval"
```

### 5. 输出到文件

推荐把 wrapper 的产物统一写到 `outputs/` 目录下：

```bash
python guard.py examples/plain-input.txt --profile coding --review --out outputs/safe.txt
```

这条命令的行为是：

- 如果最终动作是 `allow`，则将原文写入 `outputs/safe.txt`
- 如果最终动作是 `mask`，则将脱敏后的内容写入 `outputs/safe.txt`
- 如果最终动作是 `block`，则不会写出 `outputs/safe.txt`

### 6. 自动交给 Codex

如果希望在生成安全文件后自动调用 Codex，可以这样运行：

```bash
python guard.py examples/plain-input.txt --profile coding --review --out outputs/safe.txt --codex --codex-output outputs/codex-result.txt
```

当前这条链路已经在本机验证通过，运行后会发生：

1. `guard.py` 对原始内容做检测与 review
2. 根据最终动作写出 `outputs/safe.txt`
3. 自动调用 `codex exec`
4. Codex 读取 `outputs/safe.txt`
5. 把 Codex 最后一条回复写到 `outputs/codex-result.txt`

成功的完整链路如下：

```text
input.txt
  -> guard.py --review --out outputs/safe.txt
  -> outputs/safe.txt
  -> codex exec
  -> outputs/codex-result.txt
```

说明：

- 当最终动作是 `mask` 时，Codex 实际看到的是脱敏后的内容
- 当最终动作是 `allow` 时，Codex 实际看到的是原始内容
- 当最终动作是 `block` 时，不会写出 `safe.txt`，也不会自动调用 Codex

### 7. 支持的 profile

当前提供三个基础 profile：

- `coding`
- `office`
- `finance`

目前的第一版策略是：

- 命中 secret 类内容时，优先建议 `block`
- 命中 PII 或网络类内容时，优先建议 `mask`
- 未命中规则时，建议 `allow`

### 8. 运行测试

```bash
python -m unittest tests.test_redact tests.test_guard tests.test_guard_override tests.test_guard_codex
```

当前测试覆盖了：

- 普通文本脱敏
- JSON 递归脱敏
- `--map-out` 与 `--restore-map` 的往返恢复
- `guard.py` 的文件输入与标准输入
- review / override 逻辑中的建议动作与最终动作分离
- `--out` 文件输出行为
- `--codex` 命令拼装与参数校验

## 脱敏脚本使用说明

除了 wrapper 主线外，仓库中仍保留底层脱敏脚本 `codex-privacy-filter/scripts/redact.py`，用于直接演示脱敏核心逻辑。

### 1. 脱敏普通文本

```bash
python codex-privacy-filter/scripts/redact.py
```

### 2. 脱敏文件内容

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-input.txt
```

### 3. 保存 token 映射

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-input.txt --map-out examples/plain-token-map.json
```

### 4. 恢复原文

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-redacted.txt --restore-map examples/plain-token-map.json
```

## 代码结构说明

当前项目已经把核心文本处理逻辑拆分为几个职责更清晰的模块：

- `guard.py`
  负责当前主线 CLI wrapper。主要功能包括：
  - 接收文件或标准输入
  - 扫描敏感内容并生成建议动作
  - 展示原始内容和脱敏后内容
  - 支持 review / override
  - 输出安全文件
  - 自动调用 Codex
- `codex-privacy-filter/core/redactor.py`
  负责核心脱敏逻辑。主要功能包括：
  - 定义敏感信息识别规则
  - 扫描普通文本中的敏感内容
  - 递归处理 JSON、列表和嵌套对象
  - 生成类型化 token，例如 `[USER_EMAIL_xxxxxx]`
- `codex-privacy-filter/core/vault.py`
  负责 token 映射的保存、读取和恢复。
- `codex-privacy-filter/core/utils.py`
  负责通用辅助函数。
- `codex-privacy-filter/scripts/redact.py`
  负责底层命令行脱敏入口。
- `tests/test_redact.py`
  负责基础脱敏测试。
- `tests/test_guard.py`
  负责 wrapper 文件输入与标准输入测试。
- `tests/test_guard_override.py`
  负责 override 行为测试。
- `tests/test_guard_codex.py`
  负责 Codex 调用参数拼装测试。

## 可修改部分

在后续开发和迭代中，最常需要修改的部分主要有以下几类：

- `guard.py`
  当前主线 wrapper 入口，可以在这里调整 review 流程、最终动作决策、profile 行为以及后续真实 Agent 转发逻辑。
- `codex-privacy-filter/.codex-plugin/plugin.json`
  用于修改 Codex 插件实验的名称、版本号、简介、界面展示信息和默认提示词。
- `codex-privacy-filter/scripts/redact.py`
  底层命令行入口文件，可以在这里调整输入输出方式、命令行参数或者脚本调用流程。
- `codex-privacy-filter/core/`
  当前核心脱敏逻辑所在目录，可以在这里增删正则规则、调整敏感信息类型、优化 token 命名方式，或者继续扩展 JSON 递归处理与映射保存能力。
- `codex-privacy-filter/skills/SKILL.md`
  用于修改 Codex 插件实验在 Codex 中的使用说明。
- `examples/`
  用于补充示例输入和示例输出，便于演示项目效果。
- `tests/`
  用于增加测试样例，验证不同敏感信息类型是否能被正确识别和替换。
- `docs/`
  用于补充更详细的设计文档、实验记录、对比分析和后续扩展计划。

## 计划里程碑

1. 完成底层脱敏脚本和基础正则规则。
2. 完成 CLI wrapper 主线和发送前预览。
3. 为 `allow / mask / block` 与 override 补充交互流程。
4. 将 wrapper 与 Codex 的自动续跑链路接通。
5. 为 `coding / office / finance` 三类场景补充样例和文档。
6. 继续优化识别准确率，减少误报和漏报。

## 风险与挑战

- 过度脱敏可能误伤正常文本。
- 脱敏不足可能漏掉真实敏感信息。
- 不同 Agent 的插件、hook 和代理接入点差异很大，真正自动接入需要额外工程工作。
- 真实世界中的密钥格式很多，需要持续迭代规则。

## 当前状态

当前项目已经完成了第一阶段原型实现，主要包括：

- 已完成普通文本输入的敏感信息识别与脱敏
- 已完成 JSON / 嵌套对象输入的递归脱敏处理
- 已支持类型化 token 替换，而不再只是统一输出 `REDACTED`
- 已支持 `token -> 原值` 映射保存
- 已支持根据映射文件恢复原文
- 已完成 `guard.py` 的 CLI wrapper 原型
- 已支持系统自动给出 `allow / mask / block` 建议动作
- 已支持发送前预览 `Original Content / Redacted Content`
- 已支持用户接受建议或执行 override
- 已支持将最终结果写出到 `outputs/safe.txt`
- 已支持自动调用 `codex exec`
- 已验证 `guard.py -> outputs/safe.txt -> codex exec -> outputs/codex-result.txt` 链路可用
- 已完成 `redactor / vault / utils / scripts` 的基础模块拆分
- 已补充基础测试，并验证普通文本、JSON 递归处理以及 wrapper 行为可以正常工作

目前项目已经从“单一脚本演示”推进到了“具备 CLI wrapper、发送前预览、用户决策流程和 Codex 自动续跑能力的原型”阶段。

## 下一步工作

- 增加审计日志持久化
- 为 `coding / office / finance` 三类场景补充更完整样例
- 继续优化误报和漏报
- 评估是否扩展到更多 Agent 或代理方式

## 说明

本项目的目标是构建一个适合学习、演示和扩展的 Agent Privacy Guard 原型，而不是一开始就提供完整的生产级安全系统。当前主线是 CLI wrapper，Codex 插件部分作为本地技能实验保留在仓库中。

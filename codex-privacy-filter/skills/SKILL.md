---
name: privacy-filter
description: 当需要在 Codex 工作流中清洗日志、文本、命令内容或配置片段里的敏感信息时，使用这个技能调用本地脱敏脚本。
---

# 隐私过滤技能

这个技能用于在内容被继续处理、展示、复制或分享之前，先检查并脱敏其中可能存在的敏感信息。

适合处理的内容包括：

- 日志输出
- 终端命令内容
- 配置文件片段
- JSON 请求或响应
- Bug 报告中的文本附件
- 准备复制给其他人或提交给模型的敏感文本

## 什么时候使用

当文本中可能包含以下内容时，应优先使用本技能：

- API Key
- Bearer Token
- 密码
- Authorization 请求头
- 邮箱地址
- 手机号
- 内部标识符
- 其他看起来像长凭证或机密字符串的内容

## 技能目标

使用本地脚本 `./scripts/redact.py` 对输入内容进行脱敏，并在需要时保存 `token -> 原值` 映射，供后续恢复使用。

## 使用方式

### 1. 脱敏直接提供的文本

如果需要处理一段普通文本，应调用脚本并通过标准输入传入内容：

```bash
python codex-privacy-filter/scripts/redact.py
```

脚本会把脱敏后的结果输出到标准输出。

### 2. 脱敏文件内容

如果用户给出的是文件路径，应直接将文件路径作为参数传入：

```bash
python codex-privacy-filter/scripts/redact.py <file_path>
```

例如：

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-input.txt
```

### 3. 保存映射文件

如果用户要求后续能够恢复原文，或者当前任务需要保留映射关系，应追加 `--map-out`：

```bash
python codex-privacy-filter/scripts/redact.py <file_path> --map-out <map_file>
```

例如：

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-input.txt --map-out examples/plain-token-map.json
```

### 4. 恢复原文

如果用户已经提供 token 映射文件，并要求恢复脱敏前内容，应调用：

```bash
python codex-privacy-filter/scripts/redact.py <redacted_file> --restore-map <map_file>
```

例如：

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-redacted.txt --restore-map examples/plain-token-map.json
```

## 输出约定

- 默认情况下，脱敏结果输出到终端标准输出
- 使用 `--map-out` 时，映射文件保存到用户指定路径
- 如需把脱敏正文保存到文件，可使用 shell 重定向 `>`

例如：

```bash
python codex-privacy-filter/scripts/redact.py examples/plain-input.txt > examples/plain-redacted.txt
```

## 处理规则说明

脚本会优先尝试把输入解析为 JSON：

- 如果是合法 JSON，则递归处理对象、数组和字符串字段
- 如果不是 JSON，则按普通文本处理

对于字段名中带有 `token`、`password`、`secret`、`auth` 等语义的值，应提高敏感性判断优先级。

## 使用时的行为要求

使用本技能时应遵循以下原则：

1. 在内容继续传播前先脱敏，而不是事后补救。
2. 向用户展示时，默认返回脱敏后的安全结果，而不是原始敏感内容。
3. 如果脱敏结果将用于报告、分享或演示，应提醒用户人工复核，避免误报或漏报。
4. 如果用户明确要求恢复原文，只有在提供映射文件时才执行恢复。

## 限制

这是一个基于模式匹配和规则判断的本地脱敏原型：

- 可能出现误报
- 可能漏掉格式特殊的密钥
- 不能替代人工安全审查

因此，所有输出在用于正式共享、公开展示或提交前，仍建议进行人工复核。

# Agent Privacy Guard

这是一个面向 Agent 会话的本地隐私过滤原型。当前主线已经从早期的单轮 CLI wrapper，升级为：

- 本地会话型 API 后端
- 发送前预览
- `allow / mask / block`
- token 映射与恢复
- 会话审计与产物落盘
- 真实模型服务接入

当前项目已经支持：

- `mock` 模型模式，用于本地联调
- `rightcode` 模式，用于真实远程模型回复
- OpenAI-compatible 适配层

## 当前定位

本项目当前更准确的定位不是“Codex 原生插件”，而是：

**一个可复用的本地 Agent Privacy Proxy / Wrapper**

它的作用是在内容离开本机、发送给远程模型前，先完成：

1. 敏感内容扫描
2. 发送前预览
3. 脱敏或阻断
4. 用户 override
5. 会话审计

这种做法符合课程项目里允许的：

- plugin
- proxy
- wrapper
- sidecar
- OpenAI-compatible 代理方式

## 当前主线架构

```text
Web / Client
  -> Agent Privacy Guard API Server
  -> guard_core (scan / preview / mask / block / restore)
  -> session_state (session / turn / audit / artifacts)
  -> model_client (mock / rightcode / openai-compatible)
  -> remote model service
```

## 当前已实现

- 敏感信息检测与脱敏
- PII / secret / network 基础规则
- `allow / mask / block`
- 发送前 preview
- token map 保存与恢复
- 单轮 CLI 原型
- 会话型 HTTP API 后端
- 会话历史持久化
- 会话产物输出到 `outputs/`
- `mock` 模型联调
- `rightcode` 真实模型接入
- 基础单元测试

## 项目结构

```text
summer_projection/
  README.md
  guard.py
  guard_core.py
  session_guard.py
  session_state.py
  model_client.py
  server.py
  codex_client.py
  outputs/
  examples/
  tests/
  codex-privacy-filter/
```

## 核心文件说明

- [guard_core.py](C:/Users/jiahjq/Desktop/summer_projection/guard_core.py)
  负责单条消息的扫描、建议动作、预览、脱敏、恢复

- [session_state.py](C:/Users/jiahjq/Desktop/summer_projection/session_state.py)
  负责会话、轮次、审计记录、会话文件持久化

- [model_client.py](C:/Users/jiahjq/Desktop/summer_projection/model_client.py)
  负责模型调用，目前支持 `mock`、`rightcode`、`openai_compatible`

- [server.py](C:/Users/jiahjq/Desktop/summer_projection/server.py)
  当前主线 HTTP API 后端，后续 Web 前端直接接这里

- [guard.py](C:/Users/jiahjq/Desktop/summer_projection/guard.py)
  早期单轮 CLI wrapper 原型，仍然保留用于演示单条消息流程

- [session_guard.py](C:/Users/jiahjq/Desktop/summer_projection/session_guard.py)
  早期终端会话原型，当前不再是最终主线

## 快速开始

进入项目目录：

```powershell
cd C:\Users\jiahjq\Desktop\summer_projection
```

确认 Python 可用：

```powershell
python --version
```

## 启动 API 后端

### 1. 本地 mock 模式

这个模式不请求真实模型，适合本地联调：

```powershell
python server.py
```

启动后会看到：

```text
Agent Privacy Guard API listening on http://127.0.0.1:8000
Model provider: mock (mock-gpt)
```

### 2. rightcode 真实模型模式

当前代码会自动读取：

- `C:\Users\jiahjq\.codex\config.toml` 里的 `rightcode base_url`
- 默认模型名，例如 `gpt-5.4`

你只需要在启动前设置 key：

```powershell
$env:APG_MODEL_PROVIDER="rightcode"
$env:OPENAI_API_KEY="你的 rightcode key"
python server.py
```

如果启动成功，会看到：

```text
Model provider: rightcode (gpt-5.4)
```

说明当前后端已经在请求真实模型，而不是 mock。

## API 用法

### 1. 创建会话

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions" `
  -ContentType "application/json" `
  -Body '{"profile":"coding","session_id":"demo-session"}'
```

### 2. 查看发送前预览

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/preview" `
  -ContentType "application/json" `
  -Body '{"message":"email=test@example.com"}'
```

这个接口会返回：

- 原文
- 脱敏后内容
- token map
- 建议动作
- 风险等级

### 3. 提交一轮消息

例如按 `mask` 发送：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/messages" `
  -ContentType "application/json" `
  -Body '{"message":"email=test@example.com","final_action":"mask","override_reason":"demo"}'
```

例如直接 `allow`：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/messages" `
  -ContentType "application/json" `
  -Body '{"message":"hello","final_action":"allow"}'
```

如果当前是 `rightcode` 模式，这一步会真实请求模型。

### 4. 查看会话

```powershell
Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/sessions/demo-session"
```

### 5. 查看所有轮次

```powershell
Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/turns"
```

## 输出产物

当前 API 会话产物写到：

```text
outputs/api-sessions/<session-id>/
```

每一轮通常会生成：

- `turn-001-user-original.txt`
- `turn-001-user-safe.txt`
- `turn-001-token-map.json`
- `turn-001-model-raw.json`
- `turn-001-assistant-raw.txt`
- `turn-001-assistant-restored.txt`

同时会写：

- `session.json`
- `session-log.jsonl`

## 早期 CLI 原型

虽然当前主线已经是 API 后端，但项目里仍保留两个早期原型：

### 1. 单轮 CLI wrapper

```powershell
python guard.py examples\plain-input.txt --profile coding
```

### 2. 终端会话原型

```powershell
python session_guard.py --profile coding
```

它们主要用于：

- 保留开发演进过程
- 演示单条消息 preview / override
- 对比“CLI 原型”和“API 会话后端”两条路线

## 测试

运行测试：

```powershell
python -m unittest tests.test_model_client tests.test_server tests.test_session_state tests.test_guard tests.test_guard_override tests.test_guard_codex tests.test_redact
```

## 当前已验证的能力

当前已经在本机验证过：

- API 会话创建成功
- preview 接口成功
- `mask` 后发送成功
- token 恢复成功
- `rightcode` 能返回真实模型回复

例如下面这个结果就说明已经是真实模型回复：

```text
assistant_reply : Hello! How can I help?
assistant_raw_reply : Hello! How can I help?
```

如果是 mock 模式，回复会像：

```text
[mock:coding] turn 1 received. Latest message: hello
```

## 当前限制

- 当前策略规则还比较基础，覆盖面还需要继续扩展
- `rightcode` 的 `responses` 接口返回格式和本项目初版假设不完全一致
- 当前已通过回退到 `chat/completions` 兼容调用解决这一问题
- 当前前端界面还没有完成，主线先是 API 后端
- 当前还不是 Codex 应用内原生 UI 插件，而是本地会话代理/包装器

## 下一步

建议接下来按这个顺序推进：

1. 做最小 Web 前端
2. 补三类场景模板：coding / office / finance
3. 扩充规则配置化
4. 补评测集与效果报告
5. 补更完整的审计展示

## 说明

本项目当前目标是做出一个能演示“Agent 会话在发送前可以被本地扫描、预览、脱敏、阻断和审计”的可运行原型，而不是一开始就做成完整企业级 DLP 系统。

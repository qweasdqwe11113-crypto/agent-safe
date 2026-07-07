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
4. 用户确认或 override
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
- 可选 HanLP 增强中文 NER 检测
- CLI 文件级输入规则
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
  负责单条消息的扫描、建议动作、预览、脱敏、恢复。

- [session_state.py](C:/Users/jiahjq/Desktop/summer_projection/session_state.py)
  负责会话、轮次、审计记录、会话文件持久化。

- [model_client.py](C:/Users/jiahjq/Desktop/summer_projection/model_client.py)
  负责模型调用，目前支持 `mock`、`rightcode`、`openai_compatible`。

- [server.py](C:/Users/jiahjq/Desktop/summer_projection/server.py)
  当前主线 HTTP API 后端，后续 Web 前端直接接这里。

- [guard.py](C:/Users/jiahjq/Desktop/summer_projection/guard.py)
  早期单轮 CLI wrapper 原型，仍然保留用于演示单条消息流程。

- [session_guard.py](C:/Users/jiahjq/Desktop/summer_projection/session_guard.py)
  早期终端会话原型，当前不再是最终主线。

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

启动后也可以直接在浏览器打开：

```text
http://127.0.0.1:8000/
```

Web 控制台已经接通当前主流程：

- 创建或加载 session
- 输入消息并调用 `/preview`
- 查看原文 / 脱敏文 / 风险等级 / 建议动作
- 选择 `allow / mask / block`
- 调用 `/confirm` 并查看回复与历史 turn

### 2. rightcode 真实模型模式

当前代码会自动读取：

- `C:\Users\jiahjq\.codex\config.toml` 里的 `rightcode` `base_url`
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

说明当前后端已经在请求真实模型，而不是 `mock`。

## API 使用流程

当前推荐流程是：

1. 创建会话
2. 调用 `/preview` 看原文、脱敏文和三种动作的结果
3. 用户决定是否 override
4. 调用 `/confirm` 真正发送给模型

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

- `preview_id`
- 原文
- 脱敏后内容
- token map
- 建议动作
- 风险等级
- `allow / mask / block` 三种动作分别会发什么

例如返回里会包含：

- `suggested_action`
- `suggested_sent_text`
- `action_options.allow.sent_text`
- `action_options.mask.sent_text`
- `action_options.block.sent_text`

### 3. 确认最终动作并真正发送

拿到上一步的 `preview_id` 之后，再调用 `/confirm`。

例如按建议 `mask`：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/confirm" `
  -ContentType "application/json" `
  -Body '{"preview_id":"上一步返回的preview_id","final_action":"mask"}'
```

例如 override 成 `allow`：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/confirm" `
  -ContentType "application/json" `
  -Body '{"preview_id":"上一步返回的preview_id","final_action":"allow","override_reason":"demo allow override"}'
```

例如直接阻断：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/demo-session/confirm" `
  -ContentType "application/json" `
  -Body '{"preview_id":"上一步返回的preview_id","final_action":"block","override_reason":"too sensitive"}'
```

只有这一步才会真正：

- 写入 turn
- 发送给模型
- 返回模型回复

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

### 6. 说明

旧的 `/messages` 直发流程已经不再推荐使用。当前主线是：

- `/preview`
- `/confirm`

这样更符合“先看改成什么样，再决定是否 override”的产品逻辑。

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

当前 CLI 不仅支持把输入当作普通文本扫描，也支持对“整文件输入”执行额外的文件级检查。对于文件输入，当前已增加：

- 敏感文件名规则，例如 `.env`、`id_rsa`、`credentials.json`
- 敏感目录规则，例如 `node_modules`、`.git`、`.ssh`、`.aws`、`.kube`
- 二进制文件检测
- 大文件检测

这意味着当用户直接把某个文件作为输入交给 wrapper 时，系统不只扫描文件内容本身，还会结合文件名、所在目录、文件类型和文件大小做额外风险判断。

例如：

```powershell
python guard.py .env --profile coding
```

如果命中文件级高风险规则，系统会直接进入 `BLOCK` 或更高风险决策，而不只是把文件内容当普通文本处理。

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

## HanLP 增强中文 PII 检测

当前项目已经支持通过本地 HanLP 模型增强中文文本中的 PII 检测能力，适合处理“不是固定键值对，而是自然语言直接描述”的场景。

例如下面这种自由文本：

```text
我叫张三，住在深圳市南山区科技园科苑路15号，身份证号是440305199901011234
```

在接入 HanLP 后，可以识别出：

- `Person Name`
- `Street Address`
- `National Id`

并自动并入现有的 `allow / mask / block` 决策链路。

### 说明

- 当前项目中的 NER 接口由 [ner_adapter.py](C:/Users/jiahjq/Desktop/summer_projection/ner_adapter.py) 提供
- 如果本机已安装并可加载 HanLP，则优先使用 HanLP
- 如果 HanLP 不可用，则自动回退到轻量启发式识别
- 当前 HanLP 主要用于增强中文自由文本中的姓名、地址、证件号识别

### 安装 HanLP

先安装 HanLP：

```powershell
pip install hanlp
```

如需完整依赖，也可以安装：

```powershell
pip install hanlp[full]
```

### 下载并加载 HanLP NER 模型

第一次使用前，建议手动触发一次模型下载：

```powershell
python -c "import hanlp; import hanlp.pretrained.ner as ner; hanlp.load(ner.MSRA_NER_ELECTRA_SMALL_ZH); print('hanlp model ok')"
```

如果最后输出：

```text
hanlp model ok
```

说明 HanLP NER 模型已经成功下载并可用。

### 验证项目是否正在使用 HanLP

运行：

```powershell
python -c "from ner_adapter import _load_backend; print(_load_backend()[0])"
```

如果输出：

```text
hanlp
```

说明当前项目已经切换到 HanLP 后端，而不是启发式回退模式。

### 验证自由文本检测效果

可以直接运行：

```powershell
python guard.py --stdin --profile coding
```

输入示例：

```text
我叫张三，住在深圳市南山区科技园科苑路15号，身份证号是440305199901011234
```

结束输入后，预期会看到类似结果：

- `Person Name: 1`
- `Street Address: 1`
- `National Id: 1`
- `Suggested Action: MASK`

说明自由文本里的姓名、地址、证件号已经能被 HanLP 增强识别并脱敏。

## 当前已验证的能力

当前已经在本机验证过：

- API 会话创建成功
- `/preview` 接口成功
- `/confirm` 确认发送成功
- `mask` 后发送成功
- token 恢复成功
- `rightcode` 能返回真实模型回复
- 多轮上下文能被模型记住
- HanLP 增强的中文自由文本 PII 检测可用
- CLI 文件输入可识别敏感文件名、敏感目录和二进制文件风险

例如下面这个结果就说明已经是真实模型回复：

```text
assistant_reply : Hello! How can I help?
assistant_raw_reply : Hello! How can I help?
```

如果是 `mock` 模式，回复会像：

```text
[mock:coding] turn 1 received. Latest message: hello
```

如果多轮上下文正常工作，模型可以回答类似：

```text
Sure:
- You: "hello"
- Me: "Hello! How can I help?"
...
```

## 当前限制

- 当前策略规则还比较基础，覆盖面还需要继续扩展
- `rightcode` 的 `responses` 接口返回格式和本项目初版假设不完全一致
- 当前已通过回退到 `chat/completions` 兼容调用解决这一问题
- 当前前端界面还没有完成，主线先是 API 后端
- 当前还不是 Codex 应用内原生 UI 插件，而是本地会话代理/包装器

## 规则覆盖现状

如果按下面这 4 类目标来衡量：

1. PII 检测：姓名、电话、邮箱、地址、证件号等
2. Secret scanning：API key、token、私钥、数据库 URL、云凭据等
3. 文件与路径规则：例如 `.env`、`id_rsa`、`credentials.json`、`node_modules`、大型二进制文件、内部配置目录
4. 代码和日志规则：内部 endpoint、cookie、authorization header、错误堆栈中的用户信息

那么当前项目状态是：

**已经做到“基础可用”，但还没有完全达到上述完整目标。**

### 当前已经覆盖

- 姓名（基于常见字段名）
- 邮箱
- 手机号
- 地址（基于常见字段名）
- 证件号（当前已覆盖中国大陆身份证号等常见字段）
- IPv4 / IPv6 地址
- 支付卡号
- `Authorization: Bearer ...`
- 常见 secret / key / token 键值
- OpenAI key
- Anthropic key
- GitHub token
- NPM token
- Stripe secret
- 数据库 URL（如 `postgres://`、`mysql://`、`mongodb://`、`redis://`）
- 云凭据基础规则（如 AWS Access Key / Secret、Azure Storage Connection String、常见云凭据字段）
- 私钥块
- JSON 递归扫描与脱敏
- 可选 NER 式自由文本实体识别接口（当前默认回退为轻量启发式识别，可在本机安装 HanLP / spaCy 后接入真实 NER 后端）
- 文件级输入规则：敏感文件名、敏感目录、二进制文件、大文件
- 基于 profile 的 `allow / mask / block`

### 当前还明显缺失

- 更完整的云凭据覆盖（例如 GCP service account 结构专项、Azure / AWS 更多变体）
- Cookie
- 内部 endpoint / 内网 URL 专项规则
- 错误堆栈中的用户信息专项规则
- API 主线中的文件上传 / 文件对象级检查

### 当前结论

因此，如果目标是“演示发送前隐私过滤原型”，当前版本已经可以支撑基础展示；
如果目标是“覆盖更完整的企业级敏感信息检查范围”，当前规则体系还需要继续补齐。

### 建议下一步优先级

1. 先补 Secret 缺口：数据库 URL、云凭据
2. 再补代码与日志规则：Cookie、内部 endpoint、错误堆栈中的用户信息
3. 再补文件名 / 路径 / 目录规则
4. 最后补大文件 / 二进制文件规则

## 下一步

建议接下来按这个顺序推进：

1. 做最小 Web 前端
2. 补三类场景模板：coding / office / finance
3. 扩充规则配置化
4. 补评测集与效果报告
5. 补更完整的审计展示

## 说明

本项目当前目标是做出一个能演示“Agent 会话在发送前可以被本地扫描、预览、脱敏、阻断和审计”的可运行原型，而不是一开始就做成完整企业级 DLP 系统。

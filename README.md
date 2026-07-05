# Agent Privacy Guard 原型

这是一个面向 Agent 工作流的本地隐私过滤原型。当前主线方案是 `guard.py` 这个 CLI wrapper：先扫描输入内容里的敏感信息，给出 `allow / mask / block` 建议，展示发送前预览，再由用户接受建议或 override。

项目里同时保留了一版早期 Codex 插件实验代码，目录是 `codex-privacy-filter/`；但当前推荐演示和继续开发的主线，是 CLI wrapper。

## 当前已实现

- 文件输入和标准输入
- 敏感信息检测与脱敏
- 风险分级和建议动作
- 发送前预览
- `--review` 交互确认
- `--override` / `--override-reason`
- `--out` 输出最终发给 Agent 的文本
- `--codex` 自动调用 `codex exec`
- `--codex-output` 保存 Codex 原始回复
- 自动生成原文、token 映射、恢复后的回复

## 项目结构

```text
summer_projection/
  README.md
  guard.py
  outputs/
  examples/
  tests/
  codex-privacy-filter/
    core/
    scripts/
    skills/
```

## 快速开始

先进入项目目录：

```powershell
cd C:\Users\jiahjq\Desktop\summer_projection
```

确认 Python 可用：

```powershell
python --version
```

## 基本用法

处理文件：

```powershell
python guard.py examples\plain-input.txt --profile coding
```

处理标准输入：

```powershell
@"
Authorization: Bearer abcdefghijklmnopqrstuvwxyz
email: test@example.com
"@ | python guard.py --stdin --profile coding
```

## review / override

交互 review：

```powershell
python guard.py examples\plain-input.txt --profile coding --review
```

交互时：

- 直接回车：接受系统建议动作
- 输入 `allow`：允许原文继续使用
- 输入 `mask`：使用脱敏后的内容
- 输入 `block`：阻断
- 如果你改了系统建议，还需要输入 `override reason`

非交互 override：

```powershell
python guard.py examples\plain-input.txt --profile coding --override mask --override-reason "demo approval"
```

## 输出文件

推荐把 wrapper 的产物都放到 `outputs/` 目录：

```powershell
python guard.py examples\plain-input.txt --profile coding --review --out outputs\safe.txt
```

运行后，`outputs/` 里会得到这些文件：

- `original.txt`
  输入原文
- `safe.txt`
  真正准备继续交给 Agent 的文本
  如果最终动作是 `allow`，这里是原文
  如果最终动作是 `mask`，这里是脱敏后的文本
  如果最终动作是 `block`，这个文件不会写出
- `token-map.json`
  token 到原始敏感值的映射

## 自动调用 Codex

如果想在 wrapper 处理完之后，自动把结果交给 Codex：

```powershell
python guard.py examples\plain-input.txt --profile coding --review --out outputs\safe.txt --codex --codex-output outputs\codex-result.txt
```

完整链路如下：

```text
输入原文
  -> guard.py 检测 / 预览 / review
  -> outputs/original.txt
  -> outputs/safe.txt
  -> outputs/token-map.json
  -> codex exec
  -> outputs/codex-result.txt
  -> outputs/codex-result-restored.txt
```

其中：

- `codex-result.txt`
  Codex 的原始回复
- `codex-result-restored.txt`
  把 Codex 回复里的占位符 token 按 `token-map.json` 恢复后的版本

这样你就能同时保留：

- 原文
- 真正发给 Codex 的内容
- Codex 原始回答
- 恢复后的可读回答

## allow / mask / block 的逻辑

当前设计是：

1. 系统先根据规则自动检测
2. 系统给出建议动作
3. 用户看到原文、命中项、脱敏后内容
4. 用户接受建议，或者 override

也就是说，标准设计不是“纯手选”，而是“代码先判，用户可 override”。

## profile

当前内置三个 profile：

- `coding`
- `office`
- `finance`

当前第一版策略大致是：

- 命中 `secret` 时优先建议 `block`
- 命中 `pii` 或 `network` 时优先建议 `mask`
- 未命中时建议 `allow`

## 和 Codex 的接入方式

当前接入不是系统级自动拦截，而是 wrapper 式接入：

1. 先运行 `guard.py`
2. 由 `guard.py` 输出安全版本文本
3. 再由 `guard.py` 自动调用 `codex exec`

所以这更像“本地前置保护层”，而不是直接嵌进 Codex 内核里的透明拦截器。

如果后续要做得更自动，可以继续往这些方向扩展：

- 真正的 CLI wrapper
- MCP server
- OpenAI-compatible proxy
- sidecar / hook

对这个暑期项目来说，先把 CLI wrapper 做扎实，最容易形成完整 demo 和报告。

## 运行测试

```powershell
python -m unittest tests.test_redact tests.test_guard tests.test_guard_override tests.test_guard_codex
```

## 适合现在继续做的事

如果你下一步想把这个项目做成“能交付、能演示、能写报告”的版本，建议按这个顺序推进：

1. 先把 `coding / office / finance` 三套样例补完整
2. 再补评测语料和误报/漏报统计
3. 再补审计日志
4. 最后再考虑更自动的接入方式

## 说明

这个项目当前目标是做出一个能展示“Agent 发送上下文前可以被本地扫描、预览、脱敏、阻断”的原型，而不是一开始就做成完整企业级 DLP 系统。

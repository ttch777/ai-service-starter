# ai-service-starter 设计说明

## 为什么把 SDK 的 max_retries 设为 0

OpenAI SDK 自带重试机制。如果外层再重试一次，一次失败会被放大成 3×3 = 9 次调用，
这会显著增加成本、拖长尾延迟，并在上游故障时把对方打挂。
所以本项目关闭 SDK 重试，所有重试统一由 app/llm/client.py 的 _with_retry 负责。
重试采用指数退避，只对 5xx、429、连接错误和超时生效，4xx 直接抛出。

## 降级策略

按 config/models.yaml 中的 fallbacks 链逐个尝试。
缺失 API Key 的供应商会被直接跳过，不计为失败。
流式请求只在建立连接阶段允许切换模型；一旦开始输出 token 就不再切换，
避免用户看到两个回答粘在一起。

## 默认模型的选择

配置里的 default 是首选。若它没有凭证，则自动退到第一个可用模型。
这样只配了一家 Key 时，不必每次请求都先撞两个失败再降级。

## 日志设计

采用单行 JSON 格式，包含 trace_id、event、latency_ms、prompt_tokens 等字段。
uvicorn 自带的 handler 被移除，日志统一走 JsonFormatter，避免非 JSON 行混入。
每个请求由 TraceMiddleware 分配 trace_id，写进日志和响应头。

## 错误码约定

- 400 表示请求本身有问题，比如模型名不存在
- 503 表示服务暂时不可用，但这不是代码 bug，客户端应该重试或降级
- 500 表示真的出错了，应该报警

## 已知限制

没有鉴权、没有持久化、没有限流。
只覆盖 chat 接口，embedding 与 rerank 各家的接口形状不同，不在兼容范围内。

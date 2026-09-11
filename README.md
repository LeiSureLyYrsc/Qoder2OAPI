# Qoder2OAPI

把 Qoder 中国区模型接口转成 OpenAI Chat Completions（含工具调用）。不调用 `qodercn` CLI。

## 要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## 启动

```bash
uv sync
uv run qoder2oapi
```

默认监听 `127.0.0.1:8000`。首次启动会生成代理 API Key，打印到终端并写入 `data/api_key.txt`。

打开 http://127.0.0.1:8000 ，粘贴该 Key。然后可以用浏览器「登录 Qoder」或粘贴 Qoder PAT（`pt-...`，来自 [账号集成](https://qoder.com.cn/account/integrations)）把账号加入号池。

PAT 不是本代理的 API Key。PAT 会兑换成短命 job token；过期后自动 `jobToken/refresh`，失败再重新兑换。OAuth 账号不自动刷新，失效后需重新登录。

请求按号池轮询。额度耗尽或登录失效会自动打标并跳过，可在控制台清除标记。`/v1/dashboard/billing/usage` 的 credits 是号池合计。

## 调用

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <代理 API Key>" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}],
    "tools": []
  }'
```

- `GET /v1/models`
- `POST /v1/chat/completions`（支持 `stream` 与 `tools`）
- `GET /v1/dashboard/billing/usage`（credits，不是美元）
- `GET /v1/dashboard/billing/subscription`

`GET /v1/models` 返回公开名（如 `deepseek-v4-pro`、`qwen3.7-max`），内部 key（`dmodel` 等）仍可调用。

未指定时，每个模型使用其最大思考强度和最大上下文。可用 `reasoning_effort`、`max_tokens`，或 `extra_body.context_length` 覆盖窗口。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QODER2OAPI_API_KEY` | 自动生成 | 代理鉴权密钥 |
| `QODER2OAPI_HOST` | `127.0.0.1` | 监听地址 |
| `QODER2OAPI_PORT` | `8000` | 端口 |
| `QODER2OAPI_DATA_DIR` | `./data` | 代理 Key、号池凭据目录 |

## 测试

```bash
uv sync --group dev
uv run pytest -q
```

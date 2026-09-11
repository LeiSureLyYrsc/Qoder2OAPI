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

请求按号池轮询。额度耗尽或登录失效会自动打标并跳过；控制台点标记或「清除标记」后重新进入轮询。号池明文保存在项目 `data/accounts.json`，控制台可导出/导入（合并或整池替换）。导出文件含登录凭证，不要发到公开地方。`/v1/dashboard/billing/usage` 的 credits 是号池合计。

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

`GET /v1/models` 返回带 `cn/` 前缀的公开名（如 `cn/deepseek-v4-pro`、`cn/qwen3.7-max`）。不带前缀的公开名和内部 key（`dmodel` 等）仍可调用。客户端里的 DeepSeek-Flash / V4.1-Flash 对应 `cn/deepseek-v4-flash`（内部 `dfmodel`）。

未指定时，每个模型使用其最大思考强度和最大上下文。可用 `reasoning_effort`、`max_tokens`，或 `extra_body.context_length` 覆盖窗口。

## Docker 部署

官方预构建镜像托管在 GitHub Packages (GHCR)：`ghcr.io/leisurelyyrsc/qoder2oapi:latest`。
镜像内不包含任何账号与密码凭证；号池与密钥会持久化保存至映射的数据卷中（注意：`data/accounts.json` 保存明文账号登录凭证，请妥善保护宿主机目录权限）。

### 使用 Docker Compose

1. 创建数据目录并启动服务。Linux 主机需要让容器内 UID `10001` 可写该目录：
   ```bash
   mkdir -p data
   sudo chown -R 10001:10001 data
   docker compose up -d
   ```

2. 服务启动后默认监听宿主机端口 `8000`，在浏览器打开：
   ```text
   http://127.0.0.1:8000
   ```

3. 访问控制台与凭据持久化：
   - 首次启动若未指定 `QODER2OAPI_API_KEY`，会自动生成并在终端日志及宿主机 `./data/api_key.txt` 中写入代理 API Key。
   - 打开控制台粘贴该 Key 即可使用管理功能。号池数据保存在宿主机 `./data/accounts.json`。

4. 更新镜像与服务：
   ```bash
   docker compose pull
   docker compose up -d
   ```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QODER2OAPI_API_KEY` | 自动生成 | 代理鉴权密钥 |
| `QODER2OAPI_HOST` | `127.0.0.1` | 监听地址 |
| `QODER2OAPI_PORT` | `8000` | 端口 |
| `QODER2OAPI_DATA_DIR` | `./data` | 代理 Key（`api_key.txt`）和号池（`accounts.json`）目录 |

## 测试

```bash
uv sync --group dev
uv run pytest -q
```

# API 文档入口

| 文档 | 内容 |
|------|------|
| [../05-code-api.md](../05-code-api.md) | 收码 API（公开 token URL）完整规格 |
| [../02-architecture.md](../02-architecture.md) | 管理 API 与模块边界 |

实现阶段将在此目录补充 OpenAPI（`openapi.yaml`）导出。

## 路径约定（草案）

| 前缀 | 鉴权 | 用途 |
|------|------|------|
| `/admin/*` 或 `/api/admin/*` | 管理登录 | 账号、取信、生成收码 URL |
| `/api/v1/code/{token}` | token | 外部收码 |
| `/` 静态或 SSR | 管理登录后 | Web 操作台 |

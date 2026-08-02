# 05 · 收码 API

## 1. 产品行为

用户在 Web 操作台对某条**已保存到服务器的账号记录**点击 **「API 取件」**：

1. 服务端为该记录生成（或返回）**唯一** `token`（一对一）。  
2. 返回绝对 **API URL**。  
3. **浏览器缓存**同步更新该账号上的 `code_api_url` / token 展示字段。  
4. 前端一键复制 URL。  

前提：账号凭证已在服务端入库。仅浏览器缓存、未入库的账号需先「保存到服务器」。

外部系统只请求该 URL（**无需 Header、无需再传密码**）。  
后端用库中凭证取信并解析验证码。

```text
[Web] 点击 API 取件 → 服务端唯一 token → 写回浏览器缓存 → 展示 URL
[脚本] GET 或 POST URL → OpenMail 取信 → 返回验证码
```

## 2. URL 形态与方法

```text
https://{host}/api/v1/code/{token}
```

- `token`：高熵唯一值（建议 ≥ 128 bit，url-safe，前缀如 `om_c_`）。  
- **一记录一 token**；重置后旧 URL 立即失效。  
- **鉴权仅靠 URL 中的 token**；不要求 `Authorization` 等 Header。  
- **GET 与 POST 均支持**（参数：GET 用 query；POST 可用 query 或 JSON body）。  
- **运营限流暂不做**；**工程底线必须启用**：短缓存优先、同账号串行、最小 fetch 间隔（见 09 §8 / 10）。
可选参数：

| 参数 | 说明 | 默认 |
|------|------|------|
| format | `text` \| `json` \| `json_compat` \| `nullx` | `json` |
| folder | `inbox` \| `junk` | `inbox` |
| keyword | 邮件过滤关键词 | 空 |
| regex | 验证码提取正则 | 内置规则 |
| refresh | `0`\|`1` 强制绕过短缓存 | `0` |
| quick | `0`\|`1` 快速取件 | `1` |

示例：

```http
GET  /api/v1/code/om_c_xxx?format=text
POST /api/v1/code/om_c_xxx
POST /api/v1/code/om_c_xxx?format=json
Content-Type: application/json

{"keyword":"ChatGPT","regex":"\\d{6}"}
```

## 3. 返回格式

### 3.1 `format=text`

成功：正文仅为验证码

```text
123456
```

失败：短文本错误码（可配置）

```text
NULL
```

或

```text
ERROR: token invalid
```

### 3.2 `format=json`（推荐默认）

成功：

```json
{
  "ok": true,
  "code": "123456",
  "email": "user@example.com",
  "subject": "Your verification code",
  "from": "noreply@service.com",
  "date": "2026-08-01T12:00:00Z",
  "folder": "inbox",
  "fetched_at": "2026-08-01T12:01:00Z",
  "cached": false
}
```

失败：

```json
{
  "ok": false,
  "error": "no_code",
  "message": "未找到验证码"
}
```

### 3.3 `format=json_compat`

兼容常见字段名：

```json
{
  "ok": true,
  "verification_code": "123456",
  "email": "user@example.com",
  "subject": "...",
  "messages": []
}
```

### 3.4 `format=nullx`

对齐 mail.com.helper 风格：

- 成功：验证码纯文本  
- 失败：字面量 `NullX`  

## 4. 错误码（json）

| error | HTTP | 含义 |
|-------|------|------|
| invalid_token | 404/401 | token 不存在或已重置 |
| disabled | 403 | 账号或 token 禁用 |
| no_code | 200 或 404（可配） | 取信成功但无验证码 |
| fetch_failed | 502 | 上游取信失败 |
| rate_limited | 429 | 限流 |
| ssrf_blocked | 400 | http_api URL 非法（管理侧问题） |

## 5. 服务端处理步骤

```text
1. 加载 token → 已入库 Account（无效则 404）
2. 若 refresh=0 且短缓存可用且已有 code → 可直接返回
3. 否则 Provider.fetch（并滚动写回 cookies / 新 refresh_token）
4. keyword / regex 筛选与提取
5. 更新 latest_verification_code、last_used_at
6. 按 format 输出
```

（运营配额/产品化 429 暂不启用；工程底线——短缓存、串行、最小间隔——必须生效。）
## 6. 收码管理 API 权限（草案）

账号须**已入库**。权限见 [09](09-decisions.md) / [10](10-security-review-resolution.md)。

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/accounts/{id}/code-api` | **游客** | **create-or-return**：无则创建，有则返回**同一** token/URL（不静默轮换） |
| GET | `/api/accounts/{id}/code-api` | **游客** | 元数据；token 可全量返回供复制，或掩码（实现二选一，需一致） |
| POST | `/api/accounts/{id}/code-api/rotate` | **管理员**，或 body/query 提供正确 **`current_token`** | 作废旧 URL，发新 token |
| DELETE | `/api/accounts/{id}/code-api` | **仅管理员** | 删除收码映射 |

响应示例：

```json
{
  "ok": true,
  "url": "https://mail.example.com/api/v1/code/om_c_xxx",
  "token": "om_c_xxx",
  "created_at": "..."
}
```

前端把 `url`/`token` 写回该账号的 **local 缓存记录**。

**公开号池含义**：任意游客对已入库账号调用 create-or-return，可能拿到与他人相同的收码 URL，从而读取该邮箱验证码。敏感账号请只放浏览器缓存。

## 7. 安全

- token 高熵、不可枚举；日志只打前后缀。  
- 响应**永不**含 password、cookies、refresh_token、client_id。  
- HTTPS 部署。  
- rotate 作废旧 URL；禁止游客无证明随意 rotate。  
- token 在 URL 中：注意访问日志与 Referer；部署侧收敛日志。  
- **工程底线**（非运营限流产品）：短缓存优先、同账号串行、最小上游间隔；见 09 §8 / 10。  

## 8. 与留存的关系

收码 API **仅服务已入库账号**，依赖：

- 加密后的账号凭证  
- Cookie 会话（滚动更新）  
- token ↔ 账号映射  
- 可选短邮件缓存  

未入库的浏览器-only 账号不能签发长期收码 URL。详见 [07](07-privacy-and-retention.md)、[09](09-decisions.md)。

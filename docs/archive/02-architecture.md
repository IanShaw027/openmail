# 02 · 系统架构

## 1. 总体形态

**单实例自托管** Web 服务。游客免登录用操作台；管理员密码管配置。

```text
  浏览器（mail-public 风格操作台）     外部脚本
        │  游客 API / 本地缓存              │
        │  管理员会话（配置）               │ GET|POST 收码 URL
        ▼                                   ▼
┌─────────────────────────────────────────────┐
│                 OpenMail Server             │
│  Web UI  │  /api/*（游客） │ /admin/*     │
│          │  /api/v1/code/{token}           │
│  Fetch: 入库账号 | 客户端凭证代理（不落库） │
│  Provider: OAuth | Cookie | IMAP | HttpApi │
│  Store: 加密凭证 / 滚动 cookies / 短缓存   │
│  Proxy: 全局(服务端) > 账号级(用户)        │
└─────────────────────────────────────────────┘
        │ 无平台托管授权，仅用户自备凭证
        ▼
   Graph / mail.com / IMAP / CF api_url
```

详见 [09-decisions.md](09-decisions.md)。

## 2. 模块边界

| 模块 | 职责 | 不负责 |
|------|------|--------|
| **frontend** | 列表、导入导出、复制、生成 API URL 展示 | 直接连邮箱厂商 |
| **admin API** | 账号 CRUD、触发取信、读缓存邮件 | 对匿名公网暴露凭证字段（需登录） |
| **code API** | 凭 token 取验证码，多格式输出 | 返回完整凭证 |
| **provider router** | 按账号类型分发取信 | 业务 UI |
| **oauth provider** | refresh → access → Graph 读信 | 网页登录 |
| **cookie provider** | 加载 cookies → 请求网页；失败则 password 登录并写回 cookies | IMAP |
| **imap provider** | IMAP 连接、文件夹、取信 | 浏览器 Cookie |
| **http_api provider** | 请求用户配置的邮件 API，规范化消息 | 登录页解析 |
| **parser** | 从主题/正文提取验证码 | 网络 IO |
| **store** | 持久化账号、会话、短缓存、API 映射 | 业务规则之外的逻辑 |

## 3. 取信主流程

```text
Fetch(account_id, folder, quick)
  → load account + credentials + session
  → router.by(account.provider)
  → provider.fetch(...)
       oauth:   use/refresh tokens, Graph list+body
       cookie:  try cookies → if invalid login → save cookies → fetch
       imap:    connect with app password → fetch
       http_api: GET/POST api_url → normalize
  → parser.attach_verification_code(messages)
  → store.save_mail_cache (短 TTL / 条数上限)
  → update account.last_fetch_at / last_error / latest_code
  → return messages summary
```

## 4. 收码 API 主流程

```text
用户点击「API 取件」
  → 若无 token：生成 code_api_token，绑定 account_id
  → 返回绝对 URL: https://{host}/api/v1/code/{token}

外部 GET /api/v1/code/{token}?format=json&keyword=...
  → 校验 token；工程底线（缓存/串行/最小间隔）
  → 解析绑定账号
  → Fetch(account, inbox, quick=true) 或读未过期缓存
  → 按 keyword/regex 筛选
  → 取最新验证码
  → 按 format 序列化响应
```

**要点**：URL 是能力入口；真正取信仍用服务端已存凭证（含 cookies）。因此凭证**必须**服务端留存。

## 5. 部署视图

- 单进程或「API + Worker」均可；MVP 可单体。  
- 数据：SQLite（单机）或 PostgreSQL（多实例）。  
- 密钥：`APP_SECRET`（管理会话）+ `OPENMAIL_MASTER_KEY`（凭证 **必须** at-rest 加密）。  
- 反向代理：HTTPS 终止；管理端与收码 API 可同域不同路径。  

## 6. 与参考实现的映射

| 能力 | mail-public | mail.com.helper | OpenMail |
|------|-------------|-----------------|----------|
| 操作台 | Web | Tk GUI | Web |
| 取信位置 | 服务端代理 | 本机 | **服务端** |
| 微软 OAuth | 有 | 无 | 有 |
| Cookie 会话 | 无 | 有（本机文件） | 有（服务端 DB/文件） |
| IMAP | 无 | 无 | 有 |
| CF API | 服务端代理 | 无 | 有 |
| 收码 API | 弱/无专用签发 | 本机 8913 | **专用 token URL** |

## 7. 技术选型（建议，实现阶段可调整）

| 层 | 建议 | 理由 |
|----|------|------|
| 后端 | Python 3.11+（FastAPI）或 Node | helper 经验可迁 Python；FastAPI 适合 API |
| Cookie HTTP | curl_cffi / httpx | TLS 指纹、会话 |
| OAuth/Graph | httpx + 标准 OAuth refresh | 成熟 |
| IMAP | aioimaplib / imaplib | 服务端标准做法 |
| 前端 | 轻量 SPA 或服务端模板 + 少量 JS | 对齐 mail-public 操作台即可 |
| DB | SQLite 先，Postgres 后 | MVP 简单 |

选型最终以 `08-mvp-roadmap` 实现计划锁定。

## 8. 安全边界

- 管理 API：管理密码会话。  
- 游客 API：可读入库账号元数据、取信、create-or-return 收码 URL；**不可**随意 rotate/delete/删号。  
- 收码取码 URL：仅 token；响应**禁止**含凭证。  
- 日志：脱敏。  
- 出站：HttpApi **Phase 1 必须** SSRF（URL 校验、解析后禁私网、redirect 复检；可配白名单）。  
- 工程底线：短缓存、同账号串行、最小上游间隔（见 09/10）。  

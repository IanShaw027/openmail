# 08 · MVP 与路线图

## 1. MVP 目标

**功能全包含**（不砍半套）+ **各类型实号打通**：

1. 游客可用 Web 操作台（对齐 mail-public）  
2. 双存储：浏览器缓存 + 可选入库（加密）  
3. 导入/导出兼容 mail-public  
4. Provider：oauth（用户自备 token）/ cookie·mail.com（滚动续期）/ http_api / imap  
5. 取信、验证码、批量、快捷复制  
6. 收码 API：一记录一 token，GET+POST，仅 URL；生成后写浏览器缓存  
7. 管理密码 + 基础会话；全局代理（服务端）；账号代理（用户）  
8. 运营限流暂缓；**工程底线**必做  
9. 隐私文案（游客/注册用户/管理员 + 公开号池与用户私有池 + 无平台授权）  
10. 加密、SSRF、收码权限分层（见 10）  
11. **用户注册登录 + 私有池 + hourly SyncWorker + sid 动态代理 + 我的邮件搜索**（见 12）  

## 2. 阶段划分

> **Phase 0–3 均属 MVP 交付范围**（功能全包含 + 各类型实号打通）。  
> 编号只表示**推荐实现顺序**，不是「Phase 3 = 非 MVP」。  
> **MVP 完成 = Phase 0–3 全部勾选**（含 IMAP 验收）。  
> Phase 4 起才是 MVP 之后的增强。  
> 勾选状态反映**代码/测试已落地**；带「实号」字样的验收仍以人工/实环境为准。

### Phase 0 — 文档与骨架

- [x] 产品/架构/模型/API/隐私文档  
- [x] 选定后端/前端技术栈并初始化仓库代码（FastAPI + Vue 3）  
- [x] Docker 或一键启动（`Dockerfile` + `docker-compose.yml` + `Makefile` + smoke）  

### Phase 1 — 核心后端（含安全底线）· MVP

- [x] 管理鉴权（管理密码会话）  
- [x] Account CRUD + Store（SQLite）  
- [x] **凭证 at-rest 加密**（`OPENMAIL_MASTER_KEY`）  
- [x] Provider 接口 + Router  
- [x] OAuth（微软 Graph）取信  
- [x] HttpApi 取信 + **SSRF 防护（同交付）**  
- [x] Cookie Provider（mail.com；滚动续期，无默认 6h 硬删）  
- [x] 验证码解析  
- [x] 收码 API（权限：create-or-return 游客；rotate 管理员/current_token；delete 管理员）  
- [x] 工程底线：短缓存、同账号串行、最小 fetch 间隔、批量并发上限  
- [x] 邮件短缓存  

### Phase 2 — Web 操作台 · MVP

- [x] 导入/导出 UI（对齐 mail-public）  
- [x] 列表、筛选、分页；**入库=实例公开** 提示  
- [x] 点行取信、批量拉取  
- [x] 复制邮箱/验证码/API URL/导入行  
- [x] 编辑账号；rotate API（按权限）  
- [x] 收件箱/垃圾箱（Provider 支持时）  

### Phase 3 — IMAP · 用户体系 · 我的邮件 · MVP 收尾

- [x] **IMAP Provider + 域名 host 表**（单元测试覆盖；实号验收见清单 §3）  
- [x] **用户注册 / 登录 / 登出 / 会话**（开放注册 + 隐私/条款勾选）  
- [x] 用户私有池 `owner_user_id`；对自己数据完整 CRUD  
- [x] **SyncWorker** 默认 1h + 用户「同步我的」  
- [x] **动态代理模板 `{sid}`** + sticky/轮换策略  
- [x] **MailIndex 按用户隔离**；「我的邮件」UI；from/to/subject/body/时间搜索  
- [x] **游客禁止**邮件搜索与用户邮件库（API 403）  
- [x] 用户 A 不可见用户 B 数据（池/owner 隔离 + 测试）  
- [ ] 端到端验收清单（§3 + §3b）全部通过（含实号场景）  
- [x] 基础测试与示例脚本（`pytest`；`scripts/smoke_api.sh` / `make smoke`）  

### Phase 4 — MVP 之后增强（非 MVP）

- [ ] 更多 cookie 站点适配器  
- [ ] 多租户  
- [ ] 发信/回复  
- [ ] 高级导出含 session  
- [ ] 可观测性面板  
- [ ] 运营向限流/配额产品化  
- [x] 生产 Docker Compose（单容器 SPA+API；HTTPS/反代样例可继续打磨）  

## 3. MVP 验收清单

| # | 验收项 | 状态 |
|---|--------|------|
| 1 | 导入微软 `email----pass----refresh----client_id` 可取信并显示验证码 | 代码就绪；**实号**待验 |
| 2 | 导入 mail.com `email----password` 首次登录后 cookies 落库，二次取信不重复登录（mock 或实号） | mock/单测就绪；**实号**待验 |
| 3 | 导入 CF `email----https://...` 可规范化邮件 | 代码就绪；**实号**待验 |
| 4 | IMAP 至少一个域名（如 QQ 授权码）可取信 | Provider+单测就绪；**实号**待验 |
| 5 | 点击 API 取件得到 URL，`curl` 得验证码 | 代码就绪 |
| 6 | `format=text|json|nullx` 均可用 | 代码就绪 |
| 7 | 重置 API 后旧 URL 失效 | 代码就绪 |
| 8 | 删除账号后凭证与 token 不可再用 | 代码就绪 |
| 9 | 页面可见隐私摘要 | 就绪（legal + 前端页） |
| 10 | 导出文件可再导入 | 前端导入解析就绪 |
| 11 | 用户注册登录；导入 ≥2 私有账号；hourly/手动同步后「我的邮件」有数据 | **注册/同步/UI 就绪**；实号数据待验 |
| 12 | 登录用户可按发件人/收件人/主题/正文/时间搜索 **自己的** 邮件 | **就绪**（API + UI） |
| 13 | 动态 sid 代理模板生效（日志可见掩码 sid） | **就绪**（解析+设置+单测） |
| 14 | **游客无法**打开我的邮件 / 搜邮 API 返回 403 | **就绪**（单测 + smoke） |
| 15 | 用户 A 不可见用户 B 的账号与邮件 | **就绪**（权限模型） |

### 3b. 用户体系与同步验收（摘要）

详见 [12](12-trusted-pool-and-mail-sync.md) §11。  
实现要点：开放注册、`user_private` 默认同步开关、`POST /api/me/sync`、管理端全局同步与代理设置、「我的邮件」搜索。

运维与冒烟步骤见 [14-ops-and-smoke.md](14-ops-and-smoke.md)。

## 4. 建议实现顺序（开发）

```text
Phase1: Store+加密+Admin → Parser → OAuth → HttpApi+SSRF → Cookie → Code API → 工程底线
Phase2: Web 操作台 + 公开号池 + 批量/筛选/导出
Phase3: IMAP → 用户注册登录 → 私有池 → SyncWorker+sid → 我的邮件搜索 → 全量验收
（Phase0–3 全部完成 = MVP）
```

当前代码库已覆盖 Phase 1–3 主体能力；剩余主要为 **Compose 草案**、**各 Provider 实号 E2E** 与清单勾完。

## 5. 风险

| 风险 | 缓解 |
|------|------|
| mail.com 页面变更 | 适配器隔离；版本化解析 |
| OAuth 令牌失效 | 明确错误；支持更新导入 |
| IMAP 各厂商差异 | host 表 + 单测 |
| 凭证泄露 | 加密、鉴权、脱敏日志、API 重置 |
| 收码 API 被刷 | 工程底线（缓存/串行/间隔）；可关 token；运营限流后续 |

## 6. 下一步

1. ~~锁定技术栈~~（FastAPI + SQLite + Vue 3）  
2. ~~初始化 `backend/`、`frontend/`~~  
3. 可选：生产 Compose、mail.com/Graph/IMAP 实号打磨  
4. 运维：按 [14-ops-and-smoke.md](14-ops-and-smoke.md) 跑 `make smoke` / `make test`  

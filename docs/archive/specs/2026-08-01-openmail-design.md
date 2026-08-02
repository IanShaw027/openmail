# OpenMail 设计总览

- **日期**：2026-08-01  
- **状态**：**已按产品共识修订**（详见 [09-decisions.md](../09-decisions.md)）  
- **位置**：`openmail/docs/specs/2026-08-01-openmail-design.md`  

## 1. 背景

需要类似 mail-public 的**自托管邮箱取件台**，覆盖：

- 微软等 OAuth（用户自备 `client_id` + `refresh_token`，**无平台托管授权**）  
- Cookie 网页邮（mail.com 等，参考 mail.com.helper，并加强滚动续期）  
- IMAP（用户自备授权码/App Password）  
- CF / 自建 HttpApi（用户自备 `api_url`）  

部署：**单实例服务器 Web**；**游客可直接用**；**一个管理员**（管理密码 + 基础会话）。

## 2. 关键决策（以 09 为准）

| 决策 | 选择 |
|------|------|
| 部署 | 单实例自托管 |
| 用户 | 游客 + **注册用户（完整注册登录）** + 单管理员 |
| 凭证来源 | **全部用户端提供**；禁止平台 OAuth/Cookie 托管授权 |
| 存储 | **双轨**：入库则服务端加密存；未入库则浏览器缓存 + 服务端代理取信不落库 |
| 加密 | 服务端凭证 **at-rest 加密** |
| Cookie | password + cookies；**每次成功请求滚动写回**；不以 6h 硬杀可用会话；失效才自动密码重登 |
| 收码 API | 一记录一 token；生成后写回浏览器缓存；**仅 URL**；**GET+POST**；运营限流暂不做，**工程底线必须** |
| 代理 | 全局=服务端配置；账号级=用户配置 |
| 前端 | **对齐 mail-public 操作台** |
| 技术栈 | **不定死**，选合适实现 |
| MVP | **功能全包含** + 实号打通 + **用户体系/同步/我的邮件** |
| 导入 | **兼容 mail-public** |
| 用户池 | 注册登录 → 私有账号 `owner_user_id` → **默认 1h 自动拉取** |
| 动态代理 | 管理员配置 **带 `{sid}` 模板**；同步抗限流 |
| 我的邮件 | 每用户 MailIndex；搜 from/to/subject/body/时间；**游客禁止** |

## 3. 双存储与取信

```text
账号在浏览器缓存 only
  → 取信：POST 凭证到服务端 public_fetch 类接口 → 不入库
  → 收码 API：需先「保存到服务器」再生成 token

账号已保存到服务器
  → 凭证加密入库（含 cookies 滚动更新）
  → 取信读库
  → 可生成一对一收码 URL；浏览器缓存同步 URL
```

## 4. Cookie：mail.com.helper 对照

| helper | OpenMail |
|--------|----------|
| 6h 硬过期删缓存 | **不以固定 6h 丢弃仍可用 cookies**；以探测为准 |
| 恢复成功 / 每次 request 后 save | **同样滚动写回**（自动续期） |
| 失效 → 密码全量登录 | **静默自动重登**（有密码时），尽量不打扰 |
| 无密码无法重登 | 同：标记需补凭证 |

详见 [09-decisions.md §6](../09-decisions.md)。

## 5. 收码 API

```text
用户点击生成 API
  → 服务端为该「已入库记录」生成唯一 token
  → 返回 URL，并更新该账号在浏览器缓存中的 api 字段
  → 外部 GET 或 POST 该 URL 取验证码（token 在 URL 中，无需 Header）
```

详见 [05-code-api.md](../05-code-api.md)、[09-decisions.md §5](../09-decisions.md)。

## 6. 架构摘要

- 前端：mail-public 风格操作台；游客可用  
- 管理：`/admin` 类接口管理密码会话；全局代理等  
- 游客取信：入库账号 ID 取信 **或** 携带客户端凭证的代理取信  
- Provider：oauth / cookie / imap / http_api  
- 无平台代授权流程  

## 7. MVP

- 文档功能全做，不砍成「半套」  
- 实号打通：微软 OAuth 导入、mail.com Cookie、HttpApi、IMAP  
- CF 细解析可用后续你提供的凭证再对齐  
- 导入兼容 mail-public  

## 8. 文档地图

1. [01-product-requirements.md](../01-product-requirements.md)  
2. [02-architecture.md](../02-architecture.md)  
3. [03-data-model.md](../03-data-model.md)  
4. [04-providers.md](../04-providers.md)  
5. [05-code-api.md](../05-code-api.md)  
6. [06-import-export.md](../06-import-export.md)  
7. [07-privacy-and-retention.md](../07-privacy-and-retention.md)  
8. [08-mvp-roadmap.md](../08-mvp-roadmap.md)  
9. [09-decisions.md](../09-decisions.md) **← 冲突时以 09/10/12 为准**  
10. [11-ui-wireframes-and-diagrams.md](../11-ui-wireframes-and-diagrams.md)  
11. [12-trusted-pool-and-mail-sync.md](../12-trusted-pool-and-mail-sync.md)  

## 9. 自检

| 检查项 | 结果 |
|--------|------|
| 游客可用 | 是 |
| 平台托管 OAuth | 否 |
| 双存储 | 是 |
| 收码一对一 + URL only + GET/POST | 是 |
| Cookie 自动续期（非 6h 硬杀） | 是 |
| 限流 | 运营配额暂不做；工程底线必须（缓存/串行/最小间隔） |
| 凭证加密 | 是 |
| 前端参考 mail-public | 是 |

## 10. 安全审查

六条审查意见均已分析并落库，见 [10-security-review-resolution.md](../10-security-review-resolution.md)。  
要点：收码权限分层、工程底线≠运营限流、HttpApi+SSRF 同 Phase1、Cookie 无默认 6h 硬删、加密 Phase1、入库=实例公开文案。

## 11. 下一步

进入 **implementation plan** 与代码骨架。  
仍可微调：具体框架选型、CF JSON profile 样例。

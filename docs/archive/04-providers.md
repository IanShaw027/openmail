# 04 · Provider 规范

## 1. 统一接口

每个 Provider 实现同一契约（语言无关伪代码）：

```text
class Provider:
  name: str

  def can_handle(account) -> bool
  def fetch(account, *, folder, quick, limits) -> FetchResult
  def health(account) -> HealthResult   # 可选
```

### FetchResult

```json
{
  "ok": true,
  "messages": [ /* Message */ ],
  "message_count": 0,
  "folder": "inbox",
  "fetched_at": "ISO-8601",
  "credential_updates": {
    "refresh_token": "optional-new",
    "access_token": "optional",
    "session_cookies": "optional-list",
    "session_meta": {}
  },
  "session_restored": false,
  "error": null
}
```

失败时 `ok=false`，`error` 为**可展示给用户的短句**（无密钥）。

## 2. 路由规则（导入与取信）

按**字段存在性**判断，不强制用户选手动类型：

```text
if client_id and refresh_token:
    provider = oauth
elif api_url:
    provider = http_api
elif imap_host or explicit_imap_flag or domain_imap_hint:
    provider = imap
elif password and cookie_site_hint(email or site):
    provider = cookie
else:
    provider = unknown  # 导入为不可取信，提示补全
```

域名提示示例：

- `outlook.com` / `hotmail.com` / `live.com` → 优先引导 oauth（若无 token 则标 need_oauth）  
- `mail.com` 及常见 lightmailer 域 → cookie  
- `qq.com` / `163.com` / `126.com` / `gmail.com` → imap 模板  

## 3. oauth（微软 Graph 优先）

### 输入

- `email`, `client_id`, `refresh_token`  
- `password` 可选（仅存储展示/导出兼容）  

### 行为

1. 用 refresh_token 换 access_token（及可能的新 refresh_token）。  
2. 调用 Microsoft Graph 列邮件（inbox / junkemail）。  
3. quick：最近 N 封 + 必要正文；full：更大窗口。  
4. 若返回新 refresh_token → **写回 Credential**。  

### 典型错误映射

| 上游 | 用户可见 |
|------|----------|
| AADSTS70000 等 | 刷新令牌无效或已过期 |
| 401/403 | 权限不足或令牌失效 |
| 超时 | 取件超时，请重试 |

## 4. cookie（mail.com 等）

### 输入

- `email`, `password`（重登需要；用户导入）  
- `session.cookies`（可空，首次登录后维护）  
- `proxy` 可选（**账号级，用户配置**）  
- `site` 默认 `mail.com`  

### 行为（必须）— 对齐 helper 并加强自动续期

参考 mail.com.helper（`SessionCache` / `_try_restore_session` / `_request`）：

1. 加载已存 cookies → 打开文件夹页，命中 `FolderListPage` 等则视为有效。  
2. **每次成功 restore / 每次成功业务请求后写回全量 cookies**（滚动续期）。  
3. 请求中途像掉登录 → 清旧会话 → **有 password 则自动全量登录** → 写新 cookies（对用户静默）。  
4. helper 另有 **6 小时硬过期删缓存**；OpenMail **不以固定 6h 丢弃仍可用的 cookies**，以探测是否登录为准（见 09）。  

```text
优先 cookies → 成功则滚动写回
  → 失败则自动 password 登录 → 写新 cookies → 再取信
  → 无 password 且失败 → need_reauth
```

### 实现参考

- mail.com.helper：`SessionCache`、`_full_login`、`_request` 内 `save`  
- TLS：优先 Chrome impersonate（curl_cffi）  
- 同账号会话写加锁  

### 扩展其他站点

新增 `site` 适配器；同一 Session 滚动写回模型。

## 5. imap

### 输入

- `email`, `password`（授权码/App Password）  
- `host`, `port`, `ssl`  
- 文件夹映射  

### 行为

1. 服务端建立 IMAP 连接（可账号级超时）。  
2. 选择文件夹，取最近 N 封 UID。  
3. 解析 MIME → text/html → Message。  
4. 不维护 cookies；可选连接池但不作为安全边界。  

### 说明

- IMAP **适合放在服务器**处理（浏览器无法直连）。  
- 隐私：密码存服务端；对外说明见隐私文档。  
- Gmail/iCloud 等必须使用应用专用密码，不是网页登录密码。  

### 域名默认 host 表（可配置）

| 域 | host | port |
|----|------|------|
| qq.com | imap.qq.com | 993 |
| 163.com | imap.163.com | 993 |
| 126.com | imap.126.com | 993 |
| gmail.com | imap.gmail.com | 993 |
| icloud.com / me.com | imap.mail.me.com | 993 |

## 6. http_api（CF 邮箱 / 自建）

### 输入

- `email`  
- `api_url`  
- 可选 headers / profile  

### 行为

1. 服务端请求 `api_url`（注意 SSRF 策略）。  
2. 按 profile 解析 JSON/HTML → Message 列表。  
3. 无账号密码体系；`api_url` 即权限。  

### 约定 profile（MVP）

- `generic_json`：消息数组在 `messages` / `data` / 根数组  
- `cf_mail`：按常见 CF Worker 邮箱项目字段映射（实现时对照实际项目）  

## 7. 验证码解析（全 Provider 共用）

优先级建议：

1. 主题中的连续 4–8 位数字（可配）  
2. 正文中带「验证码/code/OTP」邻近数字  
3. 用户收码 API 传入的 `regex`  
4. 无则 `verification_code` 为空，仍返回邮件列表  

## 8. 并发与工程底线

（运营配额限流暂不做；以下为 **MVP 必须**。）

- 批量取信：全局与每 Provider 并发上限（如 3–5）。  
- 同账号取信 / Cookie 登录：**互斥锁**串行。  
- 同账号 real fetch：**最小间隔**（默认数秒）。  
- 收码 API：默认走短缓存，避免每次 URL 访问都打上游。  
- HttpApi：**SSRF 防护与 Provider 同发布**（禁私网、redirect 复检）。  

## 9. 测试要点

| Provider | 单测/集成关注点 |
|----------|-----------------|
| oauth | refresh 轮换写回；过期错误文案 |
| cookie | 有 cookies 不登录；失效后重登并写 cookies |
| imap | SSL、垃圾箱、中文 MIME |
| http_api | 畸形 JSON、SSRF 拒绝内网 |

# 03 · 数据模型

## 1. 设计原则

1. **按 Provider 分凭证形态**，统一账号外壳。  
2. **Cookie 类必须持久化 cookies**，并保留 password 用于重登。  
3. **OAuth 类持久化 refresh_token**，轮换后写回。  
4. **邮件缓存短生命周期**；账号与 API 映射长生命周期（直到用户删除）。  
5. 敏感字段 **必须** at-rest 加密（见 09）；导出默认不含 cookies 明文会话（可高级导出）。  

## 2. 实体关系

```text
User/Admin (MVP 可单租户)
    │
    ├── Account 1..*
    │      ├── Credential (按 provider 扩展字段)
    │      ├── Session (cookie 类专用，1:0..1)
    │      ├── MailCache (短缓存，按 folder)
    │      └── CodeApiToken 0..1（一号一链，可重置）
    └── (可选) AuditLog
```

MVP 默认**单租户**（一个管理密码保护整个实例）。多用户可后续加 `owner_id`。

## 3. Account

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| email | string | 规范化小写 |
| provider | enum | `oauth` \| `cookie` \| `imap` \| `http_api` |
| password | string? | 展示/导出/ cookie 重登；OAuth 常仅展示 |
| tag | string? | 标签 |
| note | string? | 备注 |
| status | enum | `ok` \| `error` \| `need_reauth` \| `disabled` |
| last_fetch_at | datetime? | |
| last_error | string? | 短错误，不含密钥 |
| latest_verification_code | string? | 冗余加速列表 |
| latest_code_at | datetime? | |
| created_at / updated_at | datetime | |

唯一性建议：`(email, provider, credential_fingerprint)`，避免同邮箱多种接入方式冲突时误合并。

## 4. Credential（逻辑字段，可 JSON 列）

### 4.1 oauth

| 字段 | 说明 |
|------|------|
| client_id | 微软应用 ID 等 |
| refresh_token | 刷新令牌 |
| token_expires_at | 可选 |
| access_token | 可选短缓存，可只放内存 |
| token_type | 如 `graph` |
| scopes | 可选 |

### 4.2 cookie

| 字段 | 说明 |
|------|------|
| password | **必填**（重登） |
| site | 如 `mail.com` |
| proxy | 可选 HTTP/SOCKS |

会话见 **Session**，不塞进 Credential 亦可，但必须关联存储。

### 4.3 imap

| 字段 | 说明 |
|------|------|
| password | 授权码 / App Password |
| host | 如 `imap.qq.com` |
| port | 默认 993 |
| ssl | 默认 true |
| folder_inbox | 默认 `INBOX` |
| folder_junk | 可选 |

### 4.4 http_api

| 字段 | 说明 |
|------|------|
| api_url | 取信 URL |
| method | 默认 GET |
| headers | 可选 JSON |
| response_profile | 可选解析模板 id |

## 5. Session（Cookie 类专用）

> 对应 mail.com.helper 的 `cache/sessions/*.json`，但落在服务端。

| 字段 | 类型 | 说明 |
|------|------|------|
| account_id | string | FK |
| cookies | json array | 标准 cookie 字典列表（name/value/domain/path/expires…） |
| meta | json | 如 folder_url、user-agent 提示 |
| saved_at | datetime | 上次成功写回 |
| absolute_expires_at | datetime? | **可选**绝对上限（默认不启用 6h 硬删；可配如 7d） |
| last_validated_at | datetime? | 上次探测成功 |
| valid | bool | 上次探测是否有效（提示用，不单独作为硬拦截） |

### 行为（与 09 一致，禁止默认 6h 硬过期）

1. 取信前：有 cookies 则 **注入并探测**（不因「满 6 小时」直接丢弃）。  
2. 探测仍登录 → 使用会话，并 **滚动写回** cookies / `saved_at`。  
3. 探测失败或请求中掉登录 → `valid=false`；有 password 则 **自动重登** 并整表写回。  
4. 仅当配置了 `absolute_expires_at` 且已超过时，才强制重登（可选）。  
5. 用户删号 /「清除会话」→ 删除 Session 行。  

**禁止**只存 email+password 而不存 cookies 作为唯一策略。  
**禁止**实现成 mail.com.helper 默认「6 小时删文件」而不探测。

## 6. MailCache

| 字段 | 说明 |
|------|------|
| account_id | |
| folder | `inbox` / `junk` / … |
| messages | JSON 数组（见 Message） |
| fetched_at | |
| expire_at | 短 TTL |

### Message（规范化）

```json
{
  "id": "provider-native-or-hash",
  "subject": "",
  "from": "",
  "from_address": "",
  "to": "",
  "date": "ISO-8601",
  "body_preview": "",
  "body_text": "",
  "body_html": "",
  "folder": "inbox",
  "verification_code": "123456",
  "raw_refs": {}
}
```

限制建议：每账号每文件夹最多 N 封（如 50）；HTML 最大字符数截断；TTL 默认 15–60 分钟（可配）。

## 7. CodeApiToken

| 字段 | 说明 |
|------|------|
| token | 高熵随机串（URL 路径用） |
| account_id | 绑定账号（**一号一链**） |
| created_at | |
| rotated_at | 重置时更新 |
| last_used_at | |
| enabled | bool |
| default_format | `text` \| `json` \| … |
| default_keyword | 可选 |
| default_regex | 可选 |

重置：生成新 token，旧 token 删除或标记 disabled。

## 8. 列表/统计冗余（可选）

为对齐 mail-public 统计卡片，可计算：

- total  
- fetchable（凭证齐全）  
- cached（MailCache 未过期）  
- errors（status=error 或 last_error 非空）  

## 9. 删除级联

删除 Account 时必须删除：

- Credential  
- Session（cookies）  
- MailCache  
- CodeApiToken  

## 10. 示例：cookie 账号存储形态

```json
{
  "id": "acc_01",
  "email": "user@mail.com",
  "provider": "cookie",
  "password": "secret",
  "tag": "池A",
  "credential": {
    "site": "mail.com",
    "proxy": ""
  },
  "session": {
    "cookies": [
      {"name": "Session", "value": "...", "domain": ".mail.com", "path": "/"}
    ],
    "saved_at": "2026-08-01T12:00:00Z",
    "valid": true
  },
  "code_api": {
    "token": "om_c_...",
    "url": "https://mail.example.com/api/v1/code/om_c_..."
  }
}
```

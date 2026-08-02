# 06 · 导入导出格式

## 1. 目标

- 兼容 mail-public 常见 `----` 行  
- 兼容 mail.com.helper 的「邮箱 密码 / 名称 邮箱 密码 代理」  
- **尽量自动识别 Provider**，减少手工选择  
- 导出可再导入（往返）  

## 2. 通用规则

- 一行一个账号；`#` 开头为注释  
- 分隔符优先级：`----` > Tab > `|` > 多空格/逗号  
- 自动提取行内第一个邮箱  
- 自动提取行内 `http(s)://` 作为 `api_url`（若存在）  
- 空行忽略  

## 3. 格式规格

### 3.1 微软 OAuth（oauth）

```text
邮箱----密码----refresh_token----client_id
邮箱----密码----refresh_token----client_id----标签
```

兼容：`client_id` 与 `refresh_token` 对调（用形态识别：UUID vs 长 token / `M.` 前缀）。

### 3.2 HTTP API（http_api）

```text
邮箱----https://worker.example.com/api/mail?box=xxx
邮箱----密码----https://...    # 密码可忽略或作备注
邮箱 https://...
```

### 3.3 Cookie 网页邮（cookie）

```text
邮箱----密码
名称----邮箱----密码
名称----邮箱----密码----代理
邮箱 密码
名称 邮箱 密码 代理
```

代理示例：`http://127.0.0.1:7890`、`socks5://user:pass@host:1080`。

可选显式标记（避免误判）：

```text
cookie----邮箱----密码
cookie----邮箱----密码----proxy----mail.com
```

### 3.4 IMAP

```text
imap----邮箱----授权码
imap----邮箱----授权码----imap.qq.com----993
邮箱----授权码----imap.gmail.com:993
```

无 host 时按邮箱域名查默认表（见 Providers 文档）。

### 3.5 显式 provider 前缀（推荐给工具导出）

```text
oauth----email----password----refresh_token----client_id
cookie----email----password----proxy
imap----email----password----host----port
http_api----email----api_url
```

## 4. 识别算法（摘要）

```text
parts = split(line)
if parts[0] in {oauth,cookie,imap,http_api}:
    parse_explicit(parts)
elif has_url(line):
    http_api
elif looks_like_refresh_token and looks_like_client_id:
    oauth
elif has_imap_host(parts) or parts[0]=='imap':
    imap
elif has_password_only:
    cookie or imap by domain hint
else:
    invalid
```

## 5. 导出

### 5.1 导入兼容格式（默认）

按账号当前 provider 输出 3.x 对应行，便于迁移备份。

- oauth：`email----password----refresh_token----client_id`  
- http_api：`email----api_url`  
- cookie：`email----password`（**默认不含 cookies**，避免 TXT 泄露会话）  
- imap：`imap----email----password----host----port`  

### 5.2 高级导出（可选，危险）

- `export=full`：含 cookies JSON 或 session 旁路文件  
- 仅管理端二次确认后允许  
- 文件名带 `SENSITIVE` 提示  

### 5.3 选中导出

仅导出勾选账号；无勾选时导出筛选结果或全部（与 UI 文案一致）。

## 6. 导入结果统计

返回并 toast：

| 字段 | 含义 |
|------|------|
| total_lines | 非空行 |
| imported | 新建 |
| updated | 同键更新凭证 |
| duplicates | 完全相同跳过 |
| invalid | 无法解析 |
| missing_credential | 解析了邮箱但不可取信 |

## 7. 安全注意

- 导入文件仅管理员可用。  
- 日志不打印整行（可能含 token）。  
- 导出下载应即时、不写世界可读临时文件。  

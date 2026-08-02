# 概念速览（非安装说明）

实现完成前，用本文理解上线后的使用方式。

## 管理员

1. 部署 OpenMail 到服务器，设置管理密码与 `APP_SECRET`  
2. 浏览器打开站点并登录  
3. 粘贴账号导入  
4. 点行取信，复制验证码  
5. 点「API 取件」，复制 URL 给脚本  

## 脚本

```bash
# JSON
curl -sS "https://your-host/api/v1/code/om_c_xxx?format=json"

# 纯文本验证码
curl -sS "https://your-host/api/v1/code/om_c_xxx?format=text"

# 兼容 NullX
curl -sS "https://your-host/api/v1/code/om_c_xxx?format=nullx"
```

## 导入一行示例

```text
# 微软
user@outlook.com----pass----M.xxx_refresh----00000000-0000-0000-0000-000000000000

# mail.com
user@mail.com----password123

# CF
user@your.domain----https://mail-api.example.workers.dev/inbox?to=user@your.domain

# IMAP
imap----user@qq.com----auth_code----imap.qq.com----993
```

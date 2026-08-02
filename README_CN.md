<div align="center">

<img src="assets/logo-icon.svg" alt="OpenMail" width="96" />

# OpenMail

**本地优先 · 多源邮箱控制台**

[![CI](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml/badge.svg)](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/IanShaw027/openmail?include_prereleases)](https://github.com/IanShaw027/openmail/releases)

[English](README.md) · 中文

浏览器内加密金库保管凭证，自托管 FastAPI 代理取件/发信。可选云端行为 **客户端密封**，运营方无法在无金库密钥时读取明文。

<br />

### 🚀 在线体验

**[https://mail.clomio.ai](https://mail.clomio.ai)** — 公共测试实例

> 首次访问请自建金库密码。密钥只在**你的浏览器**中；演示站仅代理取件。请勿在公共演示环境使用重要生产邮箱。

</div>

---

## ⚠️ 使用前须知

- **自托管工具**，非多租户 SaaS；密钥与合规由部署方负责。  
- **金库密码与恢复密钥不出浏览器**；两者都丢失则密文不可恢复。  
- 代理取件时服务端进程会**短暂**使用凭证访问上游邮箱 API，请勿部署在不可信主机。  
- 仅使用你有权访问的邮箱。  

详见 [SECURITY.md](SECURITY.md) 与 [docs/legal/](docs/legal/)。

---

## 为什么选 OpenMail？

| 需求 | OpenMail |
|------|----------|
| 大量临时/工作邮箱 | 统一控制台、批量取件 |
| 不信任服务端存明文 | **浏览器金库**（PBKDF2 + AES-GCM） |
| Graph / IMAP / mail.com / Worker | 可插拔 Provider |
| 自己的 VPS | 单镜像 Docker（SPA + API） |

## 功能概览

- **协议**：Microsoft Graph OAuth、IMAP（+ SMTP 发信）、mail.com Cookie、HttpApi / CF Worker  
- **金库**：恢复密钥、自动锁定清空内存  
- **2FA**：TOTP/HOTP、扫码/粘贴/批量 URI、绑定邮箱  
- **控制台**：导入、批量取件、分组、备注、本地邮件缓存  
- **安全**：设备 HMAC、SSRF、HTML 消毒、CSP  
- **运维**：可选 10 节点 WARP SOCKS 池  

## 不在范围内

- 用户注册 / 多租户管理后台  
- 完整网页邮箱产品  
- 平台代发起微软 OAuth 授权  

---

## 快速开始（Docker）

```bash
git clone https://github.com/IanShaw027/openmail.git
cd openmail
cp .env.example .env
./scripts/gen-master-key.sh    # 写入 OPENMAIL_MASTER_KEY

docker compose up -d --build
# 本机: http://127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/health
```

也可：`./scripts/install.sh`

先体验演示站：**[mail.clomio.ai](https://mail.clomio.ai)**

带 WARP 池（需 `/dev/net/tun`）：

```bash
./scripts/up-with-warp.sh
```

## 开发

```bash
# 后端
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev
```

## 文档

- [架构](docs/architecture.md) · [运维](docs/14-ops-and-smoke.md) · [WARP](docs/16-warp-proxy-pool.md)  
- [品牌资源](assets/)：`logo-icon.svg` / `logo.svg` / `logo-dark.svg` / `social-banner.svg`  

## 许可

[MIT](LICENSE) · 贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)

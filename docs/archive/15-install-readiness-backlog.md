# 15 · 安装可用度与开发待办清理

> 视角：**后面要能直接安装使用**，不是无限功能清单。  
> 更新：2026-08-02 · **产品形态：本地优先（local-first），无用户/管理员登录**

---

## 1. 一句话结论

| 维度 | 状态 |
|------|------|
| **代码能否安装跑起来** | ✅ 可以（Docker 或本地 `make`） |
| **API / 代理取信 / UI 主路径** | ✅ 已实现（local-first） |
| **四类邮箱「实号必成」** | ⚠️ 代码在，**未替你完成真实账号验收** |
| **生产级运维（HTTPS/备份/升级）** | ⚠️ 够用自托管；文档有，模板可再打磨 |

**可以开始安装使用**；无注册/登录/号池概念，凭证默认在浏览器。

---

## 2. 已经具备（不必再当开发待办）

### 安装与运行
- [x] Docker 单容器 + `docker-compose.yml` + `.env.example`
- [x] 同域托管 SPA + API（`:8000`）
- [x] `Makefile`：`dev-backend` / `dev-frontend` / `test` / `smoke` / `docker-up`
- [x] `scripts/gen-master-key.sh`、`scripts/smoke_api.sh`
- [x] 运维说明 `docs/14-ops-and-smoke.md`
- [x] SQLite 轻量列迁移（旧库缺列可自动补）

### 产品主功能（local-first）
- [x] 控制台：本机导入、代理取信（`/api/fetch/proxy`）、验证码复制
- [x] 本机邮件搜索（`/mails` + browser `mailCache`）
- [x] 设置：取件策略、保留天数、设备 license 配额
- [x] Provider：微软 Graph OAuth、HttpApi+SSRF、IMAP、mail.com Cookie
- [x] 导出/导入系统快照与凭证 TXT
- [x] 中英 i18n
- [x] 无用户/管理员登录（遗留 `/api/me/*`、账号入库、收码 create 返回 **410**；`/api/health`、`/api/fetch/*`、`/api/config/public` 保留）
- [x] 后端 pytest 通过；前端 build 通过；smoke 脚本可用

### 文案 / 文档对齐（已完成）
- [x] P0-2 清理「Auth stub / Admin placeholder」等误导文案（路由已重定向；无登录页）
- [x] P0-3 前端 README 与真实路由对齐（`/` · `/mails` · `/settings`）
- [x] 控制台去掉「保存到服务器 / 生成收码 API」死按钮

---

## 3. 待办分级（按「安装使用」优先）

### P0 — 安装/使用前建议处理

| ID | 项 | 状态 | 说明 |
|----|----|------|------|
| P0-1 | **首次部署引导清单** | ⚠️ 文档 | README「本地/Docker 启动」；缺 `MASTER_KEY` 时 `/api/health` 有 `master_key_configured` |
| P0-2 | **清理过期文案** | ✅ 已做 | 无 login/admin UI；控制台 local-first |
| P0-3 | **前端 README 过时** | ✅ 已做 | 见 `frontend/README.md` |
| P0-4 | **备份说明** | ⚠️ 文档 | 浏览器侧：系统快照导出；服务端 `./data` + `OPENMAIL_MASTER_KEY`（遗留加密行） |
| P0-5 | **HTTPS 部署说明** | ⚠️ 文档 | 反代最小示例（非必须进代码） |
| P0-6 | **实号验收** | 人工 | 按 §5 用真实 Outlook / IMAP / CF / mail.com 各测 1 个 |

### P1 — 强烈建议（稳定性 / 可维护）

| ID | 项 | 说明 |
|----|----|------|
| P1-1 | **DB 迁移策略** | `create_all` + 手写 ALTER 够单机 |
| P1-2 | **mail.com 脆弱性** | 依赖网页 HTML；站点改版会挂 |
| P1-3 | **Graph token 失效 UX** | 列表上「重新导入令牌」已有提示 |
| P1-4 | ~~公开号池二次确认~~ | ❌ 已不适用（无公开号池入库） |
| P1-5 | **日志与轮转** | Docker 默认日志 |
| P1-6 | **版本号与 CHANGELOG** | 可选 |

### P2 — 可选增强

| ID | 项 |
|----|----|
| P2-1 | 更多 Cookie 站点 |
| P2-2 | 发信打磨 |
| P2-3 | 设备 license / 配额产品化 |
| P2-4 | 可观测性 |
| P2-5 | 默认捆绑 `curl_cffi` |

### 明确不做 / 已否决

- 用户注册 / 登录 / 管理后台  
- 公开号池 / 服务端私有池入库（控制台）  
- 平台托管 OAuth 授权页  
- 完整 Webmail  

---

## 4. 「直接安装使用」最小路径

```bash
cd openmail
cp .env.example .env
./scripts/gen-master-key.sh    # 写入 OPENMAIL_MASTER_KEY（可选设备 license 相关项）

docker compose up -d --build
# 打开 http://localhost:8000
# 操作台 / → 导入账号 → 取件
# 本机邮件 /mails · 设置 /settings
```

本地开发：

```bash
make dev-backend
make dev-frontend
make smoke
```

---

## 5. 实号验收清单（人工）

| # | 场景 | 通过标准 |
|---|------|----------|
| R1 | 微软 OAuth 导入 | 能拉信 + 验证码展示/复制 |
| R2 | IMAP（如 QQ 授权码） | 能拉 INBOX |
| R3 | CF / HttpApi | 按 Worker 返回能解析 |
| R4 | mail.com 密码 | 登录或 cookies 复用成功 |
| R5 | 代理取信 | `POST /api/fetch/proxy` 正常 |
| R6 | 本机邮件搜索 | 取件后 `/mails` 可搜 |
| R7 | 离线 | API 不可用时本机账号列表仍在；取信失败有提示 |
| R8 | 快照迁移 | 导出系统快照 → 另一浏览器导入 |

---

## 6. 代码卫生

| 项 | 说明 |
|----|------|
| 410 stubs | `accounts` create/update/delete、`code-api` create、`/api/me/*` 保留兼容 |
| 活跃路径 | `health` · `public_config` · `fetch/proxy` · `fetch/send` |
| `base.py` `StubProvider` | 扩展基类，可保留 |

---

## 7. 对你问题的直接回答

**「后面可以直接安装使用吗？」**  
**可以。** 推荐：`cp .env.example .env` → 填密钥 → `docker compose up -d --build` → 打开 `:8000` 导入取件。

**形态说明：** 无登录、无公开号池；凭证与账号列表在浏览器；服务端只做代理取信与设备配额。

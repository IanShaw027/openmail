# Cloudflare WARP 代理节点池（同栈 10 节点）

在 **同一套 Docker Compose** 里拉起 10 个 WARP 出口，各自暴露 SOCKS5/HTTP（容器内 `:1080`）。OpenMail 进程内的 `PROXY_POOL` 指向这些节点，用于：

- 批量导入凭证校验（前端并发）
- 批量取件（前端并发）
- 服务端定时同步 / 个人面板收件（`SYNC_CONCURRENCY`）

**不需要** 再单独部署一套代理产品；也 **不需要** Cloudflare Workers / 开发者账号。WARP 使用 `caomingjun/warp` 的**版本标签**（compose 默认 `2026.6.880.0-2.12.0`，不要跟 `:latest`）。覆盖：`WARP_IMAGE=caomingjun/warp@sha256:…`。

## 架构

```
OpenMail ──socks5://warp-N:1080──► warp-1 … warp-10 ──WARP──► 上游邮箱
              (Docker 内网 openmail-net)
```

- **粘性**：默认 `PROXY_SID_STRATEGY=sticky_per_account`，同一邮箱固定落到同一 `warp-N`（利于 Cookie/会话）。
- **固定代理**：操作台编辑账号 →「固定代理」填 `socks5://warp-3:1080` 可钉死某节点。
- **并发**：`FETCH_CONCURRENCY=10`、`SYNC_CONCURRENCY=10`（可用环境变量改）。

## 启动

主机需要：`/dev/net/tun`、允许 `NET_ADMIN`（多数 VPS/KVM 可用）。

```bash
cd /opt/openmail   # 或本仓库路径
cp -n .env.example .env
# 填好 OPENMAIL_MASTER_KEY / PUBLIC_BASE_URL

# 推荐一键（创建 data/warp/* + profile warp）
./scripts/up-with-warp.sh

# 或手动：
mkdir -p data/warp/{1..10}
docker compose --profile warp up -d --build
```

仅核心（无 WARP 节点）：

```bash
docker compose up -d --build
# 若无节点，请在 .env 设 PROXY_POOL= 清空，避免解析不到 warp-N
```

调试：把节点端口映射到宿主机：

```bash
docker compose --profile warp \
  -f docker-compose.yml -f docker-compose.warp.yml up -d
# socks5://127.0.0.1:11001 … 11010
curl -x socks5h://127.0.0.1:11001 -fsS https://www.cloudflare.com/cdn-cgi/trace
# 期望输出含 warp=on 或 warp=plus
```

## 环境变量（`.env`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `PROXY_POOL` | 10 行 `socks5://warp-N:1080` | 多通道列表 |
| `PROXY_SID_STRATEGY` | `sticky_per_account` | 也可 `round_robin` |
| `FETCH_CONCURRENCY` | `10` | 批量/导入建议并发 |
| `SYNC_CONCURRENCY` | `10` | 定时同步并发账号数 |
| `SYNC_INTERVAL_SECONDS` | `3600` | 同步周期 |
| `WARP_LICENSE_KEY` | 空 | 可选 WARP+ |
| `WARP_SLEEP` | `5` | 节点启动等待 |

实例级代理也可经服务端 settings 覆盖（若仍写入 DB）；主路径是环境变量 `PROXY_POOL`。

## 与 local-first 产品的关系

1. 浏览器控制台批量拉取走 `POST /api/fetch/proxy`，凭证在请求体中短暂使用。  
2. WARP / `PROXY_POOL` 提升的是 **出口分散 + 并行度**，不替代 vault。  
3. 后台 `sync_worker` 仅对**未 client-sealed** 且 `sync_enabled` 的服务端行有意义；主产品以控制台拉取 + 本地 `mailCache` 为准。  
4. 客户端密封的云端行 **不会** 被服务端静默同步解密。

## 无 WARP 回退

若主机无 TUN / 策略不允许：

1. 不要使用 `--profile warp`。  
2. 使用精简 compose 或去掉 `depends_on` warp。  
3. `.env` 中 `PROXY_POOL=` 留空，直连出网。  
4. 或填你自己的 HTTP/SOCKS 列表（任意供应商）。

## 合规与注意

- 遵守 Cloudflare WARP 与目标邮箱服务条款；高频自动化有封控风险。  
- 多节点不保证 10 个完全不同出口 IP。  
- 节点首次注册 WARP 需访问公网，启动 `start_period` 约 90s。  
- 状态目录 `./data/warp/N` 请备份；删目录等于新设备重注册。

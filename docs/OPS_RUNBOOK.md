# Smart QA 线上运行手册

本文适用于单台 ECS 上通过 Docker Compose 部署的 Smart QA System。当前生产拓扑为 Nginx、FastAPI、MySQL、Redis 和 Qdrant 五个容器，只有 Nginx 的 80/443 端口对外提供服务。

## 1. 上线基线

### 云安全组

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| 22/TCP | 运维人员公网 IP/32 | SSH 运维 |
| 80/TCP | `0.0.0.0/0` | HTTP，域名与证书就绪前的临时访问 |
| 443/TCP | `0.0.0.0/0` | HTTPS |

禁止对公网开放 8000、6379、6333、3306。应用 8000 端口仅绑定宿主机回环地址，Redis 和 Qdrant 仅在 Docker 内部网络可见。

### 备份与恢复

在阿里云 ECS 控制台为系统盘创建每日自动快照，并在每次部署前创建一次手动快照。当前持久化数据位于 Docker 卷：

```bash
docker volume ls | grep '^local'
docker volume inspect deploy_qdrant-data deploy_redis-data deploy_app-data deploy_mysql-data
```

恢复系统盘快照后，确认 Docker 卷仍存在，再执行健康检查。不得使用 `docker compose down -v`，该命令会删除知识库和缓存卷。

## 2. 日常检查

在项目根目录执行：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml ps
curl -fsS http://127.0.0.1:8000/api/v1/health
docker compose --env-file .env -f deploy/docker-compose.yml logs --tail=100 app
```

预期五个容器均为 `healthy` 或 `running`，健康检查返回 HTTP 200 且 `data.status` 为 `UP`。

## 3. 发布与回滚

发布前先确认 `.env` 存在且不纳入 Git：

```bash
git fetch origin
git switch dev
git pull --ff-only origin dev
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml ps
```

回滚到已验证提交时，记录目标提交号并执行：

```bash
git checkout <commit-id>
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
```

确认恢复后，再创建一个回滚分支，避免在 `dev` 分支上长期停留于 detached HEAD。

## 4. 域名与 HTTPS

当前 IP 访问仅用于部署验收。对外演示前应准备已备案域名，申请 TLS 证书后将 Nginx 从 HTTP 配置升级为 HTTPS，并将 80 跳转到 443。证书私钥不得提交到仓库或写入镜像。

## 5. 故障排查

| 现象 | 优先检查 |
| --- | --- |
| 页面不可访问 | ECS 安全组 80/443、Nginx 容器状态、`docker compose ps` |
| `/api` 返回 502 | 应用容器日志、`curl 127.0.0.1:8000/api/v1/health`、Redis/Qdrant 状态 |
| 上传或问答失败 | 应用日志、DeepSeek API Key、MySQL 状态、磁盘空间、Embedding 模型下载网络 |
| 服务重启后检索变差 | 应用启动日志中的 BM25 重建、Qdrant 卷是否仍存在 |

排查命令：

```bash
df -h
docker compose --env-file .env -f deploy/docker-compose.yml logs -f app
docker compose --env-file .env -f deploy/docker-compose.yml logs -f nginx
```

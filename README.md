# BeeCount-Cloud

BeeCount 云端仓库（独立项目），目标对齐云服务文档 v1：

- 多设备同步（push/pull/full + WebSocket）
- 多用户共享账本（邀请码 + 角色权限）
- Web 完整记账 + 运维控制台
- 自部署优先（SQLite 默认，PostgreSQL 可选）

## Scope (v1)

- 用户认证：邮箱密码 + JWT Access/Refresh
- 共享账本：邀请兼容 + 后台按邮箱添加/移除成员 + 成员角色调整
- 邀请记录：可查看邀请码状态（active/revoked/expired/exhausted）
- 权限模型：`owner` / `editor` / `viewer`
- 成员管理口径：`owner/admin` 可管理成员（member/add/member/remove/member-role）
- 同步权限：
  - `push`: owner/editor 可写，viewer 拒绝
  - `pull`/`full`: owner/editor/viewer 可读
- 运维：健康检查、在线设备、错误检索、备份创建/恢复
- 备份副通道：DB/快照上传归档（不参与实时协同冲突）
- Web：远端数据模式的完整记账（交易/账户/分类/标签 CRUD）+ 运维入口

## Frontend 包结构（v1）

- `frontend/packages/api-client`：统一 API 请求与错误类型（auth/read/write/share/admin）。
- `frontend/packages/web-features`：业务功能组件与权限/导航/格式化逻辑。
- `frontend/packages/ui`：shadcn 风格 UI 基座（含 Radix 交互组件）。
- `frontend/apps/web`：仅负责路由、壳层布局（Top + Left + Content）与页面编排。

## 推荐环境（非强制）

- Python `3.11+`
- Node `20+`
- pnpm `9+`

## 本地开发最短路径

本地默认数据库：

- `DATABASE_URL=sqlite:///./beecount.db`
- `ALLOW_APP_RW_SCOPES` 默认 `true`（App 协作读取/设备会话所需，可按需显式设为 `false`）
- Docker 里会覆盖为 `/data/beecount.db`（容器挂载卷）

### 1) 首次安装（后端）

```bash
make setup-backend
```

### 2) 启动 API

```bash
make migrate
make dev-api
```

API 地址：`http://localhost:8080`

如果你的本地环境变量被覆盖导致数据库路径异常，可临时强制：

```bash
DATABASE_URL=sqlite:///./beecount.db make migrate
DATABASE_URL=sqlite:///./beecount.db make dev-api
```

### 3) 启动 Web（另一个终端）

```bash
make dev-web
```

Web 地址：`http://localhost:5173`

### 4) 第一次登录（本地开发）

首次本地启动通常没有用户，先注入示例账号：

```bash
make seed-demo
```

默认登录信息：

- Email: `owner@example.com`
- Password: `123456`

登录异常排查：

- `AUTH_INVALID_CREDENTIALS`: 账号不存在或密码错误，先执行 `make seed-demo`。
- `INTERNAL_ERROR`: 通常是数据库迁移未完成或 `DATABASE_URL` 配置异常，执行 `make migrate`，并确认本地为 `sqlite:///./beecount.db`。
- App 端角色显示“权限未就绪”或设备会话 `Insufficient scope`: 检查服务端环境变量 `ALLOW_APP_RW_SCOPES` 未被设为 `false`，重启服务后在 App 重新登录一次。

管理员可见性排查：

- 从 `0007_admin_bootstrap` 起，若系统没有任何管理员，迁移会自动把最早创建且启用的用户提升为管理员。
- 也可手动授予管理员：

```bash
make grant-admin EMAIL=owner@example.com
```

发布前清理诊断测试用户（`diag_*@example.com`）：

```bash
make cleanup-diag-users        # 先 dry-run
make cleanup-diag-users APPLY=1
```

### 5) 跑测试

```bash
make test
make lint
make typecheck
pnpm -C frontend/apps/web test:unit
```

说明：当前阶段已移除 Web E2E 门禁，前端以 unit + 关键手工冒烟为主。

## 一键联动开发

SQLite 模式（默认）：

```bash
make dev-up
```

PostgreSQL 联调模式：

```bash
MODE=postgres make dev-up
```

## Docker 部署

### 1) SQLite 单容器（默认）

```bash
docker compose up -d --build
```

### 2) PostgreSQL 叠加模式（联调/生产可选）

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

访问：

- API + Web Console: `http://localhost:8080`
- OpenAPI Docs: `http://localhost:8080/docs`
- Readiness: `http://localhost:8080/ready`
- Metrics: `http://localhost:8080/metrics`

### 常见排查

- 查看容器：`docker compose ps`
- 查看日志：`docker compose logs -f beecount-platform`
- 检查就绪：`curl -f http://localhost:8080/ready`
- App 协作 scope：`docker compose exec beecount-platform /bin/sh -lc 'echo $ALLOW_APP_RW_SCOPES'` 应为 `true`

## CI / Perf 说明

- `nightly-perf` 工作流默认仅手动触发（`workflow_dispatch`）。
- 不做自动定时，避免持续消耗免费 CI 时长。
- 本地也可手动运行：

```bash
python scripts/nightly_perf.py --dataset-size 1000 --read-samples 100 --output artifacts/nightly-perf.json
```

## Demo 数据

```bash
make seed-demo
```

## OpenAPI 与文档

- OpenAPI: `openapi/beecount-cloud-v1.yaml`
- 协同/备份双通道说明: `docs/COLLAB_SYNC_ARCHITECTURE.md`
- Web 写入契约: `docs/API_WRITE_CONTRACT.md`
- Web 记账说明: `docs/WEB_BOOKKEEPING_V1.md`
- 可观测说明: `docs/OBSERVABILITY.md`
- 回滚手册: `docs/ROLLBACK_SOP.md`

## 统一错误响应

API 返回统一结构（并保留 `detail` 兼容字段）：

```json
{
  "error": {
    "code": "LEDGER_NOT_FOUND",
    "message": "Ledger not found",
    "request_id": "req_xxx"
  },
  "detail": "Ledger not found"
}
```

## 网络/代理说明（pnpm）

如果 `pnpm install` 在受限网络下失败，建议先配置镜像或代理后重试：

```bash
pnpm config set registry https://registry.npmmirror.com
# 或按你本地网络策略配置 HTTPS 代理
```

# BeeCount Cloud

[![Docker Pulls](https://img.shields.io/docker/pulls/sunxiao0721/beecount-cloud)](https://hub.docker.com/r/sunxiao0721/beecount-cloud)
[![License](https://img.shields.io/badge/license-custom-blue)](./LICENSE)

**[BeeCount（蜜蜂记账）](https://github.com/TNT-Likely/BeeCount)App 的自部署同步云端。** 让 iOS / Android / Web 三端共用一份完全属于你的账本 — 无广告、无订阅、无第三方依赖。

🌐 **语言**: [中文](./README.md) | [English](./README.en.md)

![BeeCount Cloud Web 控制台](./docs/screenshot-zh.png)

---

## ✨ 核心特性

### 同步
- **双向实时同步** — 手机 / 网页改动约 2 秒内送达其他设备（WebSocket）
- **离线优先** — App 本地先写,恢复网络后自动对账;冲突按"最后写入 + 设备 ID"确定性解决
- **实体级变更** — 交易 / 账户 / 分类 / 标签 / 预算 分别跟踪,不做全量快照覆盖
- **会话自愈** — token 过期自动用本地凭证重登,网络抖动后设备重连不掉线
- **深度体检** — 同步页下拉刷新时对比本地和云端计数,发现差异自动修复

### 记账
- **多账本**,每本独立币种
- **交易** — 收入 / 支出 / 转账,多账户、分类、标签、附件
- **预算** — 按分类或总额,月 / 年周期
- **周期记账**（App）
- **CSV 导入导出**（App）
- **丰富图表** — 月度趋势、分类占比、年度热力图、储蓄率、标签/账户 Top 排行

### 偏好（跨端同步）
- 主题色、收支配色、头像、昵称
- 月份显示格式、紧凑金额、交易时间展示开关
- AI 服务商配置 + 自定义提示词（App AI 集成）

### Web 控制台
- 完整记账 UI（交易 / 账户 / 分类 / 标签）
- 响应式 Dashboard(与 App 观感一致)
- 三语 — 简体中文 / 繁體中文 / English
- 深浅色主题 + 个性化主题色
- 管理面板 — 设备 / 健康 / 同步错误 / 备份归档 / **实时服务端日志**

### 管理与运维
- 内存 ring buffer 日志查看器(级别 / 来源 / 关键词过滤 + 自动刷新)
- 设备会话列表、在线状态、强制下线
- 快照备份创建 / 恢复
- Prometheus `/metrics`,`/ready` 健康探针

---

## 📸 截图

| 中文 UI | English UI |
|---------|------------|
| ![ZH](./docs/screenshot-zh.png) | ![EN](./docs/screenshot-en.png) |

---

## 🚀 Docker Compose 部署

预构建镜像 [`sunxiao0721/beecount-cloud`](https://hub.docker.com/r/sunxiao0721/beecount-cloud) 一体化打包 FastAPI 后端 + Web 控制台 — 单容器 + 一个数据卷,搞定。

### 1) 新建 `docker-compose.yml`

```yaml
services:
  beecount-cloud:
    image: sunxiao0721/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      # 一个 volume 装全部数据:DB / 附件 / 备份 / 头像 / JWT 密钥。
      # 首次启动自动生成 32 bytes 密钥到 /data/.jwt_secret,零配置。
      - beecount_data:/data

volumes:
  beecount_data:
```


### 2) 启动

```bash
docker compose up -d
```

访问 http://localhost:8080 — 注册的第一个账号自动成为管理员,然后在 App 里填自己的服务器地址即可。

### 3) 升级

```bash
docker compose pull
docker compose up -d
```

Alembic 迁移会在容器启动时自动执行(详见[数据库迁移](#-数据库迁移))。

### 4) 备份

`beecount_data` volume 包含所有持久化数据:SQLite 数据库、附件、备份归档。直接打包 volume 即可:

```bash
docker run --rm -v beecount_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/beecount-$(date +%F).tar.gz /data
```

---

## 🗄️ 数据库迁移

schema 版本由 [Alembic](https://alembic.sqlalchemy.org/) 管理。

**每次容器启动**入口脚本会执行:

```bash
alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8080
```

所以升级镜像后,任何新迁移会在服务接收请求前自动按顺序执行。数据持久化在 `beecount_data` volume,升级无需手动介入。

如果迁移失败(罕见),容器会退出、数据库保留在升级前的版本上 — 修复问题后 `docker compose pull && up -d` 重试即可。

---

## 📱 移动端 App

安装 [BeeCount](https://github.com/TNT-Likely/BeeCount) App(iOS / Android),然后在 App 中:

1. 设置 → 云服务 → BeeCount Cloud
2. 填写服务器地址(如 `https://your-domain.com`)和登录凭证
3. 开启同步 — 首次同步会把本地已有数据推送到云端

---

## 🛠️ 本地开发

<details>
<summary>点击展开开发环境搭建</summary>

### 依赖
- Python `3.11+`
- Node `20+`、pnpm `9+`

### 首次安装

```bash
make setup-backend
pnpm -C frontend install
```

### 本地启动

```bash
# 终端 1 — API(端口 8080)
make migrate
make dev-api

# 终端 2 — Web 开发服务(端口 5173)
make dev-web
```

### 示例账号

```bash
make seed-demo
# Email: owner@example.com  Password: 123456
```

### 测试

```bash
make test        # pytest
make lint        # ruff
make typecheck   # mypy
pnpm -C frontend/apps/web test:unit
pnpm -C frontend/apps/web exec tsc --noEmit --skipLibCheck
```

### 一键联动

```bash
make dev-up
```

### 前端包结构

- `frontend/apps/web` — shell、路由、页面编排
- `frontend/packages/api-client` — HTTP + 类型化响应
- `frontend/packages/web-features` — 业务面板、权限、格式化
- `frontend/packages/ui` — shadcn 风格基座(Radix)

### 构建 Docker 镜像

```bash
docker build -t sunxiao0721/beecount-cloud:dev .
docker run -p 8080:8080 -v beecount_data:/data \
  -e JWT_SECRET=dev-secret-at-least-32-bytes-long \
  sunxiao0721/beecount-cloud:dev
```

</details>

---

## 📚 更多文档

- [部署指南](./docs/DEPLOYMENT.md)
- [迁移与回滚](./docs/MIGRATION.md)
- [可观测性](./docs/OBSERVABILITY.md)
- 运行时 OpenAPI / Swagger UI: 访问 `http://your-domain.com/docs`

## 📄 许可证

见 [LICENSE](./LICENSE)。BeeCount Cloud 双协议 — 个人自部署免费;商业使用需单独授权。

## 🔗 相关链接

- 移动端 App: https://github.com/TNT-Likely/BeeCount
- Docker Hub: https://hub.docker.com/r/sunxiao0721/beecount-cloud
- 问题反馈: https://github.com/TNT-Likely/BeeCount-Cloud/issues

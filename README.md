# BeeCount Cloud &nbsp; [English](./README.en.md)

[![Docker Pulls](https://img.shields.io/docker/pulls/sunxiao0721/beecount-cloud)](https://hub.docker.com/r/sunxiao0721/beecount-cloud)
[![License](https://img.shields.io/badge/license-BSL-blue)](./LICENSE)

**[BeeCount(蜜蜂记账)](https://github.com/TNT-Likely/BeeCount) App 的自部署同步云端。** 让 iOS / Android / Web 三端共用一份完全属于你的账本 — 无广告、无订阅、无第三方依赖。

![BeeCount Cloud Web 控制台](./docs/screenshot-zh.png)

---

## 🤔 为什么选 BeeCount Cloud

### vs 其他自托管账本

| 项 | BeeCount Cloud | Firefly III | Actual Budget | Maybe Finance |
|---|---|---|---|---|
| 移动端原生 App | ✅ iOS + Android | ❌ Web only | ⚠️ 仅 Web PWA | ❌ Web only |
| 实时多端同步 | ✅ WebSocket 秒级 | ❌ 无 | ⚠️ 文件同步 | ❌ 无 |
| AI 智能记账 | ✅ AI / OCR / 语音 | ❌ | ❌ | ⚠️ 部分 |
| 部署成本 | 单容器 + 1 个 volume | 容器 + Postgres | 容器 + 文件存储 | 多服务 |
| 加密备份 | ✅ AES-256 + 多远端 | ⚠️ 手动 | ❌ | ⚠️ 手动 |
| 中文 / i18n | ✅ 简繁中英 | ⚠️ 部分 | ❌ | ❌ |

### vs 商业云账本

- 🔐 **隐私第一** — 数据不离开你的服务器,开发者无法访问
- 💰 **完全免费** — 一个 Docker volume 的成本,没有订阅
- 🛡️ **无锁定** — 数据 = SQLite + 附件文件,随时打包带走
- 🔓 **代码可审计** — 全开源,FastAPI + React 都在仓库里

---

## ✨ 核心特性

### 同步

- **双向实时同步** — 手机 / 网页改动约 2 秒内送达其他设备(WebSocket)
- **离线优先** — App 本地先写,恢复网络后自动对账;冲突按"最后写入 + 设备 ID"确定性解决
- **实体级变更** — 交易 / 账户 / 分类 / 标签 / 预算 分别跟踪,不做全量快照覆盖
- **会话自愈** — token 过期自动用本地凭证重登,网络抖动后设备重连不掉线
- **深度体检** — 同步页下拉刷新时对比本地和云端计数,发现差异自动修复

### 记账

- **多账本**,每本独立币种
- **交易** — 收入 / 支出 / 转账,多账户、分类、标签、附件
- **预算** — 按分类或总额,月 / 年周期
- **周期记账**(App)
- **CSV 导入导出**(App,支持支付宝/微信账单)
- **丰富图表** — 月度趋势、分类占比、年度热力图、储蓄率、标签/账户 Top 排行

### 偏好(跨端同步)

- 主题色、收支配色、头像、昵称
- 月份显示格式、紧凑金额、交易时间展示开关
- AI 服务商配置 + 自定义提示词(App AI 集成)

### Web 控制台

- 完整记账 UI(交易 / 账户 / 分类 / 标签 / 预算)
- 响应式 Dashboard(与 App 观感一致,移动端友好)
- **⌘K 命令面板 + AI 文档问答** — 任意页面 ⌘K(macOS)/ Ctrl+K 打开搜索;输入 `?xxx` 或选「问 AI」,基于官方文档 RAG 检索 + 用 App 配的 LLM 生成答案,自动贴 source 链接
- **PWA 支持** — 浏览器地址栏的"安装"图标点一下,即可作为独立 app 装到桌面 / Dock,断网时离线读缓存
- 三语 — 简体中文 / 繁體中文 / English
- 深浅色主题 + 个性化主题色
- 管理面板 — 设备 / 健康 / 同步错误 / 备份归档 / **实时服务端日志**

### 管理与运维

- 内存 ring buffer 日志查看器(级别 / 来源 / 关键词过滤 + 自动刷新)
- 设备会话列表、在线状态、强制下线
- Prometheus `/metrics`,`/ready` 健康探针

---

## 🔐 备份系统

> 单容器自带的**多远端 + AES-256 加密**备份系统。本地数据库 / 附件 / JWT 密钥可定时自动推送到任意 S3 / R2 / B2 / WebDAV / Google Drive / OneDrive。

### 关键特性

- **rclone 多远端 fan-out** — 一份备份并行推到多个远端做冗余,任何一家挂了都还有
- **AES-256 加密 zip** — 备份文件是标准的密码加密 zip,**用户从对象存储下载下来双击 → macOS Archive Utility / 7-Zip / Keka / WinRAR 自动弹密码框 → 解开**(脱离 BeeCount 服务也能恢复)
- **APScheduler cron 调度** — 标准 cron 表达式,时区跟随容器 `TZ`
- **保留期清理** — 保留最近 N 天,旧的自动删,`keep_at_least=1` 防误配
- **Web 三标签页**:
  - **远端配置** — S3 / R2 / B2 / WebDAV 等表单填写,32 位口令一键生成
  - **定时任务** — cron 预设、多远端选择、立即运行
  - **历史与进度** — 实时上传进度、历史记录、一键 Restore
- **Restore 隔离目录** — 服务端恢复**绝不动 live data**,只往 `<DATA_DIR>/restore/<run_id>/` 写,用户手动 cp/rsync 替换
- **审计日志** — 每次创建 / 测试 / 揭露 / 备份运行都打 audit log

### 默认推荐

- **每天 4 点本地时间**,保留 30 天,加密 + 包含附件
- **2 个远端冗余**(如 R2 + WebDAV),其中之一挂了不影响

### 灾难恢复

只要你有 .zip 文件 + 口令,任何系统、任何标准解压工具都能解开备份,不依赖 BeeCount 服务存在。

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
      - "8869:8080"
    volumes:
      - ./data:/data
    environment:
      # —— 可选:启用 ⌘K「AI 文档问答」(对官方文档做 RAG 检索)——
      # 不填这把 key 也行,功能就走 fallback「跳官网搜文档」,其它功能完全不受影响。
      # 默认走 SiliconFlow 免费 quota(月 10 万次问答足够小规模自托管),
      # 注册 https://siliconflow.cn 拿 key 填进来即可。
      EMBEDDING_BASE_URL: https://api.siliconflow.cn/v1
      EMBEDDING_MODEL: BAAI/bge-m3
      EMBEDDING_API_KEY: ""        # ← 填你的 SiliconFlow key 启用 AI Q&A
```

> 兼容的 embedding provider 完整列表(SiliconFlow / OpenAI / 智谱 / 阿里 / 火山 / Voyage / Mistral / Jina / Together / 自托管 Ollama...)+ 切换说明 见 [`.env.example`](./.env.example)。**关键约束**:`EMBEDDING_MODEL` 必须跟 docker image 里自带的 sqlite 索引 build 时一致(默认 `BAAI/bge-m3`),换 model 必须双侧同步重 build 索引。

### 2) 启动

```bash
docker compose up -d
# 查看首次启动生成的随机管理员账号密码:
docker compose logs beecount-cloud | grep -A 10 "初次启动"
```

看到类似:

```
 BeeCount Cloud — 初次启动,已自动创建管理员账号:

   邮箱:    owner@example.com
   密码:    FIDodUnwprkw1zUi
```

拿这个账号:

- 浏览器访问 `http://<你的服务器 IP>:8869` 即可用 **Web 管理端**
- App 里选「BeeCount Cloud」,填服务器地址 + 上面账号登录

### 3) 升级

```bash
docker compose pull
docker compose up -d
```

Alembic 迁移会在容器启动时自动执行(详见[数据库迁移](#-数据库迁移))。

### 4) 备份

`./data/` 目录包含所有持久化数据:SQLite 数据库、附件、备份归档、JWT 密钥。直接打包目录即可:

```bash
tar czf beecount-$(date +%F).tar.gz ./data
```

或更推荐的:配置内置的**多远端加密备份**(见[备份系统](#-备份系统)),自动 cron 推到 S3 / R2 / WebDAV。

### 5) 自定义初始管理员

```yaml
services:
  beecount-cloud:
    image: sunxiao0721/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8869:8080"
    environment:
      # 自指定管理员账号(替代默认随机生成):
      BOOTSTRAP_ADMIN_EMAIL: me@example.com
      BOOTSTRAP_ADMIN_PASSWORD: <你的强密码>
      # 调度器时区(默认 Asia/Shanghai,跟容器 TZ 同步):
      # TZ: Asia/Shanghai
    volumes:
      - ./data:/data
```

### 6) 公网部署

建议在前面套一层 nginx / caddy 做 HTTPS + 域名。App 和 Web 都支持 `https://` 地址。

---

## 🗄️ 数据库迁移

schema 版本由 [Alembic](https://alembic.sqlalchemy.org/) 管理。

**每次容器启动**入口脚本会执行:

```bash
alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8080
```

所以升级镜像后,任何新迁移会在服务接收请求前自动按顺序执行。数据持久化在 `./data/` 目录,升级无需手动介入。

如果迁移失败(罕见),容器会退出、数据库保留在升级前的版本上 — 修复问题后 `docker compose pull && up -d` 重试即可。

---

## 📱 移动端 App 接入

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
- [同步架构](./docs/SYNC_ARCHITECTURE.md)
- 运行时 OpenAPI / Swagger UI: 访问 `http://your-domain.com/docs`

---

## 📄 许可证

本项目采用 **商业源代码许可证(Business Source License,BSL)**。

| 用途 | 许可 |
|---|---|
| ✅ **个人自部署** | 完全免费 |
| ✅ **学习研究** | 完全免费 |
| ✅ **开源贡献** | 欢迎参与 |
| ❌ **商业使用** | 需要付费授权 |

**什么算商业使用**:

- 把 BeeCount Cloud 作为商业 SaaS 提供给客户
- 在盈利性组织中部署使用
- 基于本软件提供付费云服务
- 转售或集成到付费产品里

如需商业授权,请通过 [GitHub Issues](https://github.com/TNT-Likely/BeeCount-Cloud/issues) 联系。详见 [LICENSE](./LICENSE)。

---

## 🔗 相关链接

- 移动端 App: <https://github.com/TNT-Likely/BeeCount>
- Docker Hub: <https://hub.docker.com/r/sunxiao0721/beecount-cloud>
- 问题反馈: <https://github.com/TNT-Likely/BeeCount-Cloud/issues>

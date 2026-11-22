# BeeCount Cloud — APP 开发文档

> 本文档面向移动端（iOS/Android）与第三方客户端开发者，描述如何基于 BeeCount Cloud 后端构建 APP。包含 API 规范、同步协议、认证流程、数据模型与开发约定。

---

## 1. 概述

- **后端服务**：BeeCount Cloud（FastAPI），提供 REST API + WebSocket 实时同步 + MCP。
- **客户端**：iOS/Android 原生 App、Web 控制台、第三方客户端。
- **数据归属**：所有数据存储于用户自部署的服务器，客户端通过 API 访问。

## 2. 基础信息

| 项 | 值 |
|---|---|
| Base URL | `http(s)://<host>:<port>` |
| API 前缀 | `/api/v1` |
| 认证方式 | JWT Bearer Token |
| 内容类型 | `application/json` |
| WebSocket | `ws(s)://<host>:<port>/ws` |
| 文档 | `{base}/docs`（Swagger UI） |

## 3. 认证流程

### 3.1 登录

```
POST /api/v1/auth/login
Body: { "username": "...", "password": "..." }
Response: { "access_token": "...", "refresh_token": "...", "expires_in": 3600 }
```

- 访问令牌短时效（默认 1h），刷新令牌服务端存储。
- 401 时用 refresh_token 调用 `/auth/refresh` 获取新 access_token。
- 支持 2FA（`/auth/2fa/*`）。

### 3.2 请求头

```
Authorization: Bearer <access_token>
```

### 3.3 令牌刷新（自动重试）

客户端应在 401 时自动刷新并重试原请求（最多 1 次），避免用户感知。

## 4. 核心 API 端点

### 4.1 账本（Ledgers）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/read/ledgers` | 列出用户账本 |
| POST | `/write/ledgers` | 创建账本 |
| PATCH | `/write/ledgers/{id}` | 更新账本 |
| DELETE | `/write/ledgers/{id}` | 删除账本 |

### 4.2 交易（Transactions）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/read/workspace` | 交易列表（分页/筛选） |
| POST | `/write/transactions` | 创建交易 |
| PATCH | `/write/transactions/{id}` | 更新交易 |
| DELETE | `/write/transactions/{id}` | 删除交易 |
| POST | `/write/transactions/batch` | 批量操作 |

### 4.3 账户（Accounts）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/read/accounts` | 账户列表 |
| POST | `/write/accounts` | 创建账户 |
| PATCH | `/write/accounts/{id}` | 更新账户 |
| DELETE | `/write/accounts/{id}` | 删除账户 |

### 4.4 分类/标签/预算

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PATCH/DELETE | `/read|write/categories` | 分类 CRUD |
| GET/POST/PATCH/DELETE | `/read|write/tags` | 标签 CRUD |
| GET/POST/PATCH/DELETE | `/read|write/budgets` | 预算 CRUD |

### 4.5 成员/联系人/借款

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PATCH/DELETE | `/members` | 成员 CRUD |
| GET/POST/PATCH/DELETE | `/contacts` | 联系人 CRUD |
| GET/POST/PATCH/DELETE | `/loans` | 借款 CRUD |
| POST | `/loans/{id}/repayments` | 还款 |

### 4.6 储蓄罐/报销/物品/定期

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PATCH/DELETE | `/saving-plans` | 储蓄罐 |
| POST | `/saving-plans/{id}/contribute` | 存入 |
| GET/POST/PATCH/DELETE | `/reimbursements` | 报销 |
| GET/POST/PATCH/DELETE | `/items` | 物品 |
| GET/POST/PATCH/DELETE | `/deposits` | 定期存款 |

### 4.7 同步（Sync）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sync/push` | 推送本地变更 |
| POST | `/sync/pull` | 拉取云端变更 |
| WS | `/ws` | 实时推送 |

### 4.8 附件

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/attachments/upload` | 上传附件 |
| GET | `/attachments/{id}` | 获取附件 |

### 4.9 用户/设备/偏好

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PATCH | `/profile` | 用户资料 |
| GET/POST/DELETE | `/devices` | 设备管理 |
| GET/PATCH | `/settings` | 偏好设置 |

## 5. 同步协议

### 5.1 离线优先架构

1. 本地 SQLite 先写（乐观更新）。
2. 网络恢复后调用 `/sync/push` 推送变更。
3. 调用 `/sync/pull` 拉取云端变更。
4. 冲突按"最后写入 + 设备 ID"确定性解决。

### 5.2 WebSocket 实时推送

- 连接：`ws://<host>/ws?token=<access_token>`
- 服务端推送变更事件（entity_type + entity_id + action）。
- 客户端收到后更新本地缓存。

### 5.3 实体级变更跟踪

每个实体有 `sync_id`、`updated_at`、`deleted_at`（软删除），用于增量同步。

## 6. 数据模型（APP 侧）

### 6.1 交易（Transaction）

```json
{
  "id": "uuid",
  "ledger_id": "uuid",
  "type": "income|expense|transfer|refund",
  "amount": 100.00,
  "currency": "CNY",
  "account_id": "uuid",
  "to_account_id": "uuid (transfer)",
  "category_id": "uuid",
  "tags": ["uuid"],
  "member_splits": [{"member_id": "uuid", "amount": 50}],
  "happened_at": "2026-11-20T10:00:00",
  "note": "string",
  "attachments": ["uuid"],
  "created_by": "uuid",
  "updated_at": "ISO8601",
  "sync_id": "uuid"
}
```

### 6.2 账户（Account）

```json
{
  "id": "uuid",
  "name": "string",
  "type": "cash|bank|credit_card|investment|...",
  "icon_key": "string",
  "icon_type": "lucide|bank|custom",
  "background_key": "string",
  "card_last_four": "string (up to 23 chars)",
  "billing_day": 15,
  "repayment_day": 25,
  "initial_balance": 0.00,
  "currency": "CNY"
}
```

### 6.3 成员分摊（MemberSplit）

```json
{
  "member_id": "uuid",
  "amount": 50.00,
  "ratio": 0.5
}
```

## 7. 开发约定（APP 侧）

### 7.1 网络层

- Base URL 从用户配置读取（支持自部署）。
- 所有请求走 HTTPS（生产）。
- 4xx 不重试，5xx 自动重试（最多 3 次，指数退避）。
- fetch 失败显示离线提示。

### 7.2 本地存储

- SQLite 本地数据库（离线优先）。
- 令牌存储于 Keychain/Keystore（禁止明文）。
- 附件缓存本地文件系统。

### 7.3 状态管理

- 乐观更新：本地先写，同步后对账。
- 冲突解决：服务端版本号 + 设备 ID。

### 7.4 UI/UX

- 遵循平台设计规范（iOS HIG / Material Design）。
- 深浅色主题。
- 多语言（简/繁/英）。

## 8. 错误处理

### 8.1 错误响应格式

```json
{
  "detail": "错误描述",
  "code": "ERROR_CODE",
  "field": "field_name (可选)"
}
```

### 8.2 常见错误码

| HTTP | 说明 |
|---|---|
| 400 | 请求参数错误 |
| 401 | 未认证/令牌过期 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 冲突（同步冲突） |
| 429 | 频率限制 |
| 500 | 服务端错误 |

## 9. 部署与环境

### 9.1 服务端部署

```bash
# Docker
docker compose up -d

# 或手动
pip install -r requirements.txt
alembic upgrade head
uvicorn server:app --port 8080
```

### 9.2 环境变量

| 变量 | 说明 |
|---|---|
| `JWT_SECRET` | JWT 密钥（32+ 字节） |
| `CORS_ORIGINS` | 允许的来源 |
| `DATABASE_URL` | 数据库连接（默认 SQLite） |
| `API_PREFIX` | API 前缀（默认 /api/v1） |

## 10. 测试

- 后端：`pytest`（tests/ 目录）。
- 前端：`vitest` + Playwright E2E。
- APP： XCTest / Espresso / Flutter test。

## 11. 参考资源

- Swagger UI：`http://<host>:8080/docs`
- ReDoc：`http://<host>:8080/redoc`
- OpenAPI JSON：`http://<host>:8080/openapi.json`
- SYNC_ARCHITECTURE.md：同步架构详解
- MIGRATION.md：数据库迁移指南
- OBSERVABILITY.md：监控与日志

---
*文档生成日期：2026-11-20*
EOF
echo done
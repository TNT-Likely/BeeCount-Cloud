# BeeCount Cloud — 功能开发文档

> 本文档整理 BeeCount Cloud（蜜蜂记账自部署同步云端）的完整功能模块、技术架构与开发约定，供新成员与贡献者快速上手。

## 1. 项目概述

- **定位**：BeeCount App（iOS/Android）的自部署同步云端，三端共用一份账本。
- **形态**：FastAPI 单体后端 + React Web 控制台 + WebSocket 实时同步 + MCP Server。
- **许可证**：BSL（Business Source License）。
- **多语言**：简体中文 / 繁體中文 / English。

## 2. 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy / Alembic / SQLite 或 PostgreSQL |
| 前端 | React 18 / Vite / TypeScript / TailwindCSS / shadcn-ui 风格 |
| 状态/数据 | React Query 风格 fetch 封装、Zustand-like store |
| 实时 | WebSocket（秒级推送） |
| AI | MCP Server（Claude/Cursor/Cline 集成） |
| 测试 | pytest（后端）、Vitest（前端）、Playwright（E2E） |
| 包管理 | pnpm（前端）、pip/venv（后端） |

## 3. 目录结构

```
BeeCount-Cloud/
├── src/                  # 后端源码
│   ├── models.py         # SQLAlchemy 模型
│   ├── schemas.py        # Pydantic Schema
│   ├── routers/          # 路由（read/write/admin/...）
│   ├── services/         # 业务服务
│   ├── mcp/              # MCP 工具
│   └── main.py           # 入口
├── frontend/
│   ├── apps/web/         # Web 控制台
│   └── packages/         # api-client / ui / web-features
├── alembic/              # 数据库迁移
├── tests/                # 后端测试
├── scripts/              # 运维/导入/QA 脚本
└── docs/                 # 文档
```

## 4. 核心功能模块

### 4.1 同步与协作
- 双向实时同步（WebSocket，~2s 延迟）。
- 离线优先 + 冲突解决（最后写入 + 设备 ID）。
- 实体级变更跟踪（交易/账户/分类/标签/预算）。
- 共享账本：Owner/Editor 双角色、邀请码、成员收支统计。

### 4.2 记账核心
- 多账本、多币种。
- 交易：收入/支出/转账，多账户、分类、标签、附件。
- 预算：按分类或总额，月/年周期。
- 周期记账、CSV 导入导出。

### 4.3 个人财务扩展
- **成员（Members）**：收支归属、分摊金额、详情弹窗、与交易双向跳转。
- **联系人/借款（Contacts & Loans）**：借出/借入、还款、利息、合并还款、附件。
- **定期存款（Deposits）**：利息与到期日计算、结息、提醒。
- **储蓄罐（Saving Plans）**：计划编辑、存入记录修改/删除、双账户转账、关联既有交易。
- **报销（Reimbursements）**：报销项/报销单、公司维度、到账账户。
- **物品（Items）**：物品管理、多流水关联、自动汇总成本。

### 4.4 资产账户
- 完整卡号存储、银行图标/背景、账单日/还款日、初始金额（欠款/溢缴）。
- 信用卡按月/账期分组流水、汇总、折叠。
- 资产卡片高度优化（2:1 比例）。

### 4.5 分析与可视化
- Dashboard：月度趋势、分类占比、年度热力图、储蓄率、Top 排行。
- 成员/联系人/报销详情弹窗（三段式版式）。
- 信用卡/非信用卡流水按月分组。

### 4.6 Web 控制台
- 完整记账 UI、响应式、PWA。
- ⌘K 命令面板 + AI 文档问答（RAG）。
- 三语 + 深浅色主题 + 主题色。
- 管理面板：设备/健康/同步错误/备份/实时日志。

### 4.7 MCP / AI 集成
- 自然语言查询交易、记账、改预算。
- 工具：capability_tools、flow_links、flow_sync 等。

## 5. 数据模型（核心实体）

| 实体 | 说明 |
|---|---|
| Ledger | 账本（多账本、多币种） |
| Transaction | 交易（收入/支出/转账/退款） |
| Account | 账户（含 icon/background/card_last_four） |
| Category / Tag | 分类/标签 |
| Budget | 预算 |
| Member | 成员（收支归属、分摊） |
| Contact / Loan / LoanRepayment | 联系人/借款/还款 |
| Deposit | 定期存款 |
| SavingPlan / SavingContribution | 储蓄罐/存入记录 |
| Reimbursement / ReimbursementItem / ReimbursementCompany | 报销 |
| Item / ItemFlowLink | 物品/流水关联 |

## 6. API 约定

- REST，前缀 `/api/v1`。
- 鉴权：JWT 访问令牌 + 服务端刷新令牌；401 自动刷新重试。
- 读写分离：`/read/*`、`/write/*`。
- 错误：类型化错误 + 全局处理器，返回规范化结构。
- 同步：`/sync/*`（push/pull/ws）。

## 7. 前端架构

- Monorepo：`apps/web` + `packages/{api-client,ui,web-features}`。
- 路由：`state/router.ts`。
- 实体详情弹窗：`GlobalEntityDialogs` + `txDialogEvents` 双向导航。
- 统一图标：`forms.ts` / `accountIcons.ts`。
- i18n：`i18n/{zh-CN,zh-TW,en}.ts`。

## 8. 开发约定

- **分层**：控制器 → 服务 → 仓储，控制器不含业务逻辑。
- **配置**：全部来自环境变量，启动时校验，`.env.example` 仅占位。
- **迁移**：数据库变更一律 Alembic，可回滚。
- **事务**：多步写入使用事务。
- **类型安全**：前后端类型对齐（api-client）。
- **测试**：pytest + Vitest + Playwright E2E。

## 9. 启动命令

```bash
# 后端
cd BeeCount-Cloud && .venv/bin/activate && alembic upgrade head
uvicorn server:app --port 8080 --reload

# 前端
cd BeeCount-Cloud/frontend && pnpm -C apps/web dev
```

- 后端：http://127.0.0.1:8080
- 前端：http://127.0.0.1:5173

## 10. 关键文件索引

| 功能 | 后端 | 前端 |
|---|---|---|
| 交易 | `src/routers/write/transactions.py` | `TransactionsPage.tsx` |
| 账户 | `src/routers/write/accounts.py` | `AccountsPage.tsx` / `AccountsPanel.tsx` |
| 成员 | `src/routers/members.py` | `MembersPage.tsx` |
| 借款 | `src/routers/contacts_loans.py` | `LoansPage.tsx` |
| 储蓄罐 | `src/routers/saving_plans.py` | `SavingPlansPage.tsx` |
| 报销 | `src/routers/reimbursements.py` | `ReimbursementsPage.tsx` |
| 物品 | `src/routers/items.py` | `ItemsPage.tsx` |
| 定期 | `src/routers/deposits.py` | `DepositsPage.tsx` |
| 联系人 | `src/routers/contacts_loans.py` | `ContactsPage.tsx` |
| 关联反查 | `src/routers/flow_links.py` | `GlobalEntityDialogs.tsx` |

## 11. 遗留/待办

- 移动端日期选择器体验优化（react-day-picker 在手机上占屏）。
- 交易 occurred_at 时间精度场景仍用原生 input，可统一到 datetime picker。
- Vitest Windows 偶发少收集 i18n.test.ts（环境竞态，非业务问题）。

---
*文档生成日期：2026-11-20*
EOF
echo done
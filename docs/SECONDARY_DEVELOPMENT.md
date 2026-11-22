# BeeCount Cloud — 本地二次开发文档

> 本文档记录在本仓库（用户本地副本）上相对原版 BeeCount Cloud 所做的全部定制开发。
> 涵盖功能编号 **0022–0066**、数据库迁移 **0022–0043**、新增模块、业务口径约定、数据修复脚本与踩坑经验。
> 与原版架构/API/目录基线见 [`DEVELOPMENT.md`](./DEVELOPMENT.md)。

---

## 目录

1. [二次开发总览](#1-二次开发总览)
2. [数据库迁移清单](#2-数据库迁移清单)
3. [新增模块索引](#3-新增模块索引)
4. [功能模块详解](#4-功能模块详解)
5. [核心口径约定](#5-核心口径约定)
6. [依赖变更](#6-依赖变更)
7. [测试与 QA 体系](#7-测试与-qa-体系)
8. [数据修复与导入脚本](#8-数据修复与导入脚本)
9. [已知边界与遗留项](#9-已知边界与遗留项)
10. [环境踩坑合集](#10-环境踩坑合集)
11. [关键文件索引](#11-关键文件索引)

---

## 1. 二次开发总览

### 开发时间线

| 日期 | 功能编号 | 主题 |
|---|---|---|
| 11-14 | 0022–0025 | 支出优惠、流水余额、成员分摊、退款 |
| 11-15 | 0025–0028 | 退款增强、联系人/借款、组合付款、超还显示 |
| 11-16 | 0026–0030 | 借款完善、按人结算、附件、关联人资料 |
| 11-17 | — | 数据备份、首页/标签 Bug 修复 |
| 11-18 | 0031–0047 | 定期、报销、储蓄罐、公司、物品、借款利息/合并还款、统一同步、关联跳转 |
| 11-19 | 0046–0049 | 重复渲染清理、双向跳转、信用卡分组、全站图标 |
| 11-20 | 0050–0058 | 账户页修复、弹窗三段式、储蓄罐编辑、时间选择器 |

### 规模

- **新增/修改数据库迁移**：22 个（0022–0043，其中 0032 为 hash 命名的 `17ed35b941fe_0032_credit_card_bills`）
- **新增后端模块**：8 个核心工具 + 8 个业务路由 + 7 个 Schema 文件
- **新增前端页面**：6 个（Contacts / Loans / Deposits / SavingPlans / Reimbursements / Items）+ Members
- **新增 E2E 脚本**：20+ 个（`scripts/qa/e2e_*.py`）
- **新增图标资源**： banks/cn 45 个 SVG + account 12 个 SVG

### 与原版的关键差异

| 维度 | 原版 | 本地二次开发 |
|---|---|---|
| 消费成员 | 仅有共享账本协作者 | 新增独立 user-global「消费成员」+ 分摊统计维度 |
| 业务实体 | 交易/账户/分类/标签/预算 | 新增联系人/借款/定期/储蓄罐/报销/公司/物品 7 大业务域 |
| 流水关联 | 仅退款原单、合并原单两处硬编码 | 统一 `flow_links` 全实体反查 + `flow_sync` 金额回写 + `LinkedTxList` 渲染 |
| 详情弹窗 | 简单 Dialog | 三段式版式（固定头/独立滚动/固定底栏）+ 与流水双向跳转 |
| 信用卡流水 | 单一列表 | 按月/按账期分组汇总 + 全量分页加载 |
| 图标 | Lucide + Material Symbols | 全站统一注册表 + 银行品牌 SVG + 可自定义特殊流水图标/颜色 |
| 时间选择 | 原生 `<input type="date">` | react-day-picker（日期场景） |

---

## 2. 数据库迁移清单

| 版本 | 文件 | 内容 |
|---|---|---|
| 0022 | `0022_transfer_fee_discount.py` | 转账手续费/优惠字段 |
| 0023 | `0023_expense_discount.py` | `read_tx_projection.discount_amount` 支出优惠 |
| 0024 | `0024_members.py` | 消费成员（`user_member_projection`） |
| 0025 | `0025_refunds.py` | 退款：`is_refund` / `refund_of_sync_id` / `refunded_amount` |
| 0026 | `0026_contacts_loans.py` | 联系人 / 借款 / 还款 |
| 0027 | `0027_loan_account_flow.py` | 借款账户与流水字段 `account_id/account_name/tx_sync_id` |
| 0028 | `0028_payment_splits.py` | 支出组合付款分摊 |
| 0029 | `0029_loan_repayment_attachments.py` | 还款附件 `attachment_file_ids_json` |
| 0030 | `0030_party_profiles.py` | 联系人/成员 `profile_json` |
| 0031 | `0031_deposits.py` | 定期存款 + 结息记录 |
| 0032 | `17ed35b941fe_0032_credit_card_bills.py` | 信用卡账单 |
| 0033 | `0033_loan_repayment_interest.py` | 还款利息字段 |
| 0034 | `0034_saving_plans.py` | 储蓄罐计划与存入 |
| 0035 | `0035_reimbursements.py` | 报销项 / 报销单 |
| 0036 | `0036_saving_contrib_accounts.py` | 存入记录扣款/收款账户与流水字段 |
| 0037 | `0037_reimbursement_companies.py` | 公司档案 + 报销公司快照 |
| 0038 | `0038_items.py` | 物品 / 物品流水 |
| 0039 | `0039_loan_merged_repayment.py` | 合并还款组 `loan_payment_groups` + `loan_repayments.group_id` |
| 0040 | `0040_item_multi_tx_links.py` | 物品多流水关联 `tx_sync_ids_json` |
| 0041 | `0041_account_icon_keys.py` | 补用户账户投影的 `icon_key/icon_type/background_key`（修复「模型有列、表无列」） |
| 0042 | `0042_contact_status.py` | 联系人归档/停用：`contacts.is_archived` + `contacts.is_active` |
| 0043 | `0043_user_lock_settings.py` | 用户级锁账：`users.is_locked` + `users.lock_date` |

> **注意**：功能编号 0041–0065 是需求/任务编号，**当时未产生新迁移**（head 长期保持 0040）。后续为修复账户图标字段缺失与新增联系人状态功能，补做了 0041、0042 两个迁移；当前 alembic head 为 **0043_user_lock_settings**。

---

## 3. 新增模块索引

### 后端核心工具

| 文件 | 职责 |
|---|---|
| `src/member_splits.py` | 成员分摊三种形态编解码（snapshot camelCase / Web 写请求 snake_case / projection 三列） |
| `src/payment_splits.py` | 支出组合付款分摊编解码 |
| `src/tx_running_balance.py` | 读时计算每笔流水后账户余额（不落库） |
| `src/party_profile.py` | 关联人资料归一化（头像/联系方式/银行卡，上限 30 条） |
| `src/deposit_calc.py` | 定期存款利息/到期日/状态纯函数（月末收敛、利息=本金×年利率×月数/12） |
| `src/linked_tx.py` | 关联流水公共查询（`linked_tx_details` / `linked_tx_amounts` / `linked_tx_map` 批量） |
| `src/transfer_adjustments.py` | 转账手续费/优惠联动流水（**已定义但未接入写入链路**） |
| `src/routers/flow_sync.py` | 流水金额变更 → 引用实体统一同步调度 |
| `src/routers/flow_links.py` | 流水 → 关联业务实体统一反查 |

### 后端新增路由 / Schema

- 路由：`contacts_loans.py`、`deposits.py`、`saving_plans.py`、`reimbursements.py`、`companies.py`、`items.py`、`flow_links.py`、`flow_sync.py`、`write/members.py`
- Schema：`schemas_cl.py`、`schemas_dep.py`、`schemas_sp.py`、`schemas_rb.py`、`schemas_company.py`、`schemas_items.py`、`schemas_linked.py`

### 前端新增组件 / 模块

| 路径 | 职责 |
|---|---|
| `components/dialogs/CreditCardFlowGroups.tsx` | 信用卡按月/账期分组流水 + 汇总 |
| `components/LoanAttachments.tsx` | 借款凭证上传/预览（`LoanAttachmentEditorField` / `LoanAttachmentThumbs`） |
| `components/DatePicker.tsx` | react-day-picker 封装（Dialog 弹窗，三语 locale） |
| `hooks/usePartyProfileIo.ts` | 关联人头像上传与预览 |
| `lib/txDialogEvents.ts` / `lib/txRefreshEvents.ts` | 交易详情/刷新事件总线（含 `dispatchOpenDetail*` 系列） |
| `web-features/src/lib/sectionIcons.ts` | 全站功能模块图标注册表（27 个 section） |
| `web-features/src/lib/flowIcons.ts` / `FlowIconsContext.tsx` | 7 类特殊流水图标/颜色偏好与双 Context |
| `web-features/src/components/LinkedTxList.tsx` | 关联流水统一渲染组件 |
| `web-features/src/components/PartyProfileEditor.tsx` | 关联人资料编辑/只读组件 |
| `web-features/src/components/RefundDialog.tsx` | 退款弹窗 |

---

## 4. 功能模块详解

### 4.1 交易增强（0022/0023 + 余额显示）

**支出优惠（discount_amount）**
- 口径：`amount` = 实付金额；`discount_amount` 仅作明细与统计元数据，**不冲减余额、不生成"优惠减免"流水**；优惠前金额 = `amount + discount`。
- 校验：`0 <= discount <= amount`，仅 expense 有效，切到 income/transfer 自动清除。
- 透传链路：models / schemas / projection / snapshot_builder / snapshot_mutator（create+update）/ write `_shared` / read `ledgers`+`workspace` / sync_applier merge spec。

**流水后余额（`account_balance_after` / `counter_account_balance_after`）**
- 口径：`initial_balance + income - expense - transfer(from) + transfer(to)`，与 `workspace/accounts` 聚合一致。
- **读时计算、不落库**：`compute_running_balances` 一次扫描全部可见账本投影，按 `(happened_at, tx_index, sync_id)` 升序累积；否则任何历史流水增删改都要重算后续行，与 fast path 冲突。
- 展示：TransactionRow 金额下方灰色「余额 x」/转账「余额 a → b」；详情弹窗「交易后余额」行。

**支出组合付款（0028）**：一笔支出拆到多个账户（`payment_splits`），Web 表单校验：≥2 条 / 账户不重复 / 金额>0 / 合计=总额（容差 0.01）。

### 4.2 成员与分摊（0024）

- **概念区分**：本地新增的是「消费成员」（user-global `UserMemberProjection`），与 `LedgerMember`（共享账本协作者）完全解耦。
- 写端点挂 `/write/ledgers/{id}/members`（账本仅作变更载体）；仅 income/expense 可关联，transfer 清空；多成员分摊合计须等于交易金额（按分四舍五入后比较，容差 0.01）。
- 统计：`analytics.member_ranks` 按分摊占比切 `coalesce(native_amount, amount)`；成员详情弹窗用「收支归属」口径（支出合计/收入合计/净额/笔数），分摊流水显示该成员分摊到的那一份。

### 4.3 退款（0025，多次增强）

- **强制从原单发起**：冲减原分类支出，**不计收入、不污染月度收支**；已退款原流水置灰 + 删除线 + 印章（全额红章 / 部分琥珀章）。
- **部分退款**：主金额显示**未退款金额**（`amount - refunded_amount`），原价灰色删除线。
- **备注派生**：退款备注 = 原单备注（无后缀，11-15 起移除「（退款）」），分类强制 `退款`/`refund` 且清除 `categoryId`；原单改备注时同步改写关联退款备注（需走完整 snapshot 写入路径）。
- **时间精确到秒**：`datetime-local step=1`；详情「申请退款」按钮 + 「原消费流水」跳转；「取消退款」= 删除退款流水（读时聚合 `refunded_amount` 自动归零）。
- **退款流水图标**：紫色 `Undo2`。

### 4.4 联系人与借款（0026–0033/0039/0041/0042）

**联系人**
- user-global 实体，标签/备注/从交易对手导入、详情往来概览 + 时间轴 + 对账单。
- 借款排行 `GET /contacts/loan-ranks`（**默认跨全部账本**，与成员排行的当前账本口径刻意不同）。
- 详情「记一笔借款」拆为「追加借入/追加借出」两个方向固定弹窗。
- **归档 / 停用（0066）**：两个独立布尔状态 `is_archived` / `is_active`。归档=数据完整保留但移出默认列表、排行与借款建议，可在「已归档」筛选恢复；停用=列表仍可见（带「已停用」标记）但禁止新增借款与新关联，可重新启用。
  - 列表 `GET /contacts?state=active|archived|disabled|all`，默认 `active`（未归档，含已停用）。
  - 4 个幂等动作端点 `POST /contacts/{id}/archive|unarchive|disable|enable`；**不开放通用 PATCH 改状态**，避免与过滤语义脱节。
  - 服务端 `create_loan` / `update_loan` 改绑联系人时经 `_require_contact_available` 拦截，返回 409；历史借款/还款统计不受影响。
  - 前端联系人页加状态筛选 chip、卡片状态徽章与卡片/详情双入口；借款页联系人下拉排除归档与停用（当前已绑定的值保留）。

**借款**
- 借入/借出，关联联系人，金额/时间（秒级）/约定还款/利息/凭证/分期还款，状态四态（未还清/部分还款/已结清/已逾期）。
- **流水口径**：借款金额影响账户余额、出现在交易列表，但 `exclude_from_stats + exclude_from_budget = true`（不污染月度收支）；借出本金=expense(借出)、借入本金=income(借入)、收回借款=income、偿还借款=expense；增删改联动流水。
- **ledger id 约定**：所有 ledger-scoped 落库列一律存 `ledger.id`（内部 UUID），请求/回显用 `external_id`；过滤用 `_ledger_id_filter_values` 兼容历史值。
- **利息**：并入还款流水金额（流水金额 = 本金 + 利息），备注追加「含利息 X 元」（整数不带小数）；**不再生成独立利息流水**；`interest_amount` 保留用于本金结算与徽标。
- **合并还款（0039）**：一次实际收付款 = 一条流水。`loan_payment_groups` 记录合并组，`loan_repayments.group_id` 关联；**组内仅首行持 `tx_sync_id`**；`merged_from_sync_ids_json` 供「查看原单」。本金结算仍按 `loan_repayments` 逐笔归集，两套账互不干扰。
- **按人结算**：Web 展示口径——整人 `principal = Σ amount`、`repaid = Σ total_repaid`，按 happened_at 升序「先借先还」分配到各笔；超还自动冲抵同人其他欠款。**必须基于全量 loans 分组**再过滤，否则还款池被截断会错算。后端 `Loan.status` 刻意不动。
- **优惠参与本金**：实际到账 = 实付 + 优惠 = 借款本金；`sync_loan_from_flow_edit(discount_amount=...)`，write 触发条件含 `"discount_amount" in payload`。
- **附件**：借款凭证 + 还款凭证（`LoanAttachmentEditorField` 保存前 `prepare()` 上传），附到本金/还款流水。
- **金额回写**：`flow_sync` 在 web write 路径同步借款/还款金额（移动端 sync push 改金额暂不回写，已知边界）。

### 4.5 定期存款（0031）

- 存单类型 fixed/installment/cd/structured；利息 = 本金×年利率×月数/12，`add_months` 月末收敛；状态 holding→matured。
- **资金流**：存入 = transfer 活期→`virtual:deposit` 虚拟账户（不进统计、总资产不变）；支取 = 本金转回 + 利息 income；转存 = 利息 income + 本金转回 + 新单划转（三笔流水背书）；结息 = 独立 income + 结息记录。
- 「自动转存」实现为一键 renew（非后台定时任务），到期靠铃铛/列表强提醒后用户点击。

### 4.6 储蓄罐（0034/0036）

- 计划 CRUD + 归档/删除 + 状态筛选；存入记录修改/删除（`PATCH /saving-plans/contributions/{id}`）。
- **金额实时派生**：`_effective_amounts` 从 `read_tx_projection` 读关联流水当前金额，扣减退款，统一覆盖所有写路径（删/批量删/退款/编辑/全量同步）；无关联流水的纯记录用登记金额。
- **双账户转账**：同时选扣款账户+收款账户 → 生成**一笔 transfer**（`from_account_id/to_account_id`），不拆成两条；也可关联既有交易（`tx_sync_id`，无效 404）。
- **修改存入必须同步流水**：取该 tx 最近一条 SyncChange 的**完整快照**走 `snapshot_mutator.update_transaction`，否则只带金额会把备注/分类/账户抹成 NULL。
- 详情弹窗为「目标」口径（目标金额/已存/还差/笔数 + 进度 + 月存推算），与联系人「人」口径刻意不同。

### 4.7 报销与公司（0035/0037）

- 报销项标记「需报销」，公司必填且服务端按档案回填公司名（不信任客户端）；多笔合并须同公司（否则 409）。
- 合并报销生成一笔 income（分类=报销，双 exclude=true），关联项改 reimbursed。
- 公司档案 user-global（跨账本复用），重名 409，删除后历史快照保留。
- 报销详情弹窗用「报销」口径（报销金额/笔数/账单合计/到账账户）。

### 4.8 物品管理（0038/0040/0044/0045/0046）

- 物品含价格/购入时间(秒)/渠道/分类/位置/关联账单/均值计算/目标成本/附件；物品可产生收益与支出流水。
- **成本口径**：净成本 = 购入价 + 支出流水 − 收益流水；daily_avg = 净成本÷持有天数（精确天数），per_day 用自然日；none 返回 null。派生发生在 `_item_out`，非存储字段。
- **多流水关联**：`tx_sync_ids_json` 数组（单值 `tx_sync_id` 保留兼容），多选优先；价格自动汇总关联流水金额（`auto_price`，用户手改后不再覆盖）。
- **交易 ⇄ 物品**：`link_tx_to_item()` 在 create/update 交易后调用；编辑回填用 `fetchFlowLinks` 反查。
- 关联流水显示可读信息（`tx_sync_details`：备注/金额/类型/时间/账户），不再只列裸 syncId。

### 4.9 关联跳转与同步（0042/0043/0046/0047）

- **`flow_links`**：`GET /read/transactions/{tx_sync_id}/links` 统一反查，覆盖 loan/repayment/saving_plan/item/reimbursement/deposit/contact/member；`target = {page, <实体id>}`。**必须带 `ledger_id` 过滤**，否则内部/外部 ID 双值导致链接重复。
- **`flow_sync`**：流水金额变更 → 引用实体同步。策略表：借款本金（快照+状态重算）/ 单笔还款（本金=新金额−利息）/ 合并组（按比例分摊，末行兜底）/ 储蓄罐（实时派生，无需同步）/ 物品流水（快照）/ 报销项（快照）/ 物品 price（用户独立填写，不同步）。
- **双向跳转**：实体 → 流水用 `openTxDetailBySyncId`（先关来源弹窗避免叠加）；流水 → 实体用 URL 参数（可分享/刷新/后退）+ 全局事件兜底（同页面 navigate 相同 URL 时组件不重挂载、ref guard 会拦截，靠事件补救）。
- **`LinkedTxList`**：统一渲染组件，流水已删显示「已删除」；**只读且主行已完整展示时属冗余应删除**（已在借款/存单/报销/借款详情四处清理）。

### 4.10 信用卡分组视图（0048/0050）

- `CreditCardFlowGroups`：viewMode month/cycle 分段切换；每组算 支出/收入/结余（income/refund=收入，expense=支出，transfer 转入该卡=还款收入）；默认展开最近一组。
- 账期切分：有 `billing_day` 时日号≤账单日归本月账单，之后归下月；标签带年份（`2023 账期 05-21 - 06-20`）。无账单日回退自然月并提示。
- **全量加载**：`loadAllAccountTxs` 循环分页（limit=1000，最多 50 轮）直到取完——一次性大 limit 仍是上限，早期流水会被截断。
- 账单日/还款日：前端传 ISO 字符串 `2020-01-{dd}T00:00:00`（日号放日期位），兼容历史脏数据 `1970-01-01T00:00:NN` 的秒位。

### 4.11 图标体系与 UI 统一（0049/0058）

- GitHub 调研结论：项目已集成 lucide-react，是最优解；v0.462→v1.73（`Github` 品牌图标被移除→改 `GitBranch`）。
- `sectionIcons.ts`：27 个 section 各配 Lucide 图标 + 品牌 hex 色；导航/命令面板/移动端底栏/页面头统一接入，命令面板补齐 6 个此前缺失入口。
- 特殊流水图标/颜色可自定义（7 类：转账/借款四态/退款/存款利息），存 `appearance.flow_icons` + `flow_icon_colors`，设置页可视化编辑。
- 银行品牌徽章：18 家银行关键词→简称+品牌色；账户图标/背景 `icon_key/icon_type/background_key` 全链路透传。
- 时间选择器：引入 react-day-picker 替换原生 date input（交易筛选起止日期、储蓄罐截止日期）。

### 4.12 账户页与弹窗范式（0051–0056）

- 资产卡片高度最终 `aspectRatio: 2/1`；完整卡号存储（`card_last_four` 扩至 23 位，不再 slice(-4)）。
- 信用卡初始金额入口：Label 统一「初始金额」，欠款/溢缴 Select 编辑态也显示；用 `initial_balance` 符号推导欠款/溢缴（不用当前余额）；类型切换离开信用卡时按状态还原符号。
- **三段式详情弹窗模板**（联系人/成员/报销/储蓄罐/存单统一）：
  - `DialogContent` = `flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0`
  - 头部固定（渐变底 + 大图标 + 名称 + 副标题）
  - 中间 `min-h-0 flex-1 overflow-y-auto` 独立滚动
  - 底栏固定（border-t + bg-muted/20 + size="sm" 按钮）
- Radix Dialog 的 Escape 需焦点在弹窗内，E2E 用 `button[aria-label="Close"]` 关闭更稳。

---

## 5. 核心口径约定

### 金额口径（务必区分）

| 场景 | 口径 |
|---|---|
| 支出优惠 | `amount`=实付；优惠是元数据，不冲减余额 |
| 成员 | 分摊金额（不是整笔） |
| 报销 | 报销金额 |
| 联系人 | 借还口径（应收/应付） |
| 储蓄罐 | 目标口径（目标/已存/还差/笔数） |
| 部分退款 | 主金额=未退款金额 |
| 借款有还款 | 主金额=剩余金额（按人结算时超还冲抵） |
| 借款利息 | 并入还款流水金额，不单独成流水 |

### 其他约定

- **图标优先级**：refund > transfer > loan > category
- **Token 安全**：禁止 localStorage、禁止 URL 参数存令牌；PAT 明文只返回一次
- **时间**：前端传 ISO UTC，写接口 payload 经 `model_dump(mode="json")` 后 datetime 变字符串，所有 `_utc()` 类转换必须能接字符串
- **ledger id**：REST 落库存内部 `ledger.id`，请求/回显用 `external_id`
- **改后端必须重启**：uvicorn 在本环境 `--reload` 不生效，判断方法是 `curl /openapi.json` 看 schema 是否更新

---

## 6. 依赖变更

| 依赖 | 变更 | 用途 |
|---|---|---|
| `lucide-react` | 0.462.0 → **1.73.0**（三个 workspace 包统一） | UI 图标（v1 移除品牌图标与 UMD 构建） |
| `react-day-picker` | **新增 ^10.0.2** | 日期选择器 |
| `date-fns` | **新增 ^4.4.0** | 日期格式化与 locale |

未引入其他新依赖——图标方案复用已有 lucide + Material Symbols + 自制 SVG，避免跨端不一致。

---

## 7. 测试与 QA 体系

### 单元测试

- **后端 pytest**：`tests/` 下新增 `test_member_splits.py`、`test_refund.py`、`test_refund_api.py`、`test_contacts_loans.py`、`test_deposits.py`、`test_reimbursements.py`、`test_saving_plans.py`、`test_items.py`、`test_flow_links.py`、`test_linked_tx.py`、`test_party_profile.py`、`test_flow_sync.py` 等。累计 600+ 用例通过。
- **前端 Vitest**：`apps/web/src/*.test.ts` 17 个文件、111 用例；Windows 高负载下需 `--no-file-parallelism` 避免沙箱 temp 缓存 EPERM 与偶发漏收集 `i18n.test.ts`。

### E2E 脚本（`scripts/qa/`）

覆盖：成员跳转、报销详情、储蓄罐转账/编辑、信用卡分组/全量加载、账户修复、借款利息/合并还款/单多笔一致/删除还款/多笔组跳转、物品多关联、flow_sync、flow_links、section_icons、contact_timeline_jump 等 20+ 个 Playwright 脚本。

**通用验证手法**：
1. 「在线备份 → 实测 → 回滚」三步不污染用户账本：`sqlite3.connect(src).backup(dst)` → 跑真实 HTTP → 停服务/删 WAL-SHM/覆盖回/重启。
2. 无 vitest 时可用 TypeScript `transpileModule` + `new Function` 直接抽取源码纯函数跑断言。

---

## 8. 数据修复与导入脚本

| 脚本 | 用途 |
|---|---|
| `seed_test_data.py` | 幂等导入演示数据（note 以 `[演示]` 开头） |
| `backfill_refund_notes.py` | 回填历史退款备注/分类（同步 projection + 最新 sync_change） |
| `import_*.py`（10+） | 各联系人借款数据导入，统一走 `loan_importer.py` |
| `fix_imported_loan_flows.py` / `upgrade_import_scripts.py` | 现有借款流水重整 |
| `merge_loan_interest_flows.py` | 9 条独立利息流水并入还款流水 |
| `fix_loan_principal_to_received.py` | 优惠场景本金=实付+优惠修正 |
| 数据备份 | `C:/Users/Admin/Desktop/BeeCount-Backup-*`（WAL 模式务必用 sqlite backup API，cp 会漏数据） |

---

## 9. 已知边界与遗留项

1. **转账手续费/优惠**：`transfer_adjustments.py` 已定义但未接入写入链路（fast path 只 emit 一条 SyncChange），不会自动生成金融服务费/优惠减免流水。
2. **移动端 sync push 改流水金额**：不反向同步借款等业务实体（同步只挂 web write 路径）。
3. **按人结算**：仅 Web 展示口径，后端 `Loan.status` 未改（避免破坏 API 契约）。
4. **自动转存**：一键 renew 而非后台定时任务。
5. **零存整取**：按普通单本金计息，未逐月追加建模。
6. **删除流水不级联清理**业务实体引用字段 → 关联渲染需优雅显示「已删除」（存量有 1 条存单引用悬空）。
7. **`src/routers/ai/test_provider.py`**：FastAPI 路由函数被 pytest 误收集（缺 fixture），属预存问题。
8. **`tests/test_mcp_local_files.py`**：Windows 无 `os.O_NONBLOCK`/`mkfifo`，7 项失败属平台问题。
9. **前端根 `node_modules` 为空**：tsc/vite 用 `apps/web/node_modules/.bin/` 下可执行文件。
10. **Vitest 偶发漏收集**：Windows 高负载 collect 竞态，单独跑稳定通过。

---

## 10. 环境踩坑合集

| 问题 | 解法 |
|---|---|
| 服务反复「打不开」 | 用 Bash `run_in_background=true` 托管；`nohup`/`setsid`/`&` 会随 shell 退出被回收 |
| 本地 curl 502 | 加 `--noproxy '*'`（环境有 HTTP_PROXY） |
| uvicorn `--reload` 不生效 | 手动停后台任务重启；`curl /openapi.json` 验证 |
| Vite dev 白屏（Invalid hook call） | PWA Service Worker 缓存旧 React → 仅 PROD 注册 SW + dev 插件注销旧 SW |
| PowerShell `Start-Process` 崩溃 | 系统环境同时有 `http_proxy`/`HTTP_PROXY` 两套键；改用 Bash |
| 构建被 safe-delete 拦截 | `vite build --emptyOutDir=false` 或先把旧 dist 移出仓库 |
| 测试写临时文件 EPERM | 把 `TEMP/TMP/TMPDIR/PYTEST_DEBUG_TEMPROOT` 指向工作区内目录 |
| Radix Dialog Escape 关不掉 | E2E 用 `button[aria-label="Close"]` |
| 同页面跳转弹窗不自动开 | URL 参数 ref guard 拦截 → 额外全局事件通道兜底 |
| 改 vite.config 触发批量删 `.vite/deps` 被拦 | 停 Vite → 重命名备份 `.vite`/`dist` → 重启 |


## 锁账功能（0067）

用户级全局锁（交易按账本隔离，但账户初始金额是 user-global，故锁配置放 `users` 表）：

- **配置**：`users.is_locked` 开关 + `users.lock_date`（销账截止时刻）。解锁只关开关、不清日期。
- **拦截层**：`src/lock_guard.py` 守卫挂在 `projection.upsert_tx / delete_tx / upsert_account` 三个底层投影函数。所有写交易路径（write 端点、sync push、借款/储蓄罐/报销/物品/定期业务路由、import、MCP）最终都经过投影，一次覆盖。
- **规则**：`happened_at <= lock_date` 的交易禁止新建/改/删 → HTTP 423；锁定期之后的交易不受限；锁定期内禁止改动已有账户初始金额（新建账户不受限）；解锁后立即恢复。
- **API**：`GET/PATCH /profile/lock`（web/app write scope），开启锁账必须已有日期（400）。
- **前端**：`LockContext`（订阅 `profile_change` 跨标签同步）；设置页「锁账管理」；交易行/详情按日期禁用编辑/删除/退款/复制；账户初始金额输入禁用；交易页锁账横幅。
- **关键文件**：`src/lock_guard.py`、`src/projection.py`、`src/routers/profile.py`、`frontend/apps/web/src/context/LockContext.tsx`、`frontend/apps/web/src/components/sections/LockSettingsSection.tsx`、`scripts/qa/e2e_lock_book.py`。

11. 关键文件索引

**概念/设计追溯**：代码注释中以功能编号（0024/0043/0055/0056 等）引用设计文档，可按编号在本日志与本文档中对照。

**总入口**：[`DEVELOPMENT.md`](./DEVELOPMENT.md)（架构/API/模型/前后端/测试/部署） · [`FEATURE_DEVELOPMENT.md`](./FEATURE_DEVELOPMENT.md)（功能清单） · [`APP_DEVELOPMENT.md`](./APP_DEVELOPMENT.md)（客户端对接） · [`SYNC_ARCHITECTURE.md`](./SYNC_ARCHITECTURE.md)（同步架构）

> 本文档基于 2026-11-14 至 11-20 工作日志整理，配套代码均在本地副本 `BeeCount-Cloud/`。

---
*文档生成日期：2026-11-20*

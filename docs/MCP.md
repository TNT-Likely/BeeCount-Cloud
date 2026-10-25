# BeeCount Cloud MCP Server

让 LLM(Claude Desktop / Cursor / Cline 等)通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 直接读写你的 BeeCount 账本数据。

---

## 是什么

MCP 是 Anthropic 推出的 LLM-工具集成协议。BeeCount Cloud 内置一个 MCP server,把账本能力暴露成 55 个 tool:

- **26 个读权限工具**（原有查询工具及下文工作流工具）:`list_ledgers` / `list_transactions` / `list_categories` / `list_accounts` / `list_tags` / `list_budgets` / `get_ledger_stats` / `get_analytics_summary` / `search` / `get_transaction` / `get_active_ledger`
- **29 个写权限工具**（原有记账工具及下文工作流工具）:`upload_attachment` / `create_transaction` / `create_transactions`(批量导入,一次提交多笔)/ `update_transaction` / `delete_transaction`(需二次确认)/ `create_category` / `update_budget` / `parse_and_create_from_text`(让 BeeCount AI 解析自然语言)

跟 LLM 聊天时可以这样说:

> "上个月我在外卖上花了多少?分类排名前三是什么?"
>
> "把昨天下午 3 点星巴克那笔 38 块改成 42 块,顺便加个 #咖啡 tag。"
>
> "我说一句话你帮我记一笔:刚才在便利店买了瓶水 3 块 5。"

LLM 自动调对应 tool,你不用打开 BeeCount。MCP 创建的交易会自动打上 `MCP` 标签,跟手机端"AI 记账"区分。

---

## 启用步骤

### 1. 在 BeeCount Cloud Web 创建 PAT

1. 登录 BeeCount Cloud Web Console
2. 头像下拉 → **设置 → 开发者**(`/app/settings/developer`)
3. 点 **新建 Token**:
   - **名称**:给这个 token 起个名,例如 `Claude Desktop`(后续在列表识别用)
   - **授权范围**:
     - `mcp:read` — LLM 只能查数据,**推荐先用这个**
     - `mcp:read + mcp:write` — LLM 可以新增/修改/删除交易等。**写权限请谨慎授权**
   - **有效期**:30 / 90 / 180 / 365 天 或 永不过期(默认 90)
4. **立即复制 token**!明文 `bcmcp_…` 只显示一次,关闭弹窗后无法再次查看(只剩前缀)

### 2. 在 LLM 客户端配置

BeeCount Cloud 的 MCP 用 **Streamable HTTP**(单端点),端点是部署地址加 `/api/v1/mcp`。

> 下面统一把占位符替换成真实值:
>
> - `https://your-domain.com` → BeeCount Cloud 部署地址(也可以是 `http://192.168.x.x:8080` 等内网/Tailscale 地址)
> - `bcmcp_xxx...` → 上一步生成的 PAT 明文

**推荐:原生支持 Streamable HTTP 的客户端直连,无需 `mcp-remote`。** 例如 Claude Code:

```bash
claude mcp add --transport http beecount https://your-domain.com/api/v1/mcp \
  --header "Authorization: Bearer bcmcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

新开一个会话,`claude mcp get beecount` 显示 `✔ Connected` 即成。其它带内置 HTTP MCP 客户端的 agent 同理:直接填端点 URL `https://your-domain.com/api/v1/mcp` + 请求头 `Authorization: Bearer bcmcp_…` 即可。

**仅支持 stdio 的客户端(Claude Desktop / Cursor / Cline)** 用 `mcp-remote`(npm)桥接到同一个 HTTP 端点 —— **URL 用 `/api/v1/mcp`、不再带 `/sse`**,`mcp-remote` 会自动协商 Streamable HTTP:

#### Claude Desktop

配置文件:
- macOS:`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows:`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "beecount": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-domain.com/api/v1/mcp",
        "--header",
        "Authorization:Bearer bcmcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      ]
    }
  }
}
```

> macOS 上 Claude Desktop 默认不读用户 shell PATH;如果 `npx` 找不到,把 `command` 改成 `/opt/homebrew/bin/npx`(Apple Silicon)或 `/usr/local/bin/npx`(Intel)。

完全退出 Claude Desktop(`Cmd+Q`)再启动,左下角出现 🔌 "BeeCount" 即连上。

#### Cursor

`~/.cursor/mcp.json`(或 Settings → Features → MCP UI):

```json
{
  "mcpServers": {
    "beecount": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-domain.com/api/v1/mcp",
        "--header",
        "Authorization:Bearer bcmcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      ]
    }
  }
}
```

重启 Cursor。这个文件**不要**提交到 git。

#### Cline (VS Code)

VS Code → Cline 图标 → 右上角 `…` → **Edit MCP Settings**:

```json
{
  "mcpServers": {
    "beecount": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-domain.com/api/v1/mcp",
        "--header",
        "Authorization:Bearer bcmcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      ],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

可以把 read tool 放进 `autoApprove` 减少弹窗:`["list_ledgers", "list_transactions", "list_categories", "list_accounts", "list_tags", "list_budgets", "get_active_ledger", "get_transaction", "get_ledger_stats", "get_analytics_summary", "search"]`。**write tool 别放**,UI 确认是最后一道防线。

### 3. 验证

LLM 客户端连上之后:

- 问 LLM "我的账本有哪些?" → 它会调 `list_ledgers`
- 问 "本月支出多少?" → 它会调 `get_analytics_summary`

---

## 服务端 endpoint

| | |
|---|---|
| MCP 端点(Streamable HTTP) | `https://your-domain.com/api/v1/mcp` |
| 鉴权 | `Authorization: Bearer bcmcp_…`(PAT) |

> 早期版本用的老式 SSE 端点(`/api/v1/mcp/sse` + `/api/v1/mcp/messages/`)已被 Streamable HTTP 取代,不再提供。

PAT 跟 access token 严格分流:**PAT 只能用在 `/api/v1/mcp`**,所有其他 API 接收 PAT 都返回 403。同理 access token 不能用来调 MCP endpoint。

---

## 安全模型

| 维度 | 措施 |
|---|---|
| Token 存储 | `sha256` 哈希 + `hmac.compare_digest` 常数时间比较,**明文只在创建时返一次** |
| Token 删除 | 一键物理删除,该行从 DB 移除,token 立即失效 |
| Token 过期 | 创建时可设过期日,过期后 401 |
| Scope 分离 | `mcp:read` / `mcp:write` 独立勾选;只 read 不会被升权成 write |
| 危险操作 | `delete_transaction` 必须传 `confirm=true`,首次调用返"待确认"占位符,LLM 必须跟用户确认后再调一次 |
| 写权限隔离 | PAT 不能调常规 `/api/v1/*` endpoint,只能调 MCP tool |
| 审计 | 每次 PAT 使用都 bump `last_used_at` + `last_used_ip`,Web 设置页可看 |

**如果 PAT 泄露**:立即去 Web 设置页删除该 token;同时检查 `last_used_ip` 是否有异常来源。

---

## Tool 速查表

### Read tools(需要 `mcp:read`)

| Tool | 用途 | 关键参数 |
|---|---|---|
| `list_ledgers` | 列所有账本 | — |
| `get_active_ledger` | 兼容默认账本查询；多账本先通过上下文选择 | — |
| `list_transactions` | 查交易,多维筛选 | date_from/to, category, account, q, limit |
| `get_transaction` | 单条交易详情 | sync_id |
| `list_categories` | 列分类 | kind |
| `list_accounts` | 列账户 | account_type |
| `list_tags` | 列标签 | — |
| `list_budgets` | 列预算 + 当月进度 | ledger_id |
| `get_ledger_stats` | 账本统计 | ledger_id |
| `get_analytics_summary` | 收入/支出/Top 分类 | scope (month\|year\|all), period |
| `search` | 全文模糊搜 | q, limit |

### Write tools(需要 `mcp:write`)

| Tool | 用途 | 关键参数 |
|---|---|---|
| `upload_attachment` | 上传小票/附件，返回 file_id | file_name, content_base64, ledger_id, mime_type |
| `create_transaction` | 新建交易，可附小票 | amount, tx_type, category, account, happened_at, note, tags, attachments |
| `create_transactions` | **批量**新建交易(导入正解,一次提交多笔) | transactions(list), ledger_id |
| `update_transaction` | 改交易/附件 | sync_id + 待改字段，attachments |
| `delete_transaction` | 删交易(**二次确认**) | sync_id, confirm |
| `create_category` | 新建分类 | name, kind, parent_name |
| `update_budget` | 改预算金额 | budget_id, amount |
| `parse_and_create_from_text` | 自然语言记账 | text |

---


## 完整记账与 Agent 工作流

当前共 **55 个工具：26 个读权限工具、29 个写权限工具**。原有工具保持可用，新增能力按以下场景组织：

| 场景 | 工具 |
|---|---|
| 上下文与成员 | `get_bookkeeping_context`、`list_ledger_members` |
| 账户、分类、标签、预算、账本管理 | `manage_account`、`manage_category`、`manage_tag`、`manage_budget`、`manage_ledger` |
| 可靠执行与增量续接 | `get_operation`、`list_operations`、`retry_operation`、`list_changes` |
| 资产与分析 | `get_account_balances`、`get_net_worth_history`、`analyze_transactions` |
| 小票与草稿 | `list_attachments`、`read_attachment`、`parse_receipt`、`preview_text_transactions` |
| 文件与导入 | `preview_import_file`、`preview_import`、`execute_import_plan`、`undo_import_plan`、`export_transactions` |
| 来源与对账 | `link_transaction_source`、`unlink_transaction_source`、`list_transaction_sources`、`reconcile_account`、`save_reconciliation_checkpoint` |
| 跨 Agent 协作 | `list_workflow_records`、`get_workflow_record`、`save_review_item`、`save_classification_rule`、`apply_classification_rule`、`execute_classification_preview` |
| Cloud 周期记账 | `manage_recurring_rule`、`run_due_recurring_rules` |

### 选择账本与完整转账

先调用 `get_bookkeeping_context`：多账本时返回候选，不把最早创建的账本当作用户的当前选择。指定 `ledger_id` 后，返回该账本所有者的分类、账户和标签 ID、成员角色、币种、月份起始日、Cloud 时区及 PAT scope。`list_categories/accounts/tags` 也接受 `ledger_id`，用于读取共享账本字典。所有工具都重新检查当前成员权限；即使用户是管理员，MCP 也不因此获得其他用户账本的访问范围。

完整转账在单笔、批量和编辑接口均支持 `from_account_id` / `to_account_id`（也可用 `from_account` / `to_account` 名称）。两账户必须不同且币种相同。普通交易支持 `category_id` / `account_id`；名称歧义时使用 ID，不擅自选同名项。`exclude_from_stats` 与 `exclude_from_budget` 独立传递。批量工具同样支持逐笔 `attachments`。

```json
{"name":"create_transaction","arguments":{"ledger_id":"账本ID","tx_type":"transfer","amount":2000,"from_account_id":"转出账户ID","to_account_id":"转入账户ID","idempotency_key":"statement:transfer:0001"}}
```

外币交易可传 `native_amount` 保存实际折账本本位币结算额。修改已有外币金额时，默认保留原结算比例；明确传 `native_amount` 或新币种时使用新值。净值按原币种提供历史；只有全部币种汇率齐备时才返回折算合计，并明确这是当前配置汇率的估值，不是历史汇率收益率。账户余额默认只统计自己的账本；显式选择共享账本或 `scope=visible` 时返回范围说明。历史期初余额无生效日期的既有账户会明确标注这一限制。

### 分页、并发与安全重试

`list_transactions` 返回 `has_more`、`next_cursor` 和 `revision`，以时间 + sync ID 稳定排序。游标绑定用户、账本和全部筛选条件；数据发生变更时返回 `CURSOR_INVALIDATED`，应重新开始读取。日期查询按所选 `time_zone` 的当地日历解释；`date_to` 是日期时包含整天，是时间戳时作为独占上界。`tx_type`、`category_id`、`account_id`、`tag_id` 支持精确筛选。

创建交易时提供稳定 `idempotency_key`。同键同请求回放原结果，同键不同内容返回 `IDEMPOTENCY_CONFLICT`。服务端先保存包含时间和折算额的标准化计划，再执行写入；重试不重新生成这些值。单次批量最多 200 笔，每块最多 50 笔独立提交；失败不回滚已完成块。结果包含 `operation_id`、逐步结果、失败步骤及可重试标记。`get_operation` 查状态，`retry_operation` 续接，响应完全丢失时可用 `list_operations` 查找。内部幂等回执长期保留，支持进程重启后的恢复。

编辑和删除交易可传读取结果中的 `expected_revision`；冲突返回 `REVISION_CONFLICT`，不会覆盖后续修改。`list_changes` 提供含删除标记的签名增量游标，用于下一次会话续接。

### 原文件导入、来源与撤销

1. 使用 `upload_attachment`（本地适配器可传文件路径）上传原 CSV / TSV / XLSX。
2. `preview_import_file(file_id, mapping, source_system, source_id_column)` 使用现有解析器，返回字段映射、错误、来源文件 SHA / 行号和持久化导入计划。每份计划最多 200 行；大文件通过 `next_offset` 分段。文件最多 16 MiB / 50000 行，XLSX 解压内容最多 128 MiB。模型已整理成结构化交易时直接用 `preview_import`。
3. 核对新增、已存在、疑似重复、错误及账户余额影响。已有来源编号用于精确识别；同日同金额只提示疑似重复，不自动合并。缺失分类或账户时先用管理工具创建，再重新预览。修改计划需要 `expected_revision`；执行后不能改写原计划。
4. 用户确认后，`execute_import_plan(plan_id, expected_revision, confirm=true)` 执行保存的预览。来源身份在写入前由数据库唯一约束预留，避免两个计划重复导入。执行中断可继续同一计划或 operation。
5. `undo_import_plan` 只撤销该计划新建且未被后续修改的交易；已存在的关联交易不会被删除。遇到修改冲突时报告，不覆盖用户数据。移除错误来源关联用 `unlink_transaction_source`，不会删除交易本身。
6. `reconcile_account` 按指定时点列出期初、收支、转入转出、计算余额、实际余额及差额；不自动生成调账交易。用户确认后保存按账户和来源区分的检查点及覆盖范围。

一笔交易可关联多个来源凭证。来源关系、导入计划、待核实项、检查点和规则均保存在 Cloud；换电脑或 Agent 后可通过工作流记录工具继续。`save_review_item` 保存问题与用户答复；分类规则区分推测与用户确认，并支持例外。先调用 `apply_classification_rule` 保存目标集和修改前后预览，用户确认后用 `execute_classification_preview` 执行；每个目标都有版本保护。

### 小票、资源与周期规则

`read_attachment` 返回原图或小文本；大文件和二进制返回 MCP Resource 链接。`parse_receipt` 使用用户已配置的视觉服务商，仅生成草稿；确认后创建交易时继续附上原 `file_id`。`preview_text_transactions` 同样仅生成草稿。未配置相应 AI 服务商时返回明确错误，客户端也可自行识别图片后调用普通交易工具。

导出 CSV 返回 `beecount://exports/{id}`，原附件使用 `beecount://attachments/{file_id}`。每次 Resource 读取均验证 PAT scope 和当前账本权限。`export_transactions` 上限 50000 笔，保留原币、本位币金额、ID 和排除标记，并处理电子表格公式注入；不静默截断。本地 stdio 适配器同时转发工具、资源和 Prompt。内置 `monthly_review` 与 `import_statement` Prompt 提供只读月报及确认后导入流程。

Cloud 周期规则支持日、周、月、年、间隔、结束时间、暂停和恢复。通过 `manage_recurring_rule` 预览接下来五次时间，确认后保存。月末 / 闰年按原始锚点夹取有效日期；夏令时缺失时刻跳过，重复时刻由 `dst_policy=skip|first|second` 明确决定。默认不补录历史，`allow_backfill=true` 才从过去补齐；每轮按到期规则推进，重启后继续，每次发生使用稳定幂等键。`MCP_RECURRING_ENABLED=false` 可关闭后台调度。规则执行身份固定为创建者，每次执行重查账号和角色权限。规则由 Cloud 独占执行，生成普通交易同步到 App；不要在 App 再建立同一业务的重复规则。

数据库需升级至 Alembic `0021_mcp_workflows`。新增表保存 operation、版本化工作流和来源关联；不改变既有交易同步格式。工具行为标注区分只读、可能修改、幂等和外部 AI 调用；scope 与账本角色仍由服务端强制校验。

## 小票与交易附件

先调用 `upload_attachment`，再把返回的 `file_id` 列表传给交易工具。附件和交易必须属于同一账本：

```json
{"name":"upload_attachment","arguments":{"ledger_id":"你的账本ID","file_name":"receipt.png","mime_type":"image/png","content_base64":"客户端读取图片后生成的标准Base64"}}
```

```json
{"name":"create_transaction","arguments":{"ledger_id":"你的账本ID","amount":788,"note":"便利店小票","attachments":["上传返回的file_id"]}}
```

`attachments` 是按展示顺序排列的文件 ID 列表。服务端从上传记录生成名称、大小、SHA256 和顺序，App 同步后与 Web 都能查看原图。相同账本内重复上传相同内容会复用文件 ID；不能引用其他账本的文件，列表不能包含重复 ID。

编辑语义：

- 不传 `attachments` 或传 `null`：保留原有附件。
- 传完整 ID 列表：替换原有附件，可调整顺序。
- 传 `[]`：移除全部附件。
- 追加：先 `get_transaction` 读取已有附件的 `cloudFileId`，把新 ID 加在后面再提交。

上传需要 `mcp:write`，使用现有附件大小限制（默认 64 MiB）。保留原始小票，不要为了工具参数长度而缩图、转码或分段搬运 Base64。HTTP 直连时 Base64 不带 `data:image/...;base64,` 前缀。上传工具不会创建交易；后续交易提交失败时，已上传 ID 可用于重试。上传内容和文件名不写入 MCP 调用历史摘要。此功能不执行 OCR，也不自动提取消费税。

### 客户端本地文件

需要让模型直接传文件路径时，使用仓库的 **本地 stdio MCP 适配器** `scripts/mcp_local_files.py`。它在客户端电脑运行，保留全部 Cloud 工具，将同名 `upload_attachment` 的输入改为 `file_path`；程序读取并上传原始字节，模型不接触 Base64。远程 Cloud 仍执行 PAT 鉴权、账本权限、大小校验和去重。

Claude Code 示例（需要 Python 和本项目的 `mcp>=1.27,<2` 依赖）：

```sh
claude mcp add --transport stdio --scope user \
  --env BEECOUNT_MCP_PAT=bcmcp_xxx beecount -- \
  /absolute/path/to/python /absolute/path/to/BeeCount-Cloud/scripts/mcp_local_files.py \
  --endpoint https://your-domain.com/api/v1/mcp \
  --allow-dir /absolute/path/to/receipts
```

重启客户端后，直接调用：

```json
{"name":"upload_attachment","arguments":{"ledger_id":"你的账本ID","file_path":"/absolute/path/to/receipts/receipt.png"}}
```

返回的 `file_id` 继续传给 `create_transaction` / `update_transaction`。`--allow-dir` 可重复配置，适配器拒绝目录外文件、符号链接越界、空文件和特殊文件。路径属于客户端电脑；Docker 中的 Cloud 不需要挂载用户的小票目录。HTTP 直连工具继续接受 `content_base64`，不会读取服务器任意路径。仅把 `/Users/.../receipt.png` 或下载 URL 传给 Cloud 不会上传文件。

需要脚本批量上传时，也可以使用独立客户端上传脚本：

```sh
# BEECOUNT_MCP_PAT 通过环境提供，不放在命令参数中。
python scripts/mcp_upload_receipt.py \
  --endpoint https://your-domain.com/api/v1/mcp \
  --ledger-id your-ledger-id ./receipt.png ./receipt-2.jpg
```

脚本通过实际 MCP 协议上传本地图片，输出可直接用于交易工具的 `attachments` 列表。执行脚本后仍需创建或编辑交易；已有客户端能处理文件时也可直接使用上传工具。`create_transactions` 批量工具也支持逐笔附件。

## 交易时间与 CSV 时区

`create_transaction`、`create_transactions` 和 `update_transaction` 使用相同的时间规则：

- `happened_at` 带 `Z` 或 `+08:00` 等明确偏移时，保留其实际瞬间并转换为 UTC 存储。
- 无偏移的时间优先使用 Cloud 的 `SCHEDULER_TIMEZONE`，其次使用 `TZ`。官方 Docker 镜像默认 `TZ=Asia/Shanghai`。
- Cloud 两项均未配置时，调用者需在确认来源后传 `time_zone`（例如 `Asia/Shanghai`），或在时间中提供实际偏移；不会默认猜成 UTC。
- 仅日期按所选时区的当天零点解释；未传 `happened_at` 则使用当前时间。
- 无效时区、夏令时不存在或有歧义的本地时间会报错；批量导入在写入前验证全部时间。

例如，上海时区下导入 CSV 的 `2026-05-23 21:28:52`，会存为
`2026-05-23T13:28:52+00:00`；Web 和 App 在上海时区显示仍是 **21:28:52**。
MCP 读回的交易时间也带明确的 UTC 偏移，避免客户端再次猜测。

如果 CSV 来源时区与 Cloud 不同，应传每笔时间的实际偏移，例如
`2026-05-23T21:28:52-04:00`。`time_zone` 只在 Cloud 未配置时区时作为后备。
可向 Agent 明确说明：「保留 CSV 的本地时间，遵循 Cloud 时区；不要给本地时间补 `Z`。」
Agent 若已经错误添加 `Z`，服务端无法识别原始时间是否来自本地，仍按显式 UTC 处理。

本修复只影响后续写入，不自动平移历史交易。旧版 MCP 把无偏移时间视为 UTC，
单独增加 Docker 的 `TZ` 无法修复旧版写入逻辑，需要更新到包含本修复的版本。

## 故障排查

**问:LLM 客户端连不上**

- 检查 PAT 是不是 `bcmcp_…` 开头(以及前缀 14 位),粘贴时别带空格
- 测试 endpoint(应返回 200 + `serverInfo`,不是 401/403/404):
  ```bash
  curl -X POST https://your-domain.com/api/v1/mcp \
    -H "Authorization: Bearer bcmcp_…" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
  ```
- 看 server log 是否有 401 错误 — 如果是 "Token expired" 检查 PAT 有效期;如果是 "Invalid token" 检查 token 拼写

**问:LLM 调 tool 报 "PAT missing required scope: mcp:write"**

- 创建 token 时没勾"读+写"。回 Web 设置页编辑该 token、勾上写权限即可(无需重建)
- 注意:编辑后需要**重连 LLM 客户端**才能生效 — 客户端会缓存首次拿到的 scope / 工具列表

**问:`delete_transaction` 总是返"confirmation_required"**

- 这是设计如此 — LLM 第一次调时只是预演,客户端会回话"确定删除吗",你说"是的删除"后 LLM 才带 `confirm=true` 再调一次

**问:`parse_and_create_from_text` 报 `AI_NO_CHAT_PROVIDER`**

- 需要先在 Web 设置页配 AI provider(GLM / OpenAI 等)。这个 tool 是让 BeeCount 自己的 AI 解析,跟 LLM 客户端的 AI 不同

**问:多账本时 MCP 调哪个**

- 每次会话先用 `get_bookkeeping_context` 获取候选，让用户选择后显式传 `ledger_id`。
- 多账本写请求省略 `ledger_id` 会返回 `ledger_required`，不会自动选择。`get_active_ledger` 仅保留兼容查询行为，不代表 App 当前选中的账本。

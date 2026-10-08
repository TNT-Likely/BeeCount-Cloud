# BeeCount Cloud MCP Server

让 LLM(Claude Desktop / Cursor / Cline 等)通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 直接读写你的 BeeCount 账本数据。

---

## 是什么

MCP 是 Anthropic 推出的 LLM-工具集成协议。BeeCount Cloud 内置一个 MCP server,把账本能力暴露成 19 个 tool:

- **11 个 read tool**:`list_ledgers` / `list_transactions` / `list_categories` / `list_accounts` / `list_tags` / `list_budgets` / `get_ledger_stats` / `get_analytics_summary` / `search` / `get_transaction` / `get_active_ledger`
- **8 个 write tool**:`upload_attachment` / `create_transaction` / `create_transactions`(批量导入,一次提交多笔)/ `update_transaction` / `delete_transaction`(需二次确认)/ `create_category` / `update_budget` / `parse_and_create_from_text`(让 BeeCount AI 解析自然语言)

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
| `get_active_ledger` | 当前默认账本 | — |
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

脚本通过实际 MCP 协议上传本地图片，输出可直接用于交易工具的 `attachments` 列表。执行脚本后仍需创建或编辑交易；已有客户端能处理文件时也可直接使用上传工具。`create_transactions` 批量工具暂不支持附件。

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

- 没传 `ledger_id` → 默认用**最早创建**的账本
- 建议每次会话先让 LLM `list_ledgers` 列出选项,然后后续调用都带 `ledger_id`

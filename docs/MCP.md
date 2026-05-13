# BeeCount Cloud MCP Server

让 LLM(Claude Desktop / Cursor / Cline 等)通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 直接读写你的 BeeCount 账本数据。

---

## 是什么

MCP 是 Anthropic 推出的 LLM-工具集成协议。BeeCount Cloud 内置一个 MCP server,把账本能力暴露成 17 个 tool:

- **11 个 read tool**:`list_ledgers` / `list_transactions` / `list_categories` / `list_accounts` / `list_tags` / `list_budgets` / `get_ledger_stats` / `get_analytics_summary` / `search` / `get_transaction` / `get_active_ledger`
- **6 个 write tool**:`create_transaction` / `update_transaction` / `delete_transaction`(需二次确认)/ `create_category` / `update_budget` / `parse_and_create_from_text`(让 BeeCount AI 解析自然语言)

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

> 三个客户端用 `mcp-remote` (npm) 把 stdio 桥到 BeeCount Cloud 的 SSE endpoint。统一把下面占位符替换成真实值:
>
> - `https://your-domain.com` → BeeCount Cloud 部署地址(也可以是 `http://192.168.x.x:8080` 等内网/Tailscale 地址)
> - `bcmcp_xxx...` → 上一步生成的 PAT 明文

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
        "https://your-domain.com/api/v1/mcp/sse",
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
        "https://your-domain.com/api/v1/mcp/sse",
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
        "https://your-domain.com/api/v1/mcp/sse",
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
| SSE 连接 | `https://your-domain.com/api/v1/mcp/sse` |
| 消息回信道 | `https://your-domain.com/api/v1/mcp/messages/` |
| 鉴权 | `Authorization: Bearer bcmcp_…`(PAT) |

PAT 跟 access token 严格分流:**PAT 只能用在 `/api/v1/mcp/*`**,所有其他 API 接收 PAT 都返回 403。同理 access token 不能用来调 MCP endpoint。

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
| `create_transaction` | 新建交易 | amount, tx_type, category, account, happened_at, note, tags |
| `update_transaction` | 改交易 | sync_id + 待改字段 |
| `delete_transaction` | 删交易(**二次确认**) | sync_id, confirm |
| `create_category` | 新建分类 | name, kind, parent_name |
| `update_budget` | 改预算金额 | budget_id, amount |
| `parse_and_create_from_text` | 自然语言记账 | text |

---

## 故障排查

**问:LLM 客户端连不上**

- 检查 PAT 是不是 `bcmcp_…` 开头(以及前缀 14 位),粘贴时别带空格
- 测试 endpoint:`curl -H "Authorization: Bearer bcmcp_…" https://your-domain.com/api/v1/mcp/sse` 应该返回 SSE 流(不是 401/403)
- 看 server log 是否有 401 错误 — 如果是 "Token expired" 检查 PAT 有效期;如果是 "Invalid token" 检查 token 拼写

**问:LLM 调 tool 报 "PAT missing required scope: mcp:write"**

- 创建 token 时没勾"读+写"。回 Web 设置页编辑该 token、勾上写权限即可(无需重建)
- 注意:编辑后需要**重连 LLM 客户端**才能生效 — SSE 长连接会缓存初始 scope

**问:`delete_transaction` 总是返"confirmation_required"**

- 这是设计如此 — LLM 第一次调时只是预演,客户端会回话"确定删除吗",你说"是的删除"后 LLM 才带 `confirm=true` 再调一次

**问:`parse_and_create_from_text` 报 `AI_NO_CHAT_PROVIDER`**

- 需要先在 Web 设置页配 AI provider(GLM / OpenAI 等)。这个 tool 是让 BeeCount 自己的 AI 解析,跟 LLM 客户端的 AI 不同

**问:多账本时 MCP 调哪个**

- 没传 `ledger_id` → 默认用**最早创建**的账本
- 建议每次会话先让 LLM `list_ledgers` 列出选项,然后后续调用都带 `ledger_id`

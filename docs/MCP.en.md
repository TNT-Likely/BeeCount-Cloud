# BeeCount Cloud MCP Server

Let LLM clients (Claude Desktop / Cursor / Cline / etc.) read and write your BeeCount ledger data via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

---

## What it is

MCP is Anthropic's open standard for LLM tool integration. BeeCount Cloud ships a built-in MCP server exposing 17 tools:

- **11 read tools** — `list_ledgers` / `list_transactions` / `list_categories` / `list_accounts` / `list_tags` / `list_budgets` / `get_ledger_stats` / `get_analytics_summary` / `search` / `get_transaction` / `get_active_ledger`
- **6 write tools** — `create_transaction` / `update_transaction` / `delete_transaction` (two-step confirm) / `create_category` / `update_budget` / `parse_and_create_from_text` (let BeeCount's own AI parse free-form text)

Inside your favourite LLM client you can just say:

> "How much did I spend on takeout last month? What were my top three categories?"
>
> "Change that 3pm Starbucks transaction from yesterday — 38 should be 42, and tag it #coffee."
>
> "Log this for me: I just bought a bottle of water at the convenience store for 3.50."

The LLM picks the right tool, no need to open BeeCount. Transactions created via MCP are automatically tagged `MCP` to distinguish them from the mobile "AI bookkeeping" flow.

---

## Setup

### 1. Create a PAT in BeeCount Cloud Web

1. Log into the BeeCount Cloud web console
2. Avatar dropdown → **Settings → Developer** (`/app/settings/developer`)
3. Click **New token**:
   - **Name** — a label, e.g. `Claude Desktop`
   - **Scope**:
     - `mcp:read` — LLM can read only. **Start here.**
     - `mcp:read + mcp:write` — LLM can create/edit/delete transactions. **Grant carefully.**
   - **Expiration**: 30 / 90 / 180 / 365 days or never (default 90)
4. **Copy the token immediately!** The plaintext `bcmcp_…` is shown once — after you close the dialog only the prefix is recoverable.

### 2. Configure the LLM client

> All three clients use `mcp-remote` (npm) as a stdio→SSE bridge. Replace the placeholders below:
>
> - `https://your-domain.com` → your BeeCount Cloud URL (can also be `http://192.168.x.x:8080`, Tailscale, etc.)
> - `bcmcp_xxx...` → the PAT plaintext from step 1

#### Claude Desktop

Config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

> On macOS Claude Desktop doesn't inherit the shell PATH. If `npx` isn't found, use `/opt/homebrew/bin/npx` (Apple Silicon) or `/usr/local/bin/npx` (Intel).

Fully quit Claude Desktop (`Cmd+Q`) and relaunch — the 🔌 "BeeCount" indicator in the bottom-left means it's connected.

#### Cursor

`~/.cursor/mcp.json` (or Settings → Features → MCP UI):

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

Restart Cursor. **Do not** commit this file to git.

#### Cline (VS Code)

VS Code → Cline icon → top-right `…` → **Edit MCP Settings**:

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

You may add read tools to `autoApprove` to reduce confirmation prompts:
`["list_ledgers", "list_transactions", "list_categories", "list_accounts", "list_tags", "list_budgets", "get_active_ledger", "get_transaction", "get_ledger_stats", "get_analytics_summary", "search"]`.
**Don't** add write tools — the UI confirmation is your last line of defense.

### 3. Verify

Once connected, ask the LLM:

- "What ledgers do I have?" → it'll call `list_ledgers`
- "How much did I spend this month?" → it'll call `get_analytics_summary`

---

## Server endpoints

| | |
|---|---|
| SSE channel | `https://your-domain.com/api/v1/mcp/sse` |
| Message back-channel | `https://your-domain.com/api/v1/mcp/messages/` |
| Auth | `Authorization: Bearer bcmcp_…` (PAT) |

PAT and access tokens are strictly partitioned: **PATs only work against `/api/v1/mcp/*`** — every other API rejects PATs with 403. Conversely, regular access tokens cannot call MCP endpoints.

---

## Security model

| Aspect | Mitigation |
|---|---|
| Token storage | Server stores `sha256` hash only, constant-time compare; plaintext returned exactly once at creation |
| Token deletion | One-shot physical delete — the row leaves the DB and the token becomes invalid immediately |
| Token expiration | Optional at creation; expired tokens get 401 |
| Scope separation | `mcp:read` / `mcp:write` are independently selected; read-only tokens cannot be escalated |
| Destructive ops | `delete_transaction` requires `confirm=true`; the first call returns a "needs confirmation" placeholder and the LLM must ask the user first |
| Boundary | PATs cannot call regular `/api/v1/*` endpoints — only MCP tools |
| Audit | Every PAT use bumps `last_used_at` + `last_used_ip`, visible in the web settings page |

**If a PAT leaks**: delete it from the web settings page immediately, then check `last_used_ip` for suspicious sources.

---

## Tool reference

### Read (`mcp:read`)

| Tool | Purpose | Key args |
|---|---|---|
| `list_ledgers` | List all ledgers | — |
| `get_active_ledger` | Current default ledger | — |
| `list_transactions` | Query transactions, multi-dim filter | date_from/to, category, account, q, limit |
| `get_transaction` | Single transaction detail | sync_id |
| `list_categories` | List categories | kind |
| `list_accounts` | List accounts | account_type |
| `list_tags` | List tags | — |
| `list_budgets` | Budgets + current-month progress | ledger_id |
| `get_ledger_stats` | Ledger stats | ledger_id |
| `get_analytics_summary` | Income / expense / top categories | scope (month\|year\|all), period |
| `search` | Full-text fuzzy search | q, limit |

### Write (`mcp:write`)

| Tool | Purpose | Key args |
|---|---|---|
| `create_transaction` | New transaction | amount, tx_type, category, account, happened_at, note, tags |
| `update_transaction` | Edit a transaction | sync_id + fields to change |
| `delete_transaction` | Delete (**two-step confirm**) | sync_id, confirm |
| `create_category` | New category | name, kind, parent_name |
| `update_budget` | Change budget amount | budget_id, amount |
| `parse_and_create_from_text` | Natural language → transaction | text |

---

## Troubleshooting

**Q: LLM client can't connect**

- Make sure the PAT starts with `bcmcp_…` (prefix is 14 chars), no leading/trailing spaces
- Test the endpoint: `curl -H "Authorization: Bearer bcmcp_…" https://your-domain.com/api/v1/mcp/sse` should stream SSE (not 401/403)
- Check server logs for 401 — "Token expired" → PAT past its expiration; "Invalid token" → check the token spelling

**Q: LLM tool call returns "PAT missing required scope: mcp:write"**

- The token doesn't have write scope. Open the web settings page, edit the token, check "Read + write" — no need to recreate.
- After the edit you must **reconnect the LLM client** for the new scope to take effect — the SSE long connection caches the initial scope.

**Q: `delete_transaction` keeps returning "confirmation_required"**

- By design — the first call is a dry run. The client should ask you for confirmation; once you say yes the LLM calls again with `confirm=true`.

**Q: `parse_and_create_from_text` returns `AI_NO_CHAT_PROVIDER`**

- You need to configure an AI provider (GLM / OpenAI / etc.) in the web settings first. This tool uses BeeCount's own AI to parse natural language — different from the LLM client's AI.

**Q: Which ledger does MCP use when I have multiple?**

- If `ledger_id` isn't passed → defaults to the **earliest-created** ledger.
- Recommended flow: have the LLM call `list_ledgers` at session start and pass `ledger_id` explicitly on subsequent calls.

# BeeCount Cloud MCP Server

Let LLM clients (Claude Desktop / Cursor / Cline / etc.) read and write your BeeCount ledger data via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

---

## What it is

MCP is Anthropic's open standard for LLM tool integration. BeeCount Cloud ships a built-in MCP server exposing 19 tools:

- **11 read tools** — `list_ledgers` / `list_transactions` / `list_categories` / `list_accounts` / `list_tags` / `list_budgets` / `get_ledger_stats` / `get_analytics_summary` / `search` / `get_transaction` / `get_active_ledger`
- **8 write tools** — `upload_attachment` / `create_transaction` / `create_transactions` (batch) / `update_transaction` / `delete_transaction` (two-step confirm) / `create_category` / `update_budget` / `parse_and_create_from_text` (let BeeCount's own AI parse free-form text)

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

BeeCount Cloud's MCP uses **Streamable HTTP** (a single endpoint): your deployment URL plus `/api/v1/mcp`.

> Replace the placeholders below:
>
> - `https://your-domain.com` → your BeeCount Cloud URL (can also be `http://192.168.x.x:8080`, Tailscale, etc.)
> - `bcmcp_xxx...` → the PAT plaintext from step 1

**Recommended: clients that speak Streamable HTTP natively connect directly, no `mcp-remote` needed.** For example, Claude Code:

```bash
claude mcp add --transport http beecount https://your-domain.com/api/v1/mcp \
  --header "Authorization: Bearer bcmcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Open a new session; `claude mcp get beecount` showing `✔ Connected` means it's set. Any client with a built-in HTTP MCP client works the same: point it at the endpoint URL `https://your-domain.com/api/v1/mcp` with an `Authorization: Bearer bcmcp_…` header.

**stdio-only clients (Claude Desktop / Cursor / Cline)** use `mcp-remote` (npm) to bridge to the same HTTP endpoint — **use the URL `/api/v1/mcp`, no longer `/sse`**; `mcp-remote` negotiates Streamable HTTP automatically:

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
        "https://your-domain.com/api/v1/mcp",
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
        "https://your-domain.com/api/v1/mcp",
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
| MCP endpoint (Streamable HTTP) | `https://your-domain.com/api/v1/mcp` |
| Auth | `Authorization: Bearer bcmcp_…` (PAT) |

> The legacy SSE endpoints (`/api/v1/mcp/sse` + `/api/v1/mcp/messages/`) have been replaced by Streamable HTTP and are no longer served.

PAT and access tokens are strictly partitioned: **PATs only work against `/api/v1/mcp`** — every other API rejects PATs with 403. Conversely, regular access tokens cannot call MCP endpoints.

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
| `upload_attachment` | Upload a receipt/file and return file_id | file_name, content_base64, ledger_id, mime_type |
| `create_transaction` | New transaction with optional receipts | amount, tx_type, category, account, happened_at, note, tags, attachments |
| `update_transaction` | Edit transaction/files | sync_id + fields, attachments |
| `delete_transaction` | Delete (**two-step confirm**) | sync_id, confirm |
| `create_category` | New category | name, kind, parent_name |
| `update_budget` | Change budget amount | budget_id, amount |
| `parse_and_create_from_text` | Natural language → transaction | text |

---

## Receipts and transaction attachments

Call `upload_attachment` with a filename and standard Base64 bytes (no data URL prefix), then pass the returned IDs to `create_transaction(attachments=[file_id, ...])`. Use the same ledger for uploading and creating. The server supplies authoritative filename, size, SHA256 and display order; Web and App use the existing attachment and sync paths. Duplicate uploads of identical bytes in one ledger reuse the file ID. Other-ledger files and duplicate IDs in a transaction are rejected.

For `update_transaction`, omitted/null `attachments` preserves existing files; an ordered list replaces them; `[]` removes all. To append, read current `cloudFileId` values with `get_transaction`, then submit the full list with new IDs. Uploading alone does not create a transaction, and successfully uploaded IDs can be reused if transaction submission fails. The upload tool requires `mcp:write` and applies the configured attachment size limit (64 MiB by default). Keep original receipt bytes: do not resize, transcode or split Base64 just to fit model arguments. File contents and filenames are excluded from MCP call-history summaries. No OCR/tax extraction or batch attachment support is added.

For direct file-path tool calls, use the local stdio MCP adapter `scripts/mcp_local_files.py` on the client computer. It forwards all Cloud tools and changes the same `upload_attachment` tool to accept `file_path`. The program reads and sends unchanged bytes; the model handles only the path and returned metadata. Requires Python and `mcp>=1.27,<2`:

```sh
claude mcp add --transport stdio --scope user \
  --env BEECOUNT_MCP_PAT=bcmcp_xxx beecount -- \
  /absolute/path/to/python /absolute/path/to/BeeCount-Cloud/scripts/mcp_local_files.py \
  --endpoint https://your-domain.com/api/v1/mcp \
  --allow-dir /absolute/path/to/receipts
```

Restart the client, then call:

```json
{"name":"upload_attachment","arguments":{"ledger_id":"your-ledger-id","file_path":"/absolute/path/to/receipts/receipt.png"}}
```

Pass the returned `file_id` to the transaction tools. Repeat `--allow-dir` for additional directories. Outside paths, symlink escapes, empty and special files are rejected. Cloud enforces the existing PAT/ledger rules and deduplication. Paths belong to the client; a Docker Cloud does not need a mount of the receipt folder. The direct HTTP tool retains `content_base64` and never reads arbitrary server paths.

For command-line batch uploading, the standalone client-side helper is also available:

```sh
# Provide the PAT through BEECOUNT_MCP_PAT, never as a command argument.
python scripts/mcp_upload_receipt.py \
  --endpoint https://your-domain.com/api/v1/mcp \
  --ledger-id your-ledger-id ./receipt.png ./receipt-2.jpg
```

The helper uploads via real Streamable HTTP MCP and returns an `attachments` list for the transaction tools. You still need to create/edit the transaction afterward. Local paths and download URLs cannot be used as file IDs.

## Troubleshooting

**Q: LLM client can't connect**

- Make sure the PAT starts with `bcmcp_…` (prefix is 14 chars), no leading/trailing spaces
- Test the endpoint (should return 200 + `serverInfo`, not 401/403/404):
  ```bash
  curl -X POST https://your-domain.com/api/v1/mcp \
    -H "Authorization: Bearer bcmcp_…" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
  ```
- Check server logs for 401 — "Token expired" → PAT past its expiration; "Invalid token" → check the token spelling

**Q: LLM tool call returns "PAT missing required scope: mcp:write"**

- The token doesn't have write scope. Open the web settings page, edit the token, check "Read + write" — no need to recreate.
- After the edit you must **reconnect the LLM client** for the new scope to take effect — clients cache the scope / tool list from the first connection.

**Q: `delete_transaction` keeps returning "confirmation_required"**

- By design — the first call is a dry run. The client should ask you for confirmation; once you say yes the LLM calls again with `confirm=true`.

**Q: `parse_and_create_from_text` returns `AI_NO_CHAT_PROVIDER`**

- You need to configure an AI provider (GLM / OpenAI / etc.) in the web settings first. This tool uses BeeCount's own AI to parse natural language — different from the LLM client's AI.

**Q: Which ledger does MCP use when I have multiple?**

- If `ledger_id` isn't passed → defaults to the **earliest-created** ledger.
- Recommended flow: have the LLM call `list_ledgers` at session start and pass `ledger_id` explicitly on subsequent calls.

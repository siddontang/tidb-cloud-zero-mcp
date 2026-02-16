# TiDB Cloud Zero MCP Server

Give any AI agent a persistent MySQL database through the [Model Context Protocol](https://modelcontextprotocol.io).

**Zero config** — the server automatically provisions a free [TiDB Cloud Zero](https://zero.tidbcloud.com) instance on first use. No signup, no API keys, no credentials. Just run it.

## How It Works

```
┌─────────────┐     MCP      ┌──────────────┐    HTTPS     ┌─────────────────┐
│  AI Agent   │◄────────────►│  MCP Server  │◄────────────►│ TiDB Cloud Zero │
│  (Claude,   │   stdio/http │  (this repo) │  /v1beta/sql │  (free MySQL)   │
│   Cursor)   │              │              │   pure HTTP  │                 │
└─────────────┘              └──────────────┘              └─────────────────┘
```

On first query, the server calls `POST https://zero.tidbapi.com/v1alpha1/instances` to create a free database, then uses the [TiDB Serverless HTTP API](https://github.com/tidbcloud/serverless-js) for all SQL — pure HTTPS, no MySQL driver, no persistent connections.

The instance credentials are cached locally (`~/.tidb-cloud-zero-mcp/instance.json`) and reused until expiry.

## Quick Start

```bash
git clone https://github.com/siddontang/tidb-cloud-zero-mcp.git
cd tidb-cloud-zero-mcp
uv run server.py
```

That's it. No environment variables needed. The first query auto-provisions a database.

### Connect to Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tidb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/tidb-cloud-zero-mcp", "server.py"]
    }
  }
}
```

### Connect to Claude Code

```bash
claude mcp add tidb -- uv run --project /path/to/tidb-cloud-zero-mcp server.py
```

### Connect to Cursor / Windsurf

Add to your MCP settings:

```json
{
  "tidb": {
    "command": "uv",
    "args": ["run", "--project", "/path/to/tidb-cloud-zero-mcp", "server.py"]
  }
}
```

### HTTP Transport

```bash
uv run server.py --transport http
# Connect at http://localhost:8000/mcp
```

## Bring Your Own Database (Optional)

If you already have a TiDB Cloud instance, set `TIDB_URL`:

```bash
export TIDB_URL="mysql://user:password@host/database"
uv run server.py
```

Or individual variables:

```bash
export TIDB_HOST="gateway01.us-west-2.prod.aws.tidbcloud.com"
export TIDB_USERNAME="your_user"
export TIDB_PASSWORD="your_password"
export TIDB_DATABASE="test"
```

## Tools

| Tool | Description |
|------|-------------|
| `query` | Run SELECT / SHOW / DESCRIBE / EXPLAIN |
| `execute` | Run CREATE / INSERT / UPDATE / DELETE / ALTER |
| `batch_execute` | Run multiple SQL statements sequentially |
| `list_tables` | List all tables with row counts |
| `describe_table` | Get table schema |
| `get_database_info` | Database info, version, and instance status |

## Example Interactions

Once connected, ask your AI agent:

- *"Create a users table and add some sample data"*
- *"Show me all tables in the database"*
- *"Analyze the data in the orders table"*
- *"Write a query to find the top 10 customers by revenue"*

The agent uses MCP tools to interact with TiDB Cloud Zero directly — no configuration needed.

## Architecture

Every SQL query is a single HTTP POST to TiDB's Serverless HTTP API:

```
POST https://http-{host}/v1beta/sql
Authorization: Basic {base64(user:pass)}
TiDB-Database: {database}
Content-Type: application/json

{"query": "SELECT * FROM users"}
```

This means:
- **No MySQL driver** — works anywhere with HTTPS
- **No connection management** — stateless, each query is independent
- **Edge-compatible** — runs in serverless functions and edge workers
- **Auto-provisioning** — database created on first use via Zero API

## Why TiDB Cloud Zero?

| Feature | Benefit |
|---------|---------|
| **Zero signup** | No account, no credit card — just use it |
| **MySQL compatible** | Works with every tool, ORM, and language |
| **Serverless** | No provisioning, no maintenance |
| **HTTP API** | No drivers needed, pure HTTPS |
| **Vector Search** | Store embeddings alongside relational data |
| **Disposable** | 72-hour instances for testing and demos |

## Development

```bash
uv sync                          # Install dependencies
uv run mcp dev server.py         # Test with MCP Inspector
uv run server.py --transport http # Run HTTP server
```

## License

MIT

---

**Try TiDB Cloud:** [Free Trial](https://tidbcloud.com/free-trial/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Essential 101](https://www.pingcap.com/essential101/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Startup Program](https://www.pingcap.com/tidb-cloud-startup-program/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [TiDB Cloud AI](https://www.pingcap.com/ai?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon)

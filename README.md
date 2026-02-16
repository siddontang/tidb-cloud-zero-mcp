# TiDB Cloud Zero MCP Server

Give any AI agent a persistent MySQL database through the [Model Context Protocol](https://modelcontextprotocol.io).

Built on [TiDB Cloud Zero](https://zero.tidbcloud.com) — free serverless MySQL that's perfect for AI agents.

**Pure HTTP** — no MySQL drivers, no persistent connections, no sockets. Uses the [TiDB Serverless HTTP API](https://github.com/tidbcloud/serverless-js) (`POST /v1beta/sql`), making it compatible with edge runtimes, serverless functions, and any environment that can make HTTPS requests.

## How It Works

```
┌─────────────┐     MCP      ┌──────────────┐    HTTPS     ┌─────────────────┐
│  AI Agent   │◄────────────►│  MCP Server  │◄────────────►│ TiDB Cloud Zero │
│  (Claude,   │   stdio/http │  (this repo) │  /v1beta/sql │  (free MySQL)   │
│   Cursor)   │              │              │   pure HTTP  │                 │
└─────────────┘              └──────────────┘              └─────────────────┘
```

Each SQL query is a single HTTP POST — stateless, no connection pooling, no driver dependencies.

## Quick Start

### 1. Get a free TiDB Cloud Zero database

Go to [zero.tidbcloud.com](https://zero.tidbcloud.com) and create a free database. Note your connection URL.

### 2. Install and run

```bash
git clone https://github.com/siddontang/tidb-cloud-zero-mcp.git
cd tidb-cloud-zero-mcp

# Set your TiDB Cloud Zero connection URL
export TIDB_URL="mysql://user:password@gateway01.us-west-2.prod.aws.tidbcloud.com/test"

# Run the server
uv run server.py
```

Or set individual environment variables:

```bash
export TIDB_HOST="gateway01.us-west-2.prod.aws.tidbcloud.com"
export TIDB_USERNAME="your_user"
export TIDB_PASSWORD="your_password"
export TIDB_DATABASE="test"
```

### 3. Connect your AI agent

#### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tidb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/tidb-cloud-zero-mcp", "server.py"],
      "env": {
        "TIDB_URL": "mysql://user:password@gateway01.us-west-2.prod.aws.tidbcloud.com/test"
      }
    }
  }
}
```

#### Claude Code

```bash
claude mcp add tidb -- uv run --project /path/to/tidb-cloud-zero-mcp server.py
```

#### Cursor / Windsurf

Add to your MCP settings with the same `command`, `args`, and `env` as above.

#### HTTP Transport (for web clients)

```bash
uv run server.py --transport http
# Server runs on http://localhost:8000/mcp
```

## Tools

| Tool | Description |
|------|-------------|
| `query` | Run SELECT/SHOW/DESCRIBE/EXPLAIN queries |
| `execute` | Run CREATE/INSERT/UPDATE/DELETE/ALTER statements |
| `batch_execute` | Run multiple SQL statements sequentially |
| `list_tables` | List all tables with row counts |
| `describe_table` | Get table schema (columns, types, keys) |
| `get_database_info` | Database info and TiDB version |

## Resources

| Resource | Description |
|----------|-------------|
| `tidb://tables` | List of all tables |
| `tidb://info` | Database connection info |

## Prompts

| Prompt | Description |
|--------|-------------|
| `create_crud_table` | Generate a table with CRUD best practices |
| `analyze_data` | Run a data analysis workflow on a table |

## Example Interactions

Once connected, you can ask your AI agent things like:

- *"Create a users table and add some sample data"*
- *"Show me all tables in the database"*
- *"Analyze the data in the orders table"*
- *"Write a query to find the top 10 customers by revenue"*
- *"Create a schema for a todo app"*

The agent uses the MCP tools to interact with your TiDB Cloud Zero database directly.

## Architecture

Unlike traditional database MCP servers that use MySQL/Postgres wire protocol, this server uses TiDB's **Serverless HTTP API**:

```python
# Every query is just an HTTP POST
POST https://http-{host}/v1beta/sql
Authorization: Basic {base64(user:pass)}
TiDB-Database: {database}
Content-Type: application/json

{"query": "SELECT * FROM users"}
```

This means:
- **No MySQL driver** — works anywhere that can make HTTPS requests
- **No connection management** — each query is independent
- **Edge-compatible** — runs in serverless functions, edge workers, etc.
- **Zero dependencies** — only `httpx` for HTTP and `mcp` for the protocol

## Why TiDB Cloud Zero?

| Feature | Benefit |
|---------|---------|
| **Free forever** | No credit card, no surprise bills |
| **MySQL compatible** | Works with every tool, ORM, and language |
| **Serverless** | No provisioning, no maintenance |
| **HTTP API** | No drivers needed, works from edge/serverless |
| **Scales to zero** | Only uses resources when you need them |
| **Built-in HTAP** | OLTP + OLAP in one database |

## Development

```bash
# Install dependencies
uv sync

# Run with MCP Inspector for testing
uv run mcp dev server.py

# Test directly
TIDB_URL="mysql://..." uv run python -c "
import asyncio
from server import get_database_info
print(asyncio.run(get_database_info()))
"
```

## License

MIT

---

**Try TiDB Cloud:** [Free Trial](https://tidbcloud.com/free-trial/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Essential 101](https://www.pingcap.com/essential101/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Startup Program](https://www.pingcap.com/tidb-cloud-startup-program/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [TiDB Cloud AI](https://www.pingcap.com/ai?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon)

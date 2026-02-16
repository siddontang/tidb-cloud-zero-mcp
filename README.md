# TiDB Cloud Zero MCP Server

Give any AI agent a persistent MySQL database through the [Model Context Protocol](https://modelcontextprotocol.io).

Built on [TiDB Cloud Zero](https://zero.tidbcloud.com) — free serverless MySQL that's perfect for AI agents.

## What is this?

An MCP server that connects AI agents (Claude, Cursor, Windsurf, OpenAI, etc.) to a TiDB Cloud Zero database. Agents can create tables, run queries, analyze data — all through the standard MCP protocol.

```
┌─────────────┐     MCP      ┌──────────────┐     MySQL     ┌─────────────────┐
│  AI Agent   │◄────────────►│  MCP Server  │◄─────────────►│ TiDB Cloud Zero │
│  (Claude,   │   stdio/http │  (this repo) │   port 4000   │  (free MySQL)   │
│   Cursor)   │              │              │               │                 │
└─────────────┘              └──────────────┘               └─────────────────┘
```

## Quick Start

### 1. Get a free TiDB Cloud Zero database

Go to [zero.tidbcloud.com](https://zero.tidbcloud.com) and create a free database. Note your connection details (host, user, password).

### 2. Install and run

```bash
git clone https://github.com/siddontang/tidb-cloud-zero-mcp.git
cd tidb-cloud-zero-mcp

# Set your TiDB Cloud Zero credentials
export TIDB_HOST="gateway01.us-west-2.prod.aws.tidbcloud.com"
export TIDB_PORT=4000
export TIDB_USER="your_user"
export TIDB_PASSWORD="your_password"
export TIDB_DATABASE="test"

# Run the server
uv run server.py
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
        "TIDB_HOST": "gateway01.us-west-2.prod.aws.tidbcloud.com",
        "TIDB_PORT": "4000",
        "TIDB_USER": "your_user",
        "TIDB_PASSWORD": "your_password",
        "TIDB_DATABASE": "test"
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

Add to your MCP settings:

```json
{
  "tidb": {
    "command": "uv",
    "args": ["run", "--project", "/path/to/tidb-cloud-zero-mcp", "server.py"],
    "env": {
      "TIDB_HOST": "gateway01.us-west-2.prod.aws.tidbcloud.com",
      "TIDB_PORT": "4000",
      "TIDB_USER": "your_user",
      "TIDB_PASSWORD": "your_password",
      "TIDB_DATABASE": "test"
    }
  }
}
```

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
| `batch_insert` | Insert multiple rows efficiently |
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

The agent will use the MCP tools to interact with your TiDB Cloud Zero database directly.

## Why TiDB Cloud Zero?

| Feature | Benefit |
|---------|---------|
| **Free forever** | No credit card, no surprise bills |
| **MySQL compatible** | Works with every tool, ORM, and language |
| **Serverless** | No provisioning, no maintenance |
| **Scales to zero** | Only uses resources when you need them |
| **Built-in HTAP** | OLTP + OLAP in one database |

## Development

```bash
# Install dependencies
uv sync

# Run with MCP Inspector for testing
uv run mcp dev server.py

# Run tests
uv run pytest
```

## License

MIT

---

**Try TiDB Cloud:** [Free Trial](https://tidbcloud.com/free-trial/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Essential 101](https://www.pingcap.com/essential101/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [Startup Program](https://www.pingcap.com/tidb-cloud-startup-program/?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon) • [TiDB Cloud AI](https://www.pingcap.com/ai?utm_source=sales_bdm&utm_medium=sales&utm_content=Siddon)

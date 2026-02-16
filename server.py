"""
TiDB Cloud Zero MCP Server

An MCP server that gives any AI agent (Claude, Cursor, Windsurf, etc.)
a persistent MySQL database via TiDB Cloud Zero.

**Zero config** — on first use, the server automatically provisions a free
TiDB Cloud Zero instance. No signup, no API keys, no credentials needed.

Uses the TiDB Serverless HTTP API (pure HTTP, no MySQL driver).

Usage:
    uv run server.py                    # stdio transport (for Claude Desktop)
    uv run server.py --transport http   # HTTP transport (for web clients)

Environment variables (all optional):
    TIDB_URL       - mysql://user:password@host/database (skip auto-provisioning)
    TIDB_HOST      - TiDB host (skip auto-provisioning)
    TIDB_USERNAME  - Database user
    TIDB_PASSWORD  - Database password
    TIDB_DATABASE  - Database name (default: test)

If no credentials are provided, a TiDB Cloud Zero instance is created automatically.
"""

import base64
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

# --- Constants ---
ZERO_API = "https://zero.tidbapi.com/v1alpha1/instances"
STATE_FILE = Path.home() / ".tidb-cloud-zero-mcp" / "instance.json"


# --- Configuration ---

@dataclass
class TiDBConfig:
    host: str = ""
    username: str = ""
    password: str = ""
    database: str = "test"
    expires_at: str = ""

    @property
    def api_url(self) -> str:
        return f"https://http-{self.host}/v1beta/sql"

    @property
    def auth_header(self) -> str:
        credentials = f"{self.username}:{self.password}"
        return f"Basic {base64.b64encode(credentials.encode()).decode()}"

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= exp
        except Exception:
            return False

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "database": self.database,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TiDBConfig":
        return cls(
            host=d.get("host", ""),
            username=d.get("username", ""),
            password=d.get("password", ""),
            database=d.get("database", "test"),
            expires_at=d.get("expires_at", ""),
        )

    @classmethod
    def from_env(cls) -> "TiDBConfig":
        """Load config from environment variables."""
        url = os.environ.get("TIDB_URL", "")
        if url:
            parsed = urlparse(url)
            return cls(
                host=parsed.hostname or "",
                username=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=unquote(parsed.path.lstrip("/")) or "test",
            )
        host = os.environ.get("TIDB_HOST", "")
        if host:
            return cls(
                host=host,
                username=os.environ.get("TIDB_USERNAME", ""),
                password=os.environ.get("TIDB_PASSWORD", ""),
                database=os.environ.get("TIDB_DATABASE", "test"),
            )
        return cls()

    @classmethod
    def load_saved(cls) -> "TiDBConfig | None":
        """Load saved instance from disk."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                config = cls.from_dict(data)
                if config.is_configured and not config.is_expired:
                    return config
            except Exception:
                pass
        return None

    def save(self):
        """Save instance to disk for reuse."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.to_dict(), indent=2))


# --- Global config (lazy-initialized) ---
_config: TiDBConfig | None = None


async def get_config() -> TiDBConfig:
    """Get or create TiDB config. Auto-provisions a Zero instance if needed."""
    global _config

    if _config and _config.is_configured and not _config.is_expired:
        return _config

    # 1. Try environment variables
    env_config = TiDBConfig.from_env()
    if env_config.is_configured:
        _config = env_config
        return _config

    # 2. Try saved instance
    saved = TiDBConfig.load_saved()
    if saved:
        _config = saved
        return _config

    # 3. Auto-provision a new TiDB Cloud Zero instance
    _config = await provision_zero_instance()
    return _config


async def provision_zero_instance() -> TiDBConfig:
    """Create a new TiDB Cloud Zero instance via API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZERO_API, timeout=30)

    if resp.status_code != 200:
        raise Exception(f"Failed to create TiDB Cloud Zero instance: {resp.status_code} {resp.text}")

    data = resp.json()
    instance = data["instance"]
    conn = instance["connection"]

    config = TiDBConfig(
        host=conn["host"],
        username=conn["username"],
        password=conn["password"],
        database="test",
        expires_at=instance.get("expiresAt", ""),
    )

    # Save for reuse across restarts
    config.save()
    return config


# --- HTTP SQL Client ---

@dataclass
class QueryResult:
    columns: list[dict]
    rows: list[list[str]]
    rows_affected: int | None
    last_insert_id: str | None

    def to_dicts(self) -> list[dict]:
        if not self.columns or not self.rows:
            return []
        return [
            {col["name"]: row[i] for i, col in enumerate(self.columns)}
            for row in self.rows
        ]


async def execute_sql(sql: str, database: str | None = None) -> QueryResult:
    """Execute SQL via TiDB Serverless HTTP API."""
    config = await get_config()
    db = database or config.database
    headers = {
        "Content-Type": "application/json",
        "Authorization": config.auth_header,
        "TiDB-Database": db,
    }
    body = json.dumps({"query": sql})

    async with httpx.AsyncClient() as client:
        resp = await client.post(config.api_url, headers=headers, content=body, timeout=30)

    if resp.status_code != 200:
        try:
            err = resp.json()
            raise Exception(f"TiDB API error ({resp.status_code}): {err.get('message', resp.text)}")
        except json.JSONDecodeError:
            raise Exception(f"TiDB API error ({resp.status_code}): {resp.text}")

    data = resp.json()
    return QueryResult(
        columns=data.get("types") or [],
        rows=data.get("rows") or [],
        rows_affected=data.get("rowsAffected"),
        last_insert_id=data.get("sLastInsertID"),
    )


# --- Formatting ---

def format_results(result: QueryResult, max_rows: int = 100) -> str:
    rows = result.to_dicts()
    if not rows:
        if result.rows_affected is not None:
            msg = f"OK. Rows affected: {result.rows_affected}"
            if result.last_insert_id and result.last_insert_id != "0":
                msg += f". Last insert ID: {result.last_insert_id}"
            return msg
        return "No results."

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]

    columns = list(rows[0].keys())
    widths = {col: len(str(col)) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))

    header = " | ".join(str(col).ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    lines = [header, separator]
    for row in rows:
        line = " | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        lines.append(line)

    text = "\n".join(lines)
    if truncated:
        text += f"\n... (showing {max_rows} of more rows)"
    return text


# --- MCP Server ---

mcp = FastMCP(
    "TiDB Cloud Zero",
    instructions="""You have access to a TiDB Cloud Zero MySQL database via HTTP API.
Use the provided tools to create tables, insert data, run queries, and manage schema.
TiDB is MySQL-compatible with distributed SQL support. Standard MySQL syntax works.
The database is auto-provisioned — no setup needed. Just start using it.""",
)


@mcp.tool()
async def query(sql: str) -> str:
    """Execute a read-only SQL query (SELECT, SHOW, DESCRIBE, EXPLAIN).

    Returns results as a formatted table.

    Examples:
        query("SELECT * FROM users LIMIT 10")
        query("SHOW TABLES")
        query("DESCRIBE users")
    """
    sql_upper = sql.strip().upper()
    allowed_prefixes = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
    if not any(sql_upper.startswith(p) for p in allowed_prefixes):
        return "Error: query() only supports SELECT, SHOW, DESCRIBE, and EXPLAIN. Use execute() for write operations."
    try:
        result = await execute_sql(sql)
        return format_results(result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def execute(sql: str) -> str:
    """Execute a write SQL statement (CREATE, INSERT, UPDATE, DELETE, ALTER, DROP).

    Returns the number of affected rows.

    Examples:
        execute("CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255))")
        execute("INSERT INTO users (name) VALUES ('Alice')")
    """
    try:
        result = await execute_sql(sql)
        msg = f"OK. Rows affected: {result.rows_affected or 0}"
        if result.last_insert_id and result.last_insert_id != "0":
            msg += f". Last insert ID: {result.last_insert_id}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def batch_execute(statements: list[str]) -> str:
    """Execute multiple SQL statements sequentially.

    Args:
        statements: List of SQL statements to execute in order

    Example:
        batch_execute([
            "CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255))",
            "INSERT INTO users (name) VALUES ('Alice')",
            "INSERT INTO users (name) VALUES ('Bob')"
        ])
    """
    results = []
    for i, sql in enumerate(statements):
        try:
            result = await execute_sql(sql)
            msg = f"[{i+1}] OK"
            if result.rows_affected is not None:
                msg += f" ({result.rows_affected} rows)"
            results.append(msg)
        except Exception as e:
            results.append(f"[{i+1}] Error: {e}")
    return "\n".join(results)


@mcp.tool()
async def list_tables() -> str:
    """List all tables in the current database with row counts."""
    try:
        result = await execute_sql("SHOW TABLES")
        if not result.rows:
            return "No tables found. Use execute() to create one!"

        tables = []
        for row in result.rows:
            table_name = row[0]
            try:
                count_result = await execute_sql(f"SELECT COUNT(*) as count FROM `{table_name}`")
                count = count_result.rows[0][0] if count_result.rows else "?"
            except Exception:
                count = "?"
            tables.append({"table": table_name, "rows": count})

        max_name = max(len(t["table"]) for t in tables)
        max_name = max(max_name, 5)
        lines = [f"{'table'.ljust(max_name)} | rows", f"{'-' * max_name}-+-----"]
        for t in tables:
            lines.append(f"{t['table'].ljust(max_name)} | {t['rows']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def describe_table(table: str) -> str:
    """Get the schema of a table (columns, types, keys).

    Args:
        table: Table name to describe
    """
    try:
        result = await execute_sql(f"DESCRIBE `{table}`")
        return format_results(result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def get_database_info() -> str:
    """Get database connection info, TiDB version, and instance status."""
    try:
        config = await get_config()
        version_result = await execute_sql("SELECT VERSION() as version")
        version = version_result.rows[0][0] if version_result.rows else "unknown"

        db_result = await execute_sql("SELECT DATABASE() as db")
        db = db_result.rows[0][0] if db_result.rows else "unknown"

        tables_result = await execute_sql("SHOW TABLES")
        table_count = len(tables_result.rows) if tables_result.rows else 0

        info = (
            f"Database: {db}\n"
            f"TiDB Version: {version}\n"
            f"Host: {config.host}\n"
            f"API: {config.api_url}\n"
            f"Tables: {table_count}\n"
            f"Connection: Serverless HTTP (stateless, no driver needed)\n"
        )
        if config.expires_at:
            info += f"Instance expires: {config.expires_at}\n"
        info += (
            f"\nTiDB Cloud Zero — Free serverless MySQL for AI agents.\n"
            f"Get yours at https://zero.tidbcloud.com"
        )
        return info
    except Exception as e:
        return f"Error: {e}"


# --- Resources ---

@mcp.resource("tidb://tables")
async def resource_tables() -> str:
    """List of all tables in the database."""
    return await list_tables()


@mcp.resource("tidb://info")
async def resource_info() -> str:
    """Database connection info and TiDB version."""
    return await get_database_info()


# --- Prompts ---

@mcp.prompt()
def create_crud_table(table_name: str, columns: str) -> str:
    """Generate SQL to create a table with common CRUD patterns."""
    return f"""Please create a table called `{table_name}` with these columns: {columns}

Also add:
- An auto-increment primary key `id`
- `created_at` and `updated_at` timestamps
- Appropriate indexes

Use the execute() tool to run the CREATE TABLE statement.
Then use describe_table() to verify the schema."""


@mcp.prompt()
def analyze_data(table_name: str) -> str:
    """Generate a data analysis workflow for a table."""
    return f"""Please analyze the data in the `{table_name}` table:

1. First, use describe_table("{table_name}") to see the schema
2. Use query("SELECT COUNT(*) as total FROM {table_name}") for row count
3. For numeric columns, calculate min, max, avg
4. For text columns, show distinct value counts
5. Summarize your findings"""


if __name__ == "__main__":
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")

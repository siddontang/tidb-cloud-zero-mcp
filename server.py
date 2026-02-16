"""
TiDB Cloud Zero MCP Server

An MCP server that gives any AI agent (Claude, Cursor, Windsurf, etc.)
a persistent MySQL database via TiDB Cloud Zero.

Uses the TiDB Serverless HTTP API (no MySQL driver needed — pure HTTP).
Zero dependencies beyond `mcp` and `httpx`.

Usage:
    uv run server.py                    # stdio transport (for Claude Desktop)
    uv run server.py --transport http   # HTTP transport (for web clients)

Configure via environment variables:
    TIDB_HOST      - TiDB Cloud Zero host (e.g., gateway01.us-west-2.prod.aws.tidbcloud.com)
    TIDB_USERNAME  - Database user
    TIDB_PASSWORD  - Database password
    TIDB_DATABASE  - Database name (default: test)

Or use a connection URL:
    TIDB_URL       - mysql://user:password@host/database
"""

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

# --- Configuration ---

@dataclass
class TiDBConfig:
    host: str
    username: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> "TiDBConfig":
        url = os.environ.get("TIDB_URL", "")
        if url:
            parsed = urlparse(url)
            return cls(
                host=parsed.hostname or "",
                username=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=unquote(parsed.path.lstrip("/")),
            )
        return cls(
            host=os.environ.get("TIDB_HOST", ""),
            username=os.environ.get("TIDB_USERNAME", ""),
            password=os.environ.get("TIDB_PASSWORD", ""),
            database=os.environ.get("TIDB_DATABASE", "test"),
        )

    @property
    def api_url(self) -> str:
        """TiDB Serverless HTTP API endpoint."""
        return f"https://http-{self.host}/v1beta/sql"

    @property
    def auth_header(self) -> str:
        """Basic auth header value."""
        credentials = f"{self.username}:{self.password}"
        return f"Basic {base64.b64encode(credentials.encode()).decode()}"


config = TiDBConfig.from_env()


# --- HTTP Client ---

@dataclass
class QueryResult:
    columns: list[dict]  # [{name, type, nullable}]
    rows: list[list[str]]
    rows_affected: int | None
    last_insert_id: str | None

    def to_dicts(self) -> list[dict]:
        """Convert rows to list of dicts."""
        if not self.columns or not self.rows:
            return []
        return [
            {col["name"]: row[i] for i, col in enumerate(self.columns)}
            for row in self.rows
        ]


async def execute_sql(sql: str, database: str | None = None) -> QueryResult:
    """Execute SQL via TiDB Serverless HTTP API."""
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
    """Format query results as a readable table."""
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


def format_schema(result: QueryResult) -> str:
    """Format with column type info."""
    rows = result.to_dicts()
    if not rows:
        return "No results."
    text = format_results(result)
    # Add type info from column metadata
    if result.columns:
        type_info = ", ".join(f"{c['name']}({c['type']})" for c in result.columns)
        text += f"\n\nColumn types: {type_info}"
    return text


# --- MCP Server ---

mcp = FastMCP(
    "TiDB Cloud Zero",
    instructions="""You have access to a TiDB Cloud Zero MySQL database via HTTP API.
Use the provided tools to create tables, insert data, run queries, and manage schema.
TiDB is MySQL-compatible with distributed SQL support. Standard MySQL syntax works.
The connection is serverless — no persistent connections, each query is independent.""",
)


@mcp.tool()
async def query(sql: str) -> str:
    """Execute a read-only SQL query (SELECT, SHOW, DESCRIBE, EXPLAIN).

    Returns results as a formatted table.
    Use this for reading data, inspecting schema, and exploring the database.

    Examples:
        query("SELECT * FROM users LIMIT 10")
        query("SHOW TABLES")
        query("DESCRIBE users")
        query("EXPLAIN SELECT * FROM orders WHERE user_id = 1")
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

    Returns the number of affected rows or success message.
    Use this for modifying data and schema.

    Examples:
        execute("CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255), email VARCHAR(255))")
        execute("INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com')")
        execute("UPDATE users SET name = 'Bob' WHERE id = 1")
        execute("ALTER TABLE users ADD COLUMN age INT")
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

    Useful for running migrations, seeding data, or multi-step schema changes.
    Each statement is executed independently (no transaction).

    Args:
        statements: List of SQL statements to execute in order

    Example:
        batch_execute([
            "CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255))",
            "INSERT INTO users (name) VALUES ('Alice')",
            "INSERT INTO users (name) VALUES ('Bob')",
            "CREATE INDEX idx_name ON users(name)"
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

        # Format manually
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
    """Get information about the current database connection and TiDB version."""
    try:
        version_result = await execute_sql("SELECT VERSION() as version")
        version = version_result.rows[0][0] if version_result.rows else "unknown"

        db_result = await execute_sql("SELECT DATABASE() as db")
        db = db_result.rows[0][0] if db_result.rows else "unknown"

        tables_result = await execute_sql("SHOW TABLES")
        table_count = len(tables_result.rows) if tables_result.rows else 0

        return (
            f"Database: {db}\n"
            f"TiDB Version: {version}\n"
            f"Host: {config.host}\n"
            f"API: {config.api_url}\n"
            f"Tables: {table_count}\n"
            f"Connection: Serverless HTTP (no persistent connections)\n"
            f"\nTiDB Cloud Zero — Free serverless MySQL for AI agents.\n"
            f"Get yours at https://zero.tidbcloud.com"
        )
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
    """Generate SQL to create a table with common CRUD patterns.

    Args:
        table_name: Name of the table
        columns: Comma-separated column definitions (e.g., "name VARCHAR(255), age INT")
    """
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
5. Look for any interesting patterns or anomalies
6. Summarize your findings"""


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

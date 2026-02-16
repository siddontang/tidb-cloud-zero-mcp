"""
TiDB Cloud Zero MCP Server

An MCP server that gives any AI agent (Claude, Cursor, Windsurf, etc.)
a persistent MySQL database via TiDB Cloud Zero.

Agents can create tables, run queries, manage schema — all through
the standard Model Context Protocol.

Usage:
    uv run server.py                    # stdio transport (for Claude Desktop)
    uv run server.py --transport http   # HTTP transport (for web clients)

Configure via environment variables:
    TIDB_HOST     - TiDB Cloud Zero host (default: gateway01.us-west-2.prod.aws.tidbcloud.com)
    TIDB_PORT     - TiDB Cloud Zero port (default: 4000)
    TIDB_USER     - Database user (default: your_user)
    TIDB_PASSWORD  - Database password
    TIDB_DATABASE  - Database name (default: test)
"""

import json
import os
import sys
from typing import Any

import pymysql
from mcp.server.fastmcp import FastMCP

# --- Configuration ---
TIDB_CONFIG = {
    "host": os.environ.get("TIDB_HOST", "gateway01.us-west-2.prod.aws.tidbcloud.com"),
    "port": int(os.environ.get("TIDB_PORT", "4000")),
    "user": os.environ.get("TIDB_USER", ""),
    "password": os.environ.get("TIDB_PASSWORD", ""),
    "database": os.environ.get("TIDB_DATABASE", "test"),
    "ssl": {"ca": None},  # TiDB Cloud requires SSL
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# --- MCP Server ---
mcp = FastMCP(
    "TiDB Cloud Zero",
    instructions="""You have access to a TiDB Cloud Zero MySQL database.
Use the provided tools to create tables, insert data, run queries, and manage schema.
TiDB is MySQL-compatible and supports distributed SQL, so standard MySQL syntax works.
Always use parameterized queries when possible for safety.""",
)


def get_connection():
    """Create a new database connection."""
    return pymysql.connect(**TIDB_CONFIG)


def format_results(rows: list[dict], max_rows: int = 100) -> str:
    """Format query results as a readable table."""
    if not rows:
        return "No results."
    
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    
    # Get column names
    columns = list(rows[0].keys())
    
    # Calculate column widths
    widths = {col: len(str(col)) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))
    
    # Build table
    header = " | ".join(str(col).ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    lines = [header, separator]
    for row in rows:
        line = " | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        lines.append(line)
    
    result = "\n".join(lines)
    if truncated:
        result += f"\n... (showing {max_rows} of {len(rows)}+ rows)"
    return result


@mcp.tool()
def query(sql: str) -> str:
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
    
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            return format_results(rows)
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def execute(sql: str) -> str:
    """Execute a write SQL statement (CREATE, INSERT, UPDATE, DELETE, ALTER, DROP).
    
    Returns the number of affected rows or success message.
    Use this for modifying data and schema.
    
    Examples:
        execute("CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255), email VARCHAR(255))")
        execute("INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com')")
        execute("UPDATE users SET name = 'Bob' WHERE id = 1")
        execute("ALTER TABLE users ADD COLUMN age INT")
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            affected = cursor.execute(sql)
            conn.commit()
            
            # For INSERT, try to get the last insert id
            if sql.strip().upper().startswith("INSERT"):
                last_id = cursor.lastrowid
                return f"OK. Rows affected: {affected}. Last insert ID: {last_id}"
            
            return f"OK. Rows affected: {affected}"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def batch_insert(table: str, columns: list[str], rows: list[list[Any]]) -> str:
    """Insert multiple rows into a table efficiently.
    
    Args:
        table: Table name
        columns: List of column names
        rows: List of rows, where each row is a list of values
    
    Example:
        batch_insert("users", ["name", "email"], [
            ["Alice", "alice@example.com"],
            ["Bob", "bob@example.com"],
            ["Charlie", "charlie@example.com"]
        ])
    """
    if not rows:
        return "Error: No rows to insert."
    
    placeholders = ", ".join(["%s"] * len(columns))
    cols = ", ".join(f"`{c}`" for c in columns)
    sql = f"INSERT INTO `{table}` ({cols}) VALUES ({placeholders})"
    
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
            conn.commit()
            return f"OK. Inserted {cursor.rowcount} rows into {table}."
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def list_tables() -> str:
    """List all tables in the current database with row counts."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            if not tables:
                return "No tables found. Use execute() to create one!"
            
            # Get row counts
            results = []
            key = list(tables[0].keys())[0]
            for t in tables:
                table_name = t[key]
                cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`")
                count = cursor.fetchone()["count"]
                results.append({"table": table_name, "rows": count})
            
            return format_results(results)
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def describe_table(table: str) -> str:
    """Get the schema of a table (columns, types, keys).
    
    Args:
        table: Table name to describe
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"DESCRIBE `{table}`")
            rows = cursor.fetchall()
            return format_results(rows)
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def get_database_info() -> str:
    """Get information about the current database connection and TiDB version."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION() as version")
            version = cursor.fetchone()["version"]
            cursor.execute("SELECT DATABASE() as db")
            db = cursor.fetchone()["db"]
            cursor.execute("SHOW TABLES")
            table_count = len(cursor.fetchall())
            
            return (
                f"Database: {db}\n"
                f"TiDB Version: {version}\n"
                f"Host: {TIDB_CONFIG['host']}\n"
                f"Tables: {table_count}\n"
                f"\nTiDB Cloud Zero — Free serverless MySQL for AI agents.\n"
                f"Get yours at https://zero.tidbcloud.com"
            )
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


# --- Resources ---
@mcp.resource("tidb://tables")
def resource_tables() -> str:
    """List of all tables in the database."""
    return list_tables()


@mcp.resource("tidb://info")
def resource_info() -> str:
    """Database connection info and TiDB version."""
    return get_database_info()


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

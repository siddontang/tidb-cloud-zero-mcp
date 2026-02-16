#!/usr/bin/env python3
"""
Multi-Agent Shared Memory via TiDB Cloud Zero

Three AI agents (Researcher, Writer, Reviewer) collaborate on a content
creation task, sharing knowledge and state through a TiDB Cloud Zero database.

Zero config — the database is auto-provisioned on first run.
All access is via pure HTTP (TiDB Serverless HTTP API).

Architecture:
    ┌────────────┐     ┌────────────┐     ┌────────────┐
    │ Researcher │     │   Writer   │     │  Reviewer  │
    └─────┬──────┘     └─────┬──────┘     └─────┬──────┘
          │                  │                  │
          ▼                  ▼                  ▼
    ┌─────────────────────────────────────────────────┐
    │           TiDB Cloud Zero (HTTP API)            │
    │                                                 │
    │  shared_memory  │  tasks  │  agent_log          │
    └─────────────────────────────────────────────────┘
"""

import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx

# --- TiDB Cloud Zero Auto-Provisioning ---

ZERO_API = "https://zero.tidbapi.com/v1alpha1/instances"


@dataclass
class TiDBInstance:
    host: str
    username: str
    password: str
    database: str = "test"
    expires_at: str = ""

    @property
    def api_url(self) -> str:
        return f"https://http-{self.host}/v1beta/sql"

    @property
    def auth_header(self) -> str:
        creds = f"{self.username}:{self.password}"
        return f"Basic {base64.b64encode(creds.encode()).decode()}"


async def provision() -> TiDBInstance:
    """Auto-provision a TiDB Cloud Zero instance."""
    print("🔄 Provisioning TiDB Cloud Zero instance...", flush=True)
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZERO_API, timeout=30)
    data = resp.json()["instance"]
    conn = data["connection"]
    instance = TiDBInstance(
        host=conn["host"],
        username=conn["username"],
        password=conn["password"],
        expires_at=data.get("expiresAt", ""),
    )
    print(f"✅ Database ready: {instance.host}", flush=True)
    print(f"   Expires: {instance.expires_at}", flush=True)
    return instance


async def sql(instance: TiDBInstance, query: str) -> dict:
    """Execute SQL via HTTP API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": instance.auth_header,
        "TiDB-Database": instance.database,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            instance.api_url,
            headers=headers,
            content=json.dumps({"query": query}),
            timeout=30,
        )
    if resp.status_code != 200:
        raise Exception(f"SQL error: {resp.text}")
    return resp.json()


async def sql_rows(instance: TiDBInstance, query: str) -> list[dict]:
    """Execute SQL and return rows as dicts."""
    data = await sql(instance, query)
    types = data.get("types") or []
    rows = data.get("rows") or []
    return [
        {types[i]["name"]: row[i] for i in range(len(types))}
        for row in rows
    ]


# --- Schema ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_memory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_id VARCHAR(50) NOT NULL,
    topic VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    memory_type ENUM('fact', 'insight', 'draft', 'feedback', 'final') NOT NULL,
    parent_id INT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_topic (topic),
    INDEX idx_agent (agent_id),
    INDEX idx_type (memory_type)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    assigned_to VARCHAR(50),
    status ENUM('pending', 'in_progress', 'done') DEFAULT 'pending',
    payload JSON,
    result JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL,
    INDEX idx_status (status),
    INDEX idx_assigned (assigned_to)
);

CREATE TABLE IF NOT EXISTS agent_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_id VARCHAR(50) NOT NULL,
    action VARCHAR(100) NOT NULL,
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_agent_time (agent_id, created_at DESC)
);
"""


# --- Agent Base ---

class Agent:
    def __init__(self, agent_id: str, role: str, instance: TiDBInstance):
        self.agent_id = agent_id
        self.role = role
        self.db = instance

    async def remember(self, topic: str, content: str, memory_type: str, parent_id: int = None) -> int:
        """Store something in shared memory."""
        parent = f"'{parent_id}'" if parent_id else "NULL"
        escaped = content.replace("'", "''")
        data = await sql(self.db, f"""
            INSERT INTO shared_memory (agent_id, topic, content, memory_type, parent_id)
            VALUES ('{self.agent_id}', '{topic}', '{escaped}', '{memory_type}', {parent})
        """)
        mem_id = int(data.get("sLastInsertID", 0))
        await self.log("remember", {"topic": topic, "type": memory_type, "id": mem_id})
        return mem_id

    async def recall(self, topic: str = None, memory_type: str = None, limit: int = 20) -> list[dict]:
        """Retrieve from shared memory (all agents' memories)."""
        where = []
        if topic:
            where.append(f"topic = '{topic}'")
        if memory_type:
            where.append(f"memory_type = '{memory_type}'")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return await sql_rows(self.db, f"""
            SELECT id, agent_id, topic, content, memory_type, created_at
            FROM shared_memory {clause}
            ORDER BY created_at DESC LIMIT {limit}
        """)

    async def claim_task(self) -> Optional[dict]:
        """Claim a pending task."""
        tasks = await sql_rows(self.db, f"""
            SELECT id, title, payload FROM tasks
            WHERE status = 'pending' AND (assigned_to IS NULL OR assigned_to = '{self.agent_id}')
            ORDER BY created_at ASC LIMIT 1
        """)
        if not tasks:
            return None
        task = tasks[0]
        await sql(self.db, f"""
            UPDATE tasks SET status = 'in_progress', assigned_to = '{self.agent_id}'
            WHERE id = {task['id']} AND status = 'pending'
        """)
        await self.log("claim_task", {"task_id": task["id"], "title": task["title"]})
        return task

    async def complete_task(self, task_id: int, result: dict):
        """Mark task as done with result."""
        escaped = json.dumps(result).replace("'", "''")
        await sql(self.db, f"""
            UPDATE tasks SET status = 'done', result = '{escaped}', completed_at = NOW()
            WHERE id = {task_id}
        """)
        await self.log("complete_task", {"task_id": task_id})

    async def log(self, action: str, details: dict = None):
        """Log agent activity."""
        escaped = json.dumps(details or {}).replace("'", "''")
        await sql(self.db, f"""
            INSERT INTO agent_log (agent_id, action, details)
            VALUES ('{self.agent_id}', '{action}', '{escaped}')
        """)

    def say(self, msg: str):
        icons = {"researcher": "🔍", "writer": "✍️", "reviewer": "📝"}
        icon = icons.get(self.role, "🤖")
        print(f"  {icon} [{self.agent_id}] {msg}", flush=True)


# --- Specialized Agents ---

class Researcher(Agent):
    """Gathers facts and insights on a topic."""

    async def research(self, topic: str):
        self.say(f"Researching: {topic}")
        await asyncio.sleep(0.3)

        # Simulate research findings
        findings = [
            ("TiDB Cloud Zero provides instant MySQL databases via a single API call", "fact"),
            ("Zero signup required — no account, no credit card needed", "fact"),
            ("Databases are disposable (72h TTL) — perfect for CI, demos, agent sessions", "fact"),
            ("HTTP API means no MySQL driver needed — works from edge/serverless", "fact"),
            ("Native vector search support for AI/embedding workloads", "fact"),
            ("Agents need databases that provision as fast as they think", "insight"),
            ("The 'zero friction' pattern reduces agent tool adoption from hours to seconds", "insight"),
            ("Combining disposable DBs with MCP creates a new paradigm for agent-database interaction", "insight"),
        ]

        for content, mem_type in findings:
            mem_id = await self.remember(topic, content, mem_type)
            self.say(f"  📌 [{mem_type}] {content[:60]}... (#{mem_id})")
            await asyncio.sleep(0.1)

        self.say(f"Research complete: {len(findings)} items stored in shared memory")
        return len(findings)


class Writer(Agent):
    """Writes content based on research in shared memory."""

    async def write(self, topic: str):
        self.say(f"Reading shared memory for: {topic}")
        await asyncio.sleep(0.3)

        # Read researcher's findings from shared memory
        facts = await self.recall(topic=topic, memory_type="fact")
        insights = await self.recall(topic=topic, memory_type="insight")

        self.say(f"Found {len(facts)} facts and {len(insights)} insights from Researcher")

        # Compose a draft
        fact_list = "\n".join(f"- {f['content']}" for f in reversed(facts))
        insight_list = "\n".join(f"- {i['content']}" for i in reversed(insights))

        draft = f"""# TiDB Cloud Zero: The Database for AI Agents

## Key Facts
{fact_list}

## Insights
{insight_list}

## Why It Matters
AI agents are becoming autonomous — they need infrastructure that matches their speed.
TiDB Cloud Zero removes the last barrier between an agent and a database: setup time.

One API call. One HTTP endpoint. Zero friction.
The future of agent infrastructure is disposable, instant, and API-first.
"""

        draft_id = await self.remember(topic, draft, "draft")
        self.say(f"Draft written and stored (#{draft_id})")
        return draft_id


class Reviewer(Agent):
    """Reviews drafts and provides feedback."""

    async def review(self, topic: str):
        self.say(f"Looking for drafts to review...")
        await asyncio.sleep(0.3)

        drafts = await self.recall(topic=topic, memory_type="draft")
        if not drafts:
            self.say("No drafts found!")
            return None

        draft = drafts[0]  # most recent
        self.say(f"Reviewing draft #{draft['id']} by {draft['agent_id']}")

        # Provide feedback
        feedback_items = [
            "Strong opening — 'Database for AI Agents' is a clear value prop",
            "Consider adding a code example showing the single API call",
            "The 'disposable' angle is compelling — emphasize ephemeral-by-design",
            "Add comparison: traditional DB setup (minutes/hours) vs Zero (milliseconds)",
            "Insight about 'zero friction pattern' deserves its own section",
        ]

        for fb in feedback_items:
            fb_id = await self.remember(topic, fb, "feedback", parent_id=int(draft["id"]))
            self.say(f"  💬 {fb[:60]}... (#{fb_id})")
            await asyncio.sleep(0.1)

        # Create final version with feedback incorporated
        final = draft["content"] + f"""
## Reviewer Notes
{"".join(chr(10) + '- ' + fb for fb in feedback_items)}

---
*Collaboratively created by Researcher → Writer → Reviewer through shared memory in TiDB Cloud Zero*
"""
        final_id = await self.remember(topic, final, "final", parent_id=int(draft["id"]))
        self.say(f"Review complete. Final version stored (#{final_id})")
        return final_id


# --- Main Demo ---

async def main():
    print("🤝 Multi-Agent Shared Memory Demo", flush=True)
    print("=" * 50, flush=True)
    print(flush=True)

    # Auto-provision database
    db = await provision()
    print(flush=True)

    # Initialize schema
    print("📦 Initializing schema...", flush=True)
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            await sql(db, stmt)
    print("   ✅ Tables: shared_memory, tasks, agent_log", flush=True)
    print(flush=True)

    # Create agents
    researcher = Researcher("researcher-1", "researcher", db)
    writer = Writer("writer-1", "writer", db)
    reviewer = Reviewer("reviewer-1", "reviewer", db)
    topic = "tidb-cloud-zero"

    # Phase 1: Research
    print("━" * 50, flush=True)
    print("📖 Phase 1: Research", flush=True)
    print("━" * 50, flush=True)
    await researcher.research(topic)
    print(flush=True)

    # Phase 2: Write (reads researcher's shared memory)
    print("━" * 50, flush=True)
    print("📖 Phase 2: Write", flush=True)
    print("━" * 50, flush=True)
    await writer.write(topic)
    print(flush=True)

    # Phase 3: Review (reads writer's draft from shared memory)
    print("━" * 50, flush=True)
    print("📖 Phase 3: Review", flush=True)
    print("━" * 50, flush=True)
    await reviewer.review(topic)
    print(flush=True)

    # Summary
    print("━" * 50, flush=True)
    print("📊 Shared Memory Summary", flush=True)
    print("━" * 50, flush=True)

    for mem_type in ["fact", "insight", "draft", "feedback", "final"]:
        rows = await sql_rows(db, f"SELECT COUNT(*) as c FROM shared_memory WHERE memory_type = '{mem_type}'")
        count = rows[0]["c"] if rows else 0
        icons = {"fact": "📌", "insight": "💡", "draft": "📝", "feedback": "💬", "final": "✅"}
        print(f"  {icons.get(mem_type, '📄')} {mem_type}: {count} entries", flush=True)

    # Show agent activity
    print(flush=True)
    print("🤖 Agent Activity:", flush=True)
    logs = await sql_rows(db, "SELECT agent_id, action, COUNT(*) as c FROM agent_log GROUP BY agent_id, action ORDER BY agent_id")
    for log in logs:
        print(f"  {log['agent_id']}: {log['action']} × {log['c']}", flush=True)

    # Show the memory chain
    print(flush=True)
    print("🔗 Memory Chain (how agents built on each other's work):", flush=True)
    chain = await sql_rows(db, """
        SELECT id, agent_id, memory_type, LEFT(content, 60) as preview, parent_id
        FROM shared_memory
        WHERE memory_type IN ('draft', 'feedback', 'final')
        ORDER BY created_at
    """)
    for item in chain:
        parent = f" → reply to #{item['parent_id']}" if item['parent_id'] else ""
        print(f"  #{item['id']} [{item['agent_id']}] {item['memory_type']}: {item['preview']}...{parent}", flush=True)

    print(flush=True)
    print("✨ Three agents, one shared database, zero configuration.", flush=True)
    print(f"   All via TiDB Cloud Zero HTTP API — no MySQL driver needed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

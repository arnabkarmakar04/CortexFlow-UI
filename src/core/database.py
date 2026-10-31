"""PostgreSQL connection configuration and application-table operations."""

from __future__ import annotations

import os
from typing import Any, Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import DuplicateDatabase
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

load_dotenv()

MessageRole = Literal["user", "assistant"]


def database_conninfo() -> str:
    """Return a psycopg connection string from DATABASE_URL or DB_* variables."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url

    return make_conninfo(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "cortexflow"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
    )


async def create_database_if_not_exists() -> None:
    """Create the configured application database if it does not exist."""

    target_conninfo = database_conninfo()
    target_dbname = conninfo_to_dict(target_conninfo).get("dbname")

    if not target_dbname:
        raise ValueError(
            "The database name must be specified in DATABASE_URL or DB_NAME."
        )

    admin_conninfo = make_conninfo(target_conninfo, dbname="postgres")

    async with await AsyncConnection.connect(
        admin_conninfo,
        autocommit=True,
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (target_dbname,),
            )

            if await cur.fetchone() is None:
                try:
                    await cur.execute(
                        sql.SQL("CREATE DATABASE {}").format(
                            sql.Identifier(target_dbname)
                        )
                    )
                except DuplicateDatabase:
                    # Another application instance may have created it concurrently.
                    pass


async def initialize_application_schema(pool: AsyncConnectionPool) -> None:
    """Create the application-owned conversation and transcript tables if absent.

    LangGraph's own checkpoint tables are created separately by AsyncPostgresSaver.setup().
    """
    statements = (
        """
        CREATE TABLE IF NOT EXISTS conversations (
            thread_id UUID PRIMARY KEY,
            title VARCHAR(200) NOT NULL DEFAULT 'New chat',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id BIGSERIAL PRIMARY KEY,
            thread_id UUID NOT NULL REFERENCES conversations(thread_id) ON DELETE CASCADE,
            role VARCHAR(9) NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_messages_thread_created
        ON messages (thread_id, created_at, id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_conversations_updated
        ON conversations (updated_at DESC)
        """,
        # Remove only stale abandoned chats; a blank thread may be open in another client.
        """
        DELETE FROM conversations AS c
        WHERE c.created_at < NOW() - INTERVAL '1 day'
          AND NOT EXISTS (
              SELECT 1 FROM messages AS m WHERE m.thread_id = c.thread_id
          )
        """,
    )
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for statement in statements:
                await cur.execute(statement)


def _conversation_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": row["thread_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def make_title(message: str) -> str:
    """Derive a readable sidebar title from the first line of the first user message."""
    first_line = " ".join((message or "").strip().splitlines()[:1]).strip()
    first_line = " ".join(first_line.split())
    if len(first_line) > 80:
        first_line = first_line[:77].rstrip() + "..."
    return first_line or "New chat"


async def create_conversation(pool: AsyncConnectionPool) -> dict[str, Any]:
    thread_id = uuid4()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO conversations (thread_id, title)
                VALUES (%s, 'New chat')
                RETURNING thread_id, title, created_at, updated_at
                """,
                (thread_id,),
            )
            row = await cur.fetchone()
    return _conversation_dict(row)


async def list_conversations(
    pool: AsyncConnectionPool, search_title: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """List non-empty conversations; the optional search applies to title only."""
    title_filter = (search_title or "").strip()
    pattern = f"%{title_filter}%"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT c.thread_id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id)::INTEGER AS message_count
                FROM conversations AS c
                JOIN messages AS m ON m.thread_id = c.thread_id
                WHERE c.title ILIKE %s
                GROUP BY c.thread_id, c.title, c.created_at, c.updated_at
                ORDER BY c.updated_at DESC
                LIMIT %s
                """,
                (pattern, max(1, min(limit, 200))),
            )
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_conversation_messages(
    pool: AsyncConnectionPool, thread_id: UUID
) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT thread_id, title, created_at, updated_at
                FROM conversations WHERE thread_id = %s
                """,
                (thread_id,),
            )
            conversation = await cur.fetchone()
            if conversation is None:
                return None

            await cur.execute(
                """
                SELECT id, thread_id, role, content, created_at
                FROM messages
                WHERE thread_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (thread_id,),
            )
            messages = await cur.fetchall()
    return {
        "conversation": _conversation_dict(conversation),
        "messages": [dict(message) for message in messages],
    }


async def append_message(
    pool: AsyncConnectionPool,
    thread_id: UUID,
    role: MessageRole,
    content: str,
) -> dict[str, Any] | None:
    """Persist a message and update its conversation title/order atomically."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT c.title,
                       EXISTS (
                           SELECT 1 FROM messages AS existing
                           WHERE existing.thread_id = c.thread_id
                       ) AS has_messages
                FROM conversations AS c
                WHERE c.thread_id = %s
                FOR UPDATE
                """,
                (thread_id,),
            )
            conversation = await cur.fetchone()
            if conversation is None:
                return None

            await cur.execute(
                """
                INSERT INTO messages (thread_id, role, content)
                VALUES (%s, %s, %s)
                RETURNING id, thread_id, role, content, created_at
                """,
                (thread_id, role, content),
            )
            message = await cur.fetchone()

            if role == "user" and not conversation["has_messages"]:
                await cur.execute(
                    """
                    UPDATE conversations
                    SET title = %s, updated_at = NOW()
                    WHERE thread_id = %s
                    """,
                    (make_title(content), thread_id),
                )
            else:
                await cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE thread_id = %s",
                    (thread_id,),
                )
    return dict(message)


async def rename_conversation(
    pool: AsyncConnectionPool, thread_id: UUID, title: str
) -> dict[str, Any] | None:
    """Persist a user-supplied conversation title and update sidebar ordering."""
    clean_title = " ".join(title.strip().split())
    if not clean_title:
        raise ValueError("Conversation title cannot be empty.")
    clean_title = clean_title[:200]
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE conversations
                SET title = %s, updated_at = NOW()
                WHERE thread_id = %s
                RETURNING thread_id, title, created_at, updated_at
                """,
                (clean_title, thread_id),
            )
            row = await cur.fetchone()
    return _conversation_dict(row) if row else None


async def conversation_exists(pool: AsyncConnectionPool, thread_id: UUID) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM conversations WHERE thread_id = %s",
                (thread_id,),
            )
            return await cur.fetchone() is not None


async def delete_conversation(pool: AsyncConnectionPool, thread_id: UUID) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM conversations WHERE thread_id = %s",
                (thread_id,),
            )
            return cur.rowcount > 0
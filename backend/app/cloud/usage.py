"""What cloud models cost: one row per cloud answer, and the monthly budget.

The budget (CLOUD_BUDGET_USD, default 10) is checked before a turn goes to the
cloud; over it, the turn is answered locally with a note saying why. It is an
app-side guard - set a spend limit in the Anthropic Console as well, which the
provider enforces whatever this app does.
"""

import os
from datetime import datetime, timezone

from psycopg import AsyncConnection

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_usage (
    id            BIGSERIAL PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      NUMERIC(12, 6) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cloud_usage_user_month ON cloud_usage (user_id, created_at);
"""


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


def budget_usd() -> float:
    return float(os.getenv("CLOUD_BUDGET_USD", "10"))


async def setup() -> None:
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(SCHEMA)
        await conn.commit()


async def record(*, thread_id: str, user_id: str, model: str, input_tokens: int,
                 output_tokens: int, cost_usd: float) -> None:
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "INSERT INTO cloud_usage (thread_id, user_id, model, input_tokens, output_tokens,"
            " cost_usd) VALUES (%s, %s, %s, %s, %s, %s)",
            (thread_id, user_id, model, input_tokens, output_tokens, round(cost_usd, 6)),
        )
        await conn.commit()


async def month_total(user_id: str, now: datetime | None = None) -> float:
    """USD spent on cloud answers this calendar month (UTC)."""
    url = database_url()
    if not url:
        return 0.0
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with await AsyncConnection.connect(url) as conn:
        row = await (await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cloud_usage"
            " WHERE user_id = %s AND created_at >= %s", (user_id, start),
        )).fetchone()
    return float(row[0])


async def over_budget(user_id: str) -> bool:
    return await month_total(user_id) >= budget_usd()

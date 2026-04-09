"""Tests for chat budget enforcement."""

from datetime import UTC, datetime

import pytest

from sjifire.ops.chat.budget import (
    DAILY_TOKEN_LIMIT,
    MONTHLY_TOKEN_LIMIT,
    check_budget,
    record_usage,
)
from sjifire.ops.chat.store import BudgetStore

_CURRENT_MONTH = datetime.now(UTC).strftime("%Y-%m")


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory store and ensure Cosmos env vars are unset."""
    BudgetStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.cosmos.get_cosmos_container", _noop_container)
    yield
    BudgetStore._memory.clear()


class TestCheckBudget:
    async def test_fresh_user_allowed(self):
        status = await check_budget("new@sjifire.org")
        assert status.allowed is True
        assert status.reason is None

    async def test_monthly_limit_exceeded(self):
        # Pre-load a budget that's over the monthly limit
        async with BudgetStore() as store:
            budget = await store.get_or_create("heavy@sjifire.org", _CURRENT_MONTH)
            budget.input_tokens = MONTHLY_TOKEN_LIMIT
            budget.output_tokens = 1
            await store.update(budget)

        status = await check_budget("heavy@sjifire.org")
        assert status.allowed is False
        assert "Monthly" in status.reason

    async def test_daily_limit_exceeded(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        async with BudgetStore() as store:
            budget = await store.get_or_create("busy@sjifire.org", _CURRENT_MONTH)
            budget.daily_tokens[today] = DAILY_TOKEN_LIMIT + 1
            await store.update(budget)

        status = await check_budget("busy@sjifire.org")
        assert status.allowed is False
        assert "Daily" in status.reason


class TestRecordUsage:
    async def test_records_tokens(self):
        await record_usage("user@sjifire.org", input_tokens=500, output_tokens=100)

        month = datetime.now(UTC).strftime("%Y-%m")
        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", month)

        assert budget.input_tokens == 500
        assert budget.output_tokens == 100
        assert budget.estimated_cost_usd > 0

    async def test_accumulates_usage(self):
        await record_usage("user@sjifire.org", input_tokens=500, output_tokens=100)
        await record_usage("user@sjifire.org", input_tokens=300, output_tokens=200)

        month = datetime.now(UTC).strftime("%Y-%m")
        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", month)

        assert budget.input_tokens == 800
        assert budget.output_tokens == 300

    async def test_tracks_daily_tokens(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        await record_usage("user@sjifire.org", input_tokens=1000, output_tokens=500)

        month = datetime.now(UTC).strftime("%Y-%m")
        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", month)

        assert today in budget.daily_tokens
        assert budget.daily_tokens[today] == 1500

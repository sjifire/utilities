"""Token budget enforcement for chat conversations.

All state is in Cosmos DB (no in-process caches) since containers
are ephemeral with minReplicas: 0.

Limits:
- Monthly per-user: 2M tokens (~$30 at Sonnet pricing)
- Daily per-user: 1M tokens
- Per-conversation: 50 turns (enforced in engine, not here)
- Anthropic console: $100/month hard cap (external)
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sjifire.ops.chat.store import BudgetStore

logger = logging.getLogger(__name__)

# Sonnet pricing: $3/M input, $15/M output
_INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

MONTHLY_TOKEN_LIMIT = 5_000_000
DAILY_TOKEN_LIMIT = 1_000_000


@dataclass(frozen=True)
class BudgetStatus:
    """Result of a budget check."""

    allowed: bool
    reason: str | None = None


async def check_budget(user_email: str) -> BudgetStatus:
    """Check all budget limits for a user.

    Returns BudgetStatus indicating whether the user can send a message.
    """
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    today = now.strftime("%Y-%m-%d")

    async with BudgetStore() as store:
        budget = await store.get_or_create(user_email, month)

    # Monthly limit
    monthly_total = budget.input_tokens + budget.output_tokens
    if monthly_total >= MONTHLY_TOKEN_LIMIT:
        return BudgetStatus(
            allowed=False,
            reason=f"Monthly token limit reached ({monthly_total:,}/{MONTHLY_TOKEN_LIMIT:,}). "
            f"Resets next month.",
        )

    # Daily limit
    daily_total = budget.daily_tokens.get(today, 0)
    if daily_total >= DAILY_TOKEN_LIMIT:
        return BudgetStatus(
            allowed=False,
            reason=f"Daily token limit reached ({daily_total:,}/{DAILY_TOKEN_LIMIT:,}). "
            f"Try again tomorrow.",
        )

    return BudgetStatus(allowed=True)


async def record_usage(user_email: str, input_tokens: int, output_tokens: int) -> None:
    """Record token usage in the user's monthly budget."""
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    today = now.strftime("%Y-%m-%d")

    async with BudgetStore() as store:
        budget = await store.get_or_create(user_email, month)

        budget.input_tokens += input_tokens
        budget.output_tokens += output_tokens
        daily_prev = budget.daily_tokens.get(today, 0)
        budget.daily_tokens[today] = daily_prev + input_tokens + output_tokens
        budget.estimated_cost_usd = (
            budget.input_tokens * _INPUT_COST_PER_TOKEN
            + budget.output_tokens * _OUTPUT_COST_PER_TOKEN
        )
        budget.updated_at = now

        await store.update(budget)

    logger.info(
        "Recorded usage for %s: +%d in / +%d out (month total: $%.2f)",
        user_email,
        input_tokens,
        output_tokens,
        budget.estimated_cost_usd,
    )

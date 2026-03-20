"""
helpers/budget.py — Budget tracker for auto-fix loops.
Tracks cumulative Claude invocations and estimated cost across a build session.
"""

from dataclasses import dataclass, field


@dataclass
class BudgetTracker:
    """Shared across nested fix loops to enforce per-session budget limits."""
    max_cost_usd: float = 10.0
    max_invocations: int = 20
    total_cost_usd: float = 0.0
    total_invocations: int = 0

    def record(self, cost_usd: float) -> None:
        """Record a Claude invocation and its cost."""
        self.total_invocations += 1
        self.total_cost_usd += cost_usd

    @property
    def exceeded(self) -> bool:
        return (self.total_cost_usd >= self.max_cost_usd
                or self.total_invocations >= self.max_invocations)

    @property
    def exceeded_message(self) -> str:
        return (
            f"⚠️ Build budget reached (${self.total_cost_usd:.2f} spent, "
            f"{self.total_invocations} invocations). Use /fix to continue manually."
        )

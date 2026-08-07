from dataclasses import dataclass

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import ExplainResult


@dataclass(frozen=True, slots=True)
class CostPolicy:
    maximum_estimated_rows: int
    maximum_cost: float


class QueryCostGuard:
    def validate(self, plan: ExplainResult, policy: CostPolicy) -> None:
        if (
            plan.estimated_rows is not None
            and plan.estimated_rows > policy.maximum_estimated_rows
        ):
            raise Prompt2InsightError(
                ErrorCode.QUERY_TOO_EXPENSIVE,
                "The query plan estimates too many rows.",
            )

        if plan.estimated_cost is not None and plan.estimated_cost > policy.maximum_cost:
            raise Prompt2InsightError(
                ErrorCode.QUERY_TOO_EXPENSIVE,
                "The query plan exceeds the cost threshold.",
            )

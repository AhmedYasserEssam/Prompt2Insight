import pytest

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import ExplainResult
from app.infrastructure.sql.cost_guard import CostPolicy, QueryCostGuard


@pytest.mark.parametrize(
    "plan",
    [
        ExplainResult(raw_plan={}, estimated_rows=None, estimated_cost=1),
        ExplainResult(raw_plan={}, estimated_rows=1, estimated_cost=None),
        ExplainResult(raw_plan={}, estimated_rows=101, estimated_cost=1),
        ExplainResult(raw_plan={}, estimated_rows=1, estimated_cost=101),
    ],
)
def test_missing_or_excessive_plan_estimates_fail_closed(plan: ExplainResult) -> None:
    with pytest.raises(Prompt2InsightError) as captured:
        QueryCostGuard().validate(plan, CostPolicy(maximum_estimated_rows=100, maximum_cost=100))
    assert captured.value.code is ErrorCode.QUERY_TOO_EXPENSIVE


def test_inexpensive_plan_is_accepted() -> None:
    QueryCostGuard().validate(
        ExplainResult(raw_plan={"Plan": {}}, estimated_rows=100, estimated_cost=100),
        CostPolicy(maximum_estimated_rows=100, maximum_cost=100),
    )

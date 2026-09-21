# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import pytest
from card_janitor.models import (
    AgeCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    IntervalCondition,
    ReviewHistoryCondition,
)
from card_janitor.presentation import describe_conditions

A = CardStateCondition(("review",))
B = AgeCondition(365, "first_review", "gte")
C = IntervalCondition(365, "gte")
D = ReviewHistoryCondition("exists")


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        pytest.param(
            AllConditions((A, B, C)),
            "Card state is Review\nAND Age since first review ≥ 365 days\nAND Interval ≥ 365 days",
            id="flat_conditions",
        ),
        pytest.param(
            AllConditions((A, AnyConditions((B, C)))),
            "Card state is Review\n"
            "AND (\n"
            "  Age since first review ≥ 365 days\n"
            "  OR Interval ≥ 365 days\n"
            ")",
            id="condition_followed_by_group_top_and_inner_or",
        ),
        pytest.param(
            AnyConditions((AllConditions((A, D)), C)),
            "(\n  Card state is Review\n  AND Review history exists\n)\nOR Interval ≥ 365 days",
            id="group_followed_by_condition_top_or_inner_and",
        ),
        pytest.param(
            AllConditions((AnyConditions((B, C)),)),
            "(\n  Age since first review ≥ 365 days\n  OR Interval ≥ 365 days\n)",
            id="group_only",
        ),
        pytest.param(
            AllConditions((AnyConditions((A, B)), AnyConditions((C, D)))),
            "(\n"
            "  Card state is Review\n"
            "  OR Age since first review ≥ 365 days\n"
            ")\n"
            "AND (\n"
            "  Interval ≥ 365 days\n"
            "  OR Review history exists\n"
            ")",
            id="multiple_groups",
        ),
    ],
)
def test_describe_conditions_uses_multiline_group_blocks(
    condition: AllConditions | AnyConditions,
    expected: str,
) -> None:
    assert describe_conditions(condition) == expected

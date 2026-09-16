"""The values a review's outcome can take, and nothing else (#4).

Verdicts and review statuses were free-form strings compared by equality in a
dozen places, and free-form strings drift: a reviewer's "APPROVE", a status of
"completed". These enums own the literals. A verdict that is not one of them is
INVALID when read, a status that is not one of them is refused when written, and
the playbook and docs/protocol.md list the same values - a test keeps the three
in agreement.
"""
from enum import Enum


class Verdict(str, Enum):
    """A review's verdict. A reviewer writes the first two; ao writes the other two."""
    APPROVED = "APPROVED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    UNAVAILABLE = "UNAVAILABLE"       # no review took place: not a verdict, not a round
    INVALID = "INVALID"               # an answer that broke the verdict and count schema


class ReviewStatus(str, Enum):
    """How a review run ended, recorded in strict evidence as review_status."""
    PENDING = "pending"
    COMPLETE = "complete"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


VERDICTS = tuple(item.value for item in Verdict)
REVIEWER_VERDICTS = (Verdict.APPROVED.value, Verdict.NEEDS_CHANGES.value)
REVIEW_STATUSES = tuple(item.value for item in ReviewStatus)

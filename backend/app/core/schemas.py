"""Shared response envelopes."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Offset-paginated collection.

    Offset pagination is used because the dashboard needs jump-to-page and a
    total count for its filter summary, both of which cursors do not give.
    The cost is that deep offsets degrade (the database still walks the skipped
    rows) - at the scale where that matters, list endpoints move to keyset
    pagination ordered by (joining_date, id). Noted in docs/decisions.md.
    """

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

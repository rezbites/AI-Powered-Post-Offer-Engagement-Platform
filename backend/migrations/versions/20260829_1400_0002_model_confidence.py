"""Record the model's self-reported confidence separately

The displayed `risk_confidence` is derived from observable evidence quality
(see app/domain/confidence.py). The model is *also* asked for its own
confidence, and that value is stored here rather than discarded.

Keeping both makes the gap measurable: if the model routinely claims 0.9 on
analyses our own evidence scoring rates 0.4, that is a calibration finding
worth acting on. Discarding the self-report would throw away the only data that
could ever demonstrate it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_analyses",
        sa.Column("model_confidence", sa.Float(), nullable=True),
    )
    # Count of signals discarded by the grounding guardrail. A rising rate is
    # an early warning that a prompt or model change has begun hallucinating
    # quotes, so it belongs in the ledger rather than only in logs.
    op.add_column(
        "ai_analyses",
        sa.Column("dropped_signals", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_analyses", "dropped_signals")
    op.drop_column("ai_analyses", "model_confidence")

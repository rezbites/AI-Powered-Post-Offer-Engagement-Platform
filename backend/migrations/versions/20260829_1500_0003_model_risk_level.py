"""Store the model's proposed risk band separately from the blended band

Architectural correction. The hybrid design states that the LLM performs
semantic extraction only and never picks the risk band: signals feed the
deterministic engine, which computes the band from countable facts *and*
signals together.

The model is still asked for a band, because its disagreement with the engine
is informative - a persistent gap is evidence that either the prompt or the
weights need revisiting. So it is stored here as telemetry rather than being
either discarded or, worse, silently treated as the answer.

`ai_analyses.risk_level` therefore holds the authoritative blended band that
the product displays; `model_risk_level` holds what the model proposed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_analyses",
        sa.Column("model_risk_level", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_analyses", "model_risk_level")

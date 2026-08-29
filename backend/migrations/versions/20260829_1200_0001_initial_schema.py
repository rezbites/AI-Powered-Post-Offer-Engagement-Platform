"""Initial schema

Creates the full post-offer engagement model. Tables are created in FK
dependency order and dropped in reverse.

Revision ID: 0001
Revises:
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- recruiters ---------------------------------------------------------
    op.create_table(
        "recruiters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="recruiter"),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_recruiters_email", "recruiters", ["email"], unique=True)

    # --- journey_templates --------------------------------------------------
    op.create_table(
        "journey_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- journey_stages -----------------------------------------------------
    op.create_table(
        "journey_stages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("journey_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sla_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("template_id", "key", name="uq_journey_stage_template_key"),
        sa.UniqueConstraint("template_id", "sequence", name="uq_journey_stage_template_sequence"),
    )
    op.create_index("ix_journey_stages_template_id", "journey_stages", ["template_id"])

    # --- candidates ---------------------------------------------------------
    op.create_table(
        "candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("role_title", sa.String(120), nullable=False),
        sa.Column("location", sa.String(120), nullable=False),
        sa.Column("offer_date", sa.Date(), nullable=False),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="offer_accepted"),
        sa.Column("journey_template_id", sa.String(36), sa.ForeignKey("journey_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=False, server_default="LOW"),
        sa.Column("risk_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_source", sa.String(10), nullable=False, server_default="rule"),
        sa.Column("risk_override_reason", sa.Text(), nullable=True),
        sa.Column("risk_overridden_by", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("risk_overridden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"], unique=True)
    op.create_index("ix_candidates_name", "candidates", ["name"])
    op.create_index("ix_candidates_role_title", "candidates", ["role_title"])
    op.create_index("ix_candidates_location", "candidates", ["location"])
    op.create_index("ix_candidates_joining_date", "candidates", ["joining_date"])
    op.create_index("ix_candidates_recruiter_id", "candidates", ["recruiter_id"])
    op.create_index("ix_candidates_status", "candidates", ["status"])
    op.create_index("ix_candidates_risk_level", "candidates", ["risk_level"])
    op.create_index("ix_candidates_last_interaction_at", "candidates", ["last_interaction_at"])
    # Composite indexes matching the dashboard's dominant query shapes.
    op.create_index("ix_candidates_recruiter_joining", "candidates", ["recruiter_id", "joining_date"])
    op.create_index("ix_candidates_status_risk", "candidates", ["status", "risk_level"])

    # --- candidate_stages ---------------------------------------------------
    op.create_table(
        "candidate_stages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_id", sa.String(36), sa.ForeignKey("journey_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("candidate_id", "stage_id", name="uq_candidate_stage"),
    )
    op.create_index("ix_candidate_stages_candidate_id", "candidate_stages", ["candidate_id"])
    op.create_index("ix_candidate_stages_candidate_status", "candidate_stages", ["candidate_id", "status"])

    # --- interactions -------------------------------------------------------
    op.create_table(
        "interactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column("direction", sa.String(10), nullable=False, server_default="outbound"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interactions_candidate_id", "interactions", ["candidate_id"])
    op.create_index("ix_interactions_occurred_at", "interactions", ["occurred_at"])
    op.create_index("ix_interactions_candidate_occurred", "interactions", ["candidate_id", "occurred_at"])

    # --- ai_analyses --------------------------------------------------------
    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(10), nullable=False, server_default="LOW"),
        sa.Column("risk_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("next_action", sa.String(40), nullable=False, server_default="NO_ACTION"),
        sa.Column("recommended_follow_up", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(20), nullable=False, server_default="mock"),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="valid"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_analyses_candidate_id", "ai_analyses", ["candidate_id"])
    op.create_index("ix_ai_analyses_input_hash", "ai_analyses", ["input_hash"])
    op.create_index("ix_ai_analyses_candidate_hash", "ai_analyses", ["candidate_id", "input_hash"])
    op.create_index("ix_ai_analyses_created", "ai_analyses", ["created_at"])

    # --- generated_messages -------------------------------------------------
    op.create_table(
        "generated_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(40), nullable=False, server_default="warm_professional"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="mock"),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_generated_messages_candidate_id", "generated_messages", ["candidate_id"])

    # --- follow_up_actions --------------------------------------------------
    op.create_table(
        "follow_up_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_key", sa.String(60), nullable=True),
        sa.Column("dedupe_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("recommended_action", sa.String(40), nullable=False, server_default="NO_ACTION"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Idempotency: one action per (candidate, rule, day). This is what makes
        # an hourly scheduler safe to re-run and safe to trigger by hand.
        sa.UniqueConstraint("candidate_id", "rule_key", "dedupe_date", name="uq_follow_up_idempotency"),
    )
    op.create_index("ix_follow_up_actions_candidate_id", "follow_up_actions", ["candidate_id"])
    op.create_index("ix_follow_ups_status_due", "follow_up_actions", ["status", "due_date"])

    # --- automation_runs ----------------------------------------------------
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_key", sa.String(60), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidates_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_runs_rule_key", "automation_runs", ["rule_key"])

    # --- audit_log ----------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id", "created_at"])


def downgrade() -> None:
    # Reverse dependency order so foreign keys never block a drop.
    op.drop_table("audit_log")
    op.drop_table("automation_runs")
    op.drop_table("follow_up_actions")
    op.drop_table("generated_messages")
    op.drop_table("ai_analyses")
    op.drop_table("interactions")
    op.drop_table("candidate_stages")
    op.drop_table("candidates")
    op.drop_table("journey_stages")
    op.drop_table("journey_templates")
    op.drop_table("recruiters")

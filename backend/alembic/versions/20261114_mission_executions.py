"""Mission executions on PX4 SITL and their event log (WRITTEN, NOT APPLIED to the real database)."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261114_mission_executions"
down_revision: Union[str, Sequence[str], None] = "20261113_attack_lab_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mission_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("mission_deployments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_version_id", sa.String(), sa.ForeignKey("scenario_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aircraft_id", sa.String(16), nullable=False),
        sa.Column("instance_number", sa.Integer(), nullable=False),
        sa.Column("started_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_by_name", sa.String(120), nullable=False),
        sa.Column("started_by_role", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=False),
        sa.Column("last_event_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    for column in ("deployment_id", "scenario_id", "scenario_version_id", "aircraft_id", "started_by", "state", "created_at"):
        op.create_index(f"ix_mission_executions_{column}", "mission_executions", [column])
    op.create_table(
        "mission_execution_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.String(), sa.ForeignKey("mission_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("actor_name", sa.String(120), nullable=True),
        sa.Column("executor_index", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("execution_id", "executor_index", name="uq_execution_event_index"),
    )
    for column in ("execution_id", "occurred_at"):
        op.create_index(f"ix_mission_execution_events_{column}", "mission_execution_events", [column])


def downgrade() -> None:
    op.drop_table("mission_execution_events")
    op.drop_table("mission_executions")

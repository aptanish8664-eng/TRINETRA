"""Stored Attack Lab experiment results; they outlive their experiment (WRITTEN, NOT APPLIED to the real database)."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_experiment_results"
down_revision: Union[str, Sequence[str], None] = "20261114_mission_executions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("experiment_id", sa.String(), nullable=False),
        sa.Column("experiment_name", sa.String(120), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("scenario_version_id", sa.String(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("experiment_deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("experiment_id", "owner_user_id", "created_at"):
        op.create_index(f"ix_experiment_results_{column}", "experiment_results", [column])


def downgrade() -> None:
    op.drop_table("experiment_results")

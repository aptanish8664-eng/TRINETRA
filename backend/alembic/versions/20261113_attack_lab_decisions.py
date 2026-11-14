"""Attack Lab experiments and recorded research decisions (WRITTEN, NOT APPLIED to the real database)."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261113_attack_lab_decisions"
down_revision: Union[str, Sequence[str], None] = "20261111_mission_deployments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attack_experiments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scenario_id", sa.String(), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_version_id", sa.String(), sa.ForeignKey("scenario_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preset_id", sa.String(40), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("owner_user_id", "scenario_id", "scenario_version_id", "status", "created_at"):
        op.create_index(f"ix_attack_experiments_{column}", "attack_experiments", [column])
    op.create_table(
        "research_decisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("aircraft_id", sa.String(16), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("handover_candidate", sa.String(16), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_name", sa.String(120), nullable=True),
        sa.Column("tier", sa.String(12), nullable=True),
        sa.Column("threat_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("user_id", "aircraft_id", "created_at"):
        op.create_index(f"ix_research_decisions_{column}", "research_decisions", [column])


def downgrade() -> None:
    op.drop_table("research_decisions")
    op.drop_table("attack_experiments")

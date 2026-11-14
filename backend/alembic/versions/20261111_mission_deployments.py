"""Persist exact validated Mission Control handoffs."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261111_mission_deployments"
down_revision: Union[str, Sequence[str], None] = "17ea6f9d666a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mission_deployments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scenario_id", sa.String(), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_version_id", sa.String(), sa.ForeignKey("scenario_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployed_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mission_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scenario_version_id", name="uq_deployment_scenario_version"),
    )
    op.create_index("ix_mission_deployments_scenario_id", "mission_deployments", ["scenario_id"])
    op.create_index("ix_mission_deployments_scenario_version_id", "mission_deployments", ["scenario_version_id"])
    op.create_index("ix_mission_deployments_deployed_by", "mission_deployments", ["deployed_by"])
    op.create_index("ix_mission_deployments_mission_id", "mission_deployments", ["mission_id"])
    op.create_index("ix_mission_deployments_created_at", "mission_deployments", ["created_at"])


def downgrade() -> None:
    op.drop_table("mission_deployments")

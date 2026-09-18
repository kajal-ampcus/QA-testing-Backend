"""Scope requirement codes and enforce append-only artifact versions.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Earlier crawls could all have version 1. Renumber them in creation order
    # before adding the constraint so existing maps remain readable.
    op.execute(
        """
        UPDATE application_maps AS map
        SET version = numbered.version
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY project_id ORDER BY created_at, id
            ) AS version
            FROM application_maps
        ) AS numbered
        WHERE map.id = numbered.id
        """
    )
    op.drop_constraint("requirements_req_code_key", "requirements", type_="unique")
    op.create_unique_constraint(
        "uq_requirements_project_code", "requirements", ["project_id", "req_code"]
    )
    op.create_unique_constraint(
        "uq_requirement_versions_number", "requirement_versions", ["requirement_id", "version"]
    )
    op.create_unique_constraint(
        "uq_application_maps_project_version", "application_maps", ["project_id", "version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_application_maps_project_version", "application_maps", type_="unique")
    op.drop_constraint("uq_requirement_versions_number", "requirement_versions", type_="unique")
    op.drop_constraint("uq_requirements_project_code", "requirements", type_="unique")
    op.create_unique_constraint("requirements_req_code_key", "requirements", ["req_code"])

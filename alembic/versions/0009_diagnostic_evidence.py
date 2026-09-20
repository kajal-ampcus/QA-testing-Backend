"""
Add diagnostic_evidence column to application_maps table.

Stores structured failure evidence (login errors, console errors,
network failures, screenshots) when discovery fails or is partial.
This lets developers see exactly what went wrong without re-running.

Revision: 0008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stores structured JSON evidence for failed/partial discoveries:
    # {
    #   "login_error": "Could not find submit button ...",
    #   "auth_attempted": true,
    #   "auth_succeeded": false,
    #   "screenshot_ref": "artifacts/discovery/uuid.png",
    #   "console_errors": [{"level": "error", "text": "..."}],
    #   "network_errors": [{"method": "POST", "url": "/api/login", "status": "401"}],
    #   "failed_actions": [{"action": "click(button, Log in)", "error": "RuntimeError"}],
    #   "termination_detail": "Human-readable explanation of why discovery ended"
    # }
    op.add_column(
        "application_maps",
        sa.Column(
            "diagnostic_evidence",
            postgresql.JSONB,
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("application_maps", "diagnostic_evidence")

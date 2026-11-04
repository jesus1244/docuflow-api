"""initial schema

Revision ID: 8512962ea41a
Revises:
Create Date: 2026-11-04 15:43:17.980003

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8512962ea41a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "auth_user",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("auth_user_pkey")),
    )
    op.create_index(op.f("auth_user_email_idx"), "auth_user", ["email"], unique=True)
    op.create_table(
        "auth_api_key",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_user.id"],
            name=op.f("auth_api_key_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("auth_api_key_pkey")),
        sa.UniqueConstraint("key_hash", name=op.f("auth_api_key_key_hash_key")),
    )
    op.create_index(op.f("auth_api_key_user_id_idx"), "auth_api_key", ["user_id"], unique=False)
    op.create_table(
        "auth_refresh_token",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_user.id"],
            name=op.f("auth_refresh_token_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("auth_refresh_token_pkey")),
    )
    op.create_index(
        op.f("auth_refresh_token_family_id_idx"), "auth_refresh_token", ["family_id"], unique=False
    )
    op.create_index(
        op.f("auth_refresh_token_user_id_idx"), "auth_refresh_token", ["user_id"], unique=False
    )
    op.create_table(
        "document",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="document_status"),
            nullable=False,
        ),
        sa.Column(
            "document_type",
            sa.Enum(
                "invoice", "receipt", "contract", "purchase_order", "other", name="document_type"
            ),
            nullable=True,
        ),
        sa.Column("extracted_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_reviewed", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["auth_user.id"], name=op.f("document_owner_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("document_pkey")),
    )
    op.create_index(
        "document_owner_created_idx", "document", ["owner_id", "created_at"], unique=False
    )
    op.create_index(op.f("document_status_idx"), "document", ["status"], unique=False)
    op.create_table(
        "webhook_endpoint",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("secret", sa.String(length=100), nullable=False),
        sa.Column("events", sa.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["auth_user.id"],
            name=op.f("webhook_endpoint_owner_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("webhook_endpoint_pkey")),
    )
    op.create_index(
        op.f("webhook_endpoint_owner_id_idx"), "webhook_endpoint", ["owner_id"], unique=False
    )
    op.create_table(
        "webhook_delivery",
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "succeeded", "failed", name="delivery_status"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["webhook_endpoint.id"],
            name=op.f("webhook_delivery_endpoint_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("webhook_delivery_pkey")),
    )
    op.create_index(
        op.f("webhook_delivery_endpoint_id_idx"), "webhook_delivery", ["endpoint_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(op.f("webhook_delivery_endpoint_id_idx"), table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index(op.f("webhook_endpoint_owner_id_idx"), table_name="webhook_endpoint")
    op.drop_table("webhook_endpoint")
    op.drop_index(op.f("document_status_idx"), table_name="document")
    op.drop_index("document_owner_created_idx", table_name="document")
    op.drop_table("document")
    op.drop_index(op.f("auth_refresh_token_user_id_idx"), table_name="auth_refresh_token")
    op.drop_index(op.f("auth_refresh_token_family_id_idx"), table_name="auth_refresh_token")
    op.drop_table("auth_refresh_token")
    op.drop_index(op.f("auth_api_key_user_id_idx"), table_name="auth_api_key")
    op.drop_table("auth_api_key")
    op.drop_index(op.f("auth_user_email_idx"), table_name="auth_user")
    op.drop_table("auth_user")
    for enum_name in ("delivery_status", "document_type", "document_status"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)

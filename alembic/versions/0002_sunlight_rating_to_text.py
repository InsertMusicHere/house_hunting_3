"""sunlight rating to text

sunlight_rating changes from SMALLINT to TEXT so it can hold values such as
"Good", "Great", "Average" or longer descriptive notes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing numbers (if any) are kept as their text form, e.g. 4 -> '4'.
    op.alter_column(
        'property_features', 'sunlight_rating',
        existing_type=sa.SmallInteger(),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using='sunlight_rating::text',
    )


def downgrade() -> None:
    # WARNING: text values that are not whole numbers (e.g. 'Good') cannot be
    # represented as a number and become NULL when downgrading.
    op.alter_column(
        'property_features', 'sunlight_rating',
        existing_type=sa.Text(),
        type_=sa.SmallInteger(),
        existing_nullable=True,
        postgresql_using="CASE WHEN sunlight_rating ~ '^[0-9]{1,4}$' "
                         "THEN sunlight_rating::smallint ELSE NULL END",
    )

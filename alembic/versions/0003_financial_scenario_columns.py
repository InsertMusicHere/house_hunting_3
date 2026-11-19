"""financial scenario columns

Adds the Phase 5 inputs to financial_details (first-time buyer, Toronto override, term,
compounding, non-traditional down payment, which of $ / % was edited last, legal, title
insurance, inspection and other closing costs, primary-scenario flag) and CHECK constraints
for the mortgage type, compounding and down-payment basis values.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

T = 'financial_details'


def upgrade() -> None:
    op.add_column(T, sa.Column('first_time_buyer', sa.Boolean(), nullable=True))
    op.add_column(T, sa.Column('toronto_override', sa.Boolean(), nullable=True))
    op.add_column(T, sa.Column('term_years', sa.SmallInteger(), nullable=True))
    op.add_column(T, sa.Column('compounding', sa.String(20), nullable=True))
    op.add_column(T, sa.Column('non_traditional_down_payment', sa.Boolean(), nullable=False,
                               server_default=sa.text('false')))
    op.add_column(T, sa.Column('down_payment_basis', sa.String(10), nullable=True))
    op.add_column(T, sa.Column('legal_fees', sa.Numeric(10, 2), nullable=True))
    op.add_column(T, sa.Column('title_insurance', sa.Numeric(10, 2), nullable=True))
    op.add_column(T, sa.Column('inspection_costs', sa.Numeric(10, 2), nullable=True))
    op.add_column(T, sa.Column('other_closing_costs', sa.Numeric(10, 2), nullable=True))
    op.add_column(T, sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    op.create_check_constraint(
        'ck_financial_details_mortgage_type_values', T,
        "mortgage_type IS NULL OR mortgage_type IN ('Fixed', 'Variable')")
    op.create_check_constraint(
        'ck_financial_details_compounding_values', T,
        "compounding IS NULL OR compounding IN ('semi_annual', 'monthly')")
    op.create_check_constraint(
        'ck_financial_details_down_payment_basis_values', T,
        "down_payment_basis IS NULL OR down_payment_basis IN ('amount', 'percent')")
    op.create_index(
        'uq_financial_details_one_primary', T, ['property_id'], unique=True,
        postgresql_where=sa.text('is_primary'), sqlite_where=sa.text('is_primary'))


def downgrade() -> None:
    op.drop_index('uq_financial_details_one_primary', table_name=T)
    op.drop_constraint('ck_financial_details_down_payment_basis_values', T, type_='check')
    op.drop_constraint('ck_financial_details_compounding_values', T, type_='check')
    op.drop_constraint('ck_financial_details_mortgage_type_values', T, type_='check')
    for col in ('is_primary', 'other_closing_costs', 'inspection_costs', 'title_insurance', 'legal_fees',
                'down_payment_basis', 'non_traditional_down_payment', 'compounding', 'term_years',
                'toronto_override', 'first_time_buyer'):
        op.drop_column(T, col)
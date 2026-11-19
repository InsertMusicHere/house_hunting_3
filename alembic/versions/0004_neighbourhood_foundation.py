"""Phase 6a location and research provenance.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    for name, type_ in [
        ('latitude', sa.Float()), ('longitude', sa.Float()),
        ('geocode_source', sa.Text()), ('geocode_confidence', sa.String(20)),
        ('geocoded_at', sa.DateTime(timezone=True)),
        ('location_verified_at', sa.DateTime(timezone=True)),
        ('location_is_manual', sa.Boolean()),
    ]:
        op.add_column('properties', sa.Column(name, type_, nullable=True))
    op.create_table('neighbourhood_research',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('property_id', sa.Uuid(), sa.ForeignKey('properties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('radius_m', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('params', JSONB(), nullable=False),
        sa.Column('provider_status', JSONB(), nullable=True))
    op.create_index('ix_research_property_run', 'neighbourhood_research', ['property_id', 'run_at'])
    op.create_table('research_items',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('neighbourhood_research.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('value_num', sa.Numeric(18, 6)),
        sa.Column('value_text', sa.Text()),
        sa.Column('value_json', JSONB()),
        sa.Column('unit', sa.String(40)),
        sa.Column('method', sa.String(20), nullable=False),
        sa.Column('confidence', sa.String(20)),
        sa.Column('source_name', sa.String(120)),
        sa.Column('source_url', sa.Text()),
        sa.Column('dataset_vintage', sa.String(100)),
        sa.Column('retrieved_at', sa.DateTime(timezone=True)),
        sa.Column('observed_from', sa.Date()),
        sa.Column('observed_to', sa.Date()),
        sa.Column('attribution', sa.Text()),
        sa.Column('note', sa.Text()))
    op.create_index('ix_research_items_run', 'research_items', ['run_id'])
    op.create_table('research_cache',
        sa.Column('cache_key', sa.String(64), primary_key=True),
        sa.Column('provider', sa.String(80), nullable=False),
        sa.Column('response_json', JSONB(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_research_cache_expiry', 'research_cache', ['expires_at'])


def downgrade():
    op.drop_index('ix_research_cache_expiry', table_name='research_cache')
    op.drop_table('research_cache')
    op.drop_index('ix_research_items_run', table_name='research_items')
    op.drop_table('research_items')
    op.drop_index('ix_research_property_run', table_name='neighbourhood_research')
    op.drop_table('neighbourhood_research')
    for name in ('location_is_manual', 'location_verified_at', 'geocoded_at',
                 'geocode_confidence', 'geocode_source', 'longitude', 'latitude'):
        op.drop_column('properties', name)

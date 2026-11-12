"""indexes for deletes

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 22:10:00
"""
from alembic import op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    # Cleaning up a demo deletes its customers and products. For each one the database
    # checks the orders and order lines that point at it; without these indexes it read
    # every row of those tables each time and held the database locked for minutes.
    op.create_index('ix_orders_customer', 'orders', ['customer_id'], unique=False)
    op.create_index('ix_order_lines_product', 'order_lines', ['product_id'], unique=False)


def downgrade():
    op.drop_index('ix_order_lines_product', table_name='order_lines')
    op.drop_index('ix_orders_customer', table_name='orders')

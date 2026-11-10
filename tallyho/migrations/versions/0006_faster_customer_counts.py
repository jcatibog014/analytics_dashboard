"""faster customer counts

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10 23:19:13.747307
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None




def upgrade():
    # After the benchmark (docs/BENCHMARKS.md): the customer joins the date index, so
    # "how many different customers" is read from the index alone; and new customers
    # are counted from the customers table by their first day.
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.create_index('ix_customers_workspace_first_day', ['workspace_id', 'first_order_date'], unique=False)

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_workspace_day')
        batch_op.create_index('ix_orders_workspace_day_customer', ['workspace_id', 'order_date', 'customer_id'],
                              unique=False)


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_workspace_day_customer')
        batch_op.create_index('ix_orders_workspace_day', ['workspace_id', 'order_date'], unique=False)

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_index('ix_customers_workspace_first_day')

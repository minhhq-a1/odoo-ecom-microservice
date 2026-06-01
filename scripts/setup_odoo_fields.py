"""
⚠️  DEPRECATED: This script is outdated and incomplete.

Use the Odoo addon instead:
  Location: ~/odoo-workspace/18.0/extra-addons/onnet-dc7-internal/addons/custom/a1_sale_ecom_middleware/

The addon provides:
  - All sale.order fields (x_platform, x_platform_order_id, x_platform_order_sn, x_sync_status, x_tracking_number)
  - res.partner fields (x_platform_source, x_platform_buyer_id)
  - product.product fields (x_marketplace_buffer_pct, x_block_marketplace_sync)
  - Views, ACLs, constraints, and audit logging

To install the addon:
  1. Ensure the addon is in your Odoo addons path
  2. Update the app list in Odoo
  3. Install "A1 Sale E-Commerce Middleware" module

This script only creates 5 sale.order fields and lacks views, constraints, and other models.
"""

import sys

print(__doc__)
sys.exit(1)

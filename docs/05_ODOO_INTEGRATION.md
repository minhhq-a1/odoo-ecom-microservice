# ODOO 17 INTEGRATION CONTEXT
## XML-RPC, REST API, Custom Fields & Business Logic

---

## Kết nối Odoo

### XML-RPC (Primary)

```python
import xmlrpc.client

ODOO_URL      = "https://your-odoo.com"
ODOO_DB       = "your_db"
ODOO_USER     = "api_user@company.com"
ODOO_PASSWORD = "api_password_or_key"

# Authentication
common  = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
uid     = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})

# Models
models  = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

# ⚠️ Tạo 1 api_key thay vì dùng password → Settings > Technical > API Keys
# ⚠️ Tạo dedicated API user với quyền tối thiểu cần thiết
```

### REST API (Odoo 18)

```python
# Odoo 18 hỗ trợ REST API tại /api/*
import httpx

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type":  "application/json",
}

# GET records
GET  /api/sale.order?domain=[['state','=','sale']]

# POST create
POST /api/sale.order
Body: { "partner_id": 1, "order_line": [...] }
```

---

## Custom Fields (cần tạo trên Odoo 18)

```python
# Tạo qua Settings > Technical > Fields
# hoặc qua XML-RPC:

CUSTOM_FIELDS_TO_CREATE = [
    {
        "model": "sale.order",
        "name":  "x_platform",
        "field_description": "E-Commerce Platform",
        "ttype": "selection",
        "selection": "[('shopee','Shopee'),('lazada','Lazada'),('tiktok','TikTok Shop')]",
    },
    {
        "model": "sale.order",
        "name":  "x_platform_order_id",
        "field_description": "Platform Order ID",
        "ttype": "char",
        "index": True,   # Cần index để tìm kiếm nhanh
    },
    {
        "model": "sale.order",
        "name":  "x_platform_order_sn",
        "field_description": "Platform Order SN",
        "ttype": "char",
    },
    {
        "model": "sale.order",
        "name":  "x_sync_status",
        "field_description": "Middleware Sync Status",
        "ttype": "selection",
        "selection": "[('pending','Pending'),('synced','Synced'),('error','Error')]",
    },
    {
        "model": "sale.order",
        "name":  "x_tracking_number",
        "field_description": "Tracking Number",
        "ttype": "char",
    },
]
```

---

## Odoo Client Implementation

```python
# src/odoo/client.py

class OdooClient:

    # ── Partner (Customer) ──────────────────────────────────────────────────

    def get_or_create_partner(self, address: UnifiedAddress) -> int:
        """Tìm partner theo phone, nếu không có thì tạo mới"""
        
        # Tìm theo phone (normalize: bỏ +84, thêm 0)
        phone = self._normalize_phone(address.phone)
        
        existing = self._search_read("res.partner", 
            domain=[["phone", "=", phone]],
            fields=["id"], limit=1
        )
        
        if existing:
            return existing[0]["id"]
        
        # Tạo mới
        return self._create("res.partner", {
            "name":    address.full_name,
            "phone":   phone,
            "street":  address.address_line,
            "city":    address.district,
            "comment": f"{address.ward}, {address.district}, {address.province}",
            "country_id": self._get_vietnam_id(),
            "customer_rank": 1,
        })

    # ── Sale Order ──────────────────────────────────────────────────────────

    def create_sale_order(self, order: UnifiedOrder) -> tuple[int, str]:
        """Tạo sale order, trả về (id, name)"""
        
        partner_id    = self.get_or_create_partner(order.shipping_address)
        order_lines   = self._build_order_lines(order.items)
        pricelist_id  = self._get_pricelist_vnd()
        
        so_id = self._create("sale.order", {
            "partner_id":            partner_id,
            "partner_invoice_id":    partner_id,
            "partner_shipping_id":   partner_id,
            "pricelist_id":          pricelist_id,
            "origin":                f"{order.platform.value}/{order.platform_order_id}",
            "x_platform":            order.platform.value,
            "x_platform_order_id":   order.platform_order_id,
            "x_platform_order_sn":   order.platform_order_sn or "",
            "x_sync_status":         "synced",
            "note":                  self._build_order_note(order),
            "order_line":            order_lines,
        })
        
        # Lấy tên SO (SO0001, SO0002...)
        so = self._read("sale.order", [so_id], ["name"])[0]
        
        # Tự confirm order (state: draft → sale)
        self._execute("sale.order", "action_confirm", [[so_id]])
        
        return so_id, so["name"]

    def _build_order_lines(self, items: List[UnifiedOrderItem]) -> list:
        lines = []
        for item in items:
            product_id = self._get_product_id_by_sku(item.sku)
            if not product_id:
                raise ProductNotFoundError(f"SKU not found in Odoo: {item.sku}")
            
            lines.append((0, 0, {
                "product_id":       product_id,
                "name":             f"{item.product_name} - {item.variant_name or ''}".strip(" -"),
                "product_uom_qty":  item.quantity,
                "price_unit":       float(item.discounted_price),
                "discount":         0,  # Đã tính vào price_unit
            }))
        return lines

    # ── Product / Stock ─────────────────────────────────────────────────────

    def get_stock_quantity(self, sku: str, warehouse_id: int = None) -> int:
        """Lấy virtual_available (có tính incoming - outgoing)"""
        
        domain = [["default_code", "=", sku]]
        
        result = self._search_read("product.product", domain,
            ["qty_available", "virtual_available", "id"]
        )
        
        if not result:
            return 0
        
        # Nếu cần stock theo warehouse cụ thể → dùng stock.quant
        if warehouse_id:
            return self._get_warehouse_stock(result[0]["id"], warehouse_id)
        
        return int(result[0]["virtual_available"])

    def _get_product_id_by_sku(self, sku: str) -> Optional[int]:
        result = self._search_read("product.product",
            [["default_code", "=", sku]], ["id"], limit=1
        )
        return result[0]["id"] if result else None

    # ── Inventory Adjustment ────────────────────────────────────────────────

    def create_stock_picking_from_order(self, odoo_order_id: int) -> int:
        """Tạo delivery order từ sale order"""
        # Odoo tự tạo khi confirm sale order nếu cấu hình đúng
        # Dùng hàm này nếu cần tạo thủ công
        pickings = self._search_read("stock.picking",
            [["sale_id", "=", odoo_order_id]], ["id"]
        )
        return pickings[0]["id"] if pickings else None

    # ── Utility ─────────────────────────────────────────────────────────────

    def check_order_exists(self, platform: str, platform_order_id: str) -> Optional[int]:
        result = self._search_read("sale.order",
            [
                ["x_platform", "=", platform],
                ["x_platform_order_id", "=", platform_order_id]
            ],
            ["id"], limit=1
        )
        return result[0]["id"] if result else None

    def _search_read(self, model, domain, fields, limit=None) -> list:
        kwargs = {"fields": fields}
        if limit:
            kwargs["limit"] = limit
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, "search_read", [domain], kwargs
        )

    def _create(self, model, values) -> int:
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, "create", [values]
        )

    def _execute(self, model, method, args):
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, method, args
        )
```

---

## Odoo Models Quan Trọng

```
sale.order          → Đơn bán hàng
sale.order.line     → Dòng đơn hàng
res.partner         → Khách hàng / Nhà cung cấp
product.product     → Biến thể sản phẩm (có SKU/internal_reference)
product.template    → Template sản phẩm
stock.quant         → Tồn kho thực tế theo location
stock.move          → Chuyển động kho
stock.picking       → Phiếu xuất/nhập kho
account.move        → Hóa đơn
```

---

## Quyền truy cập API User

```
Group cần thiết:
  - Sales / User (tạo/đọc sale.order)
  - Inventory / User (đọc stock, tạo picking)
  - Technical / Allow API Keys (tạo API key)

Field access:
  - res.partner: create, read, write
  - sale.order: create, read, write (KHÔNG delete)
  - product.product: read only
  - stock.quant: read only
```

---

## Xử lý lỗi XML-RPC

```python
import xmlrpc.client

try:
    result = models.execute_kw(...)
except xmlrpc.client.Fault as e:
    # e.faultCode, e.faultString
    if "AccessError" in e.faultString:
        raise OdooPermissionError(e.faultString)
    elif "ValidationError" in e.faultString:
        raise OdooValidationError(e.faultString)
    else:
        raise OdooError(e.faultString)
except ConnectionRefusedError:
    raise OdooConnectionError("Cannot connect to Odoo")
```

---

## Performance Tips

```
1. Batch operations: Dùng search_read thay vì search + read riêng lẻ
2. Limit fields: Chỉ lấy fields cần thiết, tránh lấy tất cả
3. Connection pooling: Tái sử dụng xmlrpc.client.ServerProxy object
4. Cache product mapping: product SKU → odoo_id không đổi thường xuyên
   → Cache Redis 1 giờ, invalidate khi có thay đổi
5. Avoid N+1: Batch lấy partner/product thay vì từng đơn
```

---

## Odoo 18 Breaking Changes (vs 17)

```
- Python 3.11+ required (Odoo 17 chấp nhận 3.10).
- Khung Owl 3 → Owl 2.x cho legacy views — ảnh hưởng web UI, không ảnh hưởng XML-RPC.
- `res.partner.country_id` strict type check — phải pass integer ID, không pass tuple.
- `sale.order.action_confirm()` giữ nguyên nhưng nay trả về `True/False` thay vì action dict
  → middleware không cần thay đổi (call_method ignore return).
- `product.product.virtual_available` giữ nguyên semantics.
- `default_code` (SKU) giữ nguyên.
- `stock.move` API có thêm `move_line_ids_without_package` — không tác động sync flow.
- REST API `/api/v2/*` mature hơn, chính thức ra khỏi experimental.
- ORM: `_compute_*` methods phải decorate `@api.depends_context` nếu dùng context — chỉ
  ảnh hưởng module custom của Odoo, không ảnh hưởng middleware.

Compatibility note: middleware code (XML-RPC paths /xmlrpc/2/common, /xmlrpc/2/object,
authenticate, execute_kw) hoạt động đồng nhất giữa Odoo 17 và 18 → không cần đổi
src/odoo/client.py.
```

---

## Odoo 17 Breaking Changes (vs 16) — historical

```
- REST API endpoint mới: /api/{model} (không dùng /web/dataset/call_kw nữa)
- sale.order.line: price_subtotal giờ là computed field
- stock.quant: qty_on_hand thay cho qty (backward compat còn đó)
- Python 3.10+ required
- Tất cả monetary fields dùng Decimal thay float
```

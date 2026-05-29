# SHOPEE API CONTEXT
## Shopee Open Platform API v2 - Reference & Integration Guide

---

## Tổng quan

- **Base URL**: `https://partner.shopeemobile.com/api/v2` (Production)
- **Base URL**: `https://partner.test-stable.shopeemobile.com/api/v2` (Sandbox)
- **Auth**: HMAC-SHA256 signature + OAuth2 token
- **Format**: JSON
- **Rate Limit**: Varies per endpoint (xem bên dưới)

---

## Authentication

### Credentials cần có
```
partner_id:   Lấy từ Shopee Partner Portal
partner_key:  Secret key (không được expose)
shop_id:      ID của shop
```

### Lấy Access Token (OAuth flow)

```
Step 1: Generate auth URL
GET https://partner.shopeemobile.com/api/v2/shop/auth_partner
Params: partner_id, redirect, sign, timestamp

Step 2: User approve → nhận code

Step 3: Exchange code → tokens
POST /api/v2/auth/token/get
Body: { code, shop_id, partner_id }

Response:
{
  "access_token":  "...",  // Hết hạn sau 4 giờ
  "refresh_token": "...",  // Hết hạn sau 30 ngày
  "expire_in":     14400,
  "refresh_token_expire_in": 2592000
}
```

### Refresh Token

```
POST /api/v2/auth/access_token/get
Body: { refresh_token, shop_id, partner_id }

⚠️ Gọi trước khi access_token hết hạn 30 phút
⚠️ Lưu cả token mới vào Redis ngay lập tức
```

### Request Signing

```python
import hmac, hashlib, time

def generate_sign(partner_id: str, api_path: str, partner_key: str,
                  access_token: str = "", shop_id: str = "") -> tuple[str, int]:
    timestamp  = int(time.time())
    base_str   = f"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"
    sign       = hmac.new(
        partner_key.encode("utf-8"),
        base_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return sign, timestamp

# Mọi request đều cần thêm query params:
# ?partner_id=xxx&shop_id=xxx&access_token=xxx&sign=xxx&timestamp=xxx
```

---

## Webhook Setup

### Đăng ký webhook URL

```
POST /api/v2/push/set_shop_push_config
Body: {
  "callback_url": "https://your-middleware.com/webhook/shopee",
  "push_config": {
    "3":  1,  // Order status update
    "4":  1,  // Logistics update
    "15": 1,  // Reserved stock change
  }
}
```

### Webhook Payload Structure

```json
// Event code 3: Order update
{
  "code":    3,
  "shop_id": 123456,
  "timestamp": 1234567890,
  "data": {
    "ordersn": "230101XXXXXXXX",
    "status":  "READY_TO_SHIP",
    "update_time": 1234567890
  }
}

// Event code 4: Logistics update
{
  "code":    4,
  "shop_id": 123456,
  "data": {
    "ordersn":        "230101XXXXXXXX",
    "tracking_no":    "SPXVN0000000000",
    "logistics_status": "LOGISTICS_PICKUP_DONE"
  }
}
```

### Xác thực webhook

```python
# Shopee gửi chữ ký trong header: X-Shopee-Signature
def verify_webhook(body: bytes, signature: str, partner_key: str) -> bool:
    expected = hmac.new(
        partner_key.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## Order APIs

### Lấy danh sách đơn (Polling)

```
GET /api/v2/order/get_order_list

Params:
  time_range_field: "create_time" | "update_time"
  time_from:        Unix timestamp
  time_to:          Unix timestamp (max range: 15 ngày)
  order_status:     "UNPAID" | "READY_TO_SHIP" | ... (optional)
  page_size:        max 100
  cursor:           "" (first page), sau đó dùng next_cursor

Response:
  order_list: [{ ordersn, order_status, create_time, update_time }]
  next_cursor: "..."
  more: true/false

⚠️ Rate limit: 1000 requests/phút
⚠️ time_to - time_from ≤ 15 ngày
```

### Lấy chi tiết đơn

```
GET /api/v2/order/get_order_detail

Params:
  order_sn_list: "sn1,sn2,sn3" (max 50 per request)
  response_optional_fields: "buyer_user_id,buyer_username,
    estimated_shipping_fee,recipient_address,
    actual_shipping_fee,item_list,pay_time,
    dropshipper,dropshipper_phone,invoice_data"

Response: { order_list: [...full order objects] }

⚠️ Rate limit: 400 requests/phút
⚠️ Batch tối đa 50 đơn/request → cần chunk
```

### Order Status Values

```python
SHOPEE_STATUS_MAP = {
    "UNPAID":           OrderStatus.PENDING,
    "READY_TO_SHIP":    OrderStatus.CONFIRMED,
    "PROCESSED":        OrderStatus.PROCESSING,
    "SHIPPED":          OrderStatus.SHIPPED,
    "COMPLETED":        OrderStatus.DELIVERED,
    "IN_CANCEL":        OrderStatus.CANCELLED,
    "CANCELLED":        OrderStatus.CANCELLED,
    "TO_RETURN":        OrderStatus.RETURN_REQUESTED,
    "INVOICE_PENDING":  OrderStatus.CONFIRMED,
}
```

---

## Logistics APIs

### Xác nhận lấy hàng / Tạo vận đơn

```
POST /api/v2/logistics/init_task
Body: { order_sn, ...pickup_params hoặc dropoff_params }

POST /api/v2/logistics/batch_init  (xử lý nhiều đơn)
Body: { order_list: [{ order_sn, ... }] }
```

### Lấy tracking number

```
GET /api/v2/logistics/get_tracking_number
Params: order_sn
Response: { tracking_number: "SPXVN..." }
```

---

## Product & Stock APIs

### Cập nhật tồn kho

```
POST /api/v2/product/update_stock
Body: {
  "stock_list": [
    {
      "item_id":    123456,
      "stock_info": [
        {
          "model_id": 789,         // Variant ID (0 nếu không có variant)
          "normal_stock": 50       // Số lượng muốn set
        }
      ]
    }
  ]
}

⚠️ Rate limit: 200 requests/phút
⚠️ Max 50 items/request
⚠️ Shopee dùng "set" stock, không phải "delta"
```

### Lấy tồn kho hiện tại

```
GET /api/v2/product/get_item_extra_info
Params: item_id_list (max 50)
Response: { item_list: [{ item_id, stock_info: [...] }] }
```

---

## Rate Limits Summary

| API Group | Limit |
|---|---|
| Order List | 1000 req/phút |
| Order Detail | 400 req/phút |
| Product/Stock | 200 req/phút |
| Logistics | 500 req/phút |
| Auth/Token | 50 req/phút |

### Xử lý Rate Limit

```python
# Khi nhận HTTP 429 hoặc error_code "error_api_ratelimitation"
# → Dừng lại, chờ theo Retry-After header
# → Dùng exponential backoff với jitter
# → Celery task tự động retry sau delay

@app.task(bind=True, max_retries=5)
def fetch_shopee_orders(self, ...):
    try:
        ...
    except ShopeeRateLimitError as e:
        raise self.retry(countdown=e.retry_after or 60)
```

---

## Error Codes Quan trọng

```python
SHOPEE_ERROR_CODES = {
    "error_auth":                   "Token không hợp lệ → refresh token",
    "error_auth_token_expired":     "Token hết hạn → refresh ngay",
    "error_api_ratelimitation":     "Rate limit → backoff và retry",
    "error_not_found":              "Order không tồn tại",
    "error_item_is_on_flash_sale":  "Không update stock khi flash sale",
    "error_logistics_tracking_exists": "Đã có tracking, không tạo lại",
}
```

---

## Polling Strategy (Fallback khi webhook miss)

```python
# Chạy mỗi 10 phút để catch các event webhook bị miss
# Lấy đơn có update_time trong 15 phút gần nhất (overlap 5 phút để đảm bảo)

async def polling_fallback():
    now        = int(time.time())
    time_from  = now - (15 * 60)   # 15 phút trước
    time_to    = now

    orders = await shopee.get_orders(
        time_range_field="update_time",
        time_from=time_from,
        time_to=time_to
    )

    for order in orders:
        # Gửi vào queue để xử lý (có idempotency check)
        await queue.push("orders.updated", order)
```

---

## Test & Sandbox

```
Sandbox URL: https://partner.test-stable.shopeemobile.com/api/v2
Test shop:   Tạo test shop trên Shopee Partner Portal
Test orders: Dùng Shopee Sandbox để tạo order test

⚠️ Webhook từ sandbox cần URL public → dùng ngrok khi dev local
⚠️ Token sandbox khác token production hoàn toàn
```

# SECURITY BASELINE
## Threat Model · Hardening · Compliance

---

## Threat Model (STRIDE)

| Asset | Spoof | Tamper | Repudiation | Info disclosure | DoS | Elevation |
|---|---|---|---|---|---|---|
| Shopee webhook | HMAC sig | HMAC sig | Audit log | TLS | Rate limit | — |
| Admin UI | Token auth | CSRF token | Audit log | TLS + cookie HttpOnly | Rate limit | RBAC |
| Odoo XML-RPC | API key | TLS | Odoo log | TLS, vault | Circuit breaker | Dedicated API user |
| Postgres | mTLS optional | WAL audit | pg_audit | TLS, role | Conn pool | Least priv per service |
| Redis | requirepass + ACL | TLS | — | TLS, AOF perms | maxmemory | ACL per DB |
| Secrets | KMS | KMS | Access log | KMS | — | IAM |

---

## Credential Encryption (at rest)

### Approach — envelope encryption

```
Master Key (KMS / age / sops)
   ↓ unwraps
DEK (Data Encryption Key, per platform)
   ↓ encrypts
platform_config.credentials JSONB
```

```python
# src/core/crypto.py
from cryptography.fernet import Fernet, MultiFernet

class CredentialCipher:
    """
    Multi-key Fernet cho key rotation.
    KEYS env: comma-separated, key đầu = active (encrypt + decrypt),
    còn lại = legacy (decrypt only).
    """
    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("CREDENTIAL_KEYS required")
        self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    def rotate(self, token: str) -> str:
        """Re-encrypt với key active hiện tại."""
        return self._fernet.rotate(token.encode()).decode()
```

### Key Rotation Policy

```
- Access token Shopee:   refresh mỗi 3h, expire 4h. Không phải rotation.
- partner_key Shopee:    rotation thủ công khi nghi compromise. Không có schedule.
- CREDENTIAL_KEYS:       rotate mỗi 90 ngày.
                         Quy trình:
                         1. Generate Fernet key mới: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
                         2. Prepend vào CREDENTIAL_KEYS env
                         3. Deploy
                         4. Chạy script: `python scripts/rotate_credentials.py` — re-encrypt all rows
                         5. Sau 7 ngày, drop key cũ khỏi env
- ADMIN_SECRET_TOKEN:    rotate mỗi 30 ngày.
- Odoo API key:          rotate mỗi 90 ngày, hoặc khi nhân sự nghỉ.
- DATABASE_PASSWORD:     rotate mỗi 180 ngày, dùng pgbouncer userlist auth_query.
```

### Secrets Storage

| Env | Storage |
|---|---|
| Dev | `.env` (gitignored) + age-encrypted `.env.age` cho team share |
| Staging | sops + KMS, dùng helm secret hoặc docker secret |
| Production | AWS Secrets Manager / GCP Secret Manager / HashiCorp Vault |

**Tuyệt đối KHÔNG:** commit .env, log secret value, gửi qua Slack/email.

---

## Webhook Signature Verification

```python
# Hardening 1: constant-time compare
hmac.compare_digest(expected, signature)

# Hardening 2: timestamp window (chống replay)
if abs(time.time() - payload_timestamp) > 300:
    raise SignatureExpired()

# Hardening 3: nonce dedup (Redis SET với TTL 5 phút)
nonce_key = f"webhook:nonce:{platform}:{signature[:32]}"
if not await redis.set(nonce_key, "1", ex=300, nx=True):
    raise WebhookReplay()
```

---

## Admin UI Hardening

```python
# Hiện tại: static token cookie. Nâng cấp tối thiểu:

# 1. Cookie attributes
response.set_cookie(
    "admin_token", token,
    httponly=True,
    secure=True,          # HTTPS only
    samesite="strict",
    max_age=8 * 3600,     # 8h session
    path="/admin",
)

# 2. CSRF token cho mọi POST
@router.post("/orders/{id}/retry")
async def retry(id: int, csrf: str = Form(...), _: None = Depends(verify_csrf)):
    ...

# 3. Audit log mọi action
await AuditService.log(
    actor=request.cookies.get("admin_user", "unknown"),
    action="retry_order",
    target=f"order_mapping:{id}",
    ip=request.client.host,
    user_agent=request.headers.get("user-agent"),
)

# 4. Rate limit per actor (không chỉ per IP)
@limiter.limit("10/minute", key_func=lambda r: r.cookies.get("admin_user"))

# 5. RBAC tối thiểu 2 role
ROLES = {"viewer": ["read"], "ops": ["read", "retry"], "admin": ["read", "retry", "config"]}
```

### Roadmap auth nâng cao

```
Phase 1 (now):    static token + audit log
Phase 2 (sau go-live 1 tháng):  Google OAuth / OIDC SSO
Phase 3:          RBAC đầy đủ + 2FA cho admin role
```

---

## Audit Log Schema

```python
class AuditLog(Base):
    __tablename__ = "audit_log"

    id          = Column(BigInteger, primary_key=True)
    actor       = Column(String(100), nullable=False, index=True)
    action      = Column(String(50),  nullable=False, index=True)
    # retry_order | retry_outbox | force_stock_sync | edit_config | login | logout
    target      = Column(String(200))     # e.g. "order_mapping:123"
    ip          = Column(INET)
    user_agent  = Column(Text)
    payload     = Column(JSONB)           # Before/after state
    success     = Column(Boolean, default=True)
    error       = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_audit_actor_time", "actor", "created_at"),
        Index("idx_audit_action_time", "action", "created_at"),
    )

# Retention: 365 ngày (compliance). Archive to S3 after 90 ngày.
```

---

## Database Hardening

```sql
-- 1. Dedicated role per service (least privilege)
CREATE ROLE mw_api      WITH LOGIN PASSWORD 'xxx';
CREATE ROLE mw_worker   WITH LOGIN PASSWORD 'xxx';
CREATE ROLE mw_readonly WITH LOGIN PASSWORD 'xxx';

GRANT CONNECT ON DATABASE middleware_db TO mw_api, mw_worker, mw_readonly;
GRANT USAGE   ON SCHEMA public TO mw_api, mw_worker, mw_readonly;

-- API: chỉ write outbox + read mapping
GRANT INSERT, SELECT ON webhook_outbox        TO mw_api;
GRANT SELECT         ON order_mapping         TO mw_api;
GRANT SELECT         ON platform_config       TO mw_api;
GRANT INSERT         ON audit_log             TO mw_api;

-- Worker: full CRUD
GRANT INSERT, SELECT, UPDATE ON ALL TABLES IN SCHEMA public TO mw_worker;

-- Readonly cho reporting/Grafana
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mw_readonly;

-- 2. Row-level security cho platform_config (chỉ admin)
ALTER TABLE platform_config ENABLE ROW LEVEL SECURITY;

-- 3. SSL bắt buộc
ALTER SYSTEM SET ssl = on;

-- 4. pg_audit extension
CREATE EXTENSION pgaudit;
ALTER SYSTEM SET pgaudit.log = 'write, ddl';
```

---

## Redis Hardening

```conf
# redis.conf production
requirepass "${REDIS_PASSWORD}"
rename-command CONFIG  ""
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command DEBUG   ""
rename-command KEYS    ""    # Dùng SCAN
protected-mode yes
bind 0.0.0.0 ::1
tls-port 6380
port 0                       # Tắt non-TLS trong prod

# ACL per user
user mw_cache on >password ~cache:* +get +set +del +expire
user mw_celery on >password ~* +@all
```

---

## Network

```
- API public:        443 only, TLS 1.2+ (Caddy/Nginx + Let's Encrypt)
- Admin UI:          IP whitelist (office VPN) + auth
- Internal mesh:     Docker network, không expose 5432/6379 ra ngoài
- Outbound:          Egress filter, chỉ cho phép partner.shopeemobile.com + odoo.com
- Webhook ingress:   Cloudflare WAF (rate limit, geo block nếu cần)
```

---

## Input Validation

```python
# Bắt buộc với mọi payload từ ngoài
from pydantic import BaseModel, Field, constr

class ShopeeWebhookData(BaseModel):
    ordersn: constr(strip_whitespace=True, min_length=8, max_length=50, pattern=r"^[A-Z0-9]+$")
    status:  Literal["UNPAID", "READY_TO_SHIP", ...]   # whitelist enum

class ShopeeWebhook(BaseModel):
    code:    int = Field(..., ge=1, le=100)
    shop_id: int = Field(..., gt=0)
    data:    ShopeeWebhookData

# Reject payload > 1MB (Shopee không bao giờ gửi > 100KB)
@app.middleware("http")
async def limit_body_size(request, call_next):
    if int(request.headers.get("content-length", 0)) > 1_048_576:
        return Response(status_code=413)
    return await call_next(request)
```

---

## Dependency Security

```
- pip-audit chạy mỗi PR + nightly
- Dependabot enabled
- Pin tất cả deps trong requirements.txt
- SBOM (cyclonedx-py) generate mỗi release
- Image scan: trivy / grype trong CI
```

---

## OWASP Top 10 Checklist

| Risk | Mitigation | Status |
|---|---|---|
| A01 Broken Access Control | RBAC + audit log + admin token | ✅ Spec |
| A02 Cryptographic Failures | Fernet envelope + TLS everywhere + rotation | ✅ |
| A03 Injection | Pydantic validate + SQLAlchemy parametrized + no raw SQL | ✅ |
| A04 Insecure Design | Threat model trên + outbox + circuit breaker | ✅ |
| A05 Misconfig | Hardened compose + RBAC DB + secret scan | ✅ |
| A06 Vuln components | pip-audit + Dependabot + trivy | ✅ |
| A07 Auth failures | Constant-time compare + replay protection + lockout | ✅ |
| A08 Data Integrity | HMAC signature + Outbox + idempotency key | ✅ |
| A09 Logging Failures | structlog JSON + audit + retention 365d | ✅ |
| A10 SSRF | Egress allowlist + no user-controlled URL | ✅ |

---

## Incident Response

```
P1 (security breach suspected):
  1. Rotate ALL secrets immediately (CREDENTIAL_KEYS, Odoo API, partner_key)
  2. Revoke Shopee tokens via Partner Portal
  3. Snapshot DB + Redis cho forensic
  4. Disable Admin UI (env flag DISABLE_ADMIN=true)
  5. Review audit_log 7 ngày gần nhất
  6. Notify legal + customers nếu PII leak
```

---

## Compliance Notes

```
PDPL Vietnam (Nghị định 13/2023):
  - PII: phone, address, name của buyer
  - Lưu chỉ tối thiểu cần thiết
  - Retention: order data giữ 5 năm (luật kế toán), PII riêng có thể anonymize sau 3 năm
  - Right to erasure: API endpoint cho phép xóa partner theo phone

PCI: Middleware KHÔNG xử lý card data (Shopee xử lý) → out of scope.
```

# ENVIRONMENT & DEPLOYMENT
## Docker Compose, Environment Variables, Infrastructure Setup

---

## Environment Variables (.env)

```bash
# ── Application ─────────────────────────────────────────────────────────────
ENVIRONMENT=production              # development | staging | production
LOG_LEVEL=INFO
SECRET_KEY=your-secret-key-here
ADMIN_SECRET_TOKEN=your-admin-token # Token cho Admin UI

# ── Dry-run ──────────────────────────────────────────────────────────────────
MIDDLEWARE_DRY_RUN=false            # true khi go-live lần đầu để kiểm tra

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://middleware:password@pgbouncer:6432/middleware_db
SYNC_DATABASE_URL=postgresql://middleware:password@pgbouncer:6432/middleware_db
DATABASE_REPLICA_URL=postgresql+asyncpg://middleware:password@postgres-replica:5432/middleware_db
# ^ Dùng cho read-heavy operations (reconciliation, reporting)

# ── Redis ────────────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# ── Odoo ─────────────────────────────────────────────────────────────────────
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your_database_name
ODOO_USER=api@yourcompany.com
ODOO_PASSWORD=your_api_key_here
ODOO_TIMEOUT=30

# ── Shopee ───────────────────────────────────────────────────────────────────
SHOPEE_PARTNER_ID=your_partner_id
SHOPEE_PARTNER_KEY=your_partner_key
SHOPEE_SHOP_ID=your_shop_id
SHOPEE_IS_SANDBOX=false

# ── Alerting ─────────────────────────────────────────────────────────────────
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
ALERT_EMAIL=ops@yourcompany.com

# ── Stock ────────────────────────────────────────────────────────────────────
DEFAULT_STOCK_BUFFER_PCT=10
DEFAULT_SHOPEE_ALLOCATION_PCT=100

# ── Price Sync ───────────────────────────────────────────────────────────────
PRICE_MASTER=platform               # "platform" | "odoo"
PRICE_SYNC_INTERVAL_HOURS=6

# ── Feature Flags ────────────────────────────────────────────────────────────
ENABLE_AUTO_CONFIRM_ORDER=true
ENABLE_STOCK_SYNC=true
ENABLE_POLLING_FALLBACK=true
ENABLE_RECONCILIATION=true          # Bật sau go-live ổn định
ENABLE_PRICE_SYNC=false             # Tắt cho đến khi cần
POLLING_INTERVAL_MINUTES=10
```

---

## docker-compose.yml (Development)

```yaml
version: "3.9"

services:
  # ── API Server ──────────────────────────────────────────────────────────
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - ./src:/app/src    # Hot reload cho dev
    env_file: .env
    depends_on:
      - redis
      - postgres
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

  # ── Celery Workers ──────────────────────────────────────────────────────
  worker-high:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    env_file: .env
    depends_on:
      - redis
      - postgres
    command: >
      celery -A src.workers.app worker
      --loglevel=info
      --queues=orders.created.high,shipment.confirm
      --concurrency=4
      --hostname=worker-high@%h

  worker-normal:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    env_file: .env
    depends_on:
      - redis
      - postgres
    command: >
      celery -A src.workers.app worker
      --loglevel=info
      --queues=orders.created.normal,orders.updated,stock.sync
      --concurrency=4
      --hostname=worker-normal@%h

  worker-low:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    env_file: .env
    depends_on:
      - redis
      - postgres
    command: >
      celery -A src.workers.app worker
      --loglevel=info
      --queues=reports,cleanup
      --concurrency=2
      --hostname=worker-low@%h

  # ── Celery Beat (Scheduler) ─────────────────────────────────────────────
  beat:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    env_file: .env
    depends_on:
      - redis
      - postgres
    command: >
      celery -A src.workers.app beat
      --loglevel=info
      --scheduler django_celery_beat.schedulers:DatabaseScheduler

  # ── Infrastructure ──────────────────────────────────────────────────────
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --save 900 1
      --save 300 10
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    # AOF + RDB đồng thời để đảm bảo durability

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB:       middleware_db
      POSTGRES_USER:     middleware
      POSTGRES_PASSWORD: password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init.sql:/docker-entrypoint-initdb.d/init.sql

  pgbouncer:
    image: pgbouncer/pgbouncer:latest
    ports:
      - "6432:6432"
    environment:
      DATABASES_HOST:     postgres
      DATABASES_PORT:     5432
      DATABASES_DBNAME:   middleware_db
      PGBOUNCER_POOL_MODE: transaction
      PGBOUNCER_MAX_CLIENT_CONN: 200
      PGBOUNCER_DEFAULT_POOL_SIZE: 20

  # ── Monitoring ──────────────────────────────────────────────────────────
  flower:
    image: mher/flower:2.0
    ports:
      - "5555:5555"
    environment:
      CELERY_BROKER_URL: redis://redis:6379/1
    depends_on:
      - redis

volumes:
  redis_data:
  postgres_data:
```

---

## docker-compose.prod.yml (Production overrides)

```yaml
version: "3.9"

services:
  api:
    restart: always
    deploy:
      replicas: 2
    command: >
      uvicorn src.api.main:app
      --host 0.0.0.0
      --port 8000
      --workers 4
      --no-reload

  worker-high:
    restart: always
    deploy:
      replicas: 2
    command: >
      celery -A src.workers.app worker
      --loglevel=warning
      --queues=orders.created.high,shipment.confirm
      --concurrency=8

  worker-normal:
    restart: always
    deploy:
      replicas: 2

  redis:
    command: >
      redis-server
      --appendonly yes
      --requirepass ${REDIS_PASSWORD}
      --maxmemory 2gb
      --maxmemory-policy allkeys-lru

  # Thêm Prometheus + Grafana cho production
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./docker/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/grafana/dashboards:/etc/grafana/provisioning/dashboards
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}

volumes:
  prometheus_data:
  grafana_data:
```

---

## Dockerfile

```dockerfile
# docker/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## requirements.txt

```
# Web
fastapi==0.115.0
uvicorn[standard]==0.30.0
python-multipart==0.0.9

# Task Queue
celery[redis]==5.4.0
flower==2.0.1

# Database
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
psycopg2-binary==2.9.9
alembic==1.13.3

# HTTP
httpx==0.27.0

# Data validation
pydantic==2.9.0
pydantic-settings==2.5.0

# Monitoring
prometheus-client==0.21.0
structlog==24.4.0

# Utils
python-dotenv==1.0.1
cryptography==43.0.0      # Encrypt credentials
```

---

## Khởi tạo dự án

```bash
# 1. Clone & setup
git clone ...
cd ecommerce-middleware
cp .env.example .env
# Điền đủ các biến vào .env

# 2. Khởi động infrastructure
docker-compose up -d redis postgres

# 3. Chạy migrations
docker-compose run --rm api alembic upgrade head

# 4. Tạo custom fields trên Odoo
docker-compose run --rm api python scripts/setup_odoo_fields.py

# 5. Import product mapping
docker-compose run --rm api python scripts/seed_product_mapping.py --file products.csv

# 6. Khởi động tất cả services
docker-compose up -d

# 7. Kiểm tra health
curl http://localhost:8000/health

# 8. Mở Flower dashboard
open http://localhost:5555
```

---

## Server Specs (Production - 3000 đơn/ngày)

```
API Server:     4 CPU, 8GB RAM  (x2 instances)
Workers:        4 CPU, 8GB RAM  (x4 instances)
PostgreSQL:     4 CPU, 16GB RAM, 100GB SSD NVMe
Redis:          2 CPU, 4GB RAM
Total:          ~$200-400/tháng (tùy cloud provider)

Recommended: Hetzner Cloud hoặc DigitalOcean cho cost-efficiency
```

---

## Load Test Checklist (trước go-live)

```bash
# Tool: locust hoặc k6
# Target: staging environment (mirror production)

Scenario 1: Normal load
  - 300 webhook/phút trong 10 phút
  - Kiểm tra: queue drain time < 2 phút, 0 errors

Scenario 2: Flash sale spike
  - 500 webhook trong 60 giây (burst)
  - Kiểm tra: không mất event, Odoo response time < 3s

Scenario 3: Odoo down 5 phút
  - Dừng Odoo container, gửi 100 webhooks
  - Khởi động lại Odoo
  - Kiểm tra: tất cả đơn vẫn sync thành công sau khi Odoo lên

Scenario 4: Redis restart
  - Restart Redis container, gửi 50 webhooks
  - Kiểm tra: Outbox relay job tự phục hồi, không mất đơn

Pass criteria:
  ✅ Error rate < 0.1%
  ✅ P95 order sync time < 5 phút
  ✅ Zero data loss trong mọi scenario
  ✅ Memory không tăng liên tục (no leak)
```

---

## Incident Log Template

```
Date:       YYYY-MM-DD HH:MM
Severity:   P1 (system down) | P2 (partial outage) | P3 (degraded)
Reporter:   [Tên]

Triệu chứng:
  [Mô tả ngắn gọn]

Thời điểm phát hiện: HH:MM
Thời điểm giải quyết: HH:MM
Tổng thời gian ảnh hưởng: X phút

Số đơn bị ảnh hưởng: X
Số đơn tự phục hồi: X
Số đơn cần xử lý thủ công: X

Root cause:
  [Nguyên nhân gốc]

Hành động đã thực hiện:
  HH:MM - [Hành động]
  HH:MM - [Hành động]

Bước ngăn chặn lần sau:
  [ ] [Action item] - Owner: [Tên] - Deadline: [Date]
```

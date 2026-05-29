# CI/CD · Release · Rollback

---

## Pipeline Stages

```
PR opened
  └─ ci.yml: lint → type → unit → integration → security scan → build image
       ↓ (all green + 1 approval)
       merge to main

Merge to main
  └─ release.yml:
       1. Tag semver (auto từ conventional commits)
       2. Build + push image (registry + tag)
       3. Deploy staging (auto)
       4. Smoke test staging
       5. Manual approval gate
       6. Deploy production (blue/green)
       7. Post-deploy verification
       8. Tag release as stable hoặc rollback
```

---

## .github/workflows/ci.yml

```yaml
name: CI

on:
  pull_request: {}
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: "3.12"

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - run: pip install ruff==0.6.* mypy==1.11.* types-redis types-requests
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: mypy --strict src/

  test-unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest tests/unit -n auto --cov=src --cov-report=xml --cov-fail-under=85
      - uses: codecov/codecov-action@v4

  test-integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_PASSWORD: postgres }
        ports: ["5432:5432"]
        options: --health-cmd pg_isready
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: alembic upgrade head
        env: { DATABASE_URL: "postgresql://postgres:postgres@localhost:5432/postgres" }
      - run: pytest tests/integration

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - run: pip install pip-audit bandit detect-secrets
      - run: pip-audit -r requirements.txt
      - run: bandit -r src/ -c pyproject.toml
      - run: detect-secrets scan --baseline .secrets.baseline

  build:
    needs: [lint, test-unit, test-integration, security]
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: docker/Dockerfile
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.sha }}
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: ghcr.io/${{ github.repository }}:${{ github.sha }}
          severity: HIGH,CRITICAL
          exit-code: "1"
```

---

## .github/workflows/release.yml

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to staging
        run: |
          ssh deploy@staging "cd /srv/middleware && \
            docker compose pull && \
            docker compose run --rm api alembic upgrade head && \
            docker compose up -d --remove-orphans"
      - name: Smoke test
        run: |
          curl -fsS https://staging.middleware.example.com/health
          curl -fsS https://staging.middleware.example.com/ready
          ./scripts/smoke_test.sh staging

  deploy-prod:
    needs: deploy-staging
    runs-on: ubuntu-latest
    environment:
      name: production
      url: https://middleware.example.com
    steps:
      - uses: actions/checkout@v4
      - name: Migration safety check
        run: ./scripts/check_migration_safety.py
      - name: Blue-green deploy
        run: ./scripts/deploy_blue_green.sh ${{ github.sha }}
      - name: Verify SLO post-deploy
        run: ./scripts/verify_post_deploy.sh
```

---

## Migration Safety

```python
# scripts/check_migration_safety.py
"""
Block CI nếu migration nguy hiểm trên bảng lớn (>1M rows trên prod).
Unsafe patterns:
  - ADD COLUMN NOT NULL không có DEFAULT
  - DROP COLUMN trực tiếp (cần deprecate 1 release trước)
  - ALTER COLUMN TYPE
  - CREATE INDEX không CONCURRENTLY
  - DROP INDEX không CONCURRENTLY
"""

UNSAFE_PATTERNS = [
    r"ADD COLUMN .* NOT NULL(?! DEFAULT)",
    r"DROP COLUMN",
    r"ALTER COLUMN .* TYPE",
    r"CREATE INDEX (?!CONCURRENTLY)",
    r"DROP INDEX (?!CONCURRENTLY)",
    r"LOCK TABLE",
]
```

### Expand/Contract pattern bắt buộc cho schema change

```
Release N:    Expand   — thêm column mới NULLABLE
Release N:    Code     — viết code cũ + mới song song
Release N+1:  Backfill — populate column qua background job
Release N+1:  Code     — switch sang dùng column mới
Release N+2:  Contract — drop column cũ
```

---

## Blue-Green Deploy

```bash
# scripts/deploy_blue_green.sh
#!/usr/bin/env bash
set -euo pipefail
NEW_TAG=$1
CURRENT=$(docker ps --filter "label=app=mw-api" --format "{{.Names}}" | grep -oP "(blue|green)" | head -1)
NEXT=$([ "$CURRENT" = "blue" ] && echo "green" || echo "blue")

# 1. Bring up next color
docker compose -p mw-$NEXT --env-file .env.prod up -d \
  --scale api=2 --scale worker-high=2 --scale worker-normal=2

# 2. Wait healthy
for i in {1..60}; do
  curl -fsS http://mw-$NEXT-api:8000/ready && break
  sleep 2
done

# 3. Run migration (idempotent, đã pass safety check)
docker compose -p mw-$NEXT exec api alembic upgrade head

# 4. Switch load balancer
sed -i "s/mw-$CURRENT/mw-$NEXT/" /etc/caddy/Caddyfile
caddy reload --config /etc/caddy/Caddyfile

# 5. Drain old
docker compose -p mw-$CURRENT exec worker-high celery control shutdown --timeout 60
docker compose -p mw-$CURRENT down
```

---

## Post-Deploy Verification

```bash
# scripts/verify_post_deploy.sh
# Pass criteria sau 10 phút:
#   - Error rate < 0.5%
#   - P95 webhook < 800ms
#   - 0 dead_letter increase
#   - Outbox oldest pending < 60s

WINDOW=600
END=$(date +%s)
START=$((END - WINDOW))

ERROR_RATE=$(curl -s "$PROM/api/v1/query?query=..." | jq '.data.result[0].value[1]')
[ "$(echo "$ERROR_RATE < 0.005" | bc)" -eq 1 ] || { echo "Error rate high"; exit 1; }

DLQ_DELTA=$(curl -s "$PROM/api/v1/query?query=increase(mw_outbox_dead_letter_total[10m])" | jq ...)
[ "$DLQ_DELTA" -eq 0 ] || { echo "Dead letter increase"; exit 1; }

echo "Deploy verified ✓"
```

---

## Rollback Procedure

### Auto rollback triggers
```
- Post-deploy verify fail → tự động switch LB về color cũ
- Error rate > 5% trong 5 phút sau deploy → page on-call + manual decision
```

### Manual rollback
```bash
# RB-006 (Runbook)
# 1. Switch LB
sed -i "s/mw-green/mw-blue/" /etc/caddy/Caddyfile && caddy reload
# 2. Stop new color
docker compose -p mw-green down
# 3. Migration rollback (CHỈ NẾU expand/contract đảm bảo backward compat)
docker compose -p mw-blue exec api alembic downgrade -1
# 4. Notify + post-mortem ticket
```

### Migration rollback an toàn
```
Rule: NEVER auto-rollback DDL migrations.
  - Forward-only philosophy: nếu schema sai, viết migration mới fix.
  - Chỉ rollback nếu migration là expand (add nullable column) — backward compat.
  - Contract migrations (drop column) phải có release N+2 → không bao giờ rollback.
```

---

## Versioning

```
Conventional Commits → semantic-release tự tạo tag
  feat:  → minor bump
  fix:   → patch bump
  perf:  → patch bump
  BREAKING CHANGE: → major bump

Tag format: v1.2.3
Image tag:  ghcr.io/repo:v1.2.3 + :sha-abcdef + :latest (chỉ stable)
```

---

## Environments

| Env | Purpose | Data | Promote rule |
|---|---|---|---|
| dev | Local | Fake | — |
| staging | Pre-prod | Subset prod (anonymized) | Mọi merge to main |
| canary | 5% prod traffic | Real | Sau staging smoke pass |
| production | 100% | Real | Sau canary 1h healthy |

---

## Feature Flags

```python
# src/core/feature_flags.py — đơn giản dùng env + DB override
class FeatureFlags:
    """
    Precedence: DB > env > default.
    Đổi flag không cần redeploy.
    """
    @classmethod
    async def is_enabled(cls, name: str, default: bool = False) -> bool:
        # 1. Check DB (cache 60s)
        # 2. Fallback env
        # 3. Default

# Flags:
ENABLE_RECONCILIATION         (default true sau go-live)
ENABLE_PRICE_SYNC             (default false)
ENABLE_POLLING_FALLBACK       (default true)
DISABLE_ADMIN                 (kill switch)
SHADOW_MODE                   (tạo Odoo SO nhưng không confirm)
DRY_RUN_PLATFORM_<name>       (per-platform dry run)
```

---

## Database Backup & Restore

```
Backup:
  - pg_basebackup mỗi 24h → S3 (retention 30 ngày)
  - WAL archive liên tục → S3 (retention 7 ngày, cho PITR)
  - Test restore mỗi tháng (chaos drill)

Restore drill:
  1. Spin staging-replica từ basebackup mới nhất
  2. Apply WAL đến T-5 phút
  3. Verify row count + checksum critical tables
  4. Document RTO/RPO thực tế (target: RTO < 30 phút, RPO < 5 phút)
```

---

## Release Checklist

```
[ ] All CI green
[ ] Migration safety check pass
[ ] Manual review changelog (auto-generated, verify chính xác)
[ ] Staging smoke + load test 100 req/phút trong 5 phút
[ ] Verify post-deploy script chạy được
[ ] Rollback plan written trong PR description
[ ] Stakeholder notified nếu user-facing change
[ ] Runbook updated nếu có incident pattern mới
```

#!/usr/bin/env bash
# Orchestrate 4 load test scenarios on staging.
# Usage:
#   STAGING_URL=https://staging.example.com \
#   LOAD_PARTNER_KEY=xxx LOAD_SHOP_ID=123 \
#   PROMETHEUS_URL=http://prom.staging:9090 \
#   ./scripts/run_load_tests.sh [scenario]
#
# scenario: normal | flashsale | odoo_down | redis_restart | all (default)

set -euo pipefail

: "${STAGING_URL:?STAGING_URL required}"
: "${LOAD_PARTNER_KEY:?LOAD_PARTNER_KEY required}"
: "${PROMETHEUS_URL:?PROMETHEUS_URL required}"
: "${LOAD_SHOP_ID:=123456}"
SCENARIO="${1:-all}"

run_normal() {
  echo "▶ Scenario 1: normal load (10 phút, ~300 webhook/phút)"
  locust -f tests/load/locustfile_normal.py -H "$STAGING_URL" \
    -u 5 -r 1 -t 10m --headless --csv=results/normal --only-summary
  python3 scripts/verify_load_test.py --scenario normal \
    --prometheus "$PROMETHEUS_URL" --since 600 --max-error-rate 0.001
}

run_flashsale() {
  echo "▶ Scenario 2: flash sale spike (60s burst, 500 req)"
  locust -f tests/load/locustfile_flashsale.py -H "$STAGING_URL" \
    -u 50 -r 50 -t 60s --headless --csv=results/flashsale --only-summary
  # Wait for outbox drain
  echo "⏳ Wait 5 phút for outbox drain..."
  sleep 300
  python3 scripts/verify_load_test.py --scenario flashsale \
    --prometheus "$PROMETHEUS_URL" --since 600
}

run_odoo_down() {
  echo "▶ Scenario 3: Odoo down 5 phút"
  echo "Manual step required: stop Odoo NOW (docker compose stop odoo on staging)"
  read -r -p "Press ENTER when Odoo is stopped..."
  locust -f tests/load/locustfile_odoo_down.py -H "$STAGING_URL" \
    -u 2 -r 1 -t 5m --headless --csv=results/odoo_down --only-summary
  echo "Manual step: start Odoo (docker compose start odoo)"
  read -r -p "Press ENTER when Odoo is up..."
  echo "⏳ Wait 5 phút for outbox catch-up..."
  sleep 300
  python3 scripts/verify_load_test.py --scenario odoo_down \
    --prometheus "$PROMETHEUS_URL" --since 1200
}

run_redis_restart() {
  echo "▶ Scenario 4: Redis restart mid-ingestion"
  locust -f tests/load/locustfile_redis_restart.py -H "$STAGING_URL" \
    -u 5 -r 1 -t 3m --headless --csv=results/redis_restart --only-summary &
  LOCUST_PID=$!
  sleep 60
  echo "Manual step: restart Redis NOW (docker compose restart redis on staging)"
  read -r -p "Press ENTER after Redis restarted..."
  wait $LOCUST_PID || true
  sleep 60
  python3 scripts/verify_load_test.py --scenario redis_restart \
    --prometheus "$PROMETHEUS_URL" --since 300
}

mkdir -p results

case "$SCENARIO" in
  normal)        run_normal ;;
  flashsale)     run_flashsale ;;
  odoo_down)     run_odoo_down ;;
  redis_restart) run_redis_restart ;;
  all)
    run_normal
    run_flashsale
    run_odoo_down
    run_redis_restart
    ;;
  *)             echo "Unknown scenario: $SCENARIO"; exit 1 ;;
esac

echo ""
echo "✓ Load test scenario(s) [$SCENARIO] complete. Results in results/"

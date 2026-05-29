# ADMIN UI — FastAPI + Jinja2
## Dashboard cho Ops team & Dev team

---

## Router

```python
# src/api/routers/admin.py

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_async_db
from src.models.order_mapping import OrderMapping
from src.models.outbox import WebhookOutbox
from src.services.outbox_service import OutboxService
from src.services.stock_service import StockService
from src.api.dependencies import require_admin_token

router    = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/templates")
logger    = structlog.get_logger(__name__)


# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
    _:       None         = Depends(require_admin_token),
):
    # Thống kê 24h gần nhất
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    # Order sync stats
    order_stats = await db.execute(
        select(OrderMapping.status, func.count().label("count"))
        .where(OrderMapping.created_at >= since_24h)
        .group_by(OrderMapping.status)
    )
    order_stats = {row.status: row.count for row in order_stats}

    # Outbox stats
    outbox_stats = await OutboxService.get_stats()

    # Recent failures (10 đơn lỗi gần nhất)
    recent_failures = await db.execute(
        select(OrderMapping)
        .where(OrderMapping.status.in_(["failed", "dead_letter"]))
        .order_by(desc(OrderMapping.updated_at))
        .limit(10)
    )
    recent_failures = recent_failures.scalars().all()

    # Queue depth từ Redis
    from src.core.redis import get_redis
    redis = await get_redis()
    queue_depths = {
        "high":   await redis.llen("queue:orders.created.high"),
        "normal": await redis.llen("queue:orders.created.normal"),
        "stock":  await redis.llen("queue:stock.sync"),
    }

    return templates.TemplateResponse("admin/dashboard.html", {
        "request":         request,
        "order_stats":     order_stats,
        "outbox_stats":    outbox_stats,
        "recent_failures": recent_failures,
        "queue_depths":    queue_depths,
        "now":             datetime.now(timezone.utc),
    })


# ─── Orders ──────────────────────────────────────────────────────────────────

@router.get("/orders", response_class=HTMLResponse)
async def orders_list(
    request:  Request,
    db:       AsyncSession = Depends(get_async_db),
    _:        None         = Depends(require_admin_token),
    status:   Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    search:   Optional[str] = Query(None),
    page:     int = Query(1, ge=1),
):
    page_size = 50
    offset    = (page - 1) * page_size

    query = select(OrderMapping).order_by(desc(OrderMapping.created_at))

    if status:
        query = query.where(OrderMapping.status == status)
    if platform:
        query = query.where(OrderMapping.platform == platform)
    if search:
        query = query.where(
            OrderMapping.platform_order_id.ilike(f"%{search}%")
        )

    total_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = total_result.scalar()

    result   = await db.execute(query.limit(page_size).offset(offset))
    orders   = result.scalars().all()
    pages    = (total + page_size - 1) // page_size

    return templates.TemplateResponse("admin/orders.html", {
        "request":  request,
        "orders":   orders,
        "total":    total,
        "page":     page,
        "pages":    pages,
        "status":   status,
        "platform": platform,
        "search":   search,
    })


@router.post("/orders/{order_id}/retry")
async def retry_order(
    order_id: int,
    db:       AsyncSession = Depends(get_async_db),
    _:        None         = Depends(require_admin_token),
):
    mapping = await db.get(OrderMapping, order_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Order not found")

    if mapping.status not in ("failed", "dead_letter"):
        raise HTTPException(status_code=400,
                            detail=f"Cannot retry order with status: {mapping.status}")

    # Reset và re-queue
    mapping.status      = "pending"
    mapping.retry_count = 0
    mapping.last_error  = None
    await db.commit()

    from src.workers.order_worker import sync_order_to_odoo
    sync_order_to_odoo.apply_async(
        kwargs={"order_mapping_id": order_id},
        queue="queue:orders.created.normal",
    )

    logger.info("admin_manual_retry", order_id=order_id)
    return RedirectResponse(url="/admin/orders", status_code=303)


@router.post("/orders/retry-all-failed")
async def retry_all_failed(
    db: AsyncSession = Depends(get_async_db),
    _:  None         = Depends(require_admin_token),
):
    """Retry toàn bộ failed orders (không gồm dead_letter)."""
    result = await db.execute(
        select(OrderMapping).where(OrderMapping.status == "failed").limit(200)
    )
    failed = result.scalars().all()

    from src.workers.order_worker import sync_order_to_odoo
    count = 0
    for mapping in failed:
        mapping.status      = "pending"
        mapping.retry_count = 0
        sync_order_to_odoo.apply_async(
            kwargs={"order_mapping_id": mapping.id},
            queue="queue:orders.created.normal",
        )
        count += 1

    await db.commit()
    logger.info("admin_retry_all_failed", count=count)
    return RedirectResponse(url="/admin/orders?status=pending", status_code=303)


# ─── Outbox ───────────────────────────────────────────────────────────────────

@router.get("/outbox", response_class=HTMLResponse)
async def outbox_list(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
    _:       None         = Depends(require_admin_token),
    status:  Optional[str] = Query(None),
    page:    int = Query(1, ge=1),
):
    page_size = 50
    offset    = (page - 1) * page_size

    query = select(WebhookOutbox).order_by(desc(WebhookOutbox.created_at))
    if status:
        query = query.where(WebhookOutbox.status == status)

    total  = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar()

    result  = await db.execute(query.limit(page_size).offset(offset))
    entries = result.scalars().all()

    return templates.TemplateResponse("admin/outbox.html", {
        "request": request,
        "entries": entries,
        "total":   total,
        "page":    page,
        "pages":   (total + page_size - 1) // page_size,
        "status":  status,
    })


@router.post("/outbox/{entry_id}/retry")
async def retry_outbox_entry(
    entry_id: int,
    _:        None = Depends(require_admin_token),
):
    success = await OutboxService.manual_retry(entry_id)
    if not success:
        raise HTTPException(status_code=400, detail="Retry failed")
    return RedirectResponse(url="/admin/outbox", status_code=303)


# ─── Stock ───────────────────────────────────────────────────────────────────

@router.get("/stock", response_class=HTMLResponse)
async def stock_overview(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
    _:       None         = Depends(require_admin_token),
):
    from src.models.stock_config import StockAllocationConfig
    result  = await db.execute(select(StockAllocationConfig).limit(200))
    configs = result.scalars().all()

    return templates.TemplateResponse("admin/stock.html", {
        "request": request,
        "configs": configs,
    })


@router.post("/stock/{sku}/sync")
async def force_stock_sync(
    sku: str,
    _:   None = Depends(require_admin_token),
):
    """Force sync tồn kho 1 SKU lên tất cả sàn."""
    from src.workers.stock_worker import sync_stock_for_sku
    sync_stock_for_sku.apply_async(kwargs={"sku": sku})
    return RedirectResponse(url="/admin/stock", status_code=303)


# ─── Health / System ─────────────────────────────────────────────────────────

@router.get("/system", response_class=HTMLResponse)
async def system_status(
    request: Request,
    _:       None = Depends(require_admin_token),
):
    from src.monitoring.health import check_all
    health = await check_all()
    return templates.TemplateResponse("admin/system.html", {
        "request": request,
        "health":  health,
    })
```

---

## Base Template

```html
{# src/templates/admin/base.html #}
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Admin{% endblock %} — Middleware</title>
  <style>
    :root {
      --bg:       #0f172a;
      --surface:  #1e293b;
      --border:   #334155;
      --accent:   #38bdf8;
      --success:  #4ade80;
      --warning:  #fbbf24;
      --danger:   #f87171;
      --muted:    #94a3b8;
      --text:     #e2e8f0;
      --font:     'Inter', system-ui, sans-serif;
      --mono:     'JetBrains Mono', 'Fira Code', monospace;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: var(--font);
           font-size: 14px; line-height: 1.5; min-height: 100vh; }

    /* Layout */
    .layout   { display: flex; min-height: 100vh; }
    .sidebar  { width: 220px; background: var(--surface); border-right: 1px solid var(--border);
                padding: 24px 0; position: sticky; top: 0; height: 100vh; flex-shrink: 0; }
    .main     { flex: 1; padding: 32px; overflow-x: auto; }

    /* Sidebar */
    .logo     { padding: 0 20px 24px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
    .logo-text{ font-size: 13px; font-weight: 700; letter-spacing: 1px; color: var(--accent); }
    .logo-sub { font-size: 10px; color: var(--muted); margin-top: 2px; }

    .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 20px;
                color: var(--muted); text-decoration: none; font-size: 13px;
                transition: all 0.15s; border-left: 3px solid transparent; }
    .nav-item:hover, .nav-item.active {
                color: var(--text); background: rgba(255,255,255,0.04);
                border-left-color: var(--accent); }
    .nav-icon { font-size: 15px; width: 18px; text-align: center; }

    /* Header */
    .page-header{ margin-bottom: 24px; }
    .page-title { font-size: 20px; font-weight: 700; }
    .page-desc  { color: var(--muted); font-size: 13px; margin-top: 4px; }

    /* Cards / Stats */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
                  gap: 16px; margin-bottom: 28px; }
    .stat-card  { background: var(--surface); border: 1px solid var(--border);
                  border-radius: 10px; padding: 18px 20px; }
    .stat-val   { font-size: 28px; font-weight: 800; line-height: 1; }
    .stat-label { font-size: 11px; color: var(--muted); margin-top: 6px;
                  text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-card.danger  { border-color: var(--danger); }
    .stat-card.warning { border-color: var(--warning); }
    .stat-card.success { border-color: var(--success); }

    /* Table */
    .table-wrap { background: var(--surface); border: 1px solid var(--border);
                  border-radius: 10px; overflow: hidden; }
    .table-header { padding: 16px 20px; border-bottom: 1px solid var(--border);
                    display: flex; align-items: center; justify-content: space-between; }
    .table-title  { font-weight: 600; font-size: 14px; }
    table   { width: 100%; border-collapse: collapse; }
    th      { padding: 10px 16px; text-align: left; font-size: 11px; font-weight: 600;
              color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px;
              border-bottom: 1px solid var(--border); background: rgba(0,0,0,0.2); }
    td      { padding: 11px 16px; border-bottom: 1px solid rgba(255,255,255,0.04);
              font-size: 13px; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.03); }
    .mono   { font-family: var(--mono); font-size: 12px; }

    /* Badges */
    .badge  { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px;
              border-radius: 99px; font-size: 11px; font-weight: 600; }
    .badge-success  { background: rgba(74,222,128,0.1);  color: var(--success); }
    .badge-warning  { background: rgba(251,191,36,0.1);  color: var(--warning); }
    .badge-danger   { background: rgba(248,113,113,0.1); color: var(--danger); }
    .badge-muted    { background: rgba(148,163,184,0.1); color: var(--muted); }
    .badge-info     { background: rgba(56,189,248,0.1);  color: var(--accent); }

    /* Buttons */
    .btn        { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px;
                  border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer;
                  border: none; text-decoration: none; transition: all 0.15s; }
    .btn-primary{ background: var(--accent); color: #0f172a; }
    .btn-primary:hover { filter: brightness(1.1); }
    .btn-danger { background: rgba(248,113,113,0.15); color: var(--danger);
                  border: 1px solid rgba(248,113,113,0.3); }
    .btn-danger:hover { background: rgba(248,113,113,0.25); }
    .btn-ghost  { background: rgba(255,255,255,0.06); color: var(--text);
                  border: 1px solid var(--border); }
    .btn-ghost:hover { background: rgba(255,255,255,0.1); }
    .btn-sm     { padding: 4px 10px; font-size: 12px; }

    /* Forms / Filters */
    .filters    { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
    input[type=text], select {
                  background: var(--surface); border: 1px solid var(--border);
                  color: var(--text); padding: 8px 12px; border-radius: 6px;
                  font-size: 13px; outline: none; }
    input[type=text]:focus, select:focus { border-color: var(--accent); }

    /* Alert */
    .alert      { padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;
                  font-size: 13px; display: flex; align-items: center; gap: 10px; }
    .alert-danger  { background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3);
                     color: var(--danger); }
    .alert-warning { background: rgba(251,191,36,0.1);  border: 1px solid rgba(251,191,36,0.3);
                     color: var(--warning); }

    /* Pagination */
    .pagination { display: flex; gap: 6px; margin-top: 16px; padding: 16px 20px;
                  border-top: 1px solid var(--border); }
    .page-btn   { padding: 6px 12px; border-radius: 6px; font-size: 13px; cursor: pointer;
                  border: 1px solid var(--border); background: transparent; color: var(--muted);
                  text-decoration: none; }
    .page-btn.active { background: var(--accent); color: #0f172a; border-color: var(--accent); }
    .page-btn:hover:not(.active) { background: rgba(255,255,255,0.06); color: var(--text); }

    /* Toast */
    #toast      { position: fixed; bottom: 24px; right: 24px; padding: 12px 20px;
                  border-radius: 8px; font-size: 13px; font-weight: 500; z-index: 1000;
                  opacity: 0; transition: opacity 0.3s; pointer-events: none; }
    #toast.show { opacity: 1; }
    #toast.success { background: rgba(74,222,128,0.9); color: #0f172a; }
    #toast.error   { background: rgba(248,113,113,0.9); color: #fff; }

    /* Empty state */
    .empty-state { text-align: center; padding: 48px; color: var(--muted); }
    .empty-icon  { font-size: 36px; margin-bottom: 12px; }
    .empty-text  { font-size: 14px; }

    /* Responsive */
    @media (max-width: 768px) {
      .sidebar { display: none; }
      .main    { padding: 16px; }
    }
  </style>
</head>
<body>
<div class="layout">

  <!-- Sidebar -->
  <nav class="sidebar">
    <div class="logo">
      <div class="logo-text">⚡ MIDDLEWARE</div>
      <div class="logo-sub">Odoo × E-Commerce</div>
    </div>

    <a href="/admin/"        class="nav-item {% if active == 'dashboard' %}active{% endif %}">
      <span class="nav-icon">📊</span> Dashboard
    </a>
    <a href="/admin/orders"  class="nav-item {% if active == 'orders' %}active{% endif %}">
      <span class="nav-icon">📦</span> Đơn hàng
    </a>
    <a href="/admin/outbox"  class="nav-item {% if active == 'outbox' %}active{% endif %}">
      <span class="nav-icon">📬</span> Outbox
    </a>
    <a href="/admin/stock"   class="nav-item {% if active == 'stock' %}active{% endif %}">
      <span class="nav-icon">📦</span> Tồn kho
    </a>
    <a href="/admin/system"  class="nav-item {% if active == 'system' %}active{% endif %}">
      <span class="nav-icon">🖥️</span> Hệ thống
    </a>
    <a href="http://localhost:5555" target="_blank"
       class="nav-item" style="margin-top: auto;">
      <span class="nav-icon">🌸</span> Flower
    </a>
  </nav>

  <!-- Main -->
  <main class="main">
    {% block content %}{% endblock %}
  </main>

</div>
<div id="toast"></div>
<script>
function showToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = `show ${type}`;
  setTimeout(() => { t.className = ''; }, 3000);
}
// Auto-refresh mỗi 30s nếu có data-autorefresh
if (document.body.dataset.autorefresh) {
  setTimeout(() => location.reload(), 30000);
}
</script>
</body>
</html>
```

---

## Dashboard Template

```html
{# src/templates/admin/dashboard.html #}
{% extends "admin/base.html" %}
{% set active = "dashboard" %}
{% block title %}Dashboard{% endblock %}
{% block content %}
<div class="page-header" data-autorefresh="true">
  <div class="page-title">Dashboard</div>
  <div class="page-desc">Cập nhật lúc {{ now.strftime('%H:%M:%S') }} · Tự refresh mỗi 30s</div>
</div>

<!-- Order Stats 24h -->
<div class="stats-grid">
  <div class="stat-card success">
    <div class="stat-val" style="color: var(--success)">{{ order_stats.get('success', 0) }}</div>
    <div class="stat-label">✅ Đã sync (24h)</div>
  </div>
  <div class="stat-card {% if order_stats.get('failed', 0) > 0 %}danger{% endif %}">
    <div class="stat-val" style="color: {% if order_stats.get('failed', 0) > 0 %}var(--danger){% else %}var(--text){% endif %}">
      {{ order_stats.get('failed', 0) }}
    </div>
    <div class="stat-label">❌ Lỗi (24h)</div>
  </div>
  <div class="stat-card {% if order_stats.get('dead_letter', 0) > 0 %}danger{% endif %}">
    <div class="stat-val" style="color: var(--danger)">{{ order_stats.get('dead_letter', 0) }}</div>
    <div class="stat-label">💀 Dead Letter</div>
  </div>
  <div class="stat-card">
    <div class="stat-val" style="color: var(--warning)">{{ order_stats.get('pending', 0) }}</div>
    <div class="stat-label">⏳ Đang xử lý</div>
  </div>
</div>

<!-- Queue Depth + Outbox -->
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 28px;">

  <div class="table-wrap">
    <div class="table-header"><span class="table-title">📬 Outbox Status</span></div>
    <table>
      <thead><tr><th>Status</th><th>Số lượng</th></tr></thead>
      <tbody>
        {% for status, count in outbox_stats.items() %}
        <tr>
          <td>
            {% if status == 'published' %}<span class="badge badge-success">✓ {{ status }}</span>
            {% elif status in ('failed', 'dead_letter') %}<span class="badge badge-danger">{{ status }}</span>
            {% elif status == 'pending' %}<span class="badge badge-warning">{{ status }}</span>
            {% else %}<span class="badge badge-muted">{{ status }}</span>{% endif %}
          </td>
          <td class="mono" style="font-weight: 700">{{ count }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="table-wrap">
    <div class="table-header"><span class="table-title">🚦 Queue Depth</span></div>
    <table>
      <thead><tr><th>Queue</th><th>Đang chờ</th></tr></thead>
      <tbody>
        <tr><td>High Priority</td>
          <td class="mono" style="color: {% if queue_depths.high > 100 %}var(--danger){% elif queue_depths.high > 20 %}var(--warning){% else %}var(--success){% endif %}; font-weight:700">{{ queue_depths.high }}</td></tr>
        <tr><td>Normal</td>
          <td class="mono" style="color: {% if queue_depths.normal > 500 %}var(--danger){% elif queue_depths.normal > 100 %}var(--warning){% else %}var(--text){% endif %}; font-weight:700">{{ queue_depths.normal }}</td></tr>
        <tr><td>Stock Sync</td>
          <td class="mono" style="font-weight:700">{{ queue_depths.stock }}</td></tr>
      </tbody>
    </table>
  </div>

</div>

<!-- Recent Failures -->
{% if recent_failures %}
<div class="alert alert-danger">
  ⚠️ Có {{ recent_failures|length }} đơn hàng bị lỗi gần đây. Kiểm tra và retry nếu cần.
</div>
{% endif %}

<div class="table-wrap">
  <div class="table-header">
    <span class="table-title">🔴 Đơn lỗi gần đây</span>
    {% if recent_failures %}
    <form method="POST" action="/admin/orders/retry-all-failed"
          onsubmit="return confirm('Retry tất cả failed orders?')">
      <button class="btn btn-danger btn-sm">🔄 Retry tất cả Failed</button>
    </form>
    {% endif %}
  </div>

  {% if recent_failures %}
  <table>
    <thead>
      <tr><th>ID</th><th>Platform</th><th>Order ID</th><th>Status</th><th>Lỗi</th><th>Thời gian</th><th></th></tr>
    </thead>
    <tbody>
      {% for order in recent_failures %}
      <tr>
        <td class="mono">{{ order.id }}</td>
        <td><span class="badge badge-info">{{ order.platform }}</span></td>
        <td class="mono">{{ order.platform_order_id }}</td>
        <td>
          {% if order.status == 'dead_letter' %}
            <span class="badge badge-danger">💀 dead_letter</span>
          {% else %}
            <span class="badge badge-danger">{{ order.status }}</span>
          {% endif %}
        </td>
        <td style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                   color: var(--muted); font-size: 12px" title="{{ order.last_error }}">
          {{ order.last_error or '—' }}
        </td>
        <td style="color: var(--muted); font-size: 12px">
          {{ order.updated_at.strftime('%d/%m %H:%M') if order.updated_at else '—' }}
        </td>
        <td>
          {% if order.status != 'dead_letter' %}
          <form method="POST" action="/admin/orders/{{ order.id }}/retry">
            <button class="btn btn-ghost btn-sm">🔄 Retry</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty-state">
    <div class="empty-icon">✅</div>
    <div class="empty-text">Không có lỗi nào. Hệ thống hoạt động bình thường!</div>
  </div>
  {% endif %}
</div>
{% endblock %}
```

---

## Orders Template

```html
{# src/templates/admin/orders.html #}
{% extends "admin/base.html" %}
{% set active = "orders" %}
{% block title %}Đơn hàng{% endblock %}
{% block content %}
<div class="page-header">
  <div class="page-title">📦 Đơn hàng</div>
  <div class="page-desc">Tổng {{ total }} đơn</div>
</div>

<!-- Filters -->
<form method="GET" class="filters">
  <input type="text" name="search" placeholder="🔍 Tìm Order ID..."
         value="{{ search or '' }}" style="min-width: 220px;">
  <select name="status" onchange="this.form.submit()">
    <option value="">Tất cả trạng thái</option>
    <option value="success"     {% if status == 'success' %}selected{% endif %}>✅ Success</option>
    <option value="pending"     {% if status == 'pending' %}selected{% endif %}>⏳ Pending</option>
    <option value="failed"      {% if status == 'failed' %}selected{% endif %}>❌ Failed</option>
    <option value="dead_letter" {% if status == 'dead_letter' %}selected{% endif %}>💀 Dead Letter</option>
    <option value="skipped"     {% if status == 'skipped' %}selected{% endif %}>⏭️ Skipped</option>
  </select>
  <select name="platform" onchange="this.form.submit()">
    <option value="">Tất cả sàn</option>
    <option value="shopee" {% if platform == 'shopee' %}selected{% endif %}>Shopee</option>
    <option value="lazada" {% if platform == 'lazada' %}selected{% endif %}>Lazada</option>
    <option value="tiktok" {% if platform == 'tiktok' %}selected{% endif %}>TikTok</option>
  </select>
  <button type="submit" class="btn btn-primary">Lọc</button>
  <a href="/admin/orders" class="btn btn-ghost">Reset</a>
</form>

<div class="table-wrap">
  <div class="table-header">
    <span class="table-title">Danh sách đơn hàng</span>
    {% if status == 'failed' %}
    <form method="POST" action="/admin/orders/retry-all-failed"
          onsubmit="return confirm('Retry tất cả?')">
      <button class="btn btn-danger btn-sm">🔄 Retry All Failed</button>
    </form>
    {% endif %}
  </div>

  {% if orders %}
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Platform</th><th>Platform Order ID</th>
        <th>Odoo Order</th><th>Status</th><th>Retry</th>
        <th>Thời gian</th><th>Lỗi</th><th></th>
      </tr>
    </thead>
    <tbody>
      {% for o in orders %}
      <tr>
        <td class="mono" style="color: var(--muted)">{{ o.id }}</td>
        <td><span class="badge badge-info">{{ o.platform }}</span></td>
        <td class="mono">{{ o.platform_order_id }}</td>
        <td class="mono">
          {% if o.odoo_order_name %}
            <a href="{{ settings.ODOO_URL }}/web#id={{ o.odoo_order_id }}&model=sale.order"
               target="_blank" style="color: var(--accent)">{{ o.odoo_order_name }}</a>
          {% else %}—{% endif %}
        </td>
        <td>
          {% if o.status == 'success' %}<span class="badge badge-success">✓ success</span>
          {% elif o.status == 'pending' %}<span class="badge badge-warning">⏳ pending</span>
          {% elif o.status == 'dead_letter' %}<span class="badge badge-danger">💀 dead_letter</span>
          {% elif o.status == 'failed' %}<span class="badge badge-danger">❌ failed</span>
          {% else %}<span class="badge badge-muted">{{ o.status }}</span>{% endif %}
        </td>
        <td class="mono" style="color: var(--muted)">{{ o.retry_count }}</td>
        <td style="color: var(--muted); font-size: 12px">
          {{ o.created_at.strftime('%d/%m %H:%M') if o.created_at else '—' }}
        </td>
        <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis;
                   white-space: nowrap; color: var(--muted); font-size: 11px"
            title="{{ o.last_error }}">
          {{ o.last_error or '—' }}
        </td>
        <td>
          {% if o.status in ('failed', 'dead_letter') %}
          <form method="POST" action="/admin/orders/{{ o.id }}/retry">
            <button class="btn btn-ghost btn-sm">🔄</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <!-- Pagination -->
  <div class="pagination">
    {% for p in range(1, pages + 1) %}
    <a href="?page={{ p }}{% if status %}&status={{ status }}{% endif %}{% if platform %}&platform={{ platform }}{% endif %}{% if search %}&search={{ search }}{% endif %}"
       class="page-btn {% if p == page %}active{% endif %}">{{ p }}</a>
    {% endfor %}
  </div>

  {% else %}
  <div class="empty-state">
    <div class="empty-icon">📭</div>
    <div class="empty-text">Không tìm thấy đơn hàng nào.</div>
  </div>
  {% endif %}
</div>
{% endblock %}
```

---

## System Status Template

```html
{# src/templates/admin/system.html #}
{% extends "admin/base.html" %}
{% set active = "system" %}
{% block title %}Hệ thống{% endblock %}
{% block content %}
<div class="page-header">
  <div class="page-title">🖥️ Trạng thái hệ thống</div>
</div>

<div class="stats-grid">
  {% for service, info in health.items() %}
  <div class="stat-card {% if info.ok %}success{% else %}danger{% endif %}">
    <div class="stat-val">{% if info.ok %}✅{% else %}❌{% endif %}</div>
    <div class="stat-label">{{ service }}</div>
    {% if info.detail %}
    <div style="font-size: 11px; color: var(--muted); margin-top: 8px">{{ info.detail }}</div>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endblock %}
```

---

## Auth Middleware (Simple Token)

```python
# src/api/dependencies.py

from fastapi import Depends, HTTPException, Request
from src.core.config import settings

async def require_admin_token(request: Request):
    """
    Simple token auth cho Admin UI.
    Production: thay bằng proper auth (OAuth2, SSO...)
    """
    # Kiểm tra cookie (sau khi login)
    token = request.cookies.get("admin_token")
    if token == settings.ADMIN_SECRET_TOKEN:
        return True

    # Kiểm tra query param (cho API calls)
    token = request.query_params.get("token")
    if token == settings.ADMIN_SECRET_TOKEN:
        return True

    raise HTTPException(status_code=401,
                        detail="Unauthorized. Go to /admin/login")
```

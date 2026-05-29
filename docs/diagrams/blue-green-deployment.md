# Blue-Green Deployment Diagram

## Deployment Architecture

```mermaid
graph TB
    subgraph "Load Balancer"
        LB[NGINX Load Balancer<br/>SSL Termination]
    end

    subgraph "Blue Stack (Current - v1.2.3)"
        BlueAPI1[API Server 1<br/>:8000]
        BlueAPI2[API Server 2<br/>:8000]
        BlueWorker1[Worker High<br/>queue: high]
        BlueWorker2[Worker Default<br/>queue: default]
        BlueWorker3[Worker Low<br/>queue: low]
    end

    subgraph "Green Stack (New - v1.3.0)"
        GreenAPI1[API Server 1<br/>:8000]
        GreenAPI2[API Server 2<br/>:8000]
        GreenWorker1[Worker High<br/>queue: high]
        GreenWorker2[Worker Default<br/>queue: default]
        GreenWorker3[Worker Low<br/>queue: low]
    end

    subgraph "Shared Infrastructure"
        PG[(PostgreSQL<br/>Primary + Replica)]
        Redis[(Redis<br/>Queue + Cache)]
        PgBouncer[PgBouncer<br/>Connection Pool]
    end

    LB -->|100% traffic| BlueAPI1
    LB -->|100% traffic| BlueAPI2
    LB -.->|0% traffic| GreenAPI1
    LB -.->|0% traffic| GreenAPI2

    BlueAPI1 --> PgBouncer
    BlueAPI2 --> PgBouncer
    GreenAPI1 -.-> PgBouncer
    GreenAPI2 -.-> PgBouncer

    BlueWorker1 --> Redis
    BlueWorker2 --> Redis
    BlueWorker3 --> Redis
    GreenWorker1 -.-> Redis
    GreenWorker2 -.-> Redis
    GreenWorker3 -.-> Redis

    PgBouncer --> PG

    style BlueAPI1 fill:#4a90e2
    style BlueAPI2 fill:#4a90e2
    style BlueWorker1 fill:#4a90e2
    style BlueWorker2 fill:#4a90e2
    style BlueWorker3 fill:#4a90e2

    style GreenAPI1 fill:#7ed321,stroke-dasharray: 5 5
    style GreenAPI2 fill:#7ed321,stroke-dasharray: 5 5
    style GreenWorker1 fill:#7ed321,stroke-dasharray: 5 5
    style GreenWorker2 fill:#7ed321,stroke-dasharray: 5 5
    style GreenWorker3 fill:#7ed321,stroke-dasharray: 5 5
```

## Deployment Timeline

```mermaid
gantt
    title Blue-Green Deployment Timeline
    dateFormat HH:mm
    axisFormat %H:%M

    section Preparation
    Build & push images     :done, prep1, 14:00, 10m
    Deploy green stack      :done, prep2, 14:10, 5m
    Run migrations          :done, prep3, 14:15, 3m
    Smoke tests             :done, prep4, 14:18, 5m

    section Traffic Switch
    10% to green            :active, switch1, 14:23, 5m
    Monitor metrics         :active, mon1, 14:23, 5m
    50% to green            :active, switch2, 14:28, 5m
    Monitor metrics         :active, mon2, 14:28, 5m
    100% to green           :active, switch3, 14:33, 2m

    section Verification
    Post-deploy checks      :active, verify, 14:35, 10m
    Monitor for 1 hour      :crit, monitor, 14:45, 60m

    section Cleanup
    Stop blue stack         :cleanup, 15:45, 5m
    Remove after 24h        :milestone, 15:50, 0m
```

## Traffic Switch Stages

```mermaid
graph LR
    subgraph "Stage 1: 10% Green"
        LB1[Load Balancer]
        LB1 -->|90%| Blue1[Blue Stack]
        LB1 -->|10%| Green1[Green Stack]
    end

    subgraph "Stage 2: 50% Green"
        LB2[Load Balancer]
        LB2 -->|50%| Blue2[Blue Stack]
        LB2 -->|50%| Green2[Green Stack]
    end

    subgraph "Stage 3: 100% Green"
        LB3[Load Balancer]
        LB3 -->|0%| Blue3[Blue Stack<br/>Standby]
        LB3 -->|100%| Green3[Green Stack]
    end

    Stage1[Stage 1<br/>Monitor 5 min] --> Stage2[Stage 2<br/>Monitor 5 min]
    Stage2 --> Stage3[Stage 3<br/>Monitor 1 hour]
```

## Deployment Flow

```mermaid
flowchart TD
    Start([Start Deployment]) --> PreCheck{Pre-deployment<br/>Checklist?}

    PreCheck -->|Not Ready| Abort([Abort Deployment])
    PreCheck -->|Ready| BuildImages[Build Docker Images<br/>Tag: v1.3.0]

    BuildImages --> PushRegistry[Push to Registry]
    PushRegistry --> DeployGreen[Deploy Green Stack<br/>docker-compose.green.yml]

    DeployGreen --> WaitHealthy{Green Stack<br/>Healthy?}
    WaitHealthy -->|No| CheckLogs[Check Logs]
    CheckLogs --> FixIssues[Fix Issues]
    FixIssues --> DeployGreen

    WaitHealthy -->|Yes| RunMigrations[Run Database Migrations<br/>alembic upgrade head]

    RunMigrations --> MigrationSuccess{Migration<br/>Success?}
    MigrationSuccess -->|No| RollbackMigration[Rollback Migration<br/>alembic downgrade -1]
    RollbackMigration --> Abort

    MigrationSuccess -->|Yes| SmokeTests[Run Smoke Tests<br/>Health + Webhook + Metrics]

    SmokeTests --> TestsPass{Tests<br/>Pass?}
    TestsPass -->|No| Abort

    TestsPass -->|Yes| Switch10[Switch 10% Traffic to Green]
    Switch10 --> Monitor10[Monitor 5 Minutes<br/>Error rate, latency, queue]

    Monitor10 --> Metrics10OK{Metrics<br/>OK?}
    Metrics10OK -->|No| Rollback10[Rollback to 100% Blue]
    Rollback10 --> Abort

    Metrics10OK -->|Yes| Switch50[Switch 50% Traffic to Green]
    Switch50 --> Monitor50[Monitor 5 Minutes]

    Monitor50 --> Metrics50OK{Metrics<br/>OK?}
    Metrics50OK -->|No| Rollback50[Rollback to 100% Blue]
    Rollback50 --> Abort

    Metrics50OK -->|Yes| Switch100[Switch 100% Traffic to Green]
    Switch100 --> Monitor100[Monitor 1 Hour]

    Monitor100 --> Metrics100OK{Metrics<br/>OK?}
    Metrics100OK -->|No| RollbackFull[Emergency Rollback<br/>100% to Blue]
    RollbackFull --> Abort

    Metrics100OK -->|Yes| StopBlue[Stop Blue Stack<br/>Keep for 24h]
    StopBlue --> Success([Deployment Complete])
```

## NGINX Configuration

### Initial State (100% Blue)

```nginx
upstream backend {
    server blue-api-1:8000 weight=100;
    server blue-api-2:8000 weight=100;
    server green-api-1:8000 weight=0;
    server green-api-2:8000 weight=0;
}
```

### Stage 1 (10% Green)

```nginx
upstream backend {
    server blue-api-1:8000 weight=90;
    server blue-api-2:8000 weight=90;
    server green-api-1:8000 weight=10;
    server green-api-2:8000 weight=10;
}
```

### Stage 2 (50% Green)

```nginx
upstream backend {
    server blue-api-1:8000 weight=50;
    server blue-api-2:8000 weight=50;
    server green-api-1:8000 weight=50;
    server green-api-2:8000 weight=50;
}
```

### Stage 3 (100% Green)

```nginx
upstream backend {
    server blue-api-1:8000 weight=0;
    server blue-api-2:8000 weight=0;
    server green-api-1:8000 weight=100;
    server green-api-2:8000 weight=100;
}
```

## Rollback Procedure

```mermaid
sequenceDiagram
    participant Ops as Ops Engineer
    participant LB as Load Balancer
    participant Green as Green Stack
    participant Blue as Blue Stack
    participant PG as PostgreSQL
    participant Slack as Slack

    Note over Ops,Slack: Issue detected in Green stack

    Ops->>Ops: Assess severity
    alt Critical issue
        Ops->>LB: Switch 100% to Blue<br/>(immediate)
        LB-->>Blue: Route all traffic
        Ops->>Green: Stop Green stack
        Ops->>Slack: Alert: Emergency rollback
    else Non-critical
        Ops->>LB: Reduce Green to 0%<br/>(gradual)
        LB-->>Blue: Route all traffic
        Ops->>Green: Investigate issue
    end

    Ops->>PG: Check migration status
    alt Migration applied
        Ops->>PG: Rollback migration<br/>alembic downgrade -1
        PG-->>Ops: Migration rolled back
    end

    Ops->>Ops: Verify Blue stack healthy
    Ops->>Slack: Report: Rollback complete

    Note over Ops,Slack: Post-mortem analysis
```

## Monitoring Dashboard

```mermaid
graph TB
    subgraph "Grafana Dashboard - Deployment View"
        Panel1[Traffic Distribution<br/>Blue vs Green %]
        Panel2[Error Rate<br/>Blue vs Green]
        Panel3[Response Time P95<br/>Blue vs Green]
        Panel4[Queue Depth<br/>Shared]
        Panel5[Circuit Breaker State<br/>Shared]
        Panel6[Database Connections<br/>Shared]
    end

    subgraph "Prometheus Metrics"
        M1[mw_http_requests_total<br/>label: stack=blue/green]
        M2[mw_http_request_duration_seconds<br/>label: stack=blue/green]
        M3[mw_celery_queue_length]
        M4[mw_circuit_breaker_state]
        M5[pg_stat_activity_count]
    end

    M1 --> Panel1
    M1 --> Panel2
    M2 --> Panel3
    M3 --> Panel4
    M4 --> Panel5
    M5 --> Panel6
```

## Database Migration Strategy

```mermaid
flowchart TD
    Start([Start Migration]) --> BackupDB[Backup Database<br/>pg_dump]

    BackupDB --> CheckCompat{Migration<br/>Backward<br/>Compatible?}

    CheckCompat -->|Yes| ApplyMigration[Apply Migration<br/>alembic upgrade head]
    CheckCompat -->|No| PlanDowntime[Plan Maintenance Window]

    ApplyMigration --> VerifyMigration{Verify<br/>Success?}
    VerifyMigration -->|No| RollbackMigration[Rollback Migration<br/>alembic downgrade -1]
    RollbackMigration --> RestoreBackup[Restore from Backup]
    RestoreBackup --> Abort([Abort Deployment])

    VerifyMigration -->|Yes| TestGreen[Test Green Stack<br/>with New Schema]

    TestGreen --> GreenWorks{Green<br/>Works?}
    GreenWorks -->|No| RollbackMigration

    GreenWorks -->|Yes| TestBlue[Test Blue Stack<br/>with New Schema]

    TestBlue --> BlueWorks{Blue<br/>Compatible?}
    BlueWorks -->|No| Warning[Warning: Blue incompatible<br/>Must complete switch]
    Warning --> ProceedSwitch

    BlueWorks -->|Yes| ProceedSwitch[Proceed with Traffic Switch]
    ProceedSwitch --> Success([Migration Complete])
```

## Zero-Downtime Checklist

- [ ] **Database migrations are backward compatible**
  - Old code (Blue) can read new schema
  - New code (Green) can read old schema

- [ ] **API changes are backward compatible**
  - No breaking changes to webhook endpoints
  - No breaking changes to admin API

- [ ] **Feature flags enabled**
  - New features behind flags
  - Can disable if issues detected

- [ ] **Monitoring in place**
  - Grafana dashboard ready
  - Alert rules configured
  - Slack notifications working

- [ ] **Rollback plan documented**
  - NGINX config rollback steps
  - Database migration rollback steps
  - Emergency contact list

- [ ] **Load testing completed**
  - Green stack tested under load
  - Performance comparable to Blue

- [ ] **On-call engineer available**
  - During deployment window
  - For 1 hour post-deployment

## Best Practices

1. **Gradual Traffic Switch**
   - Start with 10% to detect issues early
   - Monitor each stage for 5 minutes minimum
   - Abort if any metric degrades

2. **Keep Blue Running**
   - Don't stop Blue for 1 hour after 100% switch
   - Keep for 24 hours for emergency rollback
   - Only remove after confidence period

3. **Database Migrations**
   - Always backward compatible
   - Test with both Blue and Green
   - Have rollback script ready

4. **Monitoring**
   - Watch error rate, latency, queue depth
   - Compare Blue vs Green metrics
   - Alert on any anomaly

5. **Communication**
   - Notify team before deployment
   - Update status in Slack
   - Document any issues encountered

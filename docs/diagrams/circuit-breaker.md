# Circuit Breaker State Diagram

## State Machine

```mermaid
stateDiagram-v2
    [*] --> Closed: Initial state

    Closed --> Open: Failure threshold exceeded<br/>(5 failures in 60s)
    Closed --> Closed: Success (reset counter)

    Open --> HalfOpen: Timeout elapsed<br/>(60s for Odoo, 30s for Shopee)
    Open --> Open: All requests rejected

    HalfOpen --> Closed: Test request succeeds
    HalfOpen --> Open: Test request fails

    note right of Closed
        Normal operation
        All requests allowed
        Track failure count
    end note

    note right of Open
        Circuit tripped
        All requests rejected
        Return CircuitOpenError
        Wait for timeout
    end note

    note right of HalfOpen
        Recovery attempt
        Single test request
        Decide: Close or Open
    end note
```

## Detailed Flow

```mermaid
flowchart TD
    Start([Request to Odoo/Shopee]) --> CheckState{Circuit<br/>State?}

    CheckState -->|CLOSED| AllowRequest[Allow Request]
    CheckState -->|OPEN| CheckTimeout{Timeout<br/>Elapsed?}
    CheckState -->|HALF_OPEN| TestRequest[Test Request]

    CheckTimeout -->|No| RejectRequest[Reject with<br/>CircuitOpenError]
    CheckTimeout -->|Yes| TransitionHalfOpen[Transition to<br/>HALF_OPEN]
    TransitionHalfOpen --> TestRequest

    AllowRequest --> ExecuteRequest[Execute Request]
    ExecuteRequest --> RequestResult{Result?}

    RequestResult -->|Success| ResetCounter[Reset Failure Counter]
    ResetCounter --> Success([Return Success])

    RequestResult -->|Failure| IncrementCounter[Increment Failure Counter]
    IncrementCounter --> CheckThreshold{Threshold<br/>Exceeded?}

    CheckThreshold -->|No| RetryLater[Schedule Retry]
    CheckThreshold -->|Yes| TripCircuit[Trip Circuit<br/>Transition to OPEN]
    TripCircuit --> AlertSlack[Alert Slack]
    AlertSlack --> RejectRequest

    TestRequest --> TestResult{Test<br/>Success?}
    TestResult -->|Yes| CloseCircuit[Close Circuit<br/>Reset Counter]
    TestResult -->|No| ReopenCircuit[Reopen Circuit]

    CloseCircuit --> Success
    ReopenCircuit --> RejectRequest
    RejectRequest --> RetryLater
    RetryLater --> End([End])
    Success --> End
```

## Configuration

```mermaid
graph LR
    subgraph "Circuit Breaker Registry"
        OdooBreaker[Odoo Circuit Breaker]
        ShopeeBreaker[Shopee Circuit Breaker]
    end

    subgraph "Odoo Config"
        OdooThreshold[Failure Threshold: 5]
        OdooWindow[Time Window: 60s]
        OdooTimeout[Open Timeout: 60s]
        OdooHalfOpenMax[Half-Open Max: 1]
    end

    subgraph "Shopee Config"
        ShopeeThreshold[Failure Threshold: 5]
        ShopeeWindow[Time Window: 60s]
        ShopeeTimeout[Open Timeout: 30s]
        ShopeeHalfOpenMax[Half-Open Max: 1]
    end

    OdooBreaker --> OdooThreshold
    OdooBreaker --> OdooWindow
    OdooBreaker --> OdooTimeout
    OdooBreaker --> OdooHalfOpenMax

    ShopeeBreaker --> ShopeeThreshold
    ShopeeBreaker --> ShopeeWindow
    ShopeeBreaker --> ShopeeTimeout
    ShopeeBreaker --> ShopeeHalfOpenMax
```

## Metrics & Monitoring

```mermaid
graph TD
    subgraph "Prometheus Metrics"
        StateMetric[mw_circuit_breaker_state<br/>0=closed, 1=open, 2=half_open]
        FailureMetric[mw_circuit_breaker_failures_total]
        SuccessMetric[mw_circuit_breaker_successes_total]
        OpenTimeMetric[mw_circuit_breaker_open_duration_seconds]
    end

    subgraph "Grafana Dashboard"
        StatePanel[Circuit Breaker State<br/>Time Series]
        FailurePanel[Failure Rate<br/>Graph]
        AlertPanel[Open Duration<br/>Gauge]
    end

    subgraph "Alertmanager"
        CriticalAlert[Alert: Circuit Open > 5min<br/>Severity: Critical]
        WarningAlert[Alert: High Failure Rate<br/>Severity: Warning]
    end

    StateMetric --> StatePanel
    FailureMetric --> FailurePanel
    OpenTimeMetric --> AlertPanel

    StatePanel --> CriticalAlert
    FailurePanel --> WarningAlert
```

## Worker Retry Logic

```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant CB as Circuit Breaker
    participant Service as Odoo/Shopee
    participant Queue as Redis Queue

    Worker->>CB: call(service.method)
    CB->>CB: Check state

    alt State = CLOSED
        CB->>Service: Execute request
        Service-->>CB: Response
        alt Success
            CB->>CB: Reset failure counter
            CB-->>Worker: Return response
        else Failure
            CB->>CB: Increment failure counter
            CB->>CB: Check threshold
            alt Threshold exceeded
                CB->>CB: Transition to OPEN
                CB->>CB: Start timeout timer
            end
            CB-->>Worker: Raise exception
            Worker->>Worker: Classify error
            alt Retryable
                Worker->>Worker: Calculate countdown
                Worker->>Queue: Requeue with countdown
            else Non-retryable
                Worker->>Worker: Mark as dead_letter
            end
        end
    else State = OPEN
        CB-->>Worker: Raise CircuitOpenError
        Worker->>Worker: Calculate countdown<br/>(based on service)
        Worker->>Queue: Requeue with countdown
    else State = HALF_OPEN
        CB->>Service: Test request
        Service-->>CB: Response
        alt Success
            CB->>CB: Transition to CLOSED
            CB-->>Worker: Return response
        else Failure
            CB->>CB: Transition to OPEN
            CB-->>Worker: Raise CircuitOpenError
            Worker->>Queue: Requeue with countdown
        end
    end
```

## Recovery Scenarios

### Scenario 1: Odoo Database Maintenance

```mermaid
gantt
    title Odoo Maintenance - Circuit Breaker Behavior
    dateFormat HH:mm
    axisFormat %H:%M

    section Circuit State
    CLOSED           :done, closed1, 14:00, 5m
    OPEN (tripped)   :crit, open1, 14:05, 10m
    HALF_OPEN (test) :active, half1, 14:15, 1m
    CLOSED (recovered):done, closed2, 14:16, 14m

    section Odoo Status
    Online           :done, online1, 14:00, 5m
    Maintenance      :crit, maint, 14:05, 10m
    Online           :done, online2, 14:15, 15m

    section Worker Behavior
    Normal processing:done, work1, 14:00, 5m
    Requests rejected:crit, reject, 14:05, 10m
    Test request     :active, test, 14:15, 1m
    Normal processing:done, work2, 14:16, 14m
```

### Scenario 2: Network Partition

```mermaid
gantt
    title Network Partition - Multiple Open/Close Cycles
    dateFormat HH:mm
    axisFormat %H:%M

    section Circuit State
    CLOSED           :done, c1, 10:00, 3m
    OPEN             :crit, o1, 10:03, 1m
    HALF_OPEN        :active, h1, 10:04, 1m
    OPEN             :crit, o2, 10:05, 1m
    HALF_OPEN        :active, h2, 10:06, 1m
    CLOSED           :done, c2, 10:07, 23m

    section Network
    Stable           :done, n1, 10:00, 3m
    Flapping         :crit, flap, 10:03, 4m
    Stable           :done, n2, 10:07, 23m
```

## Best Practices

1. **Failure Threshold**
   - Set based on service SLA
   - Odoo: 5 failures (higher tolerance)
   - Shopee: 5 failures (API rate limits)

2. **Timeout Duration**
   - Odoo: 60s (database recovery time)
   - Shopee: 30s (faster API recovery)

3. **Half-Open Strategy**
   - Single test request only
   - Fail fast if test fails
   - Immediate close if test succeeds

4. **Monitoring**
   - Alert if circuit open > 5 minutes
   - Track open/close frequency
   - Correlate with service health

5. **Worker Retry**
   - Use circuit-aware countdown
   - Don't retry immediately on CircuitOpenError
   - Respect service-specific backoff

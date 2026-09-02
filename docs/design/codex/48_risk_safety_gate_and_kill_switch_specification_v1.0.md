# Risk, Safety Gate and Kill Switch Specification v1.0

> 범위: 배포·주문·복구 전 최종 deterministic veto를 제공하고 위험/사고 시 자동 중지한다.
> 원칙: Risk Engine은 추천하지 않으며, 허용/거부/중지의 명확한 근거를 낸다.

## 1. Risk decision input

| 분류 | 신호 |
|---|---|
| Mandate | strategy/asset/universe 허용 여부, autonomy, 자본·노출·손실 한도 |
| Account | 잔고 freshness, 포지션, 현금, margin, connection health |
| Market | 거래시간, 호가/유동성, 변동성, 가격 gap, venue 상태 |
| Strategy | package lifecycle, validation expiry, drift/failure condition |
| Data/Model | stale/missing data, anomaly, model revision/health |
| Operations | kill switch, incident, reconciliation state, credential/egress provenance |

## 2. Decision contract

```text
RiskDecision { outcome: ALLOW | DENY | REDUCE | PAUSE | ESCALATE,
               rule_version, input_refs, reason_codes, obligations,
               evaluated_at, expires_at, trace_id, evidence_ref }
```

동일한 pinned input과 rule version은 같은 결론을 내야 한다. `ALLOW`는 짧은 TTL을 가지며 order lifecycle에서 재사용하지 않는다.

## 3. Gate 계층

1. deployment gate: package, mandate, consent, connection, mode provenance 검사.
2. pre-intent gate: 목표 배정/주문 계획이 노출·집중·유동성·시간 한도를 만족하는지 검사.
3. pre-submit gate: 최신 가격/잔고/provider health와 kill switch를 재검사.
4. intraday gate: drawdown, volatility, data/model drift, reconciliation breach를 감시.
5. recovery gate: incident/reconciliation 후 자동 재개를 금지하고 policy/approval을 재검사.

## 4. Kill switch

kill switch는 `TENANT`, `ACCOUNT`, `STRATEGY_DEPLOYMENT`, `PROVIDER`, `GLOBAL` 범위를 가진다. 작동 시 새 intent/submit을 차단하고, 취소 가능한 미체결 주문은 workflow로 정리하며, 사용자·운영자·audit timeline에 사건을 남긴다. 해제는 원인을 해결했다는 evidence와 독립 recovery decision 없이는 불가하다.

## 5. acceptance tests

1. 어느 한 gate라도 deny/pause면 provider adapter가 호출되지 않는다.
2. drawdown·stale data·provider outage·reconciliation mismatch가 각각 독립적으로 stop/pause를 유발한다.
3. agent 또는 frontend가 RiskDecision을 override할 수 없다.
4. global kill switch는 모든 tenant의 새 주문을 막되 각 tenant 증적을 보존한다.
5. recovery는 자동으로 running 상태를 만들지 않고 fresh policy/risk checks를 요구한다.

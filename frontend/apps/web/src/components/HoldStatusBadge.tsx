import type { HoldState, ParsedHoldView, ParsedPayoutBatchView, PayoutBatchState } from "@aios/shared-types";
import { Alert, Badge } from "@aios/ui-web";

// spec §4.5 홀드 상태기계·§3.3 (C) PayoutBatchView 표시 전용 배지.
// InstrumentLifecycleBadge/CandleQualityBadge와 같은 순수 표시 컴포넌트
// 패턴 — 서버가 내려준 state를 그대로 보여줄 뿐, 홀드 전이나 정산 배치 전이를
// 클라이언트가 계산하거나 낙관적으로 갱신하지 않는다(§4.5는 ledger_hold/payouts.py
// 소관, task-658 decision).
//
// HoldStatusBadge는 PENDING인데 now가 expires_at을 지났으면(스케줄러가 아직
// expire 이벤트를 처리하지 못한 경우) 그 사실을 InstrumentLifecycleBadge의
// needsReview와 같은 방식으로 "확인 필요" 배지로만 덧붙인다 — 서버 state를
// EXPIRED로 바꿔치기하지 않는다.

interface HoldStatusBadgeProps {
  hold: ParsedHoldView;
  now?: string;
}

interface PayoutBatchStatusBadgeProps {
  payoutBatch: ParsedPayoutBatchView;
}

const HOLD_STATE_LABEL: Record<HoldState, string> = {
  PENDING: "홀드 중",
  CAPTURED: "확정",
  RELEASED: "해제",
  EXPIRED: "만료",
};

const HOLD_STATE_TONE: Record<HoldState, "neutral" | "success" | "warning" | "danger"> = {
  PENDING: "neutral",
  CAPTURED: "success",
  RELEASED: "neutral",
  EXPIRED: "warning",
};

const PAYOUT_BATCH_STATE_LABEL: Record<PayoutBatchState, string> = {
  SCHEDULED: "예정",
  RELEASED: "정산 대기",
  PAID: "지급 완료",
  FAILED: "실패",
};

const PAYOUT_BATCH_STATE_TONE: Record<PayoutBatchState, "neutral" | "success" | "warning" | "danger"> = {
  SCHEDULED: "neutral",
  RELEASED: "warning",
  PAID: "success",
  FAILED: "danger",
};

/** now가 주어지지 않았거나 파싱 불가면(Date.parse 실패) 판단 근거가 없으므로
 * "확인 필요"를 띄우지 않는다 — InstrumentLifecycleBadge.needsReview와 동일
 * 원칙(데이터 부재를 모순으로 오인하지 않는다). */
function needsExpiryReview(state: HoldState, expiresAt: string, now: string | undefined): boolean {
  if (state !== "PENDING" || now === undefined) return false;
  const nowMs = Date.parse(now);
  const expiresMs = Date.parse(expiresAt);
  if (!Number.isFinite(nowMs) || !Number.isFinite(expiresMs)) return false;
  return nowMs > expiresMs;
}

export function HoldStatusBadge({ hold, now }: HoldStatusBadgeProps) {
  if (hold.kind === "unsupported_schema_version") {
    return (
      <div data-testid="hold-status-badge">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(hold.received)}).</Alert>
      </div>
    );
  }

  if (hold.kind !== "ok") {
    return (
      <div data-testid="hold-status-badge">
        <Alert tone="danger">홀드 정보를 해석할 수 없습니다.</Alert>
      </div>
    );
  }

  const { state, expires_at: expiresAt } = hold.value;
  const flagged = needsExpiryReview(state, expiresAt, now);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="hold-status-badge">
      <Badge tone={HOLD_STATE_TONE[state]} data-testid="hold-state-badge">
        {HOLD_STATE_LABEL[state]}
      </Badge>
      {flagged && (
        <Badge tone="danger" data-testid="hold-expiry-review-badge">
          만료 확인 필요
        </Badge>
      )}
    </div>
  );
}

export function PayoutBatchStatusBadge({ payoutBatch }: PayoutBatchStatusBadgeProps) {
  if (payoutBatch.kind === "unsupported_schema_version") {
    return (
      <div data-testid="payout-batch-status-badge">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(payoutBatch.received)}).</Alert>
      </div>
    );
  }

  if (payoutBatch.kind !== "ok") {
    return (
      <div data-testid="payout-batch-status-badge">
        <Alert tone="danger">정산 배치 정보를 해석할 수 없습니다.</Alert>
      </div>
    );
  }

  const { state } = payoutBatch.value;

  return (
    <div data-testid="payout-batch-status-badge">
      <Badge tone={PAYOUT_BATCH_STATE_TONE[state]} data-testid="payout-batch-state-badge">
        {PAYOUT_BATCH_STATE_LABEL[state]}
      </Badge>
    </div>
  );
}

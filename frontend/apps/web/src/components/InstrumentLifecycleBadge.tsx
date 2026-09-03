import type { ParsedInstrumentView, SymbolAlias, SymbolStatus } from "@aios/shared-types";
import { Alert, Badge } from "@aios/ui-web";

// spec §4.2 심볼 생애주기 표시 전용 배지. CandleQualityBadge와 같은 순수 표시
// 컴포넌트 패턴 — 서버가 계산한 status를 그대로 보여줄 뿐, 상태기계를
// 클라이언트가 재계산하지 않는다(§4.2는 서버 소관, task-708 decision).
//
// alias 목록(aliases)은 이미 parseSymbolAlias를 통과한 값만 넘어온다고
// 가정한다 — 이 컴포넌트는 재파싱하지 않는다. status가 LISTED인데 `now`
// 시점에 유효한 별칭이 하나도 없으면(전부 valid_to가 과거) 그 사실을
// 숨기지 않고 "확인 필요" 배지로 별도 표기한다 — 임의로 DELISTED로
// 바꿔치기하지 않는다(서버 값을 덮어쓰지 않는다는 원칙, positionView.ts와
// 동일).
interface InstrumentLifecycleBadgeProps {
  instrument: ParsedInstrumentView;
  aliases?: SymbolAlias[];
  now: string;
}

const STATUS_LABEL: Record<SymbolStatus, string> = {
  PENDING: "대기",
  LISTED: "활성",
  SUSPENDED: "일시중지",
  DELISTED: "상장폐지",
};

const STATUS_TONE: Record<SymbolStatus, "neutral" | "success" | "warning" | "danger"> = {
  PENDING: "neutral",
  LISTED: "success",
  SUSPENDED: "warning",
  DELISTED: "danger",
};

function isAliasCurrent(alias: SymbolAlias, nowMs: number): boolean {
  const fromMs = Date.parse(alias.valid_from);
  const toMs = alias.valid_to === null ? Number.POSITIVE_INFINITY : Date.parse(alias.valid_to);
  return fromMs <= nowMs && nowMs < toMs;
}

/** aliases가 비어있으면(별칭 이력을 아직 못 받아온 경우) 판단할 근거가 없으므로
 * "확인 필요"를 띄우지 않는다 — 데이터 부재를 모순으로 오인하지 않는다. */
function needsReview(status: SymbolStatus, aliases: SymbolAlias[], now: string): boolean {
  if (status !== "LISTED" || aliases.length === 0) return false;
  const nowMs = Date.parse(now);
  if (!Number.isFinite(nowMs)) return false;
  return !aliases.some((alias) => isAliasCurrent(alias, nowMs));
}

export function InstrumentLifecycleBadge({ instrument, aliases = [], now }: InstrumentLifecycleBadgeProps) {
  if (instrument.kind === "unsupported_schema_version") {
    return (
      <div data-testid="instrument-lifecycle-badge">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(instrument.received)}).</Alert>
      </div>
    );
  }

  if (instrument.kind !== "ok") {
    return (
      <div data-testid="instrument-lifecycle-badge">
        <Alert tone="danger">인스트루먼트 정보를 해석할 수 없습니다.</Alert>
      </div>
    );
  }

  const { status, canonical_symbol: canonicalSymbol, venue_symbol: venueSymbol } = instrument.value;
  const flagged = needsReview(status, aliases, now);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="instrument-lifecycle-badge">
      <Badge tone={STATUS_TONE[status]} data-testid="status-badge">
        {STATUS_LABEL[status]}
      </Badge>
      <span className="text-xs text-fg-muted" data-testid="symbol-label">
        {canonicalSymbol} ({venueSymbol})
      </span>
      {flagged && (
        <Badge tone="warning" data-testid="needs-review-badge">
          확인 필요
        </Badge>
      )}
    </div>
  );
}

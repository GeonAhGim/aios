import type { CoverageSpanView } from "@aios/api-client";
import { deriveFreshness } from "@aios/api-client";
import { Badge } from "@aios/ui-web";

// DC-18b — task-2195(DC-18a)의 GET .../market-data/coverage 응답(병합된
// CoverageSpan 목록)을 화면에 "지연/미커버 구간"으로 표시하는 순수 표시 컴포넌트
// (CandleQualityBadge와 같은 관용 — fetch는 호출부(ChartPage)가 하고 이미 조회된
// spans를 그대로 props로 받는다).
//
// 두 판정을 분리한다: (1) 미커버 구간(요청 range를 spans가 실제로 덮는지 — 새로
// 계산해야 하는 구간 커버리지 문제, gaps.py의 plan_fetch와는 축이 다르다 —
// plan_fetch는 "다음에 뭘 가져올지"를 정하고 여기는 "지금 뭐가 비었는지"만 본다)
// (2) 지연(가장 최근 span의 end_at이 오래됐는지 — envelope.ts의 deriveFreshness를
// 그대로 재사용한다, as_of 신선도와 개념이 같다. task-2196 decision).
interface CoverageBadgeProps {
  spans: CoverageSpanView[];
  /** ISO datetime 문자열(UTC) — 커버리지를 확인할 구간. */
  rangeStart: string;
  rangeEnd: string;
  now?: Date;
  staleAfterSec?: number;
}

interface Interval {
  start: number;
  end: number;
}

function toInterval(span: CoverageSpanView): Interval | null {
  const start = Date.parse(span.startAt);
  const end = Date.parse(span.endAt);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return { start, end };
}

// [rangeStart, rangeEnd) 중 어떤 span에도 덮이지 않은 부분 구간들을 반환한다.
// spans는 이미 서버(domain/coverage/registry.merge_spans)에서 축별로 병합됐지만,
// 이 축(quality_grade별로 분리될 수 있음) 안에서도 겹칠 수 있으므로 여기서 다시
// 정렬·병합한다 — 결과 신뢰를 서버 병합에만 기대지 않는다.
function findUncoveredGaps(spans: CoverageSpanView[], rangeStart: string, rangeEnd: string): Array<[string, string]> {
  const rangeStartMs = Date.parse(rangeStart);
  const rangeEndMs = Date.parse(rangeEnd);
  if (Number.isNaN(rangeStartMs) || Number.isNaN(rangeEndMs) || rangeStartMs >= rangeEndMs) return [];

  const clamped = spans
    .map(toInterval)
    .filter((iv): iv is Interval => iv !== null)
    .map((iv) => ({ start: Math.max(iv.start, rangeStartMs), end: Math.min(iv.end, rangeEndMs) }))
    .filter((iv) => iv.start < iv.end)
    .sort((a, b) => a.start - b.start);

  const gaps: Array<[string, string]> = [];
  let cursor = rangeStartMs;
  for (const iv of clamped) {
    if (iv.start > cursor) {
      gaps.push([new Date(cursor).toISOString(), new Date(iv.start).toISOString()]);
    }
    cursor = Math.max(cursor, iv.end);
  }
  if (cursor < rangeEndMs) {
    gaps.push([new Date(cursor).toISOString(), new Date(rangeEndMs).toISOString()]);
  }
  return gaps;
}

function latestEndAt(spans: CoverageSpanView[]): string | null {
  let latest: string | null = null;
  let latestMs = -Infinity;
  for (const span of spans) {
    const ms = Date.parse(span.endAt);
    if (!Number.isNaN(ms) && ms > latestMs) {
      latestMs = ms;
      latest = span.endAt;
    }
  }
  return latest;
}

export function CoverageBadge({ spans, rangeStart, rangeEnd, now, staleAfterSec = 300 }: CoverageBadgeProps) {
  if (spans.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-2" data-testid="coverage-badge">
        <Badge tone="danger" data-testid="coverage-gap-badge">
          미커버 구간
        </Badge>
      </div>
    );
  }

  const gaps = findUncoveredGaps(spans, rangeStart, rangeEnd);
  const freshness = deriveFreshness(latestEndAt(spans), now ?? new Date(), { staleAfterSec });
  const isStale = freshness.kind === "ok" && freshness.isStale;

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="coverage-badge">
      {gaps.length > 0 ? (
        <Badge tone="warning" data-testid="coverage-gap-badge">
          미커버 {gaps.length}구간
        </Badge>
      ) : (
        <Badge tone="success" data-testid="coverage-gap-badge">
          구간 전체 커버
        </Badge>
      )}
      {isStale && (
        <Badge tone="warning" data-testid="coverage-stale-badge">
          지연됨
        </Badge>
      )}
    </div>
  );
}

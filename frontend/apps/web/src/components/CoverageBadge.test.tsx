import "@testing-library/jest-dom/vitest";
import type { CoverageSpanView } from "@aios/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CoverageBadge } from "./CoverageBadge";

// DataFreshness.test.tsx/CandleQualityBadge.test.tsx와 동일 사유(vitest.config에
// globals:true가 없어 자동 cleanup 미등록) — 여러 번 render하며 부재를 검증할 때
// 명시적으로 cleanup하지 않으면 이전 테스트 DOM이 남아 오탐이 난다.
afterEach(cleanup);

function span(overrides: Partial<CoverageSpanView> = {}): CoverageSpanView {
  return {
    instrumentId: "i-1",
    venue: "BITGET",
    assetClass: "CRYPTO",
    timeframe: "1h",
    qualityGrade: "VALIDATED",
    startAt: "2026-09-03T00:00:00Z",
    endAt: "2026-09-03T04:00:00Z",
    ...overrides,
  };
}

describe("CoverageBadge", () => {
  it("spans가 비어 있으면 미커버 구간을 보여준다", () => {
    render(<CoverageBadge spans={[]} rangeStart="2026-09-03T00:00:00Z" rangeEnd="2026-09-03T04:00:00Z" />);

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 구간");
    expect(screen.queryByTestId("coverage-stale-badge")).not.toBeInTheDocument();
  });

  it("요청 구간을 spans가 전부 덮으면 정상 배지만 보여준다", () => {
    const now = new Date("2026-09-03T04:00:30Z");
    render(
      <CoverageBadge
        spans={[span()]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T04:00:00Z"
        now={now}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("구간 전체 커버");
    expect(screen.queryByTestId("coverage-stale-badge")).not.toBeInTheDocument();
  });

  it("요청 구간의 일부만 덮으면 미커버 구간 수를 보여준다", () => {
    const now = new Date("2026-09-03T06:00:30Z");
    render(
      <CoverageBadge
        spans={[span({ startAt: "2026-09-03T00:00:00Z", endAt: "2026-09-03T02:00:00Z" })]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T06:00:00Z"
        now={now}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 1구간");
  });

  it("가장 최근 span의 end_at이 staleAfterSec을 넘기면 지연됨 배지를 함께 보여준다", () => {
    const now = new Date("2026-09-03T04:10:00Z");
    render(
      <CoverageBadge
        spans={[span()]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T04:00:00Z"
        staleAfterSec={300}
        now={now}
      />,
    );

    expect(screen.getByTestId("coverage-stale-badge")).toHaveTextContent("지연됨");
  });

  it("staleAfterSec 이내면 지연됨 배지를 보여주지 않는다", () => {
    const now = new Date("2026-09-03T04:02:00Z");
    render(
      <CoverageBadge
        spans={[span()]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T04:00:00Z"
        staleAfterSec={300}
        now={now}
      />,
    );

    expect(screen.queryByTestId("coverage-stale-badge")).not.toBeInTheDocument();
  });

  it("negative: 여러 span 사이에 낀 구간도 미커버로 잡는다(양 끝만 덮인 경우)", () => {
    const now = new Date("2026-09-03T06:00:30Z");
    render(
      <CoverageBadge
        spans={[
          span({ startAt: "2026-09-03T00:00:00Z", endAt: "2026-09-03T01:00:00Z" }),
          span({ startAt: "2026-09-03T05:00:00Z", endAt: "2026-09-03T06:00:00Z" }),
        ]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T06:00:00Z"
        now={now}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 1구간");
  });

  it("negative: rangeStart가 rangeEnd 이상인 빈 구간(0폭)은 예외를 던지지 않고 구간 전체 커버로 접는다", () => {
    render(
      <CoverageBadge
        spans={[span()]}
        rangeStart="2026-09-03T02:00:00Z"
        rangeEnd="2026-09-03T02:00:00Z"
        now={new Date("2026-09-03T02:00:00Z")}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("구간 전체 커버");
  });

  it("negative: rangeStart가 rangeEnd보다 뒤(역전된 구간)여도 예외를 던지지 않는다", () => {
    render(
      <CoverageBadge
        spans={[span()]}
        rangeStart="2026-09-03T04:00:00Z"
        rangeEnd="2026-09-03T00:00:00Z"
        now={new Date("2026-09-03T04:00:00Z")}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("구간 전체 커버");
  });

  it("negative: startAt/endAt이 파싱 불가능한 span은 커버로 세지 않고(gap으로 접힘) 다른 정상 span 판정에 전염되지 않는다", () => {
    const now = new Date("2026-09-03T06:00:30Z");
    render(
      <CoverageBadge
        spans={[
          span({ startAt: "not-a-date", endAt: "also-not-a-date" }),
          span({ startAt: "2026-09-03T04:00:00Z", endAt: "2026-09-03T06:00:00Z" }),
        ]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T06:00:00Z"
        now={now}
      />,
    );

    // 파싱 불가 span은 toInterval에서 null로 걸러지므로 [00:00, 04:00) 전체가
    // 미커버 1구간으로 남는다 — 두 번째(정상) span만큼만 커버된다.
    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 1구간");
  });

  it("red-gate 회귀: 겹치는 span을 시간순 역순·비정렬로 넘겨도 병합 결과는 정렬된 입력과 동일하다(내부 재정렬 없으면 이 테스트가 깨진다)", () => {
    const now = new Date("2026-09-03T18:00:30Z");
    // 의도적으로 뒤죽박죽 순서로 제공: C(17-18) → A(00-10, B와 겹침) → B(09-16).
    // 커서 기반 병합은 정렬을 전제하므로, 만약 findUncoveredGaps가 입력 순서
    // 그대로(정렬 없이) 훑는다면 A 다음에 C를 만나 [10,17) 전체를 갭으로 오판하고,
    // 그 뒤 B(09-16)를 만나 이미 지난 구간이라 무시해 갭이 2개(혹은 잘못된 경계)로
    // 계산된다. 정렬이 살아있는 한 [16:00, 17:00) 단 1구간만 갭이어야 한다.
    render(
      <CoverageBadge
        spans={[
          span({ startAt: "2026-09-03T17:00:00Z", endAt: "2026-09-03T18:00:00Z" }),
          span({ startAt: "2026-09-03T00:00:00Z", endAt: "2026-09-03T10:00:00Z" }),
          span({ startAt: "2026-09-03T09:00:00Z", endAt: "2026-09-03T16:00:00Z" }),
        ]}
        rangeStart="2026-09-03T00:00:00Z"
        rangeEnd="2026-09-03T18:00:00Z"
        now={now}
      />,
    );

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 1구간");
  });

  it("수치/성능: 500개의 촘촘한 span(짝수 인덱스만 실제로 존재)에서도 정확히 250개의 갭을 계산하고 짧은 시간 안에 끝난다", () => {
    const SPAN_COUNT = 500;
    const HOUR_MS = 60 * 60 * 1000;
    const base = Date.parse("2026-09-03T00:00:00Z");
    // 짝수 인덱스 span만 실존 → 홀수 인덱스 구간마다 정확히 1시간짜리 갭이 생긴다.
    const spans: CoverageSpanView[] = [];
    for (let i = 0; i < SPAN_COUNT; i += 2) {
      spans.push(
        span({
          startAt: new Date(base + i * HOUR_MS).toISOString(),
          endAt: new Date(base + (i + 1) * HOUR_MS).toISOString(),
        }),
      );
    }
    const rangeStart = new Date(base).toISOString();
    const rangeEnd = new Date(base + SPAN_COUNT * HOUR_MS).toISOString();

    const startedAt = performance.now();
    render(
      <CoverageBadge
        spans={spans}
        rangeStart={rangeStart}
        rangeEnd={rangeEnd}
        now={new Date(base + SPAN_COUNT * HOUR_MS)}
      />,
    );
    const elapsedMs = performance.now() - startedAt;

    expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent(`미커버 ${SPAN_COUNT / 2}구간`);
    // O(n log n) 정렬 기반 병합이라면 500개 span 렌더는 수백 ms를 넘길 이유가
    // 없다 — 회귀로 O(n^2)가 되면(예: 정렬 없이 각 span마다 전체를 재스캔) 이
    // 임계값이 신호를 준다.
    expect(elapsedMs).toBeLessThan(1000);
  });
});

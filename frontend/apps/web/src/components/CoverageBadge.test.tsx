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
});

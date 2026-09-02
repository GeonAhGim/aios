import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DataFreshness } from "./DataFreshness";

// vitest.config에 globals:true가 없어 testing-library의 자동 cleanup 등록이
// 동작하지 않는다 — 이 파일처럼 render를 여러 번 호출하며 부재(not.toBeInTheDocument)를
// 검증할 때는 명시적으로 cleanup하지 않으면 이전 테스트의 DOM이 남아 오탐이 난다.
afterEach(cleanup);

describe("DataFreshness", () => {
  it("staleAfterSec을 넘긴 as_of는 경고 배지와 함께 보여준다", () => {
    const now = new Date("2026-09-03T00:10:00Z");
    render(<DataFreshness asOf="2026-09-03T00:00:00Z" staleAfterSec={300} now={now} />);

    expect(screen.getByText("기준 시각 · 10분 전")).toBeInTheDocument();
    expect(screen.getByTestId("data-freshness-stale-badge")).toBeInTheDocument();
    expect(screen.getByText("지연됨")).toBeInTheDocument();
  });

  it("staleAfterSec 이내면 경고 배지를 보여주지 않는다", () => {
    const now = new Date("2026-09-03T00:02:00Z");
    render(<DataFreshness asOf="2026-09-03T00:00:00Z" staleAfterSec={300} now={now} />);

    expect(screen.getByText("기준 시각 · 2분 전")).toBeInTheDocument();
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
  });

  it("as_of가 없거나 파싱 불가면 확인 불가 문구를 보여주고 fresh로 침묵 처리하지 않는다", () => {
    const now = new Date("2026-09-03T00:00:00Z");
    const { rerender } = render(<DataFreshness asOf={undefined} now={now} />);
    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();

    rerender(<DataFreshness asOf="not-a-date" now={now} />);
    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
  });
});

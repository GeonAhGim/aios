import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemStatusPage } from "./SystemStatusPage";

let queryResult: { data: unknown; isLoading: boolean; isError: boolean } = {
  data: undefined,
  isLoading: false,
  isError: false,
};

vi.mock("@aios/shared-hooks", () => ({
  usePlatformReadiness: () => queryResult,
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

function renderPage() {
  render(
    <MemoryRouter>
      <SystemStatusPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  queryResult = { data: undefined, isLoading: false, isError: false };
});

const READY_REPORT = {
  status: "ready",
  checks: { db_pool: { ok: true, detail: null, observed: 3, threshold: 10 } },
  as_of: "2026-09-03T00:00:00Z",
};

const NOT_READY_REPORT = {
  status: "not_ready",
  checks: {
    db_pool: { ok: true, detail: null, observed: 1, threshold: 10 },
    event_bus: { ok: false, detail: "no consumers", observed: 0, threshold: 1 },
    "loop:trading": { ok: false, detail: null, observed: 900, threshold: 300 },
  },
  as_of: "2026-09-03T00:00:00Z",
};

describe("SystemStatusPage", () => {
  it("ready 응답이면 정상 배너와 체크 표를 보여준다", () => {
    queryResult = { data: READY_REPORT, isLoading: false, isError: false };
    renderPage();

    expect(screen.getByText("모든 체크가 정상입니다.")).toBeInTheDocument();
    expect(screen.getByText("db_pool")).toBeInTheDocument();
  });

  it("not_ready 응답이면 실패 원인(ok=false)만 상단에 요약한다", () => {
    queryResult = { data: NOT_READY_REPORT, isLoading: false, isError: false };
    renderPage();

    const summary = within(screen.getByTestId("readiness-failure-summary"));
    expect(summary.getByText("저하됨 — 원인 체크 2건")).toBeInTheDocument();
    expect(summary.getByText("event_bus: no consumers")).toBeInTheDocument();
    expect(summary.getByText("loop:trading")).toBeInTheDocument();
    // 정상 체크(db_pool)는 원인 요약에 나오지 않는다.
    expect(summary.queryByText(/db_pool: /)).not.toBeInTheDocument();
  });

  it("negative: checks가 빈 객체/미지 키/observed=null이어도 화면이 깨지지 않고 상태를 보여준다", () => {
    const malformed = {
      status: "not_ready",
      checks: { unknown_check: { ok: false, detail: null, observed: null, threshold: null } },
      as_of: "2026-09-03T00:00:00Z",
    };
    queryResult = { data: malformed, isLoading: false, isError: false };
    expect(() => renderPage()).not.toThrow();

    const summary = within(screen.getByTestId("readiness-failure-summary"));
    expect(summary.getByText("저하됨 — 원인 체크 1건")).toBeInTheDocument();
    expect(summary.getByText("unknown_check")).toBeInTheDocument();
    expect(screen.getByTestId("readiness-checks-table")).toBeInTheDocument();
  });

  it("negative: /readyz 503 등으로 쿼리 자체가 실패(ApiError 경로)해도 깨지지 않고 저하/미확인 상태로 표시한다", () => {
    queryResult = { data: undefined, isLoading: false, isError: true };
    expect(() => renderPage()).not.toThrow();

    expect(
      screen.getByText("상태를 확인할 수 없습니다 — 서버 응답이 없거나 형식이 예상과 다릅니다."),
    ).toBeInTheDocument();
    expect(screen.getByText("등록된 체크가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
  });

  it("negative: 응답 스키마가 다르면(unknown) ready로 단정하지 않는다", () => {
    queryResult = { data: { status: "healthy", checks: {}, as_of: "x" }, isLoading: false, isError: false };
    expect(() => renderPage()).not.toThrow();

    expect(
      screen.getByText("상태를 확인할 수 없습니다 — 서버 응답이 없거나 형식이 예상과 다릅니다."),
    ).toBeInTheDocument();
  });

  // task-936: ReadinessReport.as_of(§3.2, meta 아닌 body 필드)가
  // STALE_AFTER_SEC(300초)를 넘기면 DataFreshness가 stale 배지를 보여줘야
  // 한다 — 실제 "now"는 테스트 환경마다 달라지므로 vi.setSystemTime으로
  // 고정해 결정적으로 검증한다.
  it("as_of가 STALE_AFTER_SEC(300초)을 넘기면 지연됨 배지를 보여준다", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-03T00:10:00Z"));
    queryResult = { data: READY_REPORT, isLoading: false, isError: false };
    renderPage();

    expect(screen.getByTestId("data-freshness-stale-badge")).toBeInTheDocument();
    expect(screen.getByText("지연됨")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("as_of가 STALE_AFTER_SEC 이내면 지연됨 배지를 보여주지 않는다", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-03T00:02:00Z"));
    queryResult = { data: READY_REPORT, isLoading: false, isError: false };
    renderPage();

    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

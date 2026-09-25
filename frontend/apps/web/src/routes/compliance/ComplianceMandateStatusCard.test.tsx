import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "@aios/api-client";
import type { MandateRevisionView } from "@aios/shared-types";
import { ComplianceMandateStatusCard } from "./ComplianceMandateStatusCard";
import { ComplianceActionError } from "./ComplianceActionError";

afterEach(cleanup);

function revision(overrides: Partial<MandateRevisionView> = {}): MandateRevisionView {
  return {
    id: "rev-1",
    mandateId: "m-1",
    revisionNo: 1,
    state: "ACTIVE",
    maxTotalExposurePct: 50,
    maxSingleInstrumentPct: 10,
    minCashBufferPct: 5,
    maxDailyLossPct: 3,
    allowedAutonomy: "PAPER",
    forbiddenAssets: [],
    revisionHash: "h1",
    coolingOffStartedAt: null,
    createdAt: "2026-09-01T00:00:00Z",
    activatedAt: "2026-09-01T00:00:00Z",
    schemaVersion: "v1",
    ...overrides,
  };
}

describe("ComplianceMandateStatusCard — task-6277", () => {
  it("정상 렌더: activeRevision이 있으면 상태와 규칙 값을 보여준다", () => {
    render(
      <ComplianceMandateStatusCard
        activeRevision={revision({ maxTotalExposurePct: 42 })}
        pendingRevision={null}
      />,
    );

    expect(screen.getByText("mandate 상태")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("총 노출 한도: 42%")).toBeInTheDocument();
  });

  it("빈 데이터: activeRevision이 null이면 fail-closed 경고와 차단 안내를 보여준다(제한 없음으로 오인 불가)", () => {
    render(<ComplianceMandateStatusCard activeRevision={null} pendingRevision={null} />);

    expect(screen.getByText("위임장 미설정(주문 차단)")).toBeInTheDocument();
    expect(
      screen.getByText("활성 위임장이 없습니다 — 규칙이 없다는 뜻이 아니라 모든 주문이 차단된다는 뜻입니다."),
    ).toBeInTheDocument();
  });

  it("빈 데이터: forbiddenAssets가 비어있으면 '없음'을 보여준다", () => {
    render(
      <ComplianceMandateStatusCard activeRevision={revision({ forbiddenAssets: [] })} pendingRevision={null} />,
    );

    expect(screen.getByText("금지 자산:없음")).toBeInTheDocument();
  });

  it("데이터 있음: forbiddenAssets가 있으면 콤마로 join해서 보여준다", () => {
    render(
      <ComplianceMandateStatusCard
        activeRevision={revision({ forbiddenAssets: ["BTC", "ETH"] })}
        pendingRevision={null}
      />,
    );

    expect(screen.getByText("금지 자산:BTC, ETH")).toBeInTheDocument();
  });

  it("데이터 있음: pendingRevision이 있으면 대기 중 개정안 섹션도 함께 렌더한다", () => {
    render(
      <ComplianceMandateStatusCard
        activeRevision={revision({ state: "ACTIVE" })}
        pendingRevision={revision({ id: "rev-2", state: "PROPOSED", maxTotalExposurePct: 30 })}
      />,
    );

    expect(screen.getByText("대기 중 개정안")).toBeInTheDocument();
    expect(screen.getByText("PROPOSED")).toBeInTheDocument();
    expect(screen.getByText("총 노출 한도: 30%")).toBeInTheDocument();
  });
});

describe("ComplianceActionError — task-6277", () => {
  it("정상 렌더: 일반 오류는 ErrorMessage 배너로 메시지를 보여준다", () => {
    render(<ComplianceActionError error={new Error("boom")} />);

    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("에러 경계: 400 응답은 BadRequestNotice 경로로만 렌더한다(err.message 직접 노출 금지)", () => {
    render(<ComplianceActionError error={new ApiError(400, "raw bad request", "trace-1", "VALIDATION_UNKNOWN")} />);

    expect(screen.queryByText("raw bad request")).not.toBeInTheDocument();
  });

  it("에러 경계: 403 응답은 ForbiddenNotice 경로로만 렌더한다(err.message 직접 노출 금지)", () => {
    render(<ComplianceActionError error={new ApiError(403, "raw forbidden", "trace-2", "AUTHZ_FORBIDDEN")} />);

    expect(screen.queryByText("raw forbidden")).not.toBeInTheDocument();
  });

  it("에러 경계: errorCode가 없는 ApiError는 fallback 메시지로 렌더하고 traceId를 보여준다", () => {
    render(<ComplianceActionError error={new ApiError(500, "internal failure", "trace-3")} />);

    expect(screen.getByText("internal failure")).toBeInTheDocument();
    expect(screen.getByText((_, node) => node?.textContent === "지원코드: trace-3")).toBeInTheDocument();
  });
});

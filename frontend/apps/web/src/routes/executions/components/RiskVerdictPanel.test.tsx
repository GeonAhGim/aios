import "../../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RiskEvaluationView } from "@aios/shared-types";
import { setFeatureFlagOverride } from "../../../lib/featureFlags";
import { RiskVerdictPanel } from "./RiskVerdictPanel";

function allowVerdict(overrides: Partial<RiskEvaluationView> = {}): RiskEvaluationView {
  return {
    id: "eval-1",
    gateKind: "PRE_SUBMIT",
    outcome: "ALLOW",
    reasonCodes: [],
    obligations: [],
    ruleVersion: "risk-rules-v3",
    evaluatedAt: "2026-01-01T00:00:00Z",
    expiresAt: null,
    traceId: "trace-1",
    schemaVersion: "v1",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  setFeatureFlagOverride("FF_J3_RISK_PANEL", null);
});

describe("RiskVerdictPanel (task-7500, J3 G-4)", () => {
  it("negative: FF_J3_RISK_PANEL이 꺼져 있으면(기본값) 아무것도 렌더링하지 않는다", () => {
    const { container } = render(<RiskVerdictPanel status="success" data={allowVerdict()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("negative: 아직 제출 전(idle)이면 판정 없음 안내를 보여준다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(<RiskVerdictPanel status="idle" />);

    expect(screen.getByText("아직 판정 결과가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("ALLOW")).not.toBeInTheDocument();
    expect(screen.queryByText("DENY")).not.toBeInTheDocument();
  });

  it("판정 확인 중(pending)에는 ALLOW를 보여주지 않는다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(<RiskVerdictPanel status="pending" />);

    expect(screen.getByText("판정을 확인하는 중입니다...")).toBeInTheDocument();
    expect(screen.queryByText("ALLOW")).not.toBeInTheDocument();
  });

  it("ALLOW 판정이면 규칙 근거와 판정 시각을 함께 보여준다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(<RiskVerdictPanel status="success" data={allowVerdict()} />);

    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.getByText("risk-rules-v3")).toBeInTheDocument();
    expect(screen.getByText("2026-01-01T00:00:00Z")).toBeInTheDocument();
  });

  it("negative: DENY 판정이면 사유 코드를 사람이 읽을 수 있는 문장으로 보여준다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(
      <RiskVerdictPanel
        status="success"
        data={allowVerdict({ outcome: "DENY", reasonCodes: ["RISK_MAX_DRAWDOWN_EXCEEDED"] })}
      />,
    );

    expect(screen.getByText("DENY")).toBeInTheDocument();
    expect(
      screen.getByText("최대 손실 한도를 초과하여 거부되었습니다."),
    ).toBeInTheDocument();
  });

  it("negative: 판정 조회 자체가 실패(지연/오류)하면 ALLOW를 절대 보여주지 않고 fail-closed 문구를 보여준다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(
      <RiskVerdictPanel
        status="error"
        error={{ error_code: "EXCHANGE_UNAVAILABLE", details: {} }}
      />,
    );

    expect(
      screen.getByText(
        "판정 결과를 확인할 수 없습니다. 안전을 위해 허용되지 않은 것으로 처리합니다.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("ALLOW")).not.toBeInTheDocument();
  });

  it("negative: 판정 실패 원인이 정책/리스크 거부(reason_codes 포함)면 그 사유도 함께 보여준다", () => {
    setFeatureFlagOverride("FF_J3_RISK_PANEL", true);
    render(
      <RiskVerdictPanel
        status="error"
        error={{
          error_code: "RISK_MAX_DRAWDOWN_EXCEEDED",
          details: { reason_codes: ["RISK_MAX_DRAWDOWN_EXCEEDED"] },
        }}
      />,
    );

    expect(
      screen.getByText(
        "판정 결과를 확인할 수 없습니다. 안전을 위해 허용되지 않은 것으로 처리합니다.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("최대 손실 한도를 초과하여 거부되었습니다."),
    ).toBeInTheDocument();
    expect(screen.queryByText("ALLOW")).not.toBeInTheDocument();
  });
});

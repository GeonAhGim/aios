import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { MandateActionError } from "./MandateActionError";

afterEach(() => {
  cleanup();
});

// task-2336 문서 주석에 적힌 CM-5(작성자=승인자) 시나리오: VALIDATION_INVALID_FIELD인데
// details.fields가 비어 인라인이 뜰 자리가 없다 — BadRequestNotice가 서버 message를
// 배너로 그대로 보여줘야 한다(§3.3 규약, DoD(d): err.message 직접 렌더는 아니고
// routeApiError/classifyBadRequest 판정을 거친 결과다).
describe("MandateActionError — spec §3.3 에러 taxonomy 표면", () => {
  it("정상 렌더: 재시도 가능한 서버 오류는 taxonomy 매핑 문구와 재시도 대기시간을 보여준다", () => {
    const error = new ApiError(503, "raw upstream message", "trace-1", "DEPENDENCY_NOT_READY", 5);

    render(<MandateActionError error={error} onRetry={vi.fn()} />);

    expect(screen.getByText("서비스가 준비 중입니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument();
    expect(screen.queryByText("raw upstream message")).not.toBeInTheDocument();
    expect(screen.getByText("5초 후 재시도 가능")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeDisabled();
  });

  it("경계 입력: details.fields가 빈 VALIDATION_INVALID_FIELD(400)는 서버 message를 배너로 보여준다", () => {
    const error = new ApiError(400, "작성자와 승인자가 같을 수 없습니다.", "trace-2", "VALIDATION_INVALID_FIELD", undefined, {});

    render(<MandateActionError error={error} />);

    expect(screen.getByText("작성자와 승인자가 같을 수 없습니다.")).toBeInTheDocument();
  });

  it("거부 입력: 정책 위반(403 POLICY_LIVE_BLOCKED)은 taxonomy 문구와 거부 사유 목록을 보여주고 원문 message는 감춘다", () => {
    const error = new ApiError(403, "internal policy denial reason", "trace-3", "POLICY_LIVE_BLOCKED", undefined, {
      reason_codes: ["POLICY_LIVE_BLOCKED"],
    });

    render(<MandateActionError error={error} />);

    // taxonomy 문구는 배너(<p>)와 거부 사유 목록(<li>) 양쪽에 나타난다 — 우연히
    // 같은 한국어 문장으로 매핑됐을 뿐, 서로 다른 두 컴포넌트(ForbiddenNotice/
    // DenialReasons)가 각자 렌더한 결과다.
    const matches = screen.getAllByText("실거래 모드에서는 허용되지 않는 작업입니다.");
    expect(matches.length).toBe(2);
    expect(screen.getByRole("listitem")).toHaveTextContent("실거래 모드에서는 허용되지 않는 작업입니다.");
    expect(screen.queryByText("internal policy denial reason")).not.toBeInTheDocument();
  });
});

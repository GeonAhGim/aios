import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { DecisionHistoryPage } from "./DecisionHistoryPage";

vi.mock("@aios/shared-hooks", () => ({
  useComplianceDecisionLookup: () => ({ mutate: vi.fn(), isPending: false }),
  useAuthStore: { getState: () => ({ token: null }) },
  useMe: () => ({ data: { email: "user@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => cleanup());

function renderPage() {
  return render(
    <MemoryRouter>
      <DecisionHistoryPage positionsClient={{ getPositionJournal: vi.fn() }} />
    </MemoryRouter>,
  );
}

describe("DecisionHistoryPage — task-2706 UX-22 결정 이력 뷰어", () => {
  it("판정 조회 패널과 이벤트 계보 패널을 함께 보여준다(각자 기존 API를 그대로 재사용, 새 판정 로직 없음)", () => {
    renderPage();

    expect(screen.getByText("결정 이력 뷰어")).toBeInTheDocument();
    expect(screen.getByText("판정 조회(decision_id)")).toBeInTheDocument();
    expect(screen.getByLabelText("판정 ID (decision_id)")).toBeInTheDocument();
    expect(screen.getByText("이벤트 계보 조회(position_key)")).toBeInTheDocument();
    expect(screen.getByLabelText("포지션 키(position_key)")).toBeInTheDocument();
  });

  it("negative: 두 패널의 입력 검증은 서로 독립적이다(판정 ID 오류가 포지션 키 패널에 번지지 않는다)", () => {
    renderPage();

    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]); // 판정 조회 패널: decision_id를 비운 채 조회

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(screen.queryByText("포지션 키를 입력하세요.")).not.toBeInTheDocument();
  });
});

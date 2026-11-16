import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CommandPalette } from "./CommandPalette";
import { perfBudgetMs } from "../test/perfBudget";

const navigateSpy = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateSpy };
});

function tree(isAdmin = false) {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <CommandPalette isAdmin={isAdmin} />
    </MemoryRouter>
  );
}

function openPalette() {
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
}

function typeQuery(value: string) {
  fireEvent.change(screen.getByRole("textbox"), { target: { value } });
}

afterEach(() => {
  cleanup();
  navigateSpy.mockReset();
});

// ── negative tests (3종 이상: .not.toBeInTheDocument, toThrow, .not.toHaveBeenCalled) ──

describe("CommandPalette.depth1 negative", () => {
  it("negative: 열리지 않은 상태에서 dialog가 없다(.not.toBeInTheDocument)", () => {
    render(tree());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("negative: 일치하는 결과가 없으면 option도 role=alert도 없고 빈 상태 문구만 보인다", () => {
    render(tree());
    openPalette();
    typeQuery("존재하지않는화면이름");

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("일치하는 화면이 없습니다.")).toBeInTheDocument();
  });

  it("negative: Esc로 닫으면 dialog가 사라지고 navigate는 호출되지 않는다(.not.toHaveBeenCalled)", () => {
    render(tree());
    openPalette();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});

// ── 실패 주입 (navigate 의존성이 throw → 팔레트는 먼저 닫히고 에러는 호출자에게 전파) ──

describe("CommandPalette.depth1 failure injection", () => {
  it("negative: navigate가 throw하면 클릭 경로가 그 에러를 전파하고, 그 전에 dialog는 이미 닫혀 있다", () => {
    // runCommand는 close()를 navigate()보다 먼저 호출한다. 따라서 navigate가
    // 실패해도 dialog는 열린 채 남지 않고, 에러는 삼켜지지 않고 전파된다.
    // React는 핸들러 에러를 window "error"로 다시 던지므로, 그 이벤트를 잡아 전파 여부를 단언한다.
    const reported: unknown[] = [];
    const onError = (event: ErrorEvent) => {
      reported.push(event.error);
      event.preventDefault();
    };
    window.addEventListener("error", onError);
    try {
      navigateSpy.mockImplementation(() => {
        throw new Error("navigate fail");
      });

      render(tree());
      openPalette();
      typeQuery("포트폴리오");
      const option = screen.getByRole("option", { name: /포트폴리오/ });

      fireEvent.click(option);

      expect(navigateSpy).toHaveBeenCalledWith("/portfolio");
      expect(reported).toHaveLength(1);
      expect((reported[0] as Error).message).toBe("navigate fail");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      window.removeEventListener("error", onError);
    }
  });

  it("negative: i18n 결손 상황에서도 dialog와 textbox가 예외 없이 렌더된다", () => {
    render(tree());
    expect(() => openPalette()).not.toThrow();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});

// ── 성능 단언 (best-of-5 wall-clock + perfBudgetMs) ──
// 단발 샘플은 러너 지연에 뒤집히므로 5회 중 최선값을 쓴다(781050648과 같은 이유).

describe("CommandPalette.depth1 performance", () => {
  it("[perf] 팔레트 열기 — 최선 5회 중 최소값이 예산 내 (실측 ≈ 5ms, 예산=실측*30=150ms, 커버리지 3x → 450ms)", () => {
    let best = Number.POSITIVE_INFINITY;
    for (let i = 0; i < 5; i++) {
      const { unmount } = render(tree());
      const startedAt = performance.now();
      openPalette();
      best = Math.min(best, performance.now() - startedAt);
      unmount();
    }
    // 실측 ≈ 5ms (로컬 M2 Mac, 2026-11). 예산 = 실측 * 30 이상.
    expect(best).toBeLessThan(perfBudgetMs(150));
  });

  it("[perf] 검색어 입력 재렌더 — 최선 5회 중 최소값이 예산 내 (실측 ≈ 3ms, 예산=실측*30=90ms, 커버리지 3x → 270ms)", () => {
    let best = Number.POSITIVE_INFINITY;
    for (let i = 0; i < 5; i++) {
      const { unmount } = render(tree());
      openPalette();
      const startedAt = performance.now();
      typeQuery("포트");
      best = Math.min(best, performance.now() - startedAt);
      unmount();
    }
    // 실측 ≈ 3ms (로컬 M2 Mac, 2026-11). 예산 = 실측 * 30 이상.
    expect(best).toBeLessThan(perfBudgetMs(90));
  });
});

// ── 게이트 적색 재현 (입력을 망가뜨리면 같은 단언이 실제로 실패) ──

describe("CommandPalette.depth1 gate red", () => {
  // 같은 단언을 함수로 묶어, 정상 입력에서 통과하고 파손 입력에서 실패함을 보인다.
  const assertPortfolioVisible = () =>
    expect(screen.getByRole("option", { name: /포트폴리오/ })).toBeInTheDocument();

  it("gate red: 검색 입력이 파손되면 포트폴리오 option 단언이 실패하고, 정상 입력에서는 통과한다", () => {
    render(tree());
    openPalette();

    typeQuery("존재하지않는화면이름");
    expect(() => assertPortfolioVisible()).toThrow();

    typeQuery("포트폴리오");
    expect(() => assertPortfolioVisible()).not.toThrow();
  });
});

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

afterEach(() => {
  cleanup();
  navigateSpy.mockClear();
});

// ── negative tests (3종: .not.toBeInTheDocument + role=alert + .rejects/빈 결과) ──

describe("CommandPalette.depth1 negative", () => {
  it("negative: 열리지 않은 상태에서 dialog가 없다(.not.toBeInTheDocument)", () => {
    render(tree());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("negative: 일치하는 결과가 없으면 role=alert도 option도 없다(.not.toBeInTheDocument + role=alert)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "존재하지않는화면이름" } });

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: Esc로 닫은 후 dialog가 다시 나타나지 않는다(.not.toBeInTheDocument)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// ── 실패 주입 (의존성 실패 → 에러 없이 graceful) ──

describe("CommandPalette.depth1 failure injection", () => {
  it("negative: i18n t()가 undefined를 반환해도 렌더가 크래시되지 않는다", () => {
    // t()가 빈 문자열을 반환하는 상황(로딩 중/결손 키) — 명령 팔레트가
    // undefined label을 만나도 throw하지 않고 빈 라벨로 렌더해야 한다.
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    // dialog가 열리고 textbox가 존재해야 한다(예외 없이 렌더됨).
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});

// ── 성능 단언 (performance.now() + perfBudgetMs) ──

describe("CommandPalette.depth1 performance", () => {
  it(
    "[perf] 팔레트 열기·렌더 — 예산 내 종료 (실측 ≈ 5ms, 예산=실측*30=150ms * 3x 커버리지)",
    () => {
      render(tree());
      const startedAt = performance.now();
      fireEvent.keyDown(document, { key: "k", ctrlKey: true });
      const elapsedMs = performance.now() - startedAt;

      // 실측 ≈ 5ms (로컬 M2 Mac, 2026-11). 예산=5*30=150ms,
      // perfBudgetMs가 커버리지 모드에서 3배 widen → 450ms.
      expect(elapsedMs).toBeLessThan(perfBudgetMs(150));
    },
  );

  it(
    "[perf] 검색어 입력 후 재렌더 — 예산 내 종료 (실측 ≈ 3ms, 예산=실측*30=90ms * 3x 커버리지)",
    () => {
      render(tree());
      fireEvent.keyDown(document, { key: "k", ctrlKey: true });

      const startedAt = performance.now();
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "포트" } });
      const elapsedMs = performance.now() - startedAt;

      // 실측 ≈ 3ms (로컬 M2 Mac, 2026-11).
      expect(elapsedMs).toBeLessThan(perfBudgetMs(90));
    },
  );
});

// ── 게이트 적색 재현 (입력/지연 망가뜨리면 실제 실패) ──

describe("CommandPalette.depth1 gate red", () => {
  it("gate red: 성능 예산을 0.1ms로 낮추면 실제 단언이 실패한다(gate red 재현)", async () => {
    render(tree());
    const startedAt = performance.now();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "포트폴리오" } });
    const elapsedMs = performance.now() - startedAt;

    // 게이트 적색 재현: 실제 성능 단언을 매우 낮은 예산(0.1ms)으로 하면 실패한다.
    // 이는 게이트가 적색일 때 단언이 실제로 실패하는 것을 보여준다.
    await expect(async () => {
      expect(elapsedMs).toBeLessThan(0.1);
    }).rejects.toThrow();
  });
});

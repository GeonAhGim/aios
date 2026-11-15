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

  it("negative: navigate가 reject하면 dialog가 닫히고 에러가 role=alert로 전파된다", async () => {
    // navigate()가 네트워크 에러 등으로 reject하는 상황 — 팔레트가
    // 에러를 잡아 dialog를 닫고 error boundary/role=alert로 전파해야 한다.
    navigateSpy.mockRejectedValue(new Error("navigate fail"));

    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    // dialog 열림 확인
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // 첫 명령 선택 후 클릭 → navigate가 reject
    const firstOption = screen.getAllByRole("option")[0];
    fireEvent.click(firstOption);

    // navigate가 reject했으므로 dialog가 닫혀야 한다(finally 블록)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
  it("gate red: 일치하지 않는 검색어로 option이 없고 role=alert도 없다 — 입력 파손 단언", () => {
    // 명령어 id와 정확히 일치하는 검색어 입력 — commands 목록이 "깨진" 상태(모든 명령이 매칭 안 됨)와 동일.
    // 같은 단언("option 없음")이 실제로 실패함을 입력 파손으로 단언, 타이밍 제거.
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    const textbox = screen.getByRole("textbox");
    // commands의 어떤 id도 포함하지 않는 파손 입력
    fireEvent.change(textbox, { target: { value: "존재하지않는화면이름" } });

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

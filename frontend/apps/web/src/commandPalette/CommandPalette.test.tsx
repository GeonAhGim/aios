import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ADMIN_NAV_ITEMS, MAIN_NAV_ITEMS, SETTINGS_NAV_ITEMS } from "../components/layout/navItems";
import { CommandPalette } from "./CommandPalette";

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

describe("CommandPalette", () => {
  it("초기에는 트리거 버튼만 보이고 다이얼로그는 닫혀 있다", () => {
    render(tree());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Ctrl+K를 누르면 검색 다이얼로그가 열린다", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("트리거 버튼을 클릭해도 열린다(마우스 대체 경로)", () => {
    render(tree());
    fireEvent.click(screen.getByRole("button", { name: "명령 팔레트" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("'?'를 누르면 도움말(단축키 맵)이 열린다", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "?" });
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Ctrl/⌘ K")).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("검색으로 입력하면 일치하는 화면만 결과에 남는다", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "포트폴리오" } });

    expect(screen.getByRole("option", { name: /포트폴리오/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /대시보드/ })).not.toBeInTheDocument();
  });

  it("negative: 일치하는 화면이 없으면 빈 상태 문구를 보여준다(오류 아님)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "존재하지않는화면이름" } });

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("일치하는 화면이 없습니다.")).toBeInTheDocument();
  });

  it("Enter를 누르면 활성 항목으로 이동하고 다이얼로그가 닫힌다", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "포트폴리오" } });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });

    expect(navigateSpy).toHaveBeenCalledWith("/portfolio");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Esc를 누르면 검색어를 남긴 채 다이얼로그만 닫힌다", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it("negative: 관리자가 아니면 관리자 전용 화면은 검색 결과에 등장하지 않는다", () => {
    render(tree(false));
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "관리자" } });

    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("관리자면 관리자 전용 화면도 검색 결과에 등장한다", () => {
    render(tree(true));
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "관리자" } });

    expect(screen.getByRole("option", { name: /관리자/ })).toBeInTheDocument();
  });

  // DoD "키보드 전 경로": MAIN/SETTINGS_NAV_ITEMS 전부가 빈 쿼리 상태에서 팔레트
  // 결과 목록에 나타나야 한다 -- nav 항목이 늘어나는데 이 목록을 안 늘리면 이
  // 단언이 즉시 실패한다(navReachability.test.ts와 동일한 래칫 취지).
  it("빈 쿼리에서는 MAIN/SETTINGS 전체 nav 항목이 결과에 나타난다(전 경로 키보드 도달)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    const options = screen.getAllByRole("option").map((el) => el.textContent);
    for (const item of [...MAIN_NAV_ITEMS, ...SETTINGS_NAV_ITEMS]) {
      expect(options.some((text) => text?.includes(item.label))).toBe(true);
    }
  });

  it("관리자 세션에서는 ADMIN_NAV_ITEMS까지 포함해 결과에 나타난다", () => {
    render(tree(true));
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    const options = screen.getAllByRole("option").map((el) => el.textContent);
    for (const item of ADMIN_NAV_ITEMS) {
      expect(options.some((text) => text?.includes(item.label))).toBe(true);
    }
  });
});

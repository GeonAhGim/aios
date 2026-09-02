import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { derivePageState } from "../lib/pagination";
import { Pagination } from "./Pagination";

afterEach(() => cleanup());

describe("Pagination", () => {
  it("중간 페이지: 이전/다음 모두 활성화, 현재/총 페이지를 보여준다", () => {
    const state = derivePageState({
      page: 2,
      size: 20,
      total: 45,
      next_cursor: null,
    });
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "다음" })).not.toBeDisabled();
  });

  it("첫 페이지: 이전 버튼이 비활성화된다", () => {
    const state = derivePageState({
      page: 1,
      size: 20,
      total: 45,
      next_cursor: null,
    });
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음" })).not.toBeDisabled();
  });

  it("마지막 페이지: 다음 버튼이 비활성화된다", () => {
    const state = derivePageState({
      page: 3,
      size: 20,
      total: 45,
      next_cursor: null,
    });
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("총 페이지 0건(빈 상태): 아무것도 렌더링하지 않는다", () => {
    const state = derivePageState({
      page: 1,
      size: 20,
      total: 0,
      next_cursor: null,
    });
    const { container } = render(
      <Pagination state={state} onPageChange={() => {}} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("총 페이지 1건: 페이지네이션을 숨긴다", () => {
    const state = derivePageState({
      page: 1,
      size: 20,
      total: 5,
      next_cursor: null,
    });
    const { container } = render(
      <Pagination state={state} onPageChange={() => {}} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("negative: meta가 없는(page=undefined) 레거시 응답도 예외 없이 1페이지로 폴백해 렌더링한다", () => {
    expect(() => derivePageState(null)).not.toThrow();
    const state = derivePageState(null);
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(state.page).toBe(1);
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("다음 클릭 시 onPageChange(page+1)을 호출한다", () => {
    const state = derivePageState({
      page: 2,
      size: 20,
      total: 45,
      next_cursor: null,
    });
    const onPageChange = vi.fn();
    render(<Pagination state={state} onPageChange={onPageChange} />);

    screen.getByRole("button", { name: "다음" }).click();
    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { isResourceNotFound } from "@aios/shared-types";
import { afterEach, describe, expect, it } from "vitest";
import { NotFoundState } from "./NotFoundState";

afterEach(() => cleanup());

// spec §3.3 RESOURCE_NOT_FOUND(404)는 "재시도 아니오" — isResourceNotFound(err)가 판별하는
// 4케이스: errorCode 매칭, statusCode만 매칭, 비404, null/일반 Error.
describe("isResourceNotFound", () => {
  it("errorCode가 RESOURCE_NOT_FOUND이면 true", () => {
    expect(isResourceNotFound({ errorCode: "RESOURCE_NOT_FOUND", statusCode: 404 })).toBe(true);
  });

  it("errorCode 없이 statusCode만 404여도 true", () => {
    expect(isResourceNotFound({ statusCode: 404 })).toBe(true);
  });

  it("401/500 등 404가 아닌 에러는 false", () => {
    expect(isResourceNotFound({ errorCode: "AUTH_REQUIRED", statusCode: 401 })).toBe(false);
    expect(isResourceNotFound({ errorCode: "INTERNAL_ERROR", statusCode: 500 })).toBe(false);
  });

  it("null이거나 일반 Error면 false (throw하지 않는다)", () => {
    expect(isResourceNotFound(null)).toBe(false);
    expect(isResourceNotFound(new Error("boom"))).toBe(false);
  });
});

describe("NotFoundState", () => {
  it("제목과 설명을 보여준다", () => {
    render(<NotFoundState title="리스팅을 찾을 수 없습니다" description="삭제되었거나 존재하지 않는 항목입니다." />);

    expect(screen.getByText("리스팅을 찾을 수 없습니다")).toBeInTheDocument();
    expect(screen.getByText("삭제되었거나 존재하지 않는 항목입니다.")).toBeInTheDocument();
  });

  it("description이 없으면 생략한다", () => {
    render(<NotFoundState title="찾을 수 없습니다" />);

    expect(screen.getByText("찾을 수 없습니다")).toBeInTheDocument();
  });

  it("action이 없으면 렌더하지 않는다", () => {
    const { container } = render(<NotFoundState title="찾을 수 없습니다" />);

    expect(container.querySelectorAll("button, a").length).toBe(0);
  });

  it("title이 빈 문자열이어도(error 객체를 직접 받지 않으므로 자체 분류로 삼키지 않는다) 컨테이너는 렌더된다(무응답 아님, task-1231)", () => {
    const { container } = render(<NotFoundState title="" />);

    expect(container).not.toBeEmptyDOMElement();
    expect(container.querySelector("p")).toBeInTheDocument();
  });

  it("재시도 버튼을 두지 않는다 (§3.3 RESOURCE_NOT_FOUND는 재시도 아니오)", () => {
    render(<NotFoundState title="찾을 수 없습니다" action={<button type="button">뒤로가기</button>} />);

    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "뒤로가기" })).toBeInTheDocument();
  });
});

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AuthLayout } from "./AuthLayout";

afterEach(() => cleanup());

describe("AuthLayout", () => {
  it("title과 children을 렌더한다", () => {
    render(
      <AuthLayout title="로그인">
        <div>폼 영역</div>
      </AuthLayout>,
    );

    expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
    expect(screen.getByText("폼 영역")).toBeInTheDocument();
  });

  it("subtitle이 있으면 함께 보여준다", () => {
    render(
      <AuthLayout title="회원가입" subtitle="30초면 충분합니다">
        <div>폼</div>
      </AuthLayout>,
    );

    expect(screen.getByText("30초면 충분합니다")).toBeInTheDocument();
  });

  it("negative: subtitle이 없으면 렌더하지 않는다", () => {
    render(
      <AuthLayout title="로그인">
        <div>폼</div>
      </AuthLayout>,
    );

    expect(screen.queryByText(/충분합니다/)).not.toBeInTheDocument();
  });
});

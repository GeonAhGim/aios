import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LoginPage } from "./LoginPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useLogin: () => ({ mutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/wallet" element={<div>지갑 페이지</div>} />
        <Route path="/dashboard" element={<div>대시보드 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitLoginForm() {
  fireEvent.change(screen.getByLabelText("이메일"), { target: { value: "a@example.com" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw123456" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
}

// task-354: ProtectedRoute가 세션 만료·미로그인 시 남긴 ?next=<원경로>로
// 로그인 성공 후 복귀하는지 확인한다.
describe("LoginPage next 복귀", () => {
  it("?next=/wallet로 진입해 로그인에 성공하면 /wallet로 복귀한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-1" });
    renderAt("/login?next=%2Fwallet");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("지갑 페이지")).toBeInTheDocument());
  });

  it("next가 없으면 기본값 /dashboard로 이동한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-2" });
    renderAt("/login");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });

  // negative: 외부 사이트로 여는 open-redirect를 막는다 — "//evil.example"은
  // 프로토콜 상대 URL로 해석돼 origin이 바뀔 수 있으므로 신뢰하지 않는다.
  it("negative: next가 프로토콜 상대 URL(//)이면 기본값 /dashboard로 대체한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-3" });
    renderAt("/login?next=%2F%2Fevil.example");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });
});

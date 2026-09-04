import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BadRequestNotice } from "./BadRequestNotice";

afterEach(() => cleanup());

describe("BadRequestNotice", () => {
  it("VALIDATION_INVALID_FIELD(400) + details.fields 1건 이상: 폼 인라인이 담당하므로 배너를 렌더링하지 않는다", () => {
    const { container } = render(
      <BadRequestNotice
        error={{
          statusCode: 400,
          errorCode: "VALIDATION_INVALID_FIELD",
          error_code: "VALIDATION_INVALID_FIELD",
          details: { fields: ["body.email"] },
          message: "입력값을 확인해주세요.",
        }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("VALIDATION_INVALID_FIELD(400) + details.fields 비어있음 + message 있음: 서버 message를 배너로 보여준다(P0 무응답 회귀 방지)", () => {
    render(
      <BadRequestNotice
        error={{
          statusCode: 400,
          errorCode: "VALIDATION_INVALID_FIELD",
          error_code: "VALIDATION_INVALID_FIELD",
          details: { fields: [] },
          message: "잔고가 부족합니다.",
        }}
      />,
    );
    expect(screen.getByText("잔고가 부족합니다.")).toBeInTheDocument();
  });

  it("VALIDATION_INVALID_FIELD(400) + details.fields 비어있음 + message 없음: taxonomy 기본 문구로 폴백한다", () => {
    render(
      <BadRequestNotice
        error={{
          statusCode: 400,
          errorCode: "VALIDATION_INVALID_FIELD",
          error_code: "VALIDATION_INVALID_FIELD",
          details: {},
        }}
      />,
    );
    expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument();
  });

  it("VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400): 안내만 보여주고 액션 버튼은 없다", () => {
    render(
      <BadRequestNotice
        error={{
          statusCode: 400,
          errorCode: "VALIDATION_IDEMPOTENCY_KEY_REQUIRED",
        }}
      />,
    );

    expect(
      screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("VALIDATION_DISCLOSURE_RETIRED(400): 최신 재조회 버튼을 보여주고 onReload를 호출한다", () => {
    const onReload = vi.fn();
    render(
      <BadRequestNotice
        error={{ statusCode: 400, errorCode: "VALIDATION_DISCLOSURE_RETIRED" }}
        onReload={onReload}
      />,
    );

    expect(screen.getByText("내용이 갱신되었습니다. 최신 내용을 다시 불러와주세요.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "최신 내용 다시 불러오기" });
    fireEvent.click(button);
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("AUTH_MFA_INVALID(400): 새 코드 입력 버튼을 보여주고 onFocusMfaCode를 호출한다", () => {
    const onFocusMfaCode = vi.fn();
    render(
      <BadRequestNotice
        error={{ statusCode: 400, errorCode: "AUTH_MFA_INVALID" }}
        onFocusMfaCode={onFocusMfaCode}
      />,
    );

    expect(screen.getByText("인증 코드가 올바르지 않습니다. 다시 시도해주세요.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "새 코드 입력" });
    fireEvent.click(button);
    expect(onFocusMfaCode).toHaveBeenCalledTimes(1);
  });

  it("미지 400 코드: unknown으로 폴백해 기본 안내를 보여준다(throw 없음, 액션 버튼 없음)", () => {
    render(<BadRequestNotice error={{ statusCode: 400, errorCode: "SOME_FUTURE_CODE" }} />);

    expect(
      screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("disclosure_retired여도 onReload가 없으면 버튼을 보여주지 않는다", () => {
    render(<BadRequestNotice error={{ statusCode: 400, errorCode: "VALIDATION_DISCLOSURE_RETIRED" }} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("negative: 401/403 등 400이 아니면 아무것도 렌더링하지 않는다", () => {
    const { container: unauthorized } = render(
      <BadRequestNotice error={{ statusCode: 401, errorCode: "AUTH_REQUIRED" }} />,
    );
    expect(unauthorized).toBeEmptyDOMElement();

    cleanup();

    const { container: forbidden } = render(
      <BadRequestNotice error={{ statusCode: 403, errorCode: "AUTHZ_FORBIDDEN" }} />,
    );
    expect(forbidden).toBeEmptyDOMElement();
  });
});

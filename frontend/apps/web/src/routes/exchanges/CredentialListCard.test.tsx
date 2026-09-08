import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type { CredentialResponse } from "@aios/shared-types";
import { ApiError } from "@aios/api-client";
import { CredentialListCard } from "./CredentialListCard";
import type { CredentialWithSecretRef } from "./credentialScope";

afterEach(() => cleanup());

type CredentialFixture = CredentialResponse & CredentialWithSecretRef;

function credential(overrides: Partial<CredentialFixture> = {}): CredentialFixture {
  return {
    id: 1,
    exchange: "bitget",
    isActive: true,
    linkedAt: "2026-01-01T00:00:00Z",
    withdrawalPermissionWarning: null,
    ...overrides,
  } as CredentialFixture;
}

type Props = ComponentProps<typeof CredentialListCard>;

function baseProps(overrides: Partial<Props> = {}): Props {
  return {
    credentials: [],
    isLoading: false,
    revokingExchange: null,
    selectedExchange: null,
    balances: undefined,
    revokeError: null,
    onSelectExchange: vi.fn(),
    onRevoke: vi.fn(),
    onRetryRevoke: vi.fn(),
    ...overrides,
  };
}

describe("CredentialListCard 목록 상태", () => {
  it("isLoading이면 로딩 상태를 보여준다", () => {
    render(<CredentialListCard {...baseProps({ isLoading: true })} />);

    expect(screen.queryByText("연동된 거래소가 없습니다.")).not.toBeInTheDocument();
  });

  it("credentials가 비어 있으면 빈 상태 문구를 보여준다", () => {
    render(<CredentialListCard {...baseProps()} />);

    expect(screen.getByText("연동된 거래소가 없습니다.")).toBeInTheDocument();
  });

  it("credentials가 있으면 각 항목의 거래소명·활성 배지·연동일을 보여준다", () => {
    render(
      <CredentialListCard
        {...baseProps({ credentials: [credential()] })}
      />,
    );

    expect(screen.getByText("활성")).toBeInTheDocument();
    expect(screen.getByText(/연동일/)).toBeInTheDocument();
  });

  it("withdrawalPermissionWarning이 있으면 경고 문구를 보여준다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential({ withdrawalPermissionWarning: "출금 권한이 활성화되어 있습니다." })],
        })}
      />,
    );

    expect(screen.getByText(/출금 권한이 활성화되어 있습니다\./)).toBeInTheDocument();
  });
});

describe("CredentialListCard scope 배지·해지 버튼", () => {
  it("secretRef가 secref://live/...면 LIVE 배지와 차단 안내를 보여주고 해지 버튼을 비활성화한다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential({ secretRef: "secref://live/exchange_credential/1@v1" })],
        })}
      />,
    );

    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "해지" })).toBeDisabled();
  });

  it("revokingExchange가 해당 행과 일치하면 해지 버튼을 비활성화한다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential({ exchange: "bitget" })],
          revokingExchange: "bitget",
        })}
      />,
    );

    expect(screen.getByRole("button", { name: "해지" })).toBeDisabled();
  });

  it("잔고 조회 클릭 시 onSelectExchange(exchange)를 호출한다", () => {
    const onSelectExchange = vi.fn();
    render(
      <CredentialListCard
        {...baseProps({ credentials: [credential()], onSelectExchange })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "잔고 조회" }));

    expect(onSelectExchange).toHaveBeenCalledWith("bitget");
  });

  it("해지 클릭 시 onRevoke(exchange)를 호출한다", () => {
    const onRevoke = vi.fn();
    render(
      <CredentialListCard {...baseProps({ credentials: [credential()], onRevoke })} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "해지" }));

    expect(onRevoke).toHaveBeenCalledWith("bitget");
  });
});

describe("CredentialListCard 잔고 표시", () => {
  it("selectedExchange·balances가 주어지면 자산별 사용가능/총액을 보여준다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential()],
          selectedExchange: "bitget",
          balances: [{ exchange: "bitget", asset: "USDT", available: "100", total: "120" }],
        })}
      />,
    );

    expect(screen.getByText("USDT: 100 / 120")).toBeInTheDocument();
  });

  it("balances가 빈 배열이면 잔고 정보 없음 문구를 보여준다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential()],
          selectedExchange: "bitget",
          balances: [],
        })}
      />,
    );

    expect(screen.getByText("잔고 정보가 없습니다.")).toBeInTheDocument();
  });

  it("selectedExchange가 없으면 잔고 영역을 렌더링하지 않는다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential()],
          balances: [{ exchange: "bitget", asset: "USDT", available: "100", total: "120" }],
        })}
      />,
    );

    expect(screen.queryByText(/잔고$/)).not.toBeInTheDocument();
  });
});

// task-901 패턴: revokeError가 있으면 RevokeCredentialError(§3.3 분류기 경유)를 보여주고
// 서버 원문을 직접 노출하지 않는다.
describe("CredentialListCard 해지 에러", () => {
  it("revokeError가 있으면 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", () => {
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential()],
          revokeError: {
            exchange: "bitget",
            error: new ApiError(500, "raw server detail", "trace-1", "INTERNAL_ERROR"),
          },
        })}
      />,
    );

    expect(screen.getByText("지원코드: trace-1")).toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: revokeError가 null이면 에러 영역을 렌더링하지 않는다", () => {
    render(<CredentialListCard {...baseProps({ credentials: [credential()] })} />);

    expect(screen.queryByText(/지원코드/)).not.toBeInTheDocument();
  });

  it("다시 시도 클릭 시 onRetryRevoke를 호출한다", () => {
    const onRetryRevoke = vi.fn();
    render(
      <CredentialListCard
        {...baseProps({
          credentials: [credential()],
          revokeError: {
            exchange: "bitget",
            error: new ApiError(503, "raw", undefined, "EXCHANGE_UNAVAILABLE"),
          },
          onRetryRevoke,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect(onRetryRevoke).toHaveBeenCalled();
  });
});

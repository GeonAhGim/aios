import "@testing-library/jest-dom/vitest";
import { ApiError } from "@aios/api-client";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRetryableAction } from "../hooks/useRetryableAction";
import { RateLimitNotice } from "./RateLimitNotice";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// useRateLimitNotice는 useRetryableAction의 전역 store를 구독하므로, RateLimitNotice
// 단독으로는 상태를 만들 수 없다 — 실제 429 backoff를 겪는 run()과 함께 마운트해
// task-841이 노린 "전역 배선"(어느 화면의 run()이든 같은 배너를 띄운다)을 검증한다.
function Harness({ onSettled }: { onSettled: (calls: number) => void }) {
  const { run } = useRetryableAction<string>();
  const calls = useRef(0);

  return (
    <>
      <RateLimitNotice />
      <button
        onClick={() => {
          void run(async () => {
            calls.current += 1;
            if (calls.current === 1) {
              throw new ApiError(429, "과도한 요청", undefined, "RATE_LIMIT_EXCEEDED", 5);
            }
            onSettled(calls.current);
            return "ok";
          });
        }}
      >
        실행
      </button>
    </>
  );
}

// 항상 429로 실패해 MAX_BACKOFF_RETRIES(3회) 상한을 그대로 소진시킨다 — Harness는
// 2번째 호출에서 성공하는 해피패스만 검증하므로, 상한 소진 후 배너가 사라지고
// run()이 최종 실패를 던지는 경로(useRetryableAction.ts의 clearOwnNotice가 성공
// 경로뿐 아니라 최종 throw 직전에도 불리는지)는 이 harness로 따로 검증한다.
function AlwaysFailingHarness({ onRejected }: { onRejected: (err: unknown) => void }) {
  const { run } = useRetryableAction<string>();

  return (
    <>
      <RateLimitNotice />
      <button
        onClick={() => {
          run(async () => {
            throw new ApiError(429, "과도한 요청", undefined, "RATE_LIMIT_EXCEEDED", 1);
          }).catch(onRejected);
        }}
      >
        실행
      </button>
    </>
  );
}

describe("RateLimitNotice", () => {
  it("전역 429 알림이 없으면 아무것도 렌더링하지 않는다", () => {
    const { container } = render(<RateLimitNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it("429 backoff 동안 카운트다운을 보여주고 시간이 지나면 배너가 사라지며 재시도가 성공한다", async () => {
    vi.useFakeTimers();
    const onSettled = vi.fn();
    render(<Harness onSettled={onSettled} />);

    fireEvent.click(screen.getByRole("button", { name: "실행" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument();
    expect(screen.getByText("5초 후 자동으로 다시 시도합니다.")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText("4초 후 자동으로 다시 시도합니다.")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(onSettled).toHaveBeenCalledWith(2);
    expect(screen.queryByText(/자동으로 다시 시도합니다/)).not.toBeInTheDocument();
  });

  // 실제(가짜 아닌) 타이머로 검증한다 — retryNow는 실제 5초 백오프 setTimeout이
  // 아직 안 끝났어도 즉시 이어지는지가 핵심이라, fake timer로 두 타이머(표시용
  // 카운트다운 vs 실제 backoff)의 동시 만료 순서에 기대는 것을 피한다.
  it("'지금 다시 시도'를 누르면 남은 대기를 기다리지 않고 즉시 재시도한다(비활성 탭 타이머 스로틀링 대비)", async () => {
    const onSettled = vi.fn();
    render(<Harness onSettled={onSettled} />);

    fireEvent.click(screen.getByRole("button", { name: "실행" }));
    const retryNowButton = await screen.findByRole("button", { name: "지금 다시 시도" });
    expect(screen.getByText("5초 후 자동으로 다시 시도합니다.")).toBeInTheDocument();

    fireEvent.click(retryNowButton);

    await waitFor(() => expect(onSettled).toHaveBeenCalledWith(2));
    expect(screen.queryByText(/자동으로 다시 시도합니다/)).not.toBeInTheDocument();
  });

  it("담당 에러(429)인데 서버 message/details가 비어 있어도 고정 안내 문구를 그대로 보여준다(무응답 아님, task-1231)", async () => {
    vi.useFakeTimers();
    const onSettled = vi.fn();

    function EmptyPayloadHarness() {
      const { run } = useRetryableAction<string>();
      const calls = useRef(0);

      return (
        <>
          <RateLimitNotice />
          <button
            onClick={() => {
              void run(async () => {
                calls.current += 1;
                if (calls.current === 1) {
                  throw new ApiError(429, "", undefined, "RATE_LIMIT_EXCEEDED", 3);
                }
                onSettled(calls.current);
                return "ok";
              });
            }}
          >
            실행
          </button>
        </>
      );
    }

    render(<EmptyPayloadHarness />);
    fireEvent.click(screen.getByRole("button", { name: "실행" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument();
  });

  it("429가 백오프 상한(3회)까지 반복되면 배너가 사라지고 재시도가 실패로 끝난다", async () => {
    vi.useFakeTimers();
    const onRejected = vi.fn();
    render(<AlwaysFailingHarness onRejected={onRejected} />);

    fireEvent.click(screen.getByRole("button", { name: "실행" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("1초 후 자동으로 다시 시도합니다.")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 * 3);
    });

    expect(onRejected).toHaveBeenCalledWith(expect.objectContaining({ errorCode: "RATE_LIMIT_EXCEEDED" }));
    expect(screen.queryByText(/자동으로 다시 시도합니다/)).not.toBeInTheDocument();
  });
});

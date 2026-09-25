import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EventLineageLookupPanel } from "./EventLineageLookupPanel";

vi.mock("@aios/shared-hooks", () => ({
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(() => cleanup());

function renderPanel(getPositionJournal = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <EventLineageLookupPanel client={{ getPositionJournal }} />
    </QueryClientProvider>,
  );
  return { getPositionJournal };
}

function lookup(positionKey: string) {
  fireEvent.change(screen.getByLabelText("포지션 키(position_key)"), { target: { value: positionKey } });
  fireEvent.click(screen.getByRole("button", { name: "조회" }));
}

function journalPage(positionKey: string, items: unknown[] = [], nextCursor: string | null = null) {
  return { positionKey, items, nextCursor, asOf: "2026-09-05T12:00:00Z" };
}

describe("EventLineageLookupPanel — task-2706 UX-22 이벤트 계보(포지션 저널) 진입점", () => {
  it("negative: 포지션 키를 비운 채 조회하면 요청하지 않고 검증 메시지만 보여준다", () => {
    const { getPositionJournal } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    expect(screen.getByText("포지션 키를 입력하세요.")).toBeInTheDocument();
    expect(getPositionJournal).not.toHaveBeenCalled();
  });

  it("negative: 공백만 입력해도 trim되어 빈 값과 동일하게 거부된다", () => {
    const { getPositionJournal } = renderPanel();
    lookup("   ");

    expect(screen.getByText("포지션 키를 입력하세요.")).toBeInTheDocument();
    expect(getPositionJournal).not.toHaveBeenCalled();
  });

  it("negative: 유효성 오류 표시 중 새 키를 입력해 다시 조회하면 이전 오류 문구가 사라진다", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(screen.getByText("포지션 키를 입력하세요.")).toBeInTheDocument();

    lookup("pos-1");
    expect(screen.queryByText("포지션 키를 입력하세요.")).not.toBeInTheDocument();
  });

  it("positive: 유효한 키를 조회하면 저널 패널 진입점이 나타나고, 열면 해당 키로 요청을 보낸다", async () => {
    const getPositionJournal = vi.fn().mockResolvedValue(journalPage("pos-1"));
    renderPanel(getPositionJournal);
    lookup("pos-1");

    const openButton = await screen.findByTestId("position-journal-pos-1-open");
    fireEvent.click(openButton);

    await waitFor(() =>
      expect(getPositionJournal).toHaveBeenCalledWith({ positionKey: "pos-1", cursor: undefined, limit: 20 }),
    );
  });

  it("실패 주입(state 유출 회귀): 포지션 키를 바꿔 다시 조회해도 이전 키의 열림·저널 상태가 새 키로 새어 나가지 않는다", async () => {
    // PositionJournalPanel은 open/committed를 내부 state로 갖는다(props로 리셋되지
    // 않는다 — 컴포넌트 자체 코드·주석 참고). EventLineageLookupPanel이
    // key={submitted} 없이 positionKey prop만 바꿔 넘겼다면(실제로 발생 가능한
    // 실수), React는 같은 컴포넌트 인스턴스를 재사용해 포지션을 바꿔도 이전 키에서
    // 이미 펼쳐진 저널이 그대로 남는 회귀가 난다. 이 테스트는 그 회귀 시나리오를
    // 그대로 실행해 실제 소스의 key={submitted}가 이를 막고 있음을 증명한다.
    const getPositionJournal = vi.fn().mockImplementation(({ positionKey }: { positionKey: string }) =>
      Promise.resolve(journalPage(positionKey, [{ sequence_no: 1, entry_type: "FILL" }])),
    );

    renderPanel(getPositionJournal);
    lookup("pos-a");
    fireEvent.click(await screen.findByTestId("position-journal-pos-a-open"));
    await screen.findAllByText("FILL");

    lookup("pos-b");
    // 고쳐진 컴포넌트: pos-a의 열린 패널은 사라지고 pos-b는 다시 접힌 상태로 시작한다
    // (state가 새 키로 새어 나가지 않았다는 뜻 — 회귀였다면 pos-b도 이미 열려 있었을 것).
    expect(screen.queryByTestId("position-journal-pos-a")).not.toBeInTheDocument();
    expect(screen.getByTestId("position-journal-pos-b-open")).toBeInTheDocument();
    expect(screen.queryByTestId("position-journal-pos-b")).not.toBeInTheDocument();
  });

  it("negative: 저널이 없는 포지션 키(404)는 결과 대신 실패 표면을 보여주고 조용히 성공한 것처럼 굴지 않는다", async () => {
    const { ApiError } = await import("@aios/api-client");
    const getPositionJournal = vi.fn().mockRejectedValue(new ApiError(404, "raw", "trace-1", "RESOURCE_NOT_FOUND"));
    renderPanel(getPositionJournal);
    lookup("missing-pos");

    fireEvent.click(await screen.findByTestId("position-journal-missing-pos-open"));

    expect(await screen.findByText("저널이 없습니다.")).toBeInTheDocument();
  });
});

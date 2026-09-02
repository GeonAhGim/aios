// task-414: STATE_CONCURRENCY_CONFLICT(409)는 재시도 열이 "refetch", STATE_INVALID_TRANSITION(409)은
// "재시도 아니오"(§3.3). 두 코드 모두 HTTP status는 409로 같으므로 분기 기준은 status가 아니라
// errorCode임을 고정한다. 분류 로직 자체는 task-365(classifyRetry)가 이미 구현했으므로 여기서는
// 재구현하지 않고 이 특정 쌍의 회귀만 못박는다.
import { describe, expect, it } from "vitest";
import { classifyRetry } from "./retryable";

describe("classifyRetry — STATE_* 409 분기(task-414)", () => {
  it("STATE_CONCURRENCY_CONFLICT(409)는 refetch로 분류한다", () => {
    expect(classifyRetry({ errorCode: "STATE_CONCURRENCY_CONFLICT" })).toEqual({
      kind: "refetch",
    });
  });

  it("STATE_INVALID_TRANSITION(409)은 재시도하지 않는다(none)", () => {
    expect(classifyRetry({ errorCode: "STATE_INVALID_TRANSITION" })).toEqual({
      kind: "none",
    });
  });
});

import { describe, expect, it } from "vitest";
import { isValidRequestId, newRequestId, requestIdHeaders } from "./requestId";

describe("newRequestId", () => {
  it("26자 ULID를 만든다", () => {
    expect(newRequestId(1_700_000_000_000)).toHaveLength(26);
  });

  it("생성된 값은 isValidRequestId를 통과한다", () => {
    expect(isValidRequestId(newRequestId(1_700_000_000_000))).toBe(true);
  });

  it("같은 ms의 두 호출은 앞 10자(시간부)가 같다", () => {
    const a = newRequestId(1_700_000_000_000);
    const b = newRequestId(1_700_000_000_000);
    expect(a.slice(0, 10)).toBe(b.slice(0, 10));
  });

  it("사전순 정렬이 시간순 정렬과 같다(더 늦은 시간 → 더 큰 문자열)", () => {
    const earlier = newRequestId(1_700_000_000_000);
    const later = newRequestId(1_700_000_000_001);
    expect(earlier < later).toBe(true);
  });

  it("같은 ms라도 난수부(뒤 16자)는 매번 달라 충돌 확률이 낮다", () => {
    const a = newRequestId(1_700_000_000_000);
    const b = newRequestId(1_700_000_000_000);
    expect(a.slice(10)).not.toBe(b.slice(10));
  });
});

describe("isValidRequestId", () => {
  it("정상 ULID(26자, 허용 문자)는 통과한다", () => {
    expect(isValidRequestId("01ARZ3NDEKTSV4RRFFQ69G5FAV")).toBe(true);
  });

  it("25자는 거부한다", () => {
    expect(isValidRequestId("01ARZ3NDEKTSV4RRFFQ69G5FA")).toBe(false);
  });

  it("27자는 거부한다", () => {
    expect(isValidRequestId("01ARZ3NDEKTSV4RRFFQ69G5FAVX")).toBe(false);
  });

  it("Crockford에서 제외된 문자(I/L/O/U)를 포함하면 거부한다", () => {
    expect(isValidRequestId("0IARZ3NDEKTSV4RRFFQ69G5FAV")).toBe(false);
    expect(isValidRequestId("0LARZ3NDEKTSV4RRFFQ69G5FAV")).toBe(false);
    expect(isValidRequestId("0OARZ3NDEKTSV4RRFFQ69G5FAV")).toBe(false);
    expect(isValidRequestId("0UARZ3NDEKTSV4RRFFQ69G5FAV")).toBe(false);
  });

  it("소문자는 거부한다(정규화하지 않는다)", () => {
    expect(isValidRequestId("01arz3ndektsv4rrffq69g5fav")).toBe(false);
  });

  it("128자를 초과하면 거부한다", () => {
    expect(isValidRequestId("A".repeat(129))).toBe(false);
  });

  it("빈 문자열은 거부한다", () => {
    expect(isValidRequestId("")).toBe(false);
  });
});

describe("requestIdHeaders", () => {
  it("유효한 id를 주면 그대로 싣는다", () => {
    const id = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
    expect(requestIdHeaders(id)).toEqual({ "X-Request-Id": id });
  });

  it("id 없이 호출하면 새로 생성해 싣는다", () => {
    const headers = requestIdHeaders();
    expect(isValidRequestId(headers["X-Request-Id"])).toBe(true);
  });

  it("무효한 id(짧은 문자열)는 버리고 새로 생성한다 — 서버의 폐기·재생성 계약과 대칭", () => {
    const headers = requestIdHeaders("not-a-ulid");
    expect(headers["X-Request-Id"]).not.toBe("not-a-ulid");
    expect(isValidRequestId(headers["X-Request-Id"])).toBe(true);
  });

  it("무효한 id(금지 문자 포함)도 버리고 새로 생성한다", () => {
    const invalid = "0IARZ3NDEKTSV4RRFFQ69G5FAV";
    const headers = requestIdHeaders(invalid);
    expect(headers["X-Request-Id"]).not.toBe(invalid);
    expect(isValidRequestId(headers["X-Request-Id"])).toBe(true);
  });
});

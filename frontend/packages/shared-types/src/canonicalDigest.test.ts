import { describe, expect, it } from "vitest";
import { canonicalJson, sha256Hex } from "./canonicalDigest";

describe("canonicalJson", () => {
  it("키 순서만 다른 두 객체는 동일한 문자열을 만든다", () => {
    const a = { b: 2, a: 1 };
    const b = { a: 1, b: 2 };
    expect(canonicalJson(a)).toBe(canonicalJson(b));
  });

  it("중첩 객체도 재귀적으로 키를 정렬한다", () => {
    const nested = { outer: { z: 1, a: { y: 2, x: 3 } } };
    expect(canonicalJson(nested)).toBe('{"outer":{"a":{"x":3,"y":2},"z":1}}');
  });

  it("공백 없이 압축 직렬화한다", () => {
    expect(canonicalJson({ a: 1, b: [1, 2] })).toBe('{"a":1,"b":[1,2]}');
    expect(canonicalJson({ a: 1, b: [1, 2] })).not.toMatch(/\s/);
  });

  it("undefined 프로퍼티는 제거한다", () => {
    expect(canonicalJson({ a: 1, b: undefined })).toBe('{"a":1}');
  });

  it("배열 순서는 보존한다(정렬하지 않는다)", () => {
    expect(canonicalJson({ a: [3, 1, 2] })).toBe('{"a":[3,1,2]}');
  });

  it("number는 문자열화하지 않는다", () => {
    expect(canonicalJson({ n: 100 })).toBe('{"n":100}');
  });

  it("이미 문자열인 금액 필드는 그대로 유지한다", () => {
    expect(canonicalJson({ amount: "100.50" })).toBe('{"amount":"100.50"}');
  });

  it("undefined 최상위 값은 null이 된다", () => {
    expect(canonicalJson(undefined)).toBe("null");
  });
});

describe("sha256Hex", () => {
  it("64자 소문자 hex 문자열을 반환한다", async () => {
    const hex = await sha256Hex("hello");
    expect(hex).toMatch(/^[0-9a-f]{64}$/);
  });

  it("같은 입력은 같은 hex를 반환한다", async () => {
    const a = await sha256Hex("same-input");
    const b = await sha256Hex("same-input");
    expect(a).toBe(b);
  });

  it("다른 입력은 다른 hex를 반환한다", async () => {
    const a = await sha256Hex("input-a");
    const b = await sha256Hex("input-b");
    expect(a).not.toBe(b);
  });
});

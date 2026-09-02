import { describe, expect, it } from "vitest";
import { formatSecretRef, parseSecretRef, redactSecret, type SecretRef } from "./secretRef";

describe("parseSecretRef / formatSecretRef", () => {
  it("유효한 SecretRef 문자열을 파싱하고 다시 같은 문자열로 되돌린다(왕복)", () => {
    const raw = "secref://paper/exchange_credential/42@kid-1";
    const ref = parseSecretRef(raw);
    expect(ref).toEqual({
      scope: "paper",
      kind: "exchange_credential",
      id: "42",
      kid: "kid-1",
    });
    expect(formatSecretRef(ref as SecretRef)).toBe(raw);
  });

  it("scope=live, kind=mfa_secret도 파싱한다", () => {
    const raw = "secref://live/mfa_secret/uuid-abc@kid-2";
    expect(parseSecretRef(raw)).toEqual({
      scope: "live",
      kind: "mfa_secret",
      id: "uuid-abc",
      kid: "kid-2",
    });
  });

  it("kind=withdrawal_dest도 파싱한다", () => {
    const raw = "secref://live/withdrawal_dest/7@kid-3";
    expect(parseSecretRef(raw)).toEqual({
      scope: "live",
      kind: "withdrawal_dest",
      id: "7",
      kid: "kid-3",
    });
  });

  it("negative: scheme이 다르면 null", () => {
    expect(parseSecretRef("http://paper/exchange_credential/42@kid-1")).toBeNull();
  });

  it("negative: scope이 paper/live가 아니면 null", () => {
    expect(parseSecretRef("secref://staging/exchange_credential/42@kid-1")).toBeNull();
  });

  it("negative: kind가 허용된 3종이 아니면 null", () => {
    expect(parseSecretRef("secref://paper/api_key/42@kid-1")).toBeNull();
  });

  it("negative: @kid가 없으면 null", () => {
    expect(parseSecretRef("secref://paper/exchange_credential/42")).toBeNull();
  });

  it("negative: 완전히 형식이 다른 문자열은 null", () => {
    expect(parseSecretRef("not-a-secret-ref")).toBeNull();
    expect(parseSecretRef("")).toBeNull();
  });

  it("negative: id 또는 kid가 빈 문자열이면 null", () => {
    expect(parseSecretRef("secref://paper/exchange_credential/@kid-1")).toBeNull();
    expect(parseSecretRef("secref://paper/exchange_credential/42@")).toBeNull();
  });
});

describe("redactSecret", () => {
  it("이미 유효한 SecretRef 문자열이면 그대로 통과시킨다", () => {
    const raw = "secref://paper/exchange_credential/42@kid-1";
    expect(redactSecret(raw)).toBe(raw);
  });

  it("api_secret=value 형태로 반향된 값을 마스킹한다", () => {
    const message = 'invalid field api_secret="abcdef0123456789ABCDEF" for bitget';
    const redacted = redactSecret(message);
    expect(redacted).not.toContain("abcdef0123456789ABCDEF");
    expect(redacted).toContain("api_secret=[REDACTED]");
  });

  it("api_passphrase: value 형태(콜론)도 마스킹한다", () => {
    const message = "api_passphrase: myBitgetPassphrase12345 is invalid";
    const redacted = redactSecret(message);
    expect(redacted).not.toContain("myBitgetPassphrase12345");
  });

  it("필드명 없이 그대로 반향된 평문 키 후보도 마스킹한다", () => {
    const message = "credential rejected: abcdefghijklmnopqrstuvwxyz0123456789";
    const redacted = redactSecret(message);
    expect(redacted).not.toContain("abcdefghijklmnopqrstuvwxyz0123456789");
  });

  it("비밀로 보이지 않는 일반 메시지는 손대지 않는다", () => {
    expect(redactSecret("등록에 실패했습니다.")).toBe("등록에 실패했습니다.");
  });

  it("빈 문자열은 그대로 반환한다", () => {
    expect(redactSecret("")).toBe("");
  });
});

import { describe, expect, it } from "vitest";
import { formatSecretRef } from "@aios/shared-types";
import { LIVE_BLOCKED_NOTICE, credentialScope } from "./credentialScope";

describe("credentialScope", () => {
  it("scope=live인 SecretRef는 LIVE/danger로 표시한다", () => {
    const ref = formatSecretRef({ scope: "live", kind: "exchange_credential", id: "acc-1", kid: "k1" });

    expect(credentialScope(ref)).toEqual({ label: "LIVE", tone: "danger", isLive: true });
  });

  it("scope=paper인 SecretRef는 PAPER/success로 표시한다", () => {
    const ref = formatSecretRef({ scope: "paper", kind: "exchange_credential", id: "acc-2", kid: "k2" });

    expect(credentialScope(ref)).toEqual({ label: "PAPER", tone: "success", isLive: false });
  });

  it("negative: secretRef가 undefined면 scope를 추측하지 않고 '알 수 없음'으로 둔다", () => {
    expect(credentialScope(undefined)).toEqual({ label: "알 수 없음", tone: "neutral", isLive: false });
  });

  it("negative: 형식이 깨진 secretRef는 파싱 실패로 '알 수 없음'으로 수렴한다(예외 없음)", () => {
    expect(credentialScope("not-a-secret-ref")).toEqual({ label: "알 수 없음", tone: "neutral", isLive: false });
  });

  it("LIVE_BLOCKED_NOTICE는 POLICY_LIVE_BLOCKED의 매핑 문구다", () => {
    expect(LIVE_BLOCKED_NOTICE).toBe("실거래 모드에서는 허용되지 않는 작업입니다.");
  });
});

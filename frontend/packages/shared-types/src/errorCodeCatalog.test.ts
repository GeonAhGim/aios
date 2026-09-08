// task-2194 — src/api/contracts/error_codes.py(서버 ErrorCode 단일 카탈로그)와
// apiError.ts의 ApiErrorCode 유니온·EXACT_MESSAGES가 어긋나는 것을 막는 가드.
// task-1583(백엔드 taxonomy 세분화)→1163(프론트 후속)처럼 서버가 코드를 늘릴 때마다
// 프론트 매핑이 뒤늦게 따라가는 결함이 반복됐다 — 이 테스트는 서버 파일 원문을 직접
// 읽어 대조하므로 프론트가 손으로 미러링한 목록이 아니라 실제 카탈로그를 진실로
// 삼는다(contractDrift.test.ts/contractCoverage.test.ts와 동일 관용).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { EXACT_MESSAGES, getApiErrorMessage } from "./apiError";

// frontend/packages/shared-types/src -> repo root
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../..");
const ERROR_CODES_FILE = "src/api/contracts/error_codes.py";

function readServerSource(): string {
  return readFileSync(join(REPO_ROOT, ERROR_CODES_FILE), "utf-8");
}

// `    NAME = "NAME"` 형태의 enum 멤버 선언만 잡는다. HTTP_STATUS/RETRYABLE 딕셔너리는
// `ErrorCode.NAME: status...` 형태라 이 패턴에 걸리지 않는다.
export function extractServerErrorCodes(source: string): string[] {
  const re = /^ {4}([A-Z][A-Z0-9_]*) = "([A-Z][A-Z0-9_]*)"$/gm;
  const codes: string[] = [];
  for (const m of source.matchAll(re)) {
    if (m[1] === m[2]) codes.push(m[1]);
  }
  return codes;
}

function frontendCodes(): Set<string> {
  return new Set(Object.keys(EXACT_MESSAGES));
}

describe("error_codes.py ↔ ApiErrorCode 카탈로그 드리프트 가드(task-2194)", () => {
  it("서버의 모든 ErrorCode는 프론트 EXACT_MESSAGES에 정확 매핑돼 있다", () => {
    const serverCodes = extractServerErrorCodes(readServerSource());
    expect(serverCodes.length).toBeGreaterThan(0);
    const missing = serverCodes.filter((c) => !frontendCodes().has(c)).sort();
    expect(missing).toEqual([]);
  });

  it("프론트 EXACT_MESSAGES에 서버에 없는 코드가 남아있지 않다(사어(死語) 코드 방지)", () => {
    const serverSet = new Set(extractServerErrorCodes(readServerSource()));
    const extra = [...frontendCodes()].filter((c) => !serverSet.has(c)).sort();
    expect(extra).toEqual([]);
  });
});

describe("착수 시점 재현(DoD a, task-2194)", () => {
  it("INTEGRITY_WALLET_BALANCE_DRIFT·POLICY_DENIED·RISK_DENIED 3종을 프론트 카탈로그에서 빼면 정확히 이 3건이 미매핑으로 잡힌다", () => {
    const serverCodes = extractServerErrorCodes(readServerSource());
    const removed = new Set(["INTEGRITY_WALLET_BALANCE_DRIFT", "POLICY_DENIED", "RISK_DENIED"]);
    const withoutThree = new Set([...frontendCodes()].filter((c) => !removed.has(c)));
    const missing = serverCodes.filter((c) => !withoutThree.has(c)).sort();
    expect(missing).toEqual(["INTEGRITY_WALLET_BALANCE_DRIFT", "POLICY_DENIED", "RISK_DENIED"]);
  });
});

describe("가짜 서버 코드 주입(DoD d, negative test)", () => {
  it("픽스처에 프론트가 모르는 가짜 코드를 하나 심으면 다시 미매핑으로 잡힌다(가드가 실제 파일을 읽는다는 반증)", () => {
    const source = readServerSource();
    const withFakeCode = source.replace(
      '    INTERNAL_ERROR = "INTERNAL_ERROR"',
      '    INTERNAL_ERROR = "INTERNAL_ERROR"\n    TOTALLY_FAKE_CODE_XYZ = "TOTALLY_FAKE_CODE_XYZ"',
    );
    expect(withFakeCode).not.toEqual(source);
    const serverCodes = extractServerErrorCodes(withFakeCode);
    const missing = serverCodes.filter((c) => !frontendCodes().has(c));
    expect(missing).toEqual(["TOTALLY_FAKE_CODE_XYZ"]);
  });
});

describe("정확 매핑이 접두 폴백보다 우선한다(DoD c, task-2194)", () => {
  it("POLICY_DENIED는 EXACT_MESSAGES 문구를 쓰며, 미지의 POLICY_ 코드가 받는 접두 폴백 문구와는 다르다", () => {
    const exactMessage = getApiErrorMessage("POLICY_DENIED");
    const prefixFallbackMessage = getApiErrorMessage("POLICY_SOME_FUTURE_CODE_NOT_YET_MAPPED");
    expect(exactMessage).toBe(EXACT_MESSAGES.POLICY_DENIED);
    expect(exactMessage).not.toBe(prefixFallbackMessage);
  });
});

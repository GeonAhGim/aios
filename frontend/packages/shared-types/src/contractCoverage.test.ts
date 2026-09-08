// task-2187 배치3 — CONTRACT_FIELD_SPECS/UNREGISTERED_PARSER_ALLOWLIST 어느
// 쪽에도 없는 새 파서 파일이 조용히 미등재 상태로 들어오는 것을 막는 래칫.
// shared-types/src 아래 `export function parse[A-Z]...`를 갖는 모든 .ts 파일
// (테스트 파일 제외)을 스캔해 파일 단위로(그 파일이 내보내는 파서 함수 중
// 하나라도 어느 spec.parser에 등장하면 그 파일은 "등재됨") 커버리지를
// 판정한다 — 한 파일에 등록 함수와 미등록 함수가 섞여 있어도(예:
// instrumentView.ts의 parseInstrumentView는 등재, parseSymbolAlias는 SSOT
// 부재로 의도적 미등재) 파일이 통째로 새는 것만 막는다. extractPydanticFields/
// diffFields는 새로 만들지 않고 contractDrift.test.ts에서 그대로 재사용한다
// (DoD g).
import { readFileSync, readdirSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { diffFields, extractPydanticFields } from "./contractDrift.test";
import { CONTRACT_FIELD_SPECS, UNREGISTERED_PARSER_ALLOWLIST } from "./contractFields";

const SRC_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(SRC_DIR, "../../../..");

const PARSER_FN_RE = /^export function (parse[A-Za-z0-9_]*)\(/gm;

interface ParserFile {
  readonly fileName: string;
  readonly parsers: readonly string[];
}

function scanParserFiles(dir: string): ParserFile[] {
  const result: ParserFile[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".ts") || entry.name.endsWith(".test.ts")) continue;
    const source = readFileSync(join(dir, entry.name), "utf-8");
    const parsers = [...source.matchAll(PARSER_FN_RE)].map((m) => m[1]);
    if (parsers.length > 0) result.push({ fileName: entry.name, parsers });
  }
  return result.sort((a, b) => a.fileName.localeCompare(b.fileName));
}

// spec.parser는 "parsePostingLine / isPostingLine"처럼 사람이 읽는 자유
// 텍스트라 토큰 단위 정확 일치로 본다(부분 문자열 포함은 오탐 위험 — 예:
// "parseSession"이 "parseSessionView"에 잘못 매치되는 것을 막는다).
function isRegistered(parsers: readonly string[], specs: readonly { parser: string }[]): boolean {
  return specs.some((spec) => {
    const tokens = spec.parser.split(/[\s/,]+/).filter(Boolean);
    return parsers.some((p) => tokens.includes(p));
  });
}

function findUncovered(
  files: readonly ParserFile[],
  specs: readonly { parser: string }[],
  allowlist: ReadonlyMap<string, string>,
): string[] {
  return files
    .filter((f) => !isRegistered(f.parsers, specs))
    .filter((f) => (allowlist.get(f.fileName) ?? "").trim().length === 0)
    .map((f) => f.fileName);
}

const files = scanParserFiles(SRC_DIR);

describe("파서 커버리지 래칫(task-2187)", () => {
  it("shared-types/src의 모든 파서 파일은 CONTRACT_FIELD_SPECS 등재 또는 allowlist 사유 등록 상태다", () => {
    expect(findUncovered(files, CONTRACT_FIELD_SPECS, UNREGISTERED_PARSER_ALLOWLIST)).toEqual([]);
  });

  it("UNREGISTERED_PARSER_ALLOWLIST의 각 항목은 빈 문자열이 아닌 사유를 갖는다", () => {
    for (const [fileName, reason] of UNREGISTERED_PARSER_ALLOWLIST) {
      expect(reason.trim().length, `${fileName} 사유가 비어 있다`).toBeGreaterThan(0);
    }
  });
});

describe("스캐너 착수 시점 재현 — walletBalance/session/membership 3건(DoD a)", () => {
  it("WalletBalanceView 등재와 allowlist를 걷어내면 정확히 3건이 uncovered로 보고된다", () => {
    const specsWithoutWallet = CONTRACT_FIELD_SPECS.filter(
      (s) => !(s.file === "src/foundation/ledger/application/queries.py" && s.className === "WalletBalanceView"),
    );
    const uncovered = findUncovered(files, specsWithoutWallet, new Map());
    expect(uncovered).toEqual(["membership.ts", "session.ts", "walletBalance.ts"]);
  });
});

describe("allowlist 사유 누락 시 FAIL 재현(DoD d)", () => {
  it("사유가 빈 문자열이면 등재로 치지 않는다", () => {
    const uncovered = findUncovered(files, CONTRACT_FIELD_SPECS, new Map([["session.ts", ""]]));
    expect(uncovered).toContain("session.ts");
  });
});

describe("새 파서 파일 유입 차단(DoD e)", () => {
  it("등재/allowlist 안 된 임시 파서 파일을 추가하면 uncovered로 잡힌다", () => {
    const tmpFileName = "tmpParserLeakGuardTest.ts";
    const tmpPath = join(SRC_DIR, tmpFileName);
    writeFileSync(tmpPath, "export function parseTmpLeakGuardTest(raw: unknown): unknown {\n  return raw;\n}\n", "utf-8");
    try {
      const filesWithTmp = scanParserFiles(SRC_DIR);
      const uncovered = findUncovered(filesWithTmp, CONTRACT_FIELD_SPECS, UNREGISTERED_PARSER_ALLOWLIST);
      expect(uncovered).toContain(tmpFileName);
    } finally {
      unlinkSync(tmpPath);
    }
  });
});

describe("WalletBalanceView 드리프트 양방향 재현(DoD c)", () => {
  const QUERIES_FILE = "src/foundation/ledger/application/queries.py";
  const CLASS_NAME = "WalletBalanceView";

  function walletBalanceSpec() {
    const spec = CONTRACT_FIELD_SPECS.find((s) => s.file === QUERIES_FILE && s.className === CLASS_NAME);
    expect(spec).toBeDefined();
    return spec as NonNullable<typeof spec>;
  }

  it("held 줄을 지우면 diffFields가 missing으로 잡아낸다", () => {
    const source = readFileSync(join(REPO_ROOT, QUERIES_FILE), "utf-8");
    // 리포의 이 파일은 CRLF다 — 선행부를 `\s*`로 매칭하면 그 클래스가
    // JS 정규식 라인 종결자로 취급하는 `\r`을 삼켜 이전/다음 줄까지
    // 오염시킨다(pending_payout 줄이 같이 사라지는 회귀를 실측함). 개행이
    // 아닌 들여쓰기만 `[ \t]*`로 매칭해 줄 경계를 건드리지 않는다.
    const withoutHeld = source.replace(/^[ \t]*held: Decimal[ \t]*\r?\n/m, "");
    expect(withoutHeld).not.toEqual(source);

    const actual = extractPydanticFields(withoutHeld, CLASS_NAME);
    expect(actual).not.toBeNull();
    const { missing } = diffFields(walletBalanceSpec().fields, actual as string[]);
    expect(missing).toEqual(["held"]);
  });

  it("되돌리면(원본 소스) 다시 통과한다", () => {
    const source = readFileSync(join(REPO_ROOT, QUERIES_FILE), "utf-8");
    const actual = extractPydanticFields(source, CLASS_NAME);
    expect(actual).not.toBeNull();
    const { missing, extra } = diffFields(walletBalanceSpec().fields, actual as string[]);
    expect({ missing, extra }).toEqual({ missing: [], extra: [] });
  });
});

// task-1239 — contractFields.ts(프론트 parser가 손으로 미러링한 필드 선언)를
// src/foundation/{positions,market_data,ledger}/contracts/v1.py(백엔드 pydantic
// SSOT)와 기계적으로 1:1 대조한다. apiPaths.openapi.test.ts(task-1165)와 같은
// 관용: 파일이 없으면 조용히 skip하지 않고 readFileSync가 그대로 throw해서
// 이 테스트 파일 전체가 FAIL로 보고된다(I-10).
//
// schema_version은 양쪽에서 제외하고 비교한다 — contractFields.ts 헤더 주석
// 참고(뷰 필드가 아니라 계약 버전 태그, 5개 파서 모두 parseSchemaTagged가
// 별도로 검사).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CONTRACT_FIELD_SPECS } from "./contractFields";

// frontend/packages/shared-types/src -> repo root
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../..");

const SCHEMA_VERSION_FIELD = "schema_version";

function readRepoFile(repoRelativePath: string): string {
  return readFileSync(join(REPO_ROOT, repoRelativePath), "utf-8");
}

/**
 * `class <className>(...):` 바디에서 최상위 필드명을 뽑는다. 클래스를 못 찾으면
 * null. 독스트링(단일행·2행짜리 모두, 이 3개 파일의 실제 형태)은 건너뛰고,
 * 다음 최상위(들여쓰기 0) 선언을 만나면 클래스 바디가 끝난 것으로 본다.
 */
export function extractPydanticFields(source: string, className: string): string[] | null {
  const lines = source.split(/\r?\n/);
  const classHeaderRe = new RegExp(`^class\\s+${className}\\s*\\(`);
  const classStart = lines.findIndex((line) => classHeaderRe.test(line));
  if (classStart === -1) return null;

  const fields: string[] = [];
  let inDocstring = false;
  for (let i = classStart + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") continue;

    const indent = line.length - line.trimStart().length;
    if (indent === 0) break; // 다음 최상위 선언 — 이 클래스 바디는 끝났다

    const trimmed = line.trim();
    const tripleQuoteCount = (trimmed.match(/"""/g) ?? []).length;

    if (inDocstring) {
      if (tripleQuoteCount % 2 === 1) inDocstring = false;
      continue;
    }
    if (tripleQuoteCount > 0) {
      if (tripleQuoteCount % 2 === 1) inDocstring = true;
      continue;
    }
    if (trimmed.startsWith("#")) continue;

    const fieldMatch = /^([A-Za-z_][A-Za-z0-9_]*)\s*:/.exec(trimmed);
    if (fieldMatch) fields.push(fieldMatch[1]);
  }
  return fields;
}

export interface FieldDiff {
  readonly missing: readonly string[]; // 선언했지만 pydantic엔 없음
  readonly extra: readonly string[]; // pydantic엔 있지만 선언 안 함
}

/** schema_version을 양쪽에서 제외한 뒤 집합 대조한다. */
export function diffFields(declared: readonly string[], actual: readonly string[]): FieldDiff {
  const declaredSet = new Set(declared.filter((f) => f !== SCHEMA_VERSION_FIELD));
  const actualSet = new Set(actual.filter((f) => f !== SCHEMA_VERSION_FIELD));
  const missing = [...declaredSet].filter((f) => !actualSet.has(f)).sort();
  const extra = [...actualSet].filter((f) => !declaredSet.has(f)).sort();
  return { missing, extra };
}

const fileCache = new Map<string, string>();
function sourceFor(repoRelativePath: string): string {
  const cached = fileCache.get(repoRelativePath);
  if (cached !== undefined) return cached;
  const content = readRepoFile(repoRelativePath);
  fileCache.set(repoRelativePath, content);
  return content;
}

describe("contractFields ↔ v1.py — 파서 필드 드리프트 가드(task-1239)", () => {
  for (const spec of CONTRACT_FIELD_SPECS) {
    it(`${spec.parser}(${spec.className})는 ${spec.file}와 필드가 정확히 일치한다`, () => {
      const source = sourceFor(spec.file);
      const actual = extractPydanticFields(source, spec.className);
      expect(actual, `${spec.file}에 class ${spec.className}(...)가 없다`).not.toBeNull();
      const { missing, extra } = diffFields(spec.fields, actual as string[]);
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });
  }

  it("CONTRACT_FIELD_SPECS의 각 (file, className) 쌍은 중복이 없다", () => {
    const keys = CONTRACT_FIELD_SPECS.map((s) => `${s.file}::${s.className}`);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("contractDrift 자체 검증(negative, task-1239 DoD)", () => {
  it("존재하지 않는 클래스명을 주면 extractPydanticFields는 null을 반환한다", () => {
    const source = sourceFor("src/foundation/positions/contracts/v1.py");
    expect(extractPydanticFields(source, "TotallyNonexistentClass")).toBeNull();
  });

  it("실재하는 클래스에 존재하지 않는 필드를 선언하면(오타) diffFields가 missing으로 잡아낸다", () => {
    const source = sourceFor("src/foundation/positions/contracts/v1.py");
    const actual = extractPydanticFields(source, "PositionSnapshotView");
    expect(actual).not.toBeNull();
    const withTypo = ["position_key", "tenant_id", "quantity_typo_not_real"];
    const { missing } = diffFields(withTypo, actual as string[]);
    expect(missing).toEqual(["quantity_typo_not_real"]);
  });

  it("실제로 존재하는 필드를 선언에서 빠뜨리면 diffFields가 extra로 잡아낸다", () => {
    const source = sourceFor("src/foundation/ledger/contracts/v1.py");
    const actual = extractPydanticFields(source, "PostingLine");
    expect(actual).not.toBeNull();
    const missingOneField = ["line_no", "account_code", "side", "amount"]; // currency 누락
    const { extra } = diffFields(missingOneField, actual as string[]);
    expect(extra).toEqual(["currency"]);
  });

  it("이 회귀 가드가 스스로 무력화되지 않았음을 확인한다: 실제 CONTRACT_FIELD_SPECS에 오타난 필드를 하나 주입하면 실패를 검출한다", () => {
    const bogusSpec = { ...CONTRACT_FIELD_SPECS[0], fields: [...CONTRACT_FIELD_SPECS[0].fields, "no_such_field_xyz"] };
    const actual = extractPydanticFields(sourceFor(bogusSpec.file), bogusSpec.className);
    expect(actual).not.toBeNull();
    const { missing } = diffFields(bogusSpec.fields, actual as string[]);
    expect(missing).toEqual(["no_such_field_xyz"]);
  });
});

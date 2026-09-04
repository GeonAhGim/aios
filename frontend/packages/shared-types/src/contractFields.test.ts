// task-1332 — 배치2(인증·테넌트·비밀·헬스 파서) CONTRACT_FIELD_SPECS 등재 확인
// + negative test. 기계적 필드 대조 자체는 contractDrift.test.ts가
// CONTRACT_FIELD_SPECS 전체를 순회하며 이미 수행한다(task-1239) — 여기서는
// 이 리프가 실제로 4개 항목(ReadinessReport, CheckResult, TokenPairResponse,
// SecretRef)을 추가했는지, 그리고 그 항목들에 오타를 주입하면 회귀가
// 걸리는지만 별도로 확인한다. extractPydanticFields/diffFields는
// contractDrift.test.ts가 이미 export하므로 그대로 재사용한다.
//
// §3.4 SessionView·§3.5 MembershipView는 이번 배치에서 제외했다(contractFields.ts
// 하단 note 참고) — 백엔드에 대응하는 pydantic SSOT 클래스가 아직 없어서다.
// 없는 백엔드 클래스를 겨냥한 스펙은 extractPydanticFields가 null을 반환해
// "드리프트"가 아니라 "클래스 없음"으로 상시 FAIL하므로(contractDrift.test.ts:92
// `not.toBeNull()`), 여기 negative test 대상에도 넣지 않는다.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { diffFields, extractPydanticFields } from "./contractDrift.test";
import { CONTRACT_FIELD_SPECS } from "./contractFields";

// frontend/packages/shared-types/src -> repo root
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../..");

function readRepoFile(repoRelativePath: string): string {
  return readFileSync(join(REPO_ROOT, repoRelativePath), "utf-8");
}

const BATCH_2 = [
  { file: "src/api/routers/health.py", className: "ReadinessReport" },
  { file: "src/api/routers/health.py", className: "CheckResult" },
  { file: "src/services/auth/tokens.py", className: "TokenPairResponse" },
  { file: "src/core/security/secret_ref.py", className: "SecretRef" },
] as const;

function findSpec(file: string, className: string) {
  return CONTRACT_FIELD_SPECS.find((s) => s.file === file && s.className === className);
}

describe("배치2 CONTRACT_FIELD_SPECS 등재(task-1332)", () => {
  for (const { file, className } of BATCH_2) {
    it(`${className}이(가) ${file}로 CONTRACT_FIELD_SPECS에 등재돼 있다`, () => {
      const spec = findSpec(file, className);
      expect(spec).toBeDefined();
      expect(spec?.fields.length ?? 0).toBeGreaterThan(0);
    });
  }
});

describe("배치2 negative test — 오타 주입 시 회귀 검출(task-1332 DoD)", () => {
  for (const { file, className } of BATCH_2) {
    it(`${className}에 존재하지 않는 필드를 주입하면 diffFields가 missing으로 잡아낸다`, () => {
      const spec = findSpec(file, className);
      expect(spec).toBeDefined();
      const actual = extractPydanticFields(readRepoFile(file), className);
      expect(actual, `${file}에 class ${className}(...)가 없다`).not.toBeNull();

      const withTypo = [...(spec?.fields ?? []), "no_such_field_xyz"];
      const { missing } = diffFields(withTypo, actual as string[]);
      expect(missing).toEqual(["no_such_field_xyz"]);
    });

    it(`${className}의 실제 필드를 선언에서 하나 빠뜨리면 diffFields가 extra로 잡아낸다`, () => {
      const spec = findSpec(file, className);
      expect(spec).toBeDefined();
      const declaredFields = spec?.fields ?? [];
      expect(declaredFields.length).toBeGreaterThan(0);

      const actual = extractPydanticFields(readRepoFile(file), className);
      expect(actual).not.toBeNull();

      const droppedField = declaredFields[declaredFields.length - 1];
      const missingOneField = declaredFields.slice(0, -1);
      const { extra } = diffFields(missingOneField, actual as string[]);
      expect(extra).toEqual([droppedField]);
    });
  }
});

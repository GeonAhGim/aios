// task-2412(FE-OPS-8) — validationView.ts의 ValidationResultView 파서 필드 선언.
// contractFields.ts는 이미 P6 300줄 상한(task-2026)에 바짝 붙어 있어(298줄) 이
// 항목을 별도 파일로 두고 스프레드로 합친다(apiPaths.ts→apiRoutes.ts 분리, task-2098과
// 동일한 이유). contractDrift.test.ts/contractCoverage.test.ts는 병합된
// CONTRACT_FIELD_SPECS 전체를 대상으로 동작하므로 이 분리로 대조 로직은 바뀌지 않는다.
import type { ContractFieldSpec } from "./contractFields";

const VALIDATION_V1 = "src/foundation/validation/contracts/v1.py";

export const VALIDATION_CONTRACT_FIELD_SPECS: readonly ContractFieldSpec[] = [
  {
    file: VALIDATION_V1,
    className: "ValidationResultView",
    parser: "parseValidationResultView",
    fields: [
      "run_id",
      "strategy_id",
      "strategy_version",
      "check_type",
      "state",
      "outcome",
      "metrics",
      "warnings",
      "hard_fail_reasons",
      "obligations",
      "result_hash",
      "created_at",
    ],
  },
];

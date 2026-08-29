import type { ConditionOperator, PreviewCondition } from "@aios/shared-types";
import { Button, Input, Select } from "@aios/ui-web";

const OPERATORS: ConditionOperator[] = [
  "<",
  ">",
  "<=",
  ">=",
  "==",
  "crosses_above",
  "crosses_below",
];

interface ConditionRowProps {
  value: PreviewCondition;
  onChange: (next: PreviewCondition) => void;
  onRemove: () => void;
  indicators: string[];
}

// FD-14.2 — 코드 작성 없이 드롭다운(지표 선택) + 숫자입력(파라미터) + 연산자
// 선택으로 조건 1개를 구성한다.
export function ConditionRow({ value, onChange, onRemove, indicators }: ConditionRowProps) {
  return (
    <div className="flex items-center gap-2">
      <Select
        value={value.indicator}
        onChange={(e) => onChange({ ...value, indicator: e.target.value })}
        className="w-28"
      >
        {indicators.map((ind) => (
          <option key={ind} value={ind}>
            {ind}
          </option>
        ))}
      </Select>
      <Input
        type="number"
        placeholder="period"
        value={value.params.timeperiod ?? ""}
        onChange={(e) =>
          onChange({
            ...value,
            params: e.target.value ? { timeperiod: Number(e.target.value) } : {},
          })
        }
        className="w-20"
      />
      <Select
        value={value.operator}
        onChange={(e) => onChange({ ...value, operator: e.target.value as ConditionOperator })}
        className="w-36"
      >
        {OPERATORS.map((op) => (
          <option key={op} value={op}>
            {op}
          </option>
        ))}
      </Select>
      <Input
        type="number"
        placeholder="임계값"
        value={value.threshold}
        onChange={(e) => onChange({ ...value, threshold: Number(e.target.value) })}
        className="w-24"
      />
      <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
        삭제
      </Button>
    </div>
  );
}

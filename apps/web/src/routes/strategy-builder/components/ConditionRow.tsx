import type { ConditionOperator, PreviewCondition } from "@aios/shared-types";

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
      <select
        value={value.indicator}
        onChange={(e) => onChange({ ...value, indicator: e.target.value })}
        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
      >
        {indicators.map((ind) => (
          <option key={ind} value={ind}>
            {ind}
          </option>
        ))}
      </select>
      <input
        type="number"
        placeholder="period"
        value={value.params.timeperiod ?? ""}
        onChange={(e) =>
          onChange({
            ...value,
            params: e.target.value ? { timeperiod: Number(e.target.value) } : {},
          })
        }
        className="w-20 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
      />
      <select
        value={value.operator}
        onChange={(e) => onChange({ ...value, operator: e.target.value as ConditionOperator })}
        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
      >
        {OPERATORS.map((op) => (
          <option key={op} value={op}>
            {op}
          </option>
        ))}
      </select>
      <input
        type="number"
        placeholder="임계값"
        value={value.threshold}
        onChange={(e) => onChange({ ...value, threshold: Number(e.target.value) })}
        className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
      />
      <button
        type="button"
        onClick={onRemove}
        className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:bg-slate-800"
      >
        삭제
      </button>
    </div>
  );
}

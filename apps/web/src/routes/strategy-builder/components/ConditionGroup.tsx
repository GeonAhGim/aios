import type { PreviewCondition } from "@aios/shared-types";
import { ConditionRow } from "./ConditionRow";

interface ConditionGroupProps {
  title: string;
  conditions: PreviewCondition[];
  combine: "AND" | "OR";
  onConditionsChange: (next: PreviewCondition[]) => void;
  onCombineChange: (next: "AND" | "OR") => void;
  indicators: string[];
}

const EMPTY_CONDITION: PreviewCondition = {
  indicator: "RSI",
  params: { timeperiod: 14 },
  operator: "<",
  threshold: 30,
};

export function ConditionGroup({
  title,
  conditions,
  combine,
  onConditionsChange,
  onCombineChange,
  indicators,
}: ConditionGroupProps) {
  return (
    <div className="space-y-2 rounded border border-slate-800 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-200">{title}</h3>
        {conditions.length > 1 && (
          <select
            value={combine}
            onChange={(e) => onCombineChange(e.target.value as "AND" | "OR")}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300"
          >
            <option value="AND">모두 만족(AND)</option>
            <option value="OR">하나라도 만족(OR)</option>
          </select>
        )}
      </div>
      <div className="space-y-2">
        {conditions.map((cond, i) => (
          <ConditionRow
            key={i}
            value={cond}
            indicators={indicators}
            onChange={(next) =>
              onConditionsChange(conditions.map((c, j) => (j === i ? next : c)))
            }
            onRemove={() => onConditionsChange(conditions.filter((_, j) => j !== i))}
          />
        ))}
      </div>
      <button
        type="button"
        onClick={() => onConditionsChange([...conditions, { ...EMPTY_CONDITION }])}
        className="text-xs text-slate-400 hover:text-slate-200"
      >
        + 조건 추가
      </button>
    </div>
  );
}

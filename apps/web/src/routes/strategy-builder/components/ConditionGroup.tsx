import type { PreviewCondition } from "@aios/shared-types";
import { Select } from "@aios/ui-web";
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
    <div className="space-y-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {conditions.length > 1 && (
          <Select
            value={combine}
            onChange={(e) => onCombineChange(e.target.value as "AND" | "OR")}
            className="w-auto py-1 text-xs"
          >
            <option value="AND">모두 만족(AND)</option>
            <option value="OR">하나라도 만족(OR)</option>
          </Select>
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
        className="text-xs font-medium text-accent-hover hover:underline"
      >
        + 조건 추가
      </button>
    </div>
  );
}

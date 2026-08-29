import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CATEGORICAL_PALETTE, CATEGORICAL_SOFT_CAP, NEUTRAL_SLOT } from "./chartPalette";

export interface AllocationSlice {
  name: string;
  value: number; // 0~100 비중(%)
}

interface AllocationBarChartProps {
  allocations: AllocationSlice[];
  unallocatedPct: number;
}

interface TooltipPayloadEntry {
  name?: string;
  value?: number;
  color?: string;
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg">
      {payload
        .filter((p) => (p.value ?? 0) > 0)
        .map((p) => (
          <div key={p.name} className="flex items-center gap-2">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: p.color }}
              aria-hidden
            />
            <span className="text-fg-secondary">{p.name}</span>
            <span className="tabular ml-auto font-medium text-fg">
              {p.value?.toFixed(1)}%
            </span>
          </div>
        ))}
    </div>
  );
}

// 자산배분 = part-to-whole → 가로 스택 바(도넛 대신, dataviz 스킬 기준
// 권장 형태). 소프트 캡을 넘으면 나머지는 "기타"로 접는다.
export function AllocationBarChart({ allocations, unallocatedPct }: AllocationBarChartProps) {
  const sorted = [...allocations].sort((a, b) => b.value - a.value);
  const visible = sorted.slice(0, CATEGORICAL_SOFT_CAP);
  const rest = sorted.slice(CATEGORICAL_SOFT_CAP);
  const restTotal = rest.reduce((sum, r) => sum + r.value, 0);

  const slices = [...visible, ...(restTotal > 0 ? [{ name: "기타", value: restTotal }] : [])];
  const row: Record<string, number | string> = { category: "배분" };
  slices.forEach((s) => {
    row[s.name] = s.value;
  });
  row["미배분 현금"] = unallocatedPct;

  const legendEntries = [...slices, { name: "미배분 현금", value: unallocatedPct }];

  return (
    <div>
      <ResponsiveContainer width="100%" height={64}>
        <BarChart data={[row]} layout="vertical" margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" dataKey="category" hide />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "transparent" }} />
          {slices.map((s, i) => (
            <Bar
              key={s.name}
              dataKey={s.name}
              stackId="a"
              fill={CATEGORICAL_PALETTE[i % CATEGORICAL_PALETTE.length]}
              radius={i === 0 ? [4, 0, 0, 4] : i === slices.length - 1 ? [0, 0, 0, 0] : 0}
            >
              <Cell />
            </Bar>
          ))}
          <Bar dataKey="미배분 현금" stackId="a" fill={NEUTRAL_SLOT} radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {legendEntries.map((entry, i) => (
          <div key={entry.name} className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2 w-2 rounded-full"
              style={{
                backgroundColor:
                  entry.name === "미배분 현금"
                    ? NEUTRAL_SLOT
                    : CATEGORICAL_PALETTE[i % CATEGORICAL_PALETTE.length],
              }}
              aria-hidden
            />
            <span className="text-fg-secondary">{entry.name}</span>
            <span className="tabular text-fg-muted">{entry.value.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

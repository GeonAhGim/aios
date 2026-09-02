import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DIVERGING_DOWN, DIVERGING_UP } from "./chartPalette";

export interface DailyPnlPoint {
  tradeDate: string;
  dailyPnl: number;
  cumulativePnl: number;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

interface TooltipPayloadEntry {
  value?: number;
  payload?: DailyPnlPoint;
}

function CumulativeTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg">
      <p className="text-fg-muted">{label}</p>
      <p className="tabular font-medium text-fg">누적 {payload[0]?.value?.toFixed(2)}</p>
    </div>
  );
}

function DailyTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const value = payload[0]?.value ?? 0;
  return (
    <div className="rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg">
      <p className="text-fg-muted">{label}</p>
      <p className="tabular font-medium text-fg">일일 {value.toFixed(2)}</p>
    </div>
  );
}

// "추세" job → 단일 계열 라인(누적 손익, 1-hue) + "기준선 위/아래" job →
// 다이버징 바(일일 손익, blue 위 / red 아래) — 두 축을 하나로 합치지
// 않는다(dataviz 스킬 "One axis" 원칙).
export function PnlChart({ data }: { data: DailyPnlPoint[] }) {
  return (
    <div className="space-y-6">
      <div>
        <p className="mb-2 text-xs text-fg-muted">누적 손익 추이</p>
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="cumulativeFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={DIVERGING_UP} stopOpacity={0.18} />
                <stop offset="100%" stopColor={DIVERGING_UP} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--color-border)" vertical={false} />
            <XAxis
              dataKey="tradeDate"
              tickFormatter={formatDate}
              tick={{ fill: "var(--color-fg-muted)", fontSize: 11 }}
              axisLine={{ stroke: "var(--color-border-strong)" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "var(--color-fg-muted)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <Tooltip content={<CumulativeTooltip />} />
            <Area
              type="monotone"
              dataKey="cumulativePnl"
              stroke={DIVERGING_UP}
              strokeWidth={2}
              fill="url(#cumulativeFill)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div>
        <p className="mb-2 text-xs text-fg-muted">일별 손익</p>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-border)" vertical={false} />
            <XAxis
              dataKey="tradeDate"
              tickFormatter={formatDate}
              tick={{ fill: "var(--color-fg-muted)", fontSize: 11 }}
              axisLine={{ stroke: "var(--color-border-strong)" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "var(--color-fg-muted)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <ReferenceLine y={0} stroke="var(--color-border-strong)" />
            <Tooltip content={<DailyTooltip />} cursor={{ fill: "var(--color-surface-hover)" }} />
            <Bar dataKey="dailyPnl" radius={[2, 2, 2, 2]} maxBarSize={16}>
              {data.map((d) => (
                <Cell key={d.tradeDate} fill={d.dailyPnl >= 0 ? DIVERGING_UP : DIVERGING_DOWN} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

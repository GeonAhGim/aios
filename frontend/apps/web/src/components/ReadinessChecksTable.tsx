import type { CheckResult } from "@aios/api-client";
import { Badge } from "@aios/ui-web";

// spec §3.2 ReadinessReport.checks 표. loop:<name> 키는 서버가 루프별로 몇 개든
// 추가할 수 있으므로("전방호환" — readiness.ts 주석 참고) 별도 화이트리스트 없이
// "loop:" 접두로만 구분해 그룹을 나눈다. 읽기 전용 진단 표라 조작 버튼은 두지 않는다.
interface ReadinessChecksTableProps {
  checks: Record<string, CheckResult>;
}

interface CheckRow {
  name: string;
  check: CheckResult;
}

function formatNumber(value: number | null): string {
  return value === null ? "-" : String(value);
}

function splitLoopChecks(checks: Record<string, CheckResult>): { loops: CheckRow[]; others: CheckRow[] } {
  const loops: CheckRow[] = [];
  const others: CheckRow[] = [];
  for (const [name, check] of Object.entries(checks)) {
    (name.startsWith("loop:") ? loops : others).push({ name, check });
  }
  return { loops, others };
}

function CheckTable({ title, rows }: { title: string; rows: CheckRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h2 className="mb-2 text-sm font-medium text-fg-muted">{title}</h2>
      <table className="w-full text-sm">
        <thead className="text-left text-fg-muted">
          <tr>
            <th className="pb-2 font-normal">이름</th>
            <th className="pb-2 font-normal">상태</th>
            <th className="pb-2 font-normal">detail</th>
            <th className="pb-2 font-normal">observed</th>
            <th className="pb-2 font-normal">threshold</th>
          </tr>
        </thead>
        <tbody className="tabular text-fg">
          {rows.map(({ name, check }) => (
            <tr key={name} className="border-t border-border">
              <td className="py-2 font-mono">{name}</td>
              <td className="py-2">
                <Badge tone={check.ok ? "success" : "danger"}>{check.ok ? "정상" : "실패"}</Badge>
              </td>
              <td className="py-2 text-fg-muted">{check.detail ?? "-"}</td>
              <td className="py-2">{formatNumber(check.observed)}</td>
              <td className="py-2">{formatNumber(check.threshold)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReadinessChecksTable({ checks }: ReadinessChecksTableProps) {
  const { loops, others } = splitLoopChecks(checks);

  if (loops.length === 0 && others.length === 0) {
    return <p className="text-sm text-fg-muted">등록된 체크가 없습니다.</p>;
  }

  return (
    <div className="space-y-6" data-testid="readiness-checks-table">
      <CheckTable title="체크" rows={others} />
      <CheckTable title="루프" rows={loops} />
    </div>
  );
}

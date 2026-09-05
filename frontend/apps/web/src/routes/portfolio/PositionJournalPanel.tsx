import { Button } from "@aios/ui-web";
import { useState } from "react";
import { useCursorPage } from "../../hooks/useCursorPage";
import { usePositionJournal, type PositionsClientLike } from "../../hooks/usePositions";
import type { CursorNavigatorMeta } from "../../lib/cursorPagination";
import { PositionsQueryError } from "./PositionsQueryError";

interface CommittedPage {
  cursor: string | undefined;
  nextCursor: string | null;
}

// task-1524(LB-19): GET /v1/positions/{position_key}/journal(cursor=sequence_no 문자열,
// limit≤200)을 useCursorPage(task-462)로 앞/뒤 이동한다. 커서는 봉투 meta.page.
// next_cursor를 문자열 그대로 다음 요청에 실을 뿐 해석하지 않는다(숫자 변환 금지).
// 접힌 상태에서는 요청하지 않는다(enabled=false) — 포지션 N개가 저널 N번을 한꺼번에
// 부르지 않게 한다.
//
// 저널 항목(PositionJournalEntryView)은 shared-types에 파서가 아직 없어 raw로 받는다
// (새 파서 금지, task-1524 decision). 여기서는 필드가 없거나 형이 다르면 "-"로 그리기만
// 하고 판정(ok/invalid)은 내리지 않는다 — 판정은 후속 파서 리프의 몫이다.
const COLUMNS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "sequence_no", label: "seq" },
  { key: "entry_type", label: "유형" },
  { key: "qty_delta", label: "수량 변화" },
  { key: "price", label: "가격" },
  { key: "fee", label: "수수료" },
  { key: "realized_pnl_base", label: "실현 손익" },
  { key: "occurred_at", label: "발생 시각" },
];

function cellText(entry: unknown, key: string): string {
  if (typeof entry !== "object" || entry === null) return "-";
  const value = (entry as Record<string, unknown>)[key];
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") {
    // Money(amount·currency) — 숫자 변환 없이 문자열 그대로 이어 붙인다(§3.4).
    const money = value as { amount?: unknown; currency?: unknown };
    if (typeof money.amount !== "string") return "-";
    return typeof money.currency === "string" ? `${money.amount} ${money.currency}` : money.amount;
  }
  return String(value);
}

interface PositionJournalPanelProps {
  client: Pick<PositionsClientLike, "getPositionJournal">;
  positionKey: string;
  pageSize?: number;
}

export function PositionJournalPanel({ client, positionKey, pageSize = 20 }: PositionJournalPanelProps) {
  const [open, setOpen] = useState(false);
  // InstrumentsPage(task-824)와 같은 관용: 마지막으로 "확정된" (cursor → next_cursor)
  // 쌍을 상태로 두고 useCursorPage에 meta로 넘긴다. 새 커서의 응답이 도착했을 때만
  // 렌더 중 setState로 갱신한다(effect 없음 — 파생 상태 갱신의 React 권장 방식).
  const [committed, setCommitted] = useState<CommittedPage | null>(null);
  const meta: CursorNavigatorMeta | null = committed ? { next_cursor: committed.nextCursor } : null;
  const pager = useCursorPage(meta);
  const query = usePositionJournal(client, positionKey, { cursor: pager.cursor, limit: pageSize, enabled: open });

  if (
    query.data &&
    (!committed || committed.cursor !== pager.cursor || committed.nextCursor !== query.data.nextCursor)
  ) {
    setCommitted({ cursor: pager.cursor, nextCursor: query.data.nextCursor });
  }

  const items = query.data?.items ?? [];
  const testIdBase = `position-journal-${positionKey}`;

  if (!open) {
    return (
      <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(true)} data-testid={`${testIdBase}-open`}>
        저널 보기
      </Button>
    );
  }

  return (
    <div className="rounded-md border border-border p-3 text-sm" data-testid={testIdBase}>
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">저널</span>
        <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(false)}>
          닫기
        </Button>
      </div>

      {query.isError ? (
        <PositionsQueryError error={query.error} notFoundTitle="저널이 없습니다." onRetry={() => query.refetch()} />
      ) : query.isPending ? (
        <p className="text-fg-muted">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="text-fg-muted" data-testid={`${testIdBase}-empty`}>
          저널 항목이 없습니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-fg-muted">
              <tr>
                {COLUMNS.map((column) => (
                  <th key={column.key} className="pb-1 font-normal">
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular">
              {items.map((entry, index) => (
                <tr key={`${cellText(entry, "sequence_no")}-${index}`} className="border-t border-border">
                  {COLUMNS.map((column) => (
                    <td key={column.key} className="py-1 pr-2">
                      {cellText(entry, column.key)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-2 flex gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={pager.prev}
          disabled={!pager.hasPrev || query.isFetching}
          data-testid={`${testIdBase}-prev`}
        >
          이전
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={pager.next}
          disabled={!pager.hasNext || query.isFetching}
          data-testid={`${testIdBase}-next`}
        >
          다음
        </Button>
      </div>
    </div>
  );
}

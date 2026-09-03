import type { ApiResponsePageMeta } from "@aios/api-client";
import { isLinesBalanced, parseJournalEntryView, type JournalEntryView, type PostingLine } from "@aios/shared-types";
import { Alert, Badge, Card, EmptyState, LoadingState } from "@aios/ui-web";
import { Pagination } from "./Pagination";
import { derivePageState } from "../lib/pagination";

// spec §3.3 (C) JournalEntryView 목록 화면. 서버 라우트가 아직 없으므로(task-628과
// 같은 decision) fetch는 하지 않고 이미 받아온 raw 항목 배열을 props로 받아 각각
// parseJournalEntryView로 파싱한다 — WalletBalanceCard와 같이 파싱 실패를 숨기지
// 않고 화면에 사유와 함께 노출한다(조용히 렌더 금지).
//
// Σ차변=Σ대변 불일치도 같은 원칙이다: isLinesBalanced가 false면 항목을 정상처럼
// 그리지 않고 "합계 불일치" 경고를 덧붙인다. 페이지네이션은 derivePageState(task-152)
// ·Pagination(task-323)을 그대로 재사용한다.
interface LedgerEntryListProps {
  entries: unknown[];
  pageMeta?: ApiResponsePageMeta | null;
  onPageChange?: (page: number) => void;
  isLoading?: boolean;
}

const SIDE_LABEL: Record<PostingLine["side"], string> = {
  DEBIT: "차변",
  CREDIT: "대변",
};

function PostingLinesTable({ lines }: { lines: PostingLine[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-fg-muted">
          <th className="py-1 pr-2 font-normal">#</th>
          <th className="py-1 pr-2 font-normal">계정</th>
          <th className="py-1 pr-2 font-normal">구분</th>
          <th className="py-1 pr-2 text-right font-normal">금액</th>
          <th className="py-1 font-normal">통화</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.line_no} className="border-t border-border">
            <td className="py-1 pr-2 text-fg-muted">{line.line_no}</td>
            <td className="py-1 pr-2 font-mono text-xs">{line.account_code}</td>
            <td className="py-1 pr-2">
              <Badge tone={line.side === "DEBIT" ? "neutral" : "accent"}>{SIDE_LABEL[line.side]}</Badge>
            </td>
            <td className="tabular py-1 pr-2 text-right">{line.amount}</td>
            <td className="py-1">{line.currency}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JournalEntryCard({ entry }: { entry: JournalEntryView }) {
  const balanced = isLinesBalanced(entry.lines);
  return (
    <Card data-testid="journal-entry-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium text-fg">
            #{entry.sequence_no} · {entry.event_type}
          </p>
          <p className="text-xs text-fg-muted">
            {entry.event_ref} · {entry.posted_at}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {entry.replayed && <Badge tone="warning">재생됨</Badge>}
          {!balanced && (
            <Badge tone="danger" data-testid="sum-mismatch-badge">
              합계 불일치
            </Badge>
          )}
        </div>
      </div>

      <div className="mt-3">
        <PostingLinesTable lines={entry.lines} />
      </div>

      {!balanced && (
        <div className="mt-3">
          <Alert tone="danger">
            차변 합계와 대변 합계가 일치하지 않습니다. 원본 데이터를 신뢰할 수 없으니 표시된 금액을
            그대로 사용하지 마세요.
          </Alert>
        </div>
      )}
    </Card>
  );
}

function EntryError({ kind, received }: { kind: "unsupported_schema_version" | "invalid"; received?: unknown }) {
  if (kind === "unsupported_schema_version") {
    return (
      <Card data-testid="journal-entry-error">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(received)}).</Alert>
      </Card>
    );
  }
  return (
    <Card data-testid="journal-entry-error">
      <Alert tone="danger">거래내역을 해석할 수 없습니다.</Alert>
    </Card>
  );
}

export function LedgerEntryList({ entries, pageMeta = null, onPageChange, isLoading = false }: LedgerEntryListProps) {
  if (isLoading) {
    return <LoadingState />;
  }

  if (entries.length === 0) {
    return <EmptyState>거래내역이 없습니다.</EmptyState>;
  }

  const pageState = derivePageState(pageMeta);

  return (
    <div className="space-y-4" data-testid="ledger-entry-list">
      {entries.map((raw, index) => {
        const parsed = parseJournalEntryView(raw);
        if (parsed.kind === "ok") {
          return <JournalEntryCard key={parsed.value.entry_id} entry={parsed.value} />;
        }
        return (
          <EntryError
            key={`invalid-${index}`}
            kind={parsed.kind}
            received={parsed.kind === "unsupported_schema_version" ? parsed.received : undefined}
          />
        );
      })}

      {onPageChange && <Pagination state={pageState} onPageChange={onPageChange} />}
    </div>
  );
}

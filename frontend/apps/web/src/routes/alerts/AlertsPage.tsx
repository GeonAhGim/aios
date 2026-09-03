import {
  useCancelAlert,
  useCreateAlert,
  useIndicators,
  useMyAlerts,
} from "@aios/shared-hooks";
import { ApiError, type ApiResponsePageMeta } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError, type AlertCreateRequest } from "@aios/shared-types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { Pagination } from "../../components/Pagination";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { derivePageState } from "../../lib/pagination";

// spec §3.3 에러 taxonomy: 알림 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴 재사용).
function CreateAlertError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

const EXCHANGES = ["bitget", "kis"];
const OPERATORS = [
  "<",
  ">",
  "<=",
  ">=",
  "==",
  "crosses_above",
  "crosses_below",
];
// listMyAlerts()는 봉투 미적용 레거시 배열 응답이라(§9 PLT-12 대상 밖) 서버가
// 페이지를 나눠주지 않는다. 이미 받은 전체 배열을 derivePageState로 클라이언트
// 쪽에서 잘라 보여준다.
const ALERTS_PAGE_SIZE = 10;

const STATUS_TONE: Record<string, "neutral" | "success" | "warning"> = {
  ACTIVE: "neutral",
  TRIGGERED: "success",
  CANCELLED: "warning",
};

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "감시 중",
  TRIGGERED: "발동됨",
  CANCELLED: "취소됨",
};

export function AlertsPage() {
  const { data: alerts, isLoading } = useMyAlerts();
  const { data: indicatorList } = useIndicators();
  const createAlert = useCreateAlert();
  const cancelAlert = useCancelAlert();
  const [searchParams, setSearchParams] = useSearchParams();

  const requestedPage = Math.max(1, Number(searchParams.get("page")) || 1);
  const meta: ApiResponsePageMeta | null = alerts
    ? {
        total: alerts.length,
        page: requestedPage,
        size: ALERTS_PAGE_SIZE,
        next_cursor: null,
      }
    : null;
  const pageState = derivePageState(meta, { defaultSize: ALERTS_PAGE_SIZE });
  const pageAlerts =
    alerts && pageState.totalPages !== null && pageState.totalPages > 0
      ? alerts.slice(pageState.rangeStart - 1, pageState.rangeEnd)
      : [];

  function goToPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(nextPage));
    setSearchParams(next, { replace: true });
  }

  const indicators = indicatorList?.indicators ?? ["RSI", "SMA", "EMA"];
  const [exchange, setExchange] = useState(EXCHANGES[0]);
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [indicator, setIndicator] = useState("RSI");
  const [period, setPeriod] = useState("14");
  const [operator, setOperator] = useState<AlertCreateRequest["operator"]>("<");
  const [threshold, setThreshold] = useState("30");
  const [error, setError] = useState<unknown>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await createAlert.mutateAsync({
        exchange,
        symbol,
        timeframe,
        indicator,
        params: period ? { timeperiod: Number(period) } : {},
        operator,
        threshold: Number(threshold),
      });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("알림 생성에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="가격/지표 알림" />

        <Card className="max-w-2xl">
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="거래소">
                <Select
                  value={exchange}
                  onChange={(e) => setExchange(e.target.value)}
                >
                  {EXCHANGES.map((ex) => (
                    <option key={ex} value={ex}>
                      {exchangeLabel(ex)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="종목/심볼">
                <Input
                  type="text"
                  required
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  placeholder="BTC/USDT"
                />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="지표">
                <Select
                  value={indicator}
                  onChange={(e) => setIndicator(e.target.value)}
                >
                  {indicators.map((ind) => (
                    <option key={ind} value={ind}>
                      {ind}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="기간(period)">
                <Input
                  type="number"
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                />
              </Field>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <Field label="타임프레임">
                <Select
                  value={timeframe}
                  onChange={(e) => setTimeframe(e.target.value)}
                >
                  <option value="15m">15분</option>
                  <option value="1h">1시간</option>
                  <option value="4h">4시간</option>
                  <option value="1d">1일</option>
                </Select>
              </Field>
              <Field label="조건">
                <Select
                  value={operator}
                  onChange={(e) =>
                    setOperator(
                      e.target.value as AlertCreateRequest["operator"],
                    )
                  }
                >
                  {OPERATORS.map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="임계값">
                <Input
                  type="number"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                />
              </Field>
            </div>
            <p className="text-xs text-fg-muted">
              예: RSI가 30 밑으로(&lt;) 떨어지면 알림 — 약 1분마다 조건을
              확인합니다.
            </p>
            {error !== null && <CreateAlertError error={error} />}
            <Button
              type="submit"
              loading={createAlert.isPending}
              className="w-full"
            >
              알림 등록
            </Button>
          </form>
        </Card>

        {isLoading ? (
          <LoadingState />
        ) : alerts && alerts.length > 0 ? (
          <>
            <ul className="space-y-3">
              {pageAlerts.map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between rounded-lg border border-border bg-surface p-4"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-fg">
                        {a.symbol} · {a.indicator} {a.operator} {a.threshold}
                      </p>
                      <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>
                        {STATUS_LABEL[a.status] ?? a.status}
                      </Badge>
                    </div>
                    <p className="text-sm text-fg-muted">
                      {exchangeLabel(a.exchange)} · {a.timeframe} ·{" "}
                      {new Date(a.createdAt).toLocaleString()}
                    </p>
                    {a.status === "TRIGGERED" && (
                      <p className="tabular text-sm text-success">
                        발동값 {a.triggeredValue} (
                        {a.triggeredAt &&
                          new Date(a.triggeredAt).toLocaleString()}
                        )
                      </p>
                    )}
                  </div>
                  {a.status === "ACTIVE" && (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      loading={cancelAlert.isPending}
                      onClick={() => cancelAlert.mutate(a.id)}
                    >
                      취소
                    </Button>
                  )}
                </li>
              ))}
            </ul>
            <Pagination state={pageState} onPageChange={goToPage} />
          </>
        ) : (
          <EmptyState>등록된 알림이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}

import { ApiError, createSignalsClient, SignalsRouteNotImplementedError, type SignalsClient } from "@aios/api-client";
import { routeApiError, type SignalReceiptResponse, type SignalSourceResponse } from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Badge, Button, Card, CardTitle, EmptyState, Field, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { useTranslation } from "react-i18next";

// spec L4_analytics_authoring_backtest_marketplace_v1.0.md §9.1 SIG-6(source line
// 264) — SignalSourcesPage.tsx(시크릿 발급·회전·최근 수신 로그). 선행 리프
// SIG-1~5(src/foundation/signals/*, src/api/routers/signals.py, PLT-33 시크릿
// 회전)는 아직 없다(apiRoutes.ts signals.sources.* implemented=false 참조) — 이
// 화면은 FollowPage.tsx(UX-15)와 동일 관용으로, 라우터가 없어도 화면·상태 처리
// (로딩/오류/빈 목록/유령 경로 단락)는 완성해 두고 signalsClient prop 주입으로
// 테스트한다. 라우터가 생기면 apiRoutes.ts의 implemented만 true로 바꾸면 그대로
// 배선된다.
export interface SignalSourcesPageProps {
  signalsClient?: SignalsClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultSignalsClient(): SignalsClient {
  const getToken = () => useAuthStore.getState().token;
  return createSignalsClient(baseUrl, getToken);
}

// FollowPage.tsx의 FollowErrorBanner와 동일 관용 — 유령 경로 오류
// (SignalsRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage로 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가 없다는 사실은
// 재시도로 안 바뀐다).
function SignalsErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const notImplemented = error instanceof SignalsRouteNotImplementedError;
  const routed = routeApiError(error);
  const canRetry = !notImplemented && (routed.kind === "refetch_retry" || routed.kind === "backoff_retry");
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

// 발급·회전 직후 1회만 노출되는 시크릿 원문. 화면 상태(useState)에만 머물고
// 어떤 쿼리 캐시에도 실리지 않는다 — listSources 응답에는 애초에 secretPreview
// (마스킹)만 있으므로, 이 배너를 닫으면(또는 다른 소스를 발급/회전하면) 원문은
// 다시 볼 방법이 없다(shared-types/signals.ts SignalSourceSecretIssueResponse
// 주석과 동일 불변조건).
interface RevealedSecret {
  sourceName: string;
  plaintextSecret: string;
}

function SecretRevealBanner({ revealed, onDismiss }: { revealed: RevealedSecret; onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <div data-testid="signal-secret-reveal">
      <Alert tone="warning">
        <p>{t("legacy.signalSourcesPage.t1", { sourceName: revealed.sourceName })}</p>
        <p className="mt-2 break-all font-mono text-sm" data-testid="signal-secret-plaintext">
          {revealed.plaintextSecret}
        </p>
        <p className="mt-2 text-sm text-fg-muted">{t("legacy.signalSourcesPage.t2")}</p>
        <Button type="button" size="sm" className="mt-2" onClick={onDismiss}>
          {t("legacy.signalSourcesPage.t3")}
        </Button>
      </Alert>
    </div>
  );
}

function ReceiptLogPanel({ client, sourceId }: { client: SignalsClient; sourceId: number }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["signal-receipts", sourceId],
    queryFn: () => client.listReceipts(sourceId),
  });

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <SignalsErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  const items: SignalReceiptResponse[] = query.data?.items ?? [];
  if (items.length === 0) {
    return (
      <div data-testid="signal-receipts-empty">
        <EmptyState>{t("legacy.signalSourcesPage.t4")}</EmptyState>
      </div>
    );
  }
  return (
    <ul className="divide-y divide-border" data-testid={`signal-receipts-${sourceId}`}>
      {items.map((receipt) => (
        <li key={receipt.id} className="flex items-center justify-between py-1 text-sm">
          <span className="text-fg-muted">{new Date(receipt.receivedAt).toLocaleString()}</span>
          <span>{receipt.symbol}</span>
          <Badge tone={receipt.status === "ACCEPTED" ? "success" : "danger"}>{receipt.status}</Badge>
        </li>
      ))}
    </ul>
  );
}

function SourceCard({
  source,
  expanded,
  onToggleReceipts,
  onRotate,
  rotating,
  client,
}: {
  source: SignalSourceResponse;
  expanded: boolean;
  onToggleReceipts: () => void;
  onRotate: () => void;
  rotating: boolean;
  client: SignalsClient;
}) {
  const { t } = useTranslation();
  return (
    <Card data-testid={`signal-source-${source.id}`}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <CardTitle>{source.name}</CardTitle>
            <Badge tone={source.status === "ACTIVE" ? "success" : "neutral"}>{source.status}</Badge>
          </div>
          <p className="text-sm text-fg-muted">{source.secretPreview}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onToggleReceipts}>
            {expanded ? t("legacy.signalSourcesPage.t5") : t("legacy.signalSourcesPage.t6")}
          </Button>
          <Button type="button" variant="secondary" size="sm" disabled={rotating} onClick={onRotate}>
            {t("legacy.signalSourcesPage.t7")}
          </Button>
        </div>
      </div>
      {expanded && (
        <div className="mt-4 border-t border-border pt-4">
          <ReceiptLogPanel client={client} sourceId={source.id} />
        </div>
      )}
    </Card>
  );
}

export function SignalSourcesPage({ signalsClient }: SignalSourcesPageProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const client = useMemo(() => signalsClient ?? defaultSignalsClient(), [signalsClient]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [rotatingId, setRotatingId] = useState<number | null>(null);
  const [newSourceName, setNewSourceName] = useState("");
  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const query = useQuery({
    queryKey: ["signal-sources"],
    queryFn: () => client.listSources(),
  });

  async function handleIssue() {
    setActionError(null);
    const name = newSourceName.trim();
    if (name === "") return;
    try {
      const issued = await client.issueSource({ name });
      setRevealed({ sourceName: issued.source.name, plaintextSecret: issued.plaintextSecret });
      setNewSourceName("");
      await queryClient.invalidateQueries({ queryKey: ["signal-sources"] });
    } catch (err) {
      setActionError(err);
    }
  }

  async function handleRotate(source: SignalSourceResponse) {
    setActionError(null);
    setRotatingId(source.id);
    try {
      const rotated = await client.rotateSecret(source.id);
      setRevealed({ sourceName: rotated.source.name, plaintextSecret: rotated.plaintextSecret });
      await queryClient.invalidateQueries({ queryKey: ["signal-sources"] });
    } catch (err) {
      setActionError(err);
    } finally {
      setRotatingId(null);
    }
  }

  const items = query.data?.items ?? [];

  return (
    <AppShell>
      <div className="max-w-3xl space-y-6">
        <PageHeader title={t("legacy.signalSourcesPage.title8")} />

        <Card>
          <Field label={t("legacy.signalSourcesPage.label9")}>
            <Input
              value={newSourceName}
              onChange={(e) => setNewSourceName(e.target.value)}
              placeholder={t("legacy.signalSourcesPage.placeholder10")}
            />
          </Field>
          <Button type="button" className="mt-2" disabled={newSourceName.trim() === ""} onClick={handleIssue}>
            {t("legacy.signalSourcesPage.t11")}
          </Button>
        </Card>

        {revealed && <SecretRevealBanner revealed={revealed} onDismiss={() => setRevealed(null)} />}

        {query.isError && <SignalsErrorBanner error={query.error} onRetry={() => query.refetch()} />}
        {actionError !== null && <SignalsErrorBanner error={actionError} />}

        {!query.isError && query.isLoading && <LoadingState />}
        {!query.isError && !query.isLoading && items.length === 0 && (
          <div data-testid="signal-sources-empty">
            <EmptyState>{t("legacy.signalSourcesPage.t12")}</EmptyState>
          </div>
        )}
        {!query.isError && !query.isLoading && items.length > 0 && (
          <div className="space-y-4">
            {items.map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                expanded={expandedId === source.id}
                onToggleReceipts={() => setExpandedId((id) => (id === source.id ? null : source.id))}
                onRotate={() => handleRotate(source)}
                rotating={rotatingId === source.id}
                client={client}
              />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}

import { ApiError, createFollowClient, FollowRouteNotImplementedError, type FollowClient } from "@aios/api-client";
import {
  findAdvisoryLanguageViolations,
  NOT_INVESTMENT_ADVICE_DISCLAIMER_KO,
  routeApiError,
  type FollowSizingPolicy,
  type FollowSubscriptionResponse,
} from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Badge, Button, Card, CardTitle, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";

// spec L4_product_experience_and_discovery_v1.0.md UX-15 — FollowPage.tsx(팔로우
// 관리·성과 비교). 선행 리프 UX-13/UX-14(src/foundation/follow/*)와 이를 감싸는
// API 라우터(src/api/routers/follow.py)는 아직 없다(apiRoutes.ts follow.subscriptions.*
// implemented=false 참조) — 이 화면은 SweepResultsPage.tsx(BT-18)·SessionsPage.tsx
// (task-1325)와 동일 관용으로, 라우터가 없어도 화면·상태 처리(로딩/오류/빈 목록/
// 유령 경로 단락)는 완성해 두고 followClient prop 주입으로 테스트한다. 라우터가
// 생기면 apiRoutes.ts의 implemented만 true로 바꾸면 그대로 배선된다.
export interface FollowPageProps {
  followClient?: FollowClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultFollowClient(): FollowClient {
  const getToken = () => useAuthStore.getState().token;
  return createFollowClient(baseUrl, getToken);
}

const SIZING_POLICY_LABEL: Record<FollowSizingPolicy, string> = {
  MIRROR_PERCENTAGE: "비중 그대로 복제",
  FIXED_NOTIONAL: "고정 금액",
};

const STATUS_TONE: Record<FollowSubscriptionResponse["status"], "success" | "neutral" | "danger"> = {
  ACTIVE: "success",
  PAUSED: "neutral",
  CANCELLED: "danger",
};

// SweepResultsPage.tsx(BT-18)의 SweepErrorBanner와 동일 관용 — 유령 경로 오류
// (FollowRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage(message= prop, errorSurface.guard.test.ts task-1048 승인 경로)로
// 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가 없다는 사실은 재시도로 안 바뀐다).
function FollowErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const notImplemented = error instanceof FollowRouteNotImplementedError;
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

function PerformancePanel({ client, subscriptionId }: { client: FollowClient; subscriptionId: number }) {
  const query = useQuery({
    queryKey: ["follow-performance", subscriptionId],
    queryFn: () => client.getPerformanceComparison(subscriptionId),
  });

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <FollowErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  if (!query.data || query.data.points.length === 0) {
    return <p className="text-sm text-fg-muted">아직 비교할 성과 데이터가 없습니다.</p>;
  }
  return (
    <div className="space-y-2 text-sm">
      <p className="text-fg-muted">
        추적 오차 <span className="tabular text-fg">{query.data.trackingDifferencePct}%</span>
      </p>
      <ul className="divide-y divide-border">
        {query.data.points.map((point) => (
          <li key={point.asOf} className="flex items-center justify-between py-1">
            <span className="text-fg-muted">{new Date(point.asOf).toLocaleDateString()}</span>
            <span className="tabular">
              원본 {point.sourceReturnPct}% · 팔로워 {point.followerReturnPct}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SubscriptionCard({
  subscription,
  expanded,
  onToggle,
  onCancel,
  cancelling,
  client,
}: {
  subscription: FollowSubscriptionResponse;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  cancelling: boolean;
  client: FollowClient;
}) {
  const cancellable = subscription.status !== "CANCELLED";
  return (
    <Card data-testid={`follow-subscription-${subscription.id}`}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <CardTitle>리스팅 #{subscription.sourceListingId}</CardTitle>
            <Badge tone={STATUS_TONE[subscription.status]}>{subscription.status}</Badge>
            <Badge tone="accent">PAPER</Badge>
          </div>
          <p className="text-sm text-fg-muted">
            {SIZING_POLICY_LABEL[subscription.sizingPolicy]} · 최대 {subscription.maxNotional}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onToggle}>
            {expanded ? "성과 숨기기" : "성과 비교"}
          </Button>
          {cancellable && (
            <Button type="button" variant="danger" size="sm" disabled={cancelling} onClick={onCancel}>
              해지
            </Button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="mt-4 border-t border-border pt-4">
          <PerformancePanel client={client} subscriptionId={subscription.id} />
        </div>
      )}
    </Card>
  );
}

export function FollowPage({ followClient }: FollowPageProps) {
  const queryClient = useQueryClient();
  const client = useMemo(() => followClient ?? defaultFollowClient(), [followClient]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const query = useQuery({
    queryKey: ["follow-subscriptions"],
    queryFn: () => client.listSubscriptions(),
  });

  async function handleCancel(subscriptionId: number) {
    setActionError(null);
    setCancellingId(subscriptionId);
    try {
      await client.cancelSubscription(subscriptionId);
      await queryClient.invalidateQueries({ queryKey: ["follow-subscriptions"] });
    } catch (err) {
      setActionError(err);
    } finally {
      setCancellingId(null);
    }
  }

  const items = query.data?.items ?? [];

  return (
    <AppShell>
      <div className="max-w-3xl space-y-6">
        <PageHeader title="팔로우 관리" />
        <div data-testid="follow-advisory-disclaimer">
          <Alert tone="warning">{NOT_INVESTMENT_ADVICE_DISCLAIMER_KO}</Alert>
        </div>

        {query.isError && <FollowErrorBanner error={query.error} onRetry={() => query.refetch()} />}
        {actionError !== null && <FollowErrorBanner error={actionError} />}

        {!query.isError && query.isLoading && <LoadingState />}
        {!query.isError && !query.isLoading && items.length === 0 && (
          <EmptyState>팔로우 중인 전략이 없습니다.</EmptyState>
        )}
        {!query.isError && !query.isLoading && items.length > 0 && (
          <div className="space-y-4">
            {items.map((subscription) => (
              <SubscriptionCard
                key={subscription.id}
                subscription={subscription}
                expanded={expandedId === subscription.id}
                onToggle={() => setExpandedId((id) => (id === subscription.id ? null : subscription.id))}
                onCancel={() => handleCancel(subscription.id)}
                cancelling={cancellingId === subscription.id}
                client={client}
              />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}

// UX-15 "자문 오인 표현 금지 검수"(spec 109행) — 화면 코드 안의 사용자 노출
// 리터럴을 findAdvisoryLanguageViolations로 감사한다. FollowPage.test.tsx가 실제
// 렌더된 DOM 텍스트에 대해서도 같은 함수를 돌려 이중으로 검수한다(정적 문자열
// 감사 하나만으로는 JSX 밖 조합 문자열을 놓칠 수 있으므로).
export const FOLLOW_PAGE_USER_FACING_STRINGS: readonly string[] = [
  NOT_INVESTMENT_ADVICE_DISCLAIMER_KO,
  "팔로우 관리",
  "팔로우 중인 전략이 없습니다.",
  "성과 비교",
  "성과 숨기기",
  "해지",
  "비중 그대로 복제",
  "고정 금액",
  "추적 오차",
  "아직 비교할 성과 데이터가 없습니다.",
];

export function auditFollowPageAdvisoryLanguage(): string[] {
  return FOLLOW_PAGE_USER_FACING_STRINGS.flatMap((text) => findAdvisoryLanguageViolations(text));
}

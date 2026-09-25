import {
  ApiError,
  createWhatIfClient,
  WhatIfRouteNotImplementedError,
  type WhatIfClient,
} from "@aios/api-client";
import {
  describeWhatIfOrderIssues,
  routeApiError,
  type WhatIfOrderInput,
  type WhatIfOrderSide,
  type WhatIfOrderValidationIssue,
} from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Button, Card, CardTitle, Field, Input, LoadingState, Select } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ErrorMessage } from "../../components/ErrorMessage";

// spec L4_product_experience_and_discovery_v1.0.md UX-12 — WhatIfPanel.tsx(가상
// 주문 영향 미리보기). 선행 리프 UX-9/UX-10(src/foundation/whatif/{domain/impact,
// application/preview_order}.py)와 이를 감싸는 API 라우터(src/api/routers/whatif.py)는
// 아직 없다(apiRoutes.ts의 whatif.previewOrder implemented=false 참조) —
// ScreenerPage.tsx(UX-8)·FollowPage.tsx(UX-15)와 동일 관용으로, 라우터가 없어도
// 화면·상태 처리(로딩/오류/검증/유령 경로 단락)는 완성해 두고 whatIfClient prop
// 주입으로 테스트한다. 라우터가 생기면 apiRoutes.ts의 implemented만 true로 바꾸면
// 그대로 배선된다.
//
// preview_order.py(UX-10)가 "읽기 전용: 주문·이벤트 생성 없음"을 계약하므로 이 패널도
// 실제 주문을 내지 않는다 — 입력한 가상 주문을 서버로 보내 영향(노출·집중도·VaR·한도
// 여유 델타, UX-9 domain/impact.py)만 조회한다.
//
// RebalancePage.tsx가 생성한 거래 초안을 미리보기할 때는 initialOrder로 그 거래를
// 채워 넣는다 — 호출부가 selection이 바뀔 때마다 새 key를 줘서(RebalancePage.tsx
// 참조) 이 컴포넌트를 리마운트하는 관용을 쓴다(effect로 내부 상태를 외부 prop과
// 동기화하지 않는다).
export interface WhatIfPanelProps {
  whatIfClient?: WhatIfClient;
  initialOrder?: WhatIfOrderInput;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultWhatIfClient(): WhatIfClient {
  const getToken = () => useAuthStore.getState().token;
  return createWhatIfClient(baseUrl, getToken);
}

// ScreenerPage.tsx(UX-8)의 ScreenerErrorBanner와 동일 관용 — 유령 경로 오류
// (WhatIfRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage로 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가 없다는 사실은
// 재시도로 안 바뀐다).
function WhatIfErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notImplemented = error instanceof WhatIfRouteNotImplementedError;
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

function orderIssueMessage(t: TFunction<"translation", undefined>, issue: WhatIfOrderValidationIssue): string {
  switch (issue.code) {
    case "symbol_required":
      return t("whatif.orderValidation.symbolRequired");
    case "quantity_invalid":
      return t("whatif.orderValidation.quantityInvalid");
  }
}

export function WhatIfPanel({ whatIfClient, initialOrder }: WhatIfPanelProps) {
  const { t } = useTranslation();
  const client = useMemo(() => whatIfClient ?? defaultWhatIfClient(), [whatIfClient]);

  const [symbol, setSymbol] = useState(initialOrder?.symbol ?? "");
  const [side, setSide] = useState<WhatIfOrderSide>(initialOrder?.side ?? "BUY");
  const [quantity, setQuantity] = useState(initialOrder?.quantity ?? "");
  const [validationIssues, setValidationIssues] = useState<WhatIfOrderValidationIssue[]>([]);
  const [submitted, setSubmitted] = useState<WhatIfOrderInput | null>(initialOrder ?? null);

  const query = useQuery({
    queryKey: ["whatif-preview-order", submitted],
    queryFn: () => client.previewOrder(submitted as WhatIfOrderInput),
    enabled: submitted !== null,
  });

  function handleRun() {
    const order: WhatIfOrderInput = { symbol, side, quantity };
    const issues = describeWhatIfOrderIssues(order);
    setValidationIssues(issues);
    if (issues.length > 0) return;
    setSubmitted(order);
  }

  return (
    <Card data-testid="whatif-panel">
      <CardTitle>{t("whatif.order.title")}</CardTitle>
      <div className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <Field label={t("whatif.order.symbolLabel")}>
            <Input data-testid="whatif-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </Field>
          <Field label={t("whatif.order.sideLabel")}>
            <Select
              data-testid="whatif-side"
              value={side}
              onChange={(e) => setSide(e.target.value as WhatIfOrderSide)}
            >
              <option value="BUY">{t("whatif.order.side.buy")}</option>
              <option value="SELL">{t("whatif.order.side.sell")}</option>
            </Select>
          </Field>
          <Field label={t("whatif.order.quantityLabel")}>
            <Input data-testid="whatif-quantity" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </Field>
        </div>

        {validationIssues.length > 0 && (
          <div data-testid="whatif-validation-alert">
            <Alert tone="danger">
              <ul className="list-disc space-y-1 pl-4">
                {validationIssues.map((issue, i) => (
                  <li key={i}>{orderIssueMessage(t, issue)}</li>
                ))}
              </ul>
            </Alert>
          </div>
        )}

        <Button type="button" data-testid="whatif-run" onClick={handleRun}>
          {t("whatif.order.run")}
        </Button>

        {submitted === null && <p className="text-sm text-fg-muted">{t("whatif.order.beforeRun")}</p>}
        {submitted !== null && query.isLoading && <LoadingState />}
        {submitted !== null && query.isError && (
          <WhatIfErrorBanner error={query.error} onRetry={() => query.refetch()} />
        )}
        {submitted !== null && !query.isError && !query.isLoading && query.data && (
          <dl data-testid="whatif-impact-result" className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-fg-muted">{t("whatif.impact.exposureDeltaLabel")}</dt>
              <dd className="tabular" data-testid="whatif-impact-exposure">
                {query.data.exposureDeltaPct}%
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">{t("whatif.impact.concentrationDeltaLabel")}</dt>
              <dd className="tabular" data-testid="whatif-impact-concentration">
                {query.data.concentrationDeltaPct}%
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">{t("whatif.impact.varDeltaLabel")}</dt>
              <dd className="tabular" data-testid="whatif-impact-var">
                {query.data.varDelta}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">{t("whatif.impact.limitHeadroomDeltaLabel")}</dt>
              <dd className="tabular" data-testid="whatif-impact-limit-headroom">
                {query.data.limitHeadroomDelta}
              </dd>
            </div>
          </dl>
        )}
      </div>
    </Card>
  );
}

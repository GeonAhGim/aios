import { useCreateListing, useMyStrategies } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, EmptyState, Field, Input, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useFieldErrors } from "../../hooks/useFieldErrors";
import { useTranslation } from "react-i18next";

function strategyKey(strategyId: string, version: string): string {
  return `${strategyId}@${version}`;
}

// spec §3.3 에러 taxonomy: 리스팅 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
//
// task-954: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// BadRequestNotice가 자체적으로 null을 렌더한다(task-364 설계) — 그래서 지금까지
// 이 경로는 배너도 인라인도 없이 완전히 조용했다. fieldErrors를 ErrorMessage에
// 넘겨 계약(비어있지 않으면 배너 생략)을 지키고, 실제 표시는 아래 입력 옆
// Field.error로 한다.
function CreateListingError({ error, fieldErrors }: { error: unknown; fieldErrors: Record<string, string> }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

export function SellStrategyPage() {
  const { t } = useTranslation();
  const { data: strategies, isLoading } = useMyStrategies();
  const [selectedKey, setSelectedKey] = useState("");
  const [price, setPrice] = useState("10.00");
  const [clientError, setClientError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const createListing = useCreateListing();
  const navigate = useNavigate();
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  useEffect(() => {
    if (strategies && strategies.length > 0 && !selectedKey) {
      setSelectedKey(strategyKey(strategies[0].strategyId, strategies[0].version));
    }
  }, [strategies, selectedKey]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setClientError(null);
    setError(null);
    setFromError(null);
    const [strategyId, strategyVersion] = selectedKey.split("@");
    if (!strategyId || !strategyVersion) {
      setClientError(t("legacy.sellStrategyPage.t7"));
      return;
    }
    try {
      const listing = await createListing.mutateAsync({
        strategyId,
        strategyVersion,
        price,
      });
      navigate(`/marketplace/${listing.id}`, { state: { listing } });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error(t("legacy.sellStrategyPage.t8")));
      setFromError(err);
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title={t("legacy.sellStrategyPage.title1")} />
        {isLoading ? (
          <LoadingState />
        ) : !strategies || strategies.length === 0 ? (
          <EmptyState>
            {t("legacy.sellStrategyPage.t2", { val: " " })}
            <Link to="/strategy-builder" className="text-accent-hover hover:underline">
              {t("legacy.sellStrategyPage.t3")}</Link>
            .
          </EmptyState>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="space-y-3 rounded-lg border border-border bg-surface p-6"
          >
            <Field label={t("legacy.sellStrategyPage.label4")} error={fieldErrors.strategy_id ?? fieldErrors.strategy_version}>
              <Select
                value={selectedKey}
                onChange={(e) => {
                  setSelectedKey(e.target.value);
                  clearField("strategy_id");
                  clearField("strategy_version");
                }}
              >
                {strategies.map((s) => (
                  <option key={strategyKey(s.strategyId, s.version)} value={strategyKey(s.strategyId, s.version)}>
                    {s.strategyId}@{s.version} ({s.lifecycleStatus})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("legacy.sellStrategyPage.label5")} error={fieldErrors.price}>
              <Input
                type="number"
                step="0.01"
                value={price}
                onChange={(e) => {
                  setPrice(e.target.value);
                  clearField("price");
                }}
              />
            </Field>
            {clientError && <Alert>{clientError}</Alert>}
            {error !== null && <CreateListingError error={error} fieldErrors={fieldErrors} />}
            <Button type="submit" loading={createListing.isPending} className="w-full">
              {t("legacy.sellStrategyPage.t6")}</Button>
          </form>
        )}
      </div>
    </AppShell>
  );
}

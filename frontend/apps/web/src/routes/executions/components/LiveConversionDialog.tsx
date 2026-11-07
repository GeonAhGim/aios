import { useLiveConversion, useLiveReadiness } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  type ExecutionCardResponse,
  type LiveReadinessResponse,
} from "@aios/shared-types";
import { Alert, Button, Field, Input, LoadingState, useDialogFocusTrap } from "@aios/ui-web";
import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BadRequestNotice } from "../../../components/BadRequestNotice";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { ForbiddenNotice } from "../../../components/ForbiddenNotice";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../../hooks/useIdempotentSubmit";

// J8 LP-5: 모의 -> 실전 전환 화면. 1단계에서 모의 운용 실적(LP-2)을 보고 손실 한도·초기 노출을 정하고,
// 2단계에서 요약을 다시 확인하며 비밀번호로 재인증한 뒤 전환한다(LP-3). 실적이 없는 항목은
// 0이나 빈칸이 아니라 "알 수 없음"과 사유를 그대로 보여 준다 -- 없는 데이터를 좋은 성과로 보이게 하지 않는다.
const TITLE_ID = "live-conversion-title";

interface LiveConversionDialogProps {
  execution: ExecutionCardResponse;
  onClose: () => void;
}

function toNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

function formatPercent(ratio: string): string {
  return `${(Number(ratio) * 100).toFixed(1)}%`;
}

function formatMoney(value: number): string {
  return value.toFixed(2);
}

function SubmitError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
    />
  );
}

function EvidenceRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-fg-muted">{label}</dt>
      <dd className="tabular">{value}</dd>
    </>
  );
}

function EvidenceTable({ data }: { data: LiveReadinessResponse }) {
  const { t } = useTranslation();
  // 값이 null이면 서버가 준 사유(unavailable)와 함께 "알 수 없음"으로 보여 준다.
  const unknown = (key: string) => {
    const reason = data.unavailable[key];
    return reason
      ? t("liveConversion.unknownWithReason", { reason })
      : t("liveConversion.unknown");
  };
  const ratio = (value: string | null, key: string) =>
    value === null ? unknown(key) : formatPercent(value);
  const fees =
    data.feeTotals === null
      ? unknown("fee_totals")
      : Object.entries(data.feeTotals)
          .map(([currency, amount]) => `${amount} ${currency}`)
          .join(", ") || "0";
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      <EvidenceRow
        label={t("liveConversion.period")}
        value={
          data.periodDays === null
            ? unknown("period")
            : t("liveConversion.periodDays", { days: Number(data.periodDays).toFixed(1) })
        }
      />
      <EvidenceRow
        label={t("liveConversion.orders")}
        value={t("liveConversion.ordersValue", {
          total: data.orderCount,
          filled: data.filledCount,
          rejected: data.rejectedCount,
        })}
      />
      <EvidenceRow label={t("liveConversion.fillRatio")} value={ratio(data.fillRatio, "fill_ratio")} />
      <EvidenceRow
        label={t("liveConversion.partialFillRatio")}
        value={ratio(data.partialFillRatio, "partial_fill_ratio")}
      />
      <EvidenceRow
        label={t("liveConversion.unfilledRatio")}
        value={ratio(data.unfilledRatio, "unfilled_ratio")}
      />
      <EvidenceRow
        label={t("liveConversion.rejectRatio")}
        value={ratio(data.rejectRatio, "reject_ratio")}
      />
      <EvidenceRow
        label={t("liveConversion.slippage")}
        value={
          data.avgSlippageBps === null
            ? unknown("avg_slippage_bps")
            : t("liveConversion.slippageValue", {
                bps: data.avgSlippageBps,
                n: data.slippageSampleSize,
              })
        }
      />
      <EvidenceRow label={t("liveConversion.fees")} value={fees} />
      <EvidenceRow
        label={t("liveConversion.netPnl")}
        value={data.netPnl === null ? unknown("net_pnl") : data.netPnl}
      />
      <EvidenceRow
        label={t("liveConversion.recoveryEvents")}
        value={
          data.recoveryEventCount === null
            ? unknown("recovery_event_count")
            : t("liveConversion.recoveryEventsValue", { n: data.recoveryEventCount })
        }
      />
    </dl>
  );
}

export function LiveConversionDialog({ execution, onClose }: LiveConversionDialogProps) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocusTrap({ isOpen: true, onClose, containerRef: dialogRef });

  const readiness = useLiveReadiness(execution.executionId);
  const conversion = useLiveConversion();
  const { submit } = useIdempotentSubmit(`executions.liveConversion:${execution.executionId}`);

  const [step, setStep] = useState<"input" | "confirm">("input");
  const [target, setTarget] = useState(execution.allocatedCapital);
  const [maxLoss, setMaxLoss] = useState("");
  const [exposurePct, setExposurePct] = useState("10");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const targetNum = toNumber(target);
  const maxLossNum = toNumber(maxLoss);
  const exposureNum = toNumber(exposurePct);
  const openedCapital =
    targetNum !== null && exposureNum !== null ? (targetNum * exposureNum) / 100 : null;

  function validate(): string | null {
    if (targetNum === null || targetNum <= 0) return t("liveConversion.invalidTarget");
    if (maxLossNum === null || maxLossNum <= 0 || maxLossNum > targetNum) {
      return t("liveConversion.invalidMaxLoss");
    }
    if (exposureNum === null || exposureNum < 1 || exposureNum > 100) {
      return t("liveConversion.invalidExposure");
    }
    return null;
  }

  function handleReview(e: FormEvent) {
    e.preventDefault();
    const problem = validate();
    setValidation(problem);
    if (problem === null) setStep("confirm");
  }

  async function handleConvert(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await submit((idempotencyKey) =>
        conversion.mutateAsync({
          executionId: execution.executionId,
          idempotencyKey,
          body: {
            allocatedCapital: target.trim(),
            currency: "USDT",
            exchange: execution.exchange,
            maxLossAmount: maxLoss.trim(),
            initialExposurePct: exposurePct.trim(),
            password,
            totpCode: totpCode.trim() === "" ? null : totpCode.trim(),
          },
        }),
      );
      setPassword("");
      setTotpCode("");
      onClose();
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      // 비밀번호·인증 코드는 실패 뒤에도 남겨 두지 않는다.
      setPassword("");
      setTotpCode("");
      setError(err);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto rounded-xl border border-warning/30 bg-surface p-6"
      >
        <h2 id={TITLE_ID} className="text-lg font-semibold text-fg">
          {t("liveConversion.title")} · {execution.strategyId}
        </h2>
        <Alert tone="warning">{t("liveConversion.guardNotice")}</Alert>

        {step === "input" ? (
          <form noValidate onSubmit={handleReview} className="space-y-4">
            <section className="space-y-2">
              <h3 className="text-sm font-medium text-fg">{t("liveConversion.evidenceTitle")}</h3>
              {readiness.isLoading ? (
                <LoadingState />
              ) : readiness.isError || !readiness.data ? (
                <Alert tone="danger">{t("liveConversion.evidenceError")}</Alert>
              ) : (
                <EvidenceTable data={readiness.data} />
              )}
            </section>

            <p className="text-xs text-fg-muted">{t("liveConversion.realMoneyNotice")}</p>
            <div className="grid grid-cols-2 gap-4">
              <Field label={t("liveConversion.targetCapital")}>
                <Input
                  type="number"
                  required
                  min="0"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                />
              </Field>
              <Field label={t("liveConversion.maxLoss")}>
                <Input
                  type="number"
                  required
                  min="0"
                  value={maxLoss}
                  onChange={(e) => setMaxLoss(e.target.value)}
                />
              </Field>
              <Field label={t("liveConversion.initialExposure")}>
                <Input
                  type="number"
                  required
                  min="1"
                  max="100"
                  value={exposurePct}
                  onChange={(e) => setExposurePct(e.target.value)}
                />
              </Field>
              <div className="text-sm">
                <p className="text-fg-muted">{t("liveConversion.openedCapital")}</p>
                <p className="tabular text-fg">
                  {openedCapital === null
                    ? t("liveConversion.unknown")
                    : t("liveConversion.openedCapitalValue", { amount: formatMoney(openedCapital) })}
                </p>
              </div>
            </div>
            {validation !== null && <Alert tone="danger">{validation}</Alert>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                {t("liveConversion.cancel")}
              </Button>
              <Button type="submit">{t("liveConversion.toConfirm")}</Button>
            </div>
          </form>
        ) : (
          <form onSubmit={(e) => void handleConvert(e)} className="space-y-4">
            <h3 className="text-sm font-medium text-fg">{t("liveConversion.confirmTitle")}</h3>
            <p className="text-sm text-fg">
              {t("liveConversion.confirmSummary", {
                strategy: execution.strategyId,
                exchange: execution.exchange,
                target: target.trim(),
                opened: openedCapital === null ? "-" : formatMoney(openedCapital),
                maxLoss: maxLoss.trim(),
              })}
            </p>
            <p className="text-xs text-fg-muted">{t("liveConversion.reauthHint")}</p>
            <Field label={t("liveConversion.password")}>
              <Input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Field label={t("liveConversion.totp")}>
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
              />
            </Field>
            {error !== null && <SubmitError error={error} />}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setStep("input")}>
                {t("liveConversion.back")}
              </Button>
              <Button
                type="submit"
                loading={conversion.isPending}
                className="!bg-warning !text-slate-950 hover:!bg-warning/90"
              >
                {t("liveConversion.submit")}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

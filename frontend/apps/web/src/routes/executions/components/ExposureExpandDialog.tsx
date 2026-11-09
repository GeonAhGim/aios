import { useExpandExposure } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  type ExecutionCardResponse,
} from "@aios/shared-types";
import { Alert, Button, Field, Input, useDialogFocusTrap } from "@aios/ui-web";
import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BadRequestNotice } from "../../../components/BadRequestNotice";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { ForbiddenNotice } from "../../../components/ForbiddenNotice";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../../hooks/useIdempotentSubmit";

// J8 LP-4: 허용 자본 확대(노출 비율만 높임). 재인증(비밀번호+TOTP) 필수.
// 축소 불가 — 줄이려면 실행 일시정지.
const TITLE_ID = "exposure-expand-title";

interface ExposureExpandDialogProps {
  execution: ExecutionCardResponse;
  onClose: () => void;
}

function toNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
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

/**
 * 노출 확대 대화상자.
 *
 * - guardNotice: 현재 실전 주문 안전 가드를 알려준다.
 * - 현재 허용 자본(execution.allocatedCapital)을 보여주고, 새 노출(%)을 받는다.
 * - 범위(1~100) 밖이면 invalidExposure를 표시하고 요청을 보내지 않는다.
 * - onlyIncrease 안내를 보여준다.
 * - 비밀번호/TOTP 재인증을 받는다.
 * - 성공하면 비밀번호를 비우고 onClose. 실패하면 비밀번호·TOTP를 비우고 닫지 않는다.
 */
export function ExposureExpandDialog({ execution, onClose }: ExposureExpandDialogProps) {
  const { t } = useTranslation();
  const { submit } = useIdempotentSubmit(`exposure-expand:${execution.executionId}`);
  const expand = useExpandExposure();
  const trapRef = useRef<HTMLDivElement>(null);

  useDialogFocusTrap({
    isOpen: true,
    onClose,
    containerRef: trapRef,
  });

  const [exposurePct, setExposurePct] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<unknown | null>(null);

  const allocatedCapital = execution.allocatedCapital;

  const exposureNum = toNumber(exposurePct);
  const exposureOutOfRange =
    exposureNum !== null && (exposureNum < 1 || exposureNum > 100);
  const exposureEmpty = exposurePct.trim() === "";
  const submitDisabled = exposureEmpty || exposureOutOfRange || !password;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitDisabled) return;

    setError(null);
    try {
      await submit((_idempotencyKey) =>
        expand.mutateAsync({
          executionId: execution.executionId,
          body: {
            exposurePct: exposurePct.trim(),
            currency: "USDT",
            exchange: execution.exchange,
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
      // 서버 거부: 비밀번호·TOTP를 비우고 오류를 보이며 닫지 않는다.
      if (err instanceof ApiError && err.statusCode >= 400 && err.statusCode < 500) {
        setPassword("");
        setTotpCode("");
      }
      setError(err);
    }
  }

  return (
    <div
      ref={trapRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-labelledby={TITLE_ID}
        className="w-full max-w-md rounded-lg bg-surface p-6 shadow-xl"
      >
        <div className="flex items-center justify-between">
          <h2 id={TITLE_ID} className="text-lg font-semibold text-fg">
            {t("exposureExpand.title")}
          </h2>
          <Button size="sm" variant="ghost" onClick={onClose}>
            {t("exposureExpand.cancel")}
          </Button>
        </div>

        <div className="mt-4 space-y-4">
          <Alert tone="warning">{t("exposureExpand.guardNotice")}</Alert>

          <div className="flex items-baseline justify-between text-sm">
            <span className="text-fg-muted">{t("exposureExpand.currentCapital")}</span>
            <span className="tabular">
              {t("exposureExpand.currentCapitalValue", { amount: allocatedCapital })}
            </span>
          </div>

          <form onSubmit={handleSubmit} noValidate>
            <Field label={t("exposureExpand.newExposure")}>
              <Input
                type="number"
                inputMode="numeric"
                min="1"
                max="100"
                value={exposurePct}
                onChange={(e) => setExposurePct(e.target.value)}
                placeholder="1~100"
              />
            </Field>
            {exposureOutOfRange && (
              <p className="mt-1 text-xs text-danger">
                {t("exposureExpand.invalidExposure")}
              </p>
            )}
            <p className="mt-1 text-xs text-fg-muted">
              {t("exposureExpand.onlyIncrease")}
            </p>

            <Field label={t("exposureExpand.password")}>
              <Input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Field label={t("exposureExpand.totp")}>
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
              />
            </Field>
            <p className="text-xs text-fg-muted">
              {t("exposureExpand.reauthHint")}
            </p>

            {error !== null && <SubmitError error={error} />}

            <div className="mt-4 flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                {t("exposureExpand.cancel")}
              </Button>
              <Button
                type="submit"
                loading={expand.isPending}
                disabled={submitDisabled}
                className="!bg-warning !text-slate-950 hover:!bg-warning/90"
              >
                {t("exposureExpand.submit")}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

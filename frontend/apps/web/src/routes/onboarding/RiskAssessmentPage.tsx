import { useSubmitRiskAssessment } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  type InvestmentGoal,
  type LiquidityNeed,
} from "@aios/shared-types";
import { Button, Field, Input, Select } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useTranslation } from "react-i18next";

// F-3(task-10642, UX_JOURNEYS.md §6 J1 사용감 소견): 제출 시점에 세션이 만료(401
// AUTH_TOKEN_EXPIRED)되면 재로그인 후 돌아와도 5개 필드를 처음부터 다시 입력해야
// 했다. 평가 응답(비밀값이 아니다 — 금지 사항은 secret/passphrase 저장만)만
// sessionStorage에 임시로 담아두고, 같은 탭에서 돌아오면 복원한다. 제출 성공 시
// 즉시 지운다.
const DRAFT_STORAGE_KEY = "aios.riskAssessment.draft.v1";

interface RiskAssessmentDraft {
  yearsOfExperience: number;
  investableRatioPct: number;
  lossTolerancePct: number;
  investmentGoal: InvestmentGoal;
  liquidityNeed: LiquidityNeed;
}

function readDraft(): RiskAssessmentDraft | null {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as RiskAssessmentDraft;
  } catch {
    return null;
  }
}

function writeDraft(draft: RiskAssessmentDraft) {
  try {
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // sessionStorage 불가(사생활 보호 모드 등)해도 폼 자체는 계속 쓸 수 있어야 한다 — 무시.
  }
}

function clearDraft() {
  try {
    window.sessionStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // no-op: 지우기 실패는 치명적이지 않다.
  }
}

// spec §3.3/§3.4: 제출 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정한다. 이 화면은 FD-15.1 필수 게이트라 작성
// 시간이 길어지기 쉬워, 그 사이 액세스 토큰이 만료되면 401
// AUTH_TOKEN_EXPIRED로 거부될 수 있다 — isSessionExpiredErrorCode(task-354)가
// 이미 잡는 갈래를 ErrorMessage(errorCode 매핑)로 그대로 보여준다(task-902).
function SubmitError({ error }: { error: unknown }) {
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

// FD-15.1 필수 게이트 — 회원가입 직후(MFA 완료 후) 스킵 불가.
export function RiskAssessmentPage() {
  const { t } = useTranslation();
  const initialDraft = readDraft();
  const [yearsOfExperience, setYearsOfExperience] = useState(initialDraft?.yearsOfExperience ?? 0);
  const [investableRatioPct, setInvestableRatioPct] = useState(initialDraft?.investableRatioPct ?? 10);
  const [lossTolerancePct, setLossTolerancePct] = useState(initialDraft?.lossTolerancePct ?? 10);
  const [investmentGoal, setInvestmentGoal] = useState<InvestmentGoal>(
    initialDraft?.investmentGoal ?? "LONG_TERM_GROWTH",
  );
  const [liquidityNeed, setLiquidityNeed] = useState<LiquidityNeed>(
    initialDraft?.liquidityNeed ?? "WITHIN_1_YEAR",
  );
  const [error, setError] = useState<unknown>(null);
  const [restoredFromDraft] = useState(initialDraft !== null);
  const submit = useSubmitRiskAssessment();
  const navigate = useNavigate();

  useEffect(() => {
    writeDraft({
      yearsOfExperience,
      investableRatioPct,
      lossTolerancePct,
      investmentGoal,
      liquidityNeed,
    });
  }, [yearsOfExperience, investableRatioPct, lossTolerancePct, investmentGoal, liquidityNeed]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await submit.mutateAsync({
        yearsOfExperience,
        investableRatioPct,
        lossTolerancePct,
        investmentGoal,
        liquidityNeed,
      });
      clearDraft();
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error(t("legacy.riskAssessmentPage.t12")));
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 py-12">
      <div className="w-full max-w-lg">
        <Link
          to="/onboarding/mfa-setup"
          className="mb-4 inline-block text-sm text-accent-hover hover:underline"
        >
          {t("legacy.riskAssessmentPage.backLink")}
        </Link>
        {restoredFromDraft && (
          <p role="status" className="mb-4 text-sm text-fg-muted">
            {t("legacy.riskAssessmentPage.restoredNotice")}
          </p>
        )}
        <div className="mb-6 text-center">
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-lg font-bold text-bg">
            A
          </span>
          <h1 className="text-xl font-semibold text-fg">{t("legacy.riskAssessmentPage.t1")}</h1>
          <p className="mt-1 text-sm text-fg-muted">
            {t("legacy.riskAssessmentPage.t2")}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-border bg-surface p-6">
          <Field label={t("legacy.riskAssessmentPage.label3")}>
            <Input
              type="number"
              min={0}
              required
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(Number(e.target.value))}
            />
          </Field>

          <Field label={`순자산 대비 투자 가능 비중 — ${investableRatioPct}%`}>
            <input
              type="range"
              min={0}
              max={100}
              value={investableRatioPct}
              onChange={(e) => setInvestableRatioPct(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </Field>

          <Field label={`원금 대비 감내 가능한 손실 수준 — ${lossTolerancePct}%`}>
            <input
              type="range"
              min={0}
              max={100}
              value={lossTolerancePct}
              onChange={(e) => setLossTolerancePct(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </Field>

          <Field label={t("legacy.riskAssessmentPage.label4")}>
            <Select
              value={investmentGoal}
              onChange={(e) => setInvestmentGoal(e.target.value as InvestmentGoal)}
            >
              <option value="SHORT_TERM_PROFIT">{t("legacy.riskAssessmentPage.t5")}</option>
              <option value="LONG_TERM_GROWTH">{t("legacy.riskAssessmentPage.t6")}</option>
            </Select>
          </Field>

          <Field label={t("legacy.riskAssessmentPage.label7")}>
            <Select
              value={liquidityNeed}
              onChange={(e) => setLiquidityNeed(e.target.value as LiquidityNeed)}
            >
              <option value="WITHIN_1_YEAR">{t("legacy.riskAssessmentPage.t8")}</option>
              <option value="1_TO_3_YEARS">{t("legacy.riskAssessmentPage.t9")}</option>
              <option value="OVER_3_YEARS">{t("legacy.riskAssessmentPage.t10")}</option>
            </Select>
          </Field>

          {error !== null && <SubmitError error={error} />}
          <Button type="submit" loading={submit.isPending} className="w-full">
            {t("legacy.riskAssessmentPage.t11")}</Button>
        </form>
      </div>
    </div>
  );
}

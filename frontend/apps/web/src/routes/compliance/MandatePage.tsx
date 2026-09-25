import {
  useActivateMandateRevision,
  useCreateMandateDraft,
  useMandateStatus,
  usePauseMandate,
  useProposeMandateAmendment,
  useResumeMandate,
} from "@aios/shared-hooks";
import { classifyStateConflict } from "@aios/shared-types";
import type { MandateRevisionView, MandateRuleInput } from "@aios/shared-types";
import { Badge, Button, Card, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { MandateActionError } from "../mandates/MandateActionError";
import { MandateRuleForm } from "../mandates/MandateRuleForm";

// task-6320(CM-19): 컴플라이언스 섹션의 위임장 편집·승인 화면. ComplianceMandateStatusCard
// (CM-17)는 조회 전용이고, 편집·승인(초안/개정 제출, activate, pause/resume)은 이
// 파일의 몫이라고 그 파일 상단 주석이 명시한다. GET /mandates/status는 테넌트당
// active/pending 리비전 각 최대 1건만 주므로, DoD(1) "목록"은 그 둘을 배열로 모아
// 렌더한다 — 없으면 빈 상태(negative)를 보여준다.
function MandateListItem({ label, revision }: { label: string; revision: MandateRevisionView }) {
  const { t } = useTranslation();
  return (
    <li className="border-b border-border py-3 last:border-b-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-fg">{label}</span>
        <StatusBadge status={revision.state} />
      </div>
      <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-fg-muted">
        <li>{t("legacy.mandatesPage.t1", { maxTotalExposurePct: revision.maxTotalExposurePct })}</li>
        <li>{t("legacy.mandatesPage.t2", { maxSingleInstrumentPct: revision.maxSingleInstrumentPct })}</li>
        <li>{t("legacy.mandatesPage.t3", { minCashBufferPct: revision.minCashBufferPct })}</li>
        <li>{t("legacy.mandatesPage.t4", { maxDailyLossPct: revision.maxDailyLossPct })}</li>
        <li>{t("legacy.mandatesPage.t5", { allowedAutonomy: revision.allowedAutonomy })}</li>
        <li>
          {t("legacy.mandatesPage.t6")}
          {revision.forbiddenAssets.length > 0 ? revision.forbiddenAssets.join(", ") : t("mandatePage.noneLabel")}
        </li>
      </ul>
    </li>
  );
}

export function MandatePage() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useMandateStatus();
  const createDraft = useCreateMandateDraft();
  const proposeAmendment = useProposeMandateAmendment();
  const activate = useActivateMandateRevision();
  const pause = usePauseMandate();
  const resume = useResumeMandate();

  const [createError, setCreateError] = useState<unknown>(null);
  const [activateError, setActivateError] = useState<unknown>(null);
  const [lifecycleError, setLifecycleError] = useState<unknown>(null);

  function handleCreate(rules: MandateRuleInput) {
    setCreateError(null);
    const mutation = data?.activeRevision ? proposeAmendment : createDraft;
    mutation.mutate(rules, { onError: setCreateError });
  }

  function handleActivate(revisionId: string) {
    setActivateError(null);
    activate.mutate(
      { revisionId },
      {
        onError: (err) => {
          setActivateError(err);
          // 동시 activate 경합(409 STATE_CONCURRENCY_CONFLICT)은 재조회로 최신
          // activeRevision/pendingRevision을 다시 반영한다(MandatesPage DoD c와 동일).
          if (classifyStateConflict(err) === "refetch_retry") refetch();
        },
      },
    );
  }

  // DoD(3) 교차 테넌트 404 동형: 다른 테넌트의 위임장을 조회했을 때 서버가 돌려주는
  // 404도, 그 외 상태 조회 실패(예: 500)와 동일하게 MandateActionError 한 경로로만
  // 렌더한다 — "다른 테넌트 소유"임을 구분해 보여주는 별도 분기를 두지 않는다.
  if (isError) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title={t("mandatePage.pageTitle")} />
          <MandateActionError error={error} onRetry={() => refetch()} />
        </div>
      </AppShell>
    );
  }

  if (isLoading || !data) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title={t("mandatePage.pageTitle")} />
          <LoadingState />
        </div>
      </AppShell>
    );
  }

  const { activeRevision, pendingRevision } = data;
  const listItems: { label: string; revision: MandateRevisionView }[] = [];
  if (activeRevision) listItems.push({ label: t("mandatePage.activeLabel"), revision: activeRevision });
  if (pendingRevision) listItems.push({ label: t("mandatePage.pendingLabel"), revision: pendingRevision });

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("mandatePage.pageTitle")} />

        <Card>
          <div className="flex items-center gap-2">
            <h2 className="font-medium text-fg">{t("mandatePage.listTitle")}</h2>
            {!activeRevision && <Badge tone="warning">{t("legacy.mandatesPage.t8")}</Badge>}
          </div>
          {listItems.length === 0 ? (
            <p className="mt-2 text-sm text-fg-muted">{t("mandatePage.listEmpty")}</p>
          ) : (
            <ul className="mt-2">
              {listItems.map((item) => (
                <MandateListItem key={item.revision.id} label={item.label} revision={item.revision} />
              ))}
            </ul>
          )}
        </Card>

        {activeRevision && (activeRevision.state === "ACTIVE" || activeRevision.state === "PAUSED") && (
          <Card>
            <h2 className="font-medium text-fg">{t("legacy.mandatesPage.t7")}</h2>
            {activeRevision.state === "ACTIVE" && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={pause.isPending}
                onClick={() => pause.mutate(undefined, { onError: setLifecycleError })}
              >
                {t("legacy.mandatesPage.t9")}
              </Button>
            )}
            {activeRevision.state === "PAUSED" && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={resume.isPending}
                onClick={() => resume.mutate(undefined, { onError: setLifecycleError })}
              >
                {t("legacy.mandatesPage.t10")}
              </Button>
            )}
            {lifecycleError !== null && (
              <div className="mt-2">
                <MandateActionError error={lifecycleError} />
              </div>
            )}
          </Card>
        )}

        <Card>
          <h2 className="font-medium text-fg">{t("legacy.mandatesPage.t15")}</h2>
          {pendingRevision ? (
            <>
              <div className="mt-3">
                <Button type="button" loading={activate.isPending} onClick={() => handleActivate(pendingRevision.id)}>
                  {t("legacy.mandatesPage.t16")}
                </Button>
              </div>
              {/* DoD(2): 승인(activate) 실패는 조용히 삼키지 않고 거부 사유를 노출한다
                  — 버튼은 비활성화하지 않는다(CM-5 작성자=승인자 400은 MandatesPage와
                  같은 계약). */}
              {activateError !== null && (
                <div className="mt-2">
                  <MandateActionError error={activateError} onRetry={() => refetch()} />
                </div>
              )}
            </>
          ) : (
            <div className="mt-3">
              <MandateRuleForm
                submitLabel={activeRevision ? t("mandatePage.amendSubmitLabel") : t("mandatePage.draftSubmitLabel")}
                pending={createDraft.isPending || proposeAmendment.isPending}
                onSubmit={handleCreate}
              />
              {/* DoD(2): 초안/개정안 제출 실패도 동일하게 화면에 노출한다(무음 실패 금지). */}
              {createError !== null && (
                <div className="mt-2">
                  <MandateActionError error={createError} />
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

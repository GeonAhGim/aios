import { createAiClient, type AiClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, PageHeader } from "@aios/ui-web";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { ExperimentsSection } from "./AiExperiments";
import { ProposalsSection } from "./AiProposals";
import { ProviderSettingsSection } from "./AiProviderSettings";
import { TokensSection } from "./AiTokens";

// spec L4_ai_research_strategy_factory_v1.0.md AI-22 — AiStudioPage.tsx(공급자
// 설정·토큰·제안 목록·실험 비교·승격 버튼 확인 흐름). 선행 리프 AI-1~16(gateway·
// providers·factory·experiments)과 이를 감싸는 API 라우터(src/api/routers/ai.py,
// AI-17)는 아직 없다(apiRoutes.ts ai.* implemented=false 참조) — 이 화면은
// FollowPage.tsx(UX-15)·SweepResultsPage.tsx(BT-18)와 동일 관용으로, 라우터가
// 없어도 화면·상태 처리(로딩/오류/빈 목록/유령 경로 단락/확인 흐름)는 완성해
// 두고 aiClient prop 주입으로 테스트한다. 라우터가 생기면 apiRoutes.ts의
// implemented만 true로 바꾸면 그대로 배선된다. 4개 섹션(AiProviderSettings/
// AiTokens/AiProposals/AiExperiments)으로 나눈 이유는 ADR-2026-09-10-C(500줄
// 경고) — 이 파일은 조립만 한다. 사용자 노출 문구는 전부 catalog.ko.ts의 ai.* 키를
// t()로 조회한다(check_i18n_literals.mjs 리터럴 게이트, task-2685).
export interface AiStudioPageProps {
  aiClient?: AiClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultAiClient(): AiClient {
  const getToken = () => useAuthStore.getState().token;
  return createAiClient(baseUrl, getToken);
}

export function AiStudioPage({ aiClient }: AiStudioPageProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const client = useMemo(() => aiClient ?? defaultAiClient(), [aiClient]);

  function refreshExperiments() {
    queryClient.invalidateQueries({ queryKey: ["ai-experiments"] });
  }

  return (
    <AppShell>
      <div className="max-w-4xl space-y-8">
        <PageHeader title={t("ai.pageTitle")} />

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-fg-secondary">{t("ai.sections.providers")}</h2>
          <ProviderSettingsSection client={client} />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-fg-secondary">{t("ai.sections.tokens")}</h2>
          <TokensSection client={client} />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-fg-secondary">{t("ai.sections.proposals")}</h2>
          <Alert tone="warning">{t("ai.proposals.disclaimer")}</Alert>
          <ProposalsSection client={client} onPromoted={refreshExperiments} />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-fg-secondary">{t("ai.sections.experiments")}</h2>
          <ExperimentsSection client={client} />
        </section>
      </div>
    </AppShell>
  );
}

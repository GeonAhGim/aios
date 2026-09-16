import { Button, PageHeader } from "@aios/ui-web";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { DEMO_INSTRUMENTS } from "./demoDataset";

// task-2632(U-10): 데모 모드 진입점. 실 연결 없이 고정 샘플 종목 3개 중 하나를
// 골라 DemoChartPage로 이동한다 — "첫 백테스트까지 5분" DoD의 1단계.
export function DemoModePage() {
  const { t } = useTranslation();

  return (
    <AppShell>
      <div className="max-w-2xl space-y-6">
        <PageHeader title={t("demoMode.pageTitle")} />
        <p className="text-sm text-fg-muted">{t("demoMode.description")}</p>
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-fg-secondary">{t("demoMode.instrumentListLabel")}</h2>
          <ul className="space-y-2">
            {DEMO_INSTRUMENTS.map((instrument) => (
              <li
                key={instrument.id}
                className="flex items-center justify-between rounded-lg border border-border bg-surface p-4"
              >
                <p className="font-medium text-fg">
                  {instrument.label}{" "}
                  <span className="text-xs font-normal text-fg-muted">({t("demoMode.sampleTag")})</span>
                </p>
                <Link to={`/onboarding/demo/${instrument.id}`}>
                  <Button type="button" size="sm" data-testid={`demo-start-${instrument.id}`}>
                    {t("demoMode.startCta")}
                  </Button>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </AppShell>
  );
}

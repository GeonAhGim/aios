import type { AiClient } from "@aios/api-client";
import type { AiProviderSettingView } from "@aios/shared-types";
import { Button, Card, CardTitle, EmptyState, Field, Input, LoadingState } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AiErrorBanner } from "./AiErrorBanner";
import { PROVIDER_NAME_KEY } from "./aiShared";

// task-2657(AI-22): AiStudioPage.tsx "공급자 설정" 섹션 — provider별 사용 여부·
// 일일 비용 상한(§2.2 domain/budget.py)을 인라인으로 편집한다.
function ProviderRow({
  setting,
  client,
  onUpdated,
}: {
  setting: AiProviderSettingView;
  client: AiClient;
  onUpdated: () => void;
}) {
  const { t } = useTranslation();
  const [budget, setBudget] = useState(setting.dailyBudgetUsd);
  const [enabled, setEnabled] = useState(setting.enabled);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      await client.updateProviderSetting(setting.provider, { enabled, dailyBudgetUsd: budget });
      onUpdated();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card data-testid={`ai-provider-${setting.provider}`}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <CardTitle>{t(PROVIDER_NAME_KEY[setting.provider])}</CardTitle>
          <label className="flex items-center gap-2 text-sm text-fg-muted">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="accent-accent"
            />
            {t("ai.providers.enabledLabel")}
          </label>
        </div>
        <div className="flex items-end gap-2">
          <Field label={t("ai.providers.dailyBudgetLabel")}>
            <Input value={budget} onChange={(e) => setBudget(e.target.value)} className="w-32" />
          </Field>
          <Button type="button" size="sm" loading={saving} onClick={handleSave}>
            {t("common.save")}
          </Button>
        </div>
      </div>
      {error !== null && <AiErrorBanner error={error} />}
    </Card>
  );
}

export function ProviderSettingsSection({ client }: { client: AiClient }) {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ["ai-providers"], queryFn: () => client.listProviderSettings() });
  const items = query.data ?? [];

  if (query.isError) return <AiErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  if (query.isLoading) return <LoadingState />;
  if (items.length === 0) return <EmptyState>{t("ai.providers.empty")}</EmptyState>;

  return (
    <div className="space-y-3">
      {items.map((setting) => (
        <ProviderRow key={setting.provider} setting={setting} client={client} onUpdated={() => query.refetch()} />
      ))}
    </div>
  );
}

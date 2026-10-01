import { Button, Card, CardTitle, Field, Input, Select } from "@aios/ui-web";
import type { FormEvent } from "react";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { RegisterCredentialError } from "./ExchangeCredentialErrors";
import { useTranslation } from "react-i18next";

const EXCHANGES = ["bitget", "kis"];

export function RegisterCredentialForm({
  exchange,
  apiKey,
  apiSecret,
  apiPassphrase,
  fieldErrors,
  error,
  secretsCleared,
  isPending,
  onExchangeChange,
  onApiKeyChange,
  onApiSecretChange,
  onApiPassphraseChange,
  onSubmit,
  onRetry,
}: {
  exchange: string;
  apiKey: string;
  apiSecret: string;
  apiPassphrase: string;
  fieldErrors: Record<string, string>;
  error: unknown;
  // F-5(task-10642): 등록 실패 시 submitRegistration이 보안상 Secret/Passphrase를
  // 지운다(의도적 동작, 유지) — 그 이유를 모르면 매번 "왜 비었지" 하고 다시 타이핑해야
  // 한다. error와 함께 true로 넘어오면 비운 이유를 배너로 설명한다.
  secretsCleared: boolean;
  isPending: boolean;
  onExchangeChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onApiSecretChange: (value: string) => void;
  onApiPassphraseChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Card className="max-w-lg">
      <CardTitle>{t("legacy.registerCredentialForm.t1")}</CardTitle>
      <form onSubmit={onSubmit} className="space-y-3">
        <Field label={t("legacy.registerCredentialForm.label2")} error={fieldErrors.exchange}>
          <Select value={exchange} onChange={(e) => onExchangeChange(e.target.value)}>
            {EXCHANGES.map((ex) => (
              <option key={ex} value={ex}>
                {exchangeLabel(ex)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="API Key" error={fieldErrors.api_key}>
          <Input
            type="password"
            required
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
        </Field>
        <Field label="API Secret" error={fieldErrors.api_secret}>
          <Input
            type="password"
            required
            value={apiSecret}
            onChange={(e) => onApiSecretChange(e.target.value)}
          />
        </Field>
        {exchange === "bitget" && (
          <Field label="API Passphrase" error={fieldErrors.api_passphrase}>
            <Input
              type="password"
              required
              value={apiPassphrase}
              onChange={(e) => onApiPassphraseChange(e.target.value)}
            />
          </Field>
        )}
        {secretsCleared && (
          <p role="status" className="text-sm text-fg-muted">
            {t("legacy.registerCredentialForm.secretsClearedNotice")}
          </p>
        )}
        {error !== null && (
          <RegisterCredentialError error={error} onRetry={onRetry} fieldErrors={fieldErrors} />
        )}
        <Button type="submit" loading={isPending} className="w-full">
          {t("legacy.registerCredentialForm.t3")}</Button>
      </form>
    </Card>
  );
}

import { Button, Card, CardTitle, Field, Input, Select } from "@aios/ui-web";
import type { FormEvent } from "react";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { RegisterCredentialError } from "./ExchangeCredentialErrors";

const EXCHANGES = ["bitget", "kis"];

export function RegisterCredentialForm({
  exchange,
  apiKey,
  apiSecret,
  apiPassphrase,
  fieldErrors,
  error,
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
  isPending: boolean;
  onExchangeChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onApiSecretChange: (value: string) => void;
  onApiPassphraseChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  onRetry: () => void;
}) {
  return (
    <Card className="max-w-lg">
      <CardTitle>새 거래소 연동</CardTitle>
      <form onSubmit={onSubmit} className="space-y-3">
        <Field label="거래소" error={fieldErrors.exchange}>
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
        {error !== null && (
          <RegisterCredentialError error={error} onRetry={onRetry} fieldErrors={fieldErrors} />
        )}
        <Button type="submit" loading={isPending} className="w-full">
          등록
        </Button>
      </form>
    </Card>
  );
}

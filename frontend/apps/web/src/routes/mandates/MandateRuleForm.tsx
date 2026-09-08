import type { MandateAutonomy, MandateRuleInput } from "@aios/shared-types";
import { Button, Field, Input, Select } from "@aios/ui-web";
import { useState } from "react";

// task-2336(FE-OPS-2): MandatesPage에서 분리(P6 300줄 상한) — 초안 작성(drafts)과
// 개정안 제안(amendments) 양쪽에서 재사용하는 규칙 입력 폼. 필드는 서버
// MandateRuleInput(75번 §3 6개 규칙) 1:1이다 — decision: "프론트에서 규칙 의미를
// 재구현하지 않는다(판정은 서버 권위, CM-A5)".
const AUTONOMY_OPTIONS: MandateAutonomy[] = ["OBSERVE", "PAPER", "LIMITED_LIVE"];

function emptyRuleInput(): MandateRuleInput {
  return {
    maxTotalExposurePct: 50,
    maxSingleInstrumentPct: 20,
    minCashBufferPct: 5,
    maxDailyLossPct: 3,
    allowedAutonomy: "OBSERVE",
    forbiddenAssets: [],
  };
}

export function MandateRuleForm({
  submitLabel,
  pending,
  onSubmit,
}: {
  submitLabel: string;
  pending: boolean;
  onSubmit: (rules: MandateRuleInput) => void;
}) {
  const [rules, setRules] = useState<MandateRuleInput>(emptyRuleInput());
  const [forbiddenAssetsText, setForbiddenAssetsText] = useState("");

  function field<K extends keyof MandateRuleInput>(key: K, value: MandateRuleInput[K]) {
    setRules((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit() {
    onSubmit({
      ...rules,
      forbiddenAssets: forbiddenAssetsText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    });
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="총 노출 한도(%)">
          <Input
            type="number"
            value={rules.maxTotalExposurePct}
            onChange={(e) => field("maxTotalExposurePct", Number(e.target.value))}
          />
        </Field>
        <Field label="단일 종목 한도(%)">
          <Input
            type="number"
            value={rules.maxSingleInstrumentPct}
            onChange={(e) => field("maxSingleInstrumentPct", Number(e.target.value))}
          />
        </Field>
        <Field label="최소 현금 버퍼(%)">
          <Input
            type="number"
            value={rules.minCashBufferPct}
            onChange={(e) => field("minCashBufferPct", Number(e.target.value))}
          />
        </Field>
        <Field label="일일 손실 한도(%)">
          <Input
            type="number"
            value={rules.maxDailyLossPct}
            onChange={(e) => field("maxDailyLossPct", Number(e.target.value))}
          />
        </Field>
        <Field label="허용 자율성">
          <Select
            value={rules.allowedAutonomy}
            onChange={(e) => field("allowedAutonomy", e.target.value as MandateAutonomy)}
          >
            {AUTONOMY_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="금지 자산(쉼표 구분)">
          <Input
            type="text"
            value={forbiddenAssetsText}
            onChange={(e) => setForbiddenAssetsText(e.target.value)}
            placeholder="BTC, ETH"
          />
        </Field>
      </div>
      <Button type="button" loading={pending} onClick={handleSubmit}>
        {submitLabel}
      </Button>
    </div>
  );
}

import { Button, Card, Field, Input } from "@aios/ui-web";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { usePositionsClient, type PositionsClientLike } from "../../hooks/usePositions";
import { PositionJournalPanel } from "../portfolio/PositionJournalPanel";

// task-2706 UX-22: spec L4_product_experience_and_discovery_v1.0.md §9 UX-22
// ("주문...데이터 계보를 이벤트에서 역추적해 표시"). pos_journal(FA-14/LB-19,
// PositionJournalPanel task-1524)을 그대로 재사용한다 — 새 파서·새 집계 없음
// (UX-A5와 동형: 서버가 이미 내려주는 저널 항목을 raw로 보여줄 뿐 해석하지
// 않는다, PositionJournalPanel.tsx 상단 주석 참고). 주문(order_events)·재생
// (FA-15 replay) 전용 조회 API는 아직 라우터가 없어(조사 완료, src/api/routers에
// 없음) 이 리프 범위 밖 — 이 패널이 새로 더하는 건 position_key를 손으로 입력해
// 조회를 시작하는 진입점 하나뿐이다(PortfolioPositionsLive는 포지션 목록에서
// 자동으로 키를 넘기지만, 결정 이력 뷰어는 임의의 포지션을 역추적 대상으로
// 받는다). key={submitted}로 PositionJournalPanel을 강제 재마운트해 이전 조회의
// 열림 상태·페이지 상태가 새 키로 넘어가지 않게 한다(state 유출 방지).
export interface EventLineageLookupPanelProps {
  client?: Pick<PositionsClientLike, "getPositionJournal">;
}

export function EventLineageLookupPanel({ client: injected }: EventLineageLookupPanelProps) {
  const { t } = useTranslation();
  // usePositionsClient()는 항상 무조건 호출한다(Hooks 규칙) — injected가 있으면
  // 이 기본 클라이언트는 그냥 버려진다(비용은 객체 생성 1회뿐, 네트워크 호출 없음).
  const defaultClient = usePositionsClient();
  const client = injected ?? defaultClient;
  const [positionKey, setPositionKey] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  function handleLookup() {
    const trimmed = positionKey.trim();
    if (trimmed === "") {
      setValidationError(t("decisions.eventLineage.validationEmpty"));
      setSubmitted(null);
      return;
    }
    setValidationError(null);
    setSubmitted(trimmed);
  }

  return (
    <Card>
      <h2 className="font-medium text-fg">{t("decisions.eventLineage.heading")}</h2>
      <div className="mt-3 flex items-end gap-2">
        <Field label={t("decisions.eventLineage.label")}>
          <Input type="text" value={positionKey} onChange={(e) => setPositionKey(e.target.value)} />
        </Field>
        <Button type="button" variant="secondary" onClick={handleLookup}>
          {t("decisions.eventLineage.button")}
        </Button>
      </div>

      {validationError !== null && <p className="mt-2 text-sm text-danger">{validationError}</p>}

      {submitted !== null && (
        <div className="mt-3">
          <PositionJournalPanel key={submitted} client={client} positionKey={submitted} />
        </div>
      )}
    </Card>
  );
}

# 거래소 추가 파이프라인 (BR-18)

Spec 근거: ADR-2026-09-06-I("브로커 우선, 100% 커버리지"), ADR-2026-09-24-A
Decision 5. 규범은 `docs/design/02_exchange_adapter_v1.3.md`,
`docs/specs/L4_execution_oms_and_exchange_v1.0.md` §1(R10)·§2-A/§2-B/§2-F,
`docs/design/INVARIANTS.md` I-02·I-03이다 — 이 문서는 그 규범을 기계 검증
가능한 절차로 정리한 가이드일 뿐, 상충하면 스펙/INVARIANTS가 이긴다.

새 거래소를 붙일 때, 다음 다섯 단계를 순서대로 밟는다.

## 1. SPI 구현 — `ExchangeAdapter`

새 어댑터는 `src/exchanges/common/adapter.py`의 `ExchangeAdapter(ABC)`를
상속한다. SPI는 두 그룹으로 나뉜다.

- **추상 메서드/프로퍼티(14개)** — 반드시 구현해야 인스턴스화가 된다
  (`is_paper_trading`, `is_sandboxed`, `get_capabilities`, `place_order`,
  `cancel_order`, `modify_order`, `get_order`, `get_balance`, `get_positions`,
  `get_ticker`, `get_orderbook`, `get_ohlcv`, `health_check`,
  `subscribe_ticker_stream`). 구현하지 않으면 `TypeError`로 즉시 실패한다
  (ABCMeta가 강제).
- **L4-13 확장 조회 기본구현(5개)** — `get_open_orders`, `get_fills`,
  `find_order_by_client_id`, `venue_profile`, `subscribe_order_stream`.
  ABC 기본 구현은 전부 `self._unsupported(name)`을 호출해
  `UnsupportedCapabilityError`(capability, adapter)를 던진다 — 이게
  **의도된 "나는 이거 지원 안 해" 신호**다. `NotImplementedError`나 조용한
  `[]`/`None` 반환은 안 된다(호출자가 "지원 안 함"과 "일시적 빈 응답"을
  구분 못 하게 된다). 지원하지 않는 확장 기능은 오버라이드하지 말고 ABC
  기본값 그대로 두거나, 명시적으로 `raise self._unsupported("...")`를
  다시 던지며 이유를 문서화한다(`PaperSimulatorAdapter.modify_order`가
  후자의 예).

SPI 전체는 `scripts/check_exchange_spi.py`의 `spi_surface()`가 ABC 자체에서
동적으로 나열한다 — 이 문서에 메서드 개수를 하드코딩하지 않는 이유다(ABC가
바뀌면 게이트도 같이 갱신된다).

## 2. Capability 선언 — `get_capabilities()` + `venue_profile()`

두 개의 서로 다른 capability 모델이 있다.

- `ExchangeCapability`(`src/exchanges/common/types.py`) — 자산군/기능의
  거친 선언(`supports_spot`/`supports_futures`/`supports_websocket` 등).
  `get_capabilities()`가 반환한다.
- `VenueCapabilityProfile`(`src/services/oms/domain/venue_profile.py`) —
  주문 경로에 필요한 정밀한 계약(`supports_ws_orders`,
  `supports_client_order_id`, `supports_modify`, `supports_cancel`,
  `supports_batch`, `price_tick`/`qty_lot`/`min_notional`, `rate_limits`,
  `verified: Literal["LIVE_VERIFIED","DOC_ONLY","ESTIMATED"]`). `venue_profile()`
  이 반환한다.

관례: venue별로 `src/exchanges/<venue>/venue_profile.py`에 `*_PROFILE`
상수(예: `BITGET_SPOT_PROFILE`)를 정의하고, 어댑터 클래스의
`venue_profile()` 메서드가 그 상수를 반환하도록 배선한다
(`src/exchanges/bitget/adapter.py`의 `venue_profile()` 참고). **상수만
정의하고 메서드 배선을 빼먹으면, 호출은 조용히 ABC 기본값
(`UnsupportedCapabilityError`)으로 떨어진다** — 이 문서 작성 시점에 KIS/
NH 두 어댑터가 실제로 이 결함을 갖고 있었고(`check_exchange_spi.py`가
잡아냄), BR-18에서 수정됐다. 새 거래소를 추가할 때 반드시
`scripts/check_exchange_spi.py`를 돌려 같은 실수를 반복하지 않는지
확인한다.

`get_capabilities()`와 `venue_profile()`이 선언하는 내용은 실제 구현과
반드시 일치해야 한다 — 예를 들어 `supports_ws_orders=True`라고 선언했으면
`subscribe_order_stream()`이 ABC 기본값이 아니라 실제 구현이어야 한다
(`check_exchange_spi.py`가 이 교차검증을 한다).

## 3. Factory 등록 — `register_exchange_adapter_factory`

기존 3개 거래소(bitget/kis/nh)는 `src/exchanges/factory.py`의
`build_adapter()` 안에서 `if exchange == "..."` 분기로 직접 생성된다.
**4번째 이후 거래소는 이 분기를 건드리지 않고** BR-9(task-1787,
ADR-2026-09-06-I D5)의 확장점으로 연다:

```python
from src.exchanges.factory import register_exchange_adapter_factory

def _my_exchange_factory(api_key, api_secret, extra, demo_mode):
    return MyExchangeAdapter(api_key, api_secret, extra, demo_mode=demo_mode)

register_exchange_adapter_factory("my_exchange", _my_exchange_factory)
```

중요한 불변식: 이 확장점은 LIVE 어댑터 fail-closed 가드
(`AIOS_ALLOW_LIVE_ADAPTER` 환경변수, 정확히 `"1"`이어야 함, truthy 강제
변환 없음) **뒤에** 있다 — 즉 새 거래소도 `demo_mode=False`로 열려면 다른
거래소와 똑같이 이 가드를 통과해야 한다(우회 경로가 아니다). 참고 테스트:
`tests/unit/exchanges/test_factory_spi_extension.py`.

`SUPPORTED_EXCHANGES` 튜플은 문서/에러 메시지용 목록이다 — BR-9 확장
거래소를 여기에 추가할지는 선택이며, 추가하지 않아도 `register_exchange_
adapter_factory`로 열린 거래소는 정상 동작한다.

## 4. 커버리지 매트릭스 + SPI 게이트

- `scripts/exchange_coverage_template.py` — 새 거래소용 커버리지 매트릭스
  스크립트 템플릿. 복사해서 venue 이름과 import만 바꾸면 그 거래소 하나에
  대해 `check_exchange_spi.py`와 동일한 검사를 단독으로 돌릴 수 있다.
- `scripts/check_exchange_spi.py` — 등록된 모든 실 어댑터(bitget/kis/nh,
  그리고 paper_sim 드라이런)를 한 번에 검사하는 CI 게이트. 로컬 CI의
  guards 단계에 배선한다(OPS-42: 신규 게이트는 처음엔 `STEP_MODE=warn` +
  baseline으로 등록하고, 기존 위반이 0건임을 확인한 뒤 hard failure로
  승격한다).

새 거래소를 추가하면 `scripts/check_exchange_spi.py`의 `adapter_matrix()`에
항목을 추가하고, 그 어댑터가 위반 없이 통과하는지 확인한다.

## 5. 계약 테스트 킷 + ratchet 파일

- **계약 테스트**: `tests/contract/exchanges/<venue>/`에 실제 거래소 응답
  스키마를 고정하는 테스트를 둔다(신규 필드 추가는 minor, 필드 제거·의미
  변경은 신규 버전 모듈 — `review-exchange` 스킬 체크리스트 #7). 프로덕션
  코드가 계약 밖 필드에 암묵 의존하면 안 된다.
- **ratchet 파일**: 아직 검증 못 한 외부 사실(문서화 안 된 엔드포인트,
  응답 형식 등)은 추측 구현 대신 `NotImplementedError`를 던지고 파일
  첫 20줄 안에 `# ratchet-allow: <이유>` 주석을 남긴다
  (`scripts/check_code_ratchets.py`/`check_consistency.py`가 강제).
  실계좌 확보 전까지 조사 못 한 항목(레버리지 배율 등)은 과장된 기본값
  대신 보수적 기본값을 쓰고 그 사유를 주석에 남긴다(KIS 어댑터의
  `max_leverage=Decimal("1")` 사례 참고).

## 6. 실거래/모의 분기 + LIVE 거절 관례

- 생성자는 `demo_mode`(또는 동등한 플래그)를 받아 실거래/모의 엔드포인트를
  분기한다(KIS의 `PAPER_BASE_URL`/`REAL_BASE_URL`이 그 예).
- `factory.build_adapter(..., demo_mode=False)`를 호출했는데
  `AIOS_ALLOW_LIVE_ADAPTER` 환경변수가 정확히 `"1"`이 아니면
  `FrozenZonePaperAdapterBlockedError`로 fail-closed 거부한다
  (`factory.py`의 `_assert_live_adapter_allowed`, ADR-2026-08-29-E). 이
  가드는 BR-9 확장 거래소를 포함해 **모든** 거래소에 적용된다 — 새 거래소
  라고 예외를 두지 않는다.
- `is_paper_trading`/`is_sandboxed` 프로퍼티는 실제 생성자 인자를 그대로
  노출해야 한다(레드팀 감사 2026-09-01-08) — 하드코딩된 `True`/`False`나
  별도 상태로 어긋나게 두지 않는다.

## 드라이런 검증 (BR-18 DoD)

`PaperSimulatorAdapter`를 "4번째/5번째 거래소"로 가정해 위 파이프라인이
실제로 기계 검증 가능한지 증명한다: paper는 정적 `*_PROFILE` 상수 대신
`src/exchanges/paper/venue_profile.py`의 `profile_for(reference)`로 참조
거래소 프로파일을 복제해 쓴다(`venue="paper_sim"`, `verified="ESTIMATED"`로
치환, 깊은 복사). `scripts/check_exchange_spi.py`는 `profile_for
(BITGET_SPOT_PROFILE)`을 대표 프로파일로 넘겨 paper_sim 어댑터에도 같은
검사 행렬을 돌린다 — 결과: 위반 0건(어댑터가 이미 `venue_profile()`을
올바르게 배선했고, `supports_ws_orders`가 참조와 함께 `False`로
복제되므로 `subscribe_order_stream` 미구현과 모순되지 않는다).

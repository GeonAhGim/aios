"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_futureoption 미착수 TR 청크 01.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost, _KISWsHost
from src.exchanges.kis.websocket_connection import (
    ConnectFn,
    MessageHandler,
    ReconnectHook,
    _connect,
    _run_kis_ws_subscription,
)
from src.exchanges.kis.websocket_mixin import (
    WS_PAPER_URL,
    WS_REAL_URL,
)
from src.exchanges.kis.websocket_parsing import _build_subscribe_message


class KISGeneratedOverseasFutureoption01Mixin(_KISRestHost, _KISWsHost):

    async def asking_price_hdfff010(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션]실시간시세 > 해외선물옵션 실시간호가[실시간-018] -- WS tr_id=HDFFF010.
        필수 파라미터: tr_key. 응답 필드: series_cd, recv_date, recv_time, prev_price,
        bid_qntt_1, bid_num_1, bid_price_1, ask_qntt_1, ask_num_1, ask_price_1, bid_qntt_2,
        bid_num_2, bid_price_2, ask_qntt_2, ask_num_2, ask_price_2, bid_qntt_3, bid_num_3,
        bid_price_3, ask_qntt_3, ask_num_3, ask_price_3, bid_qntt_4, bid_num_4, bid_price_4,
        ask_qntt_4, ask_num_4, ask_price_4, bid_qntt_5, bid_num_5, bid_price_5, ask_qntt_5,
        ask_num_5, ask_price_5, sttl_price.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "HDFFF010", tr_key
        )

        async def _on_frame(raw: str) -> None:
            await callback(raw)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            _on_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def ccnl_hdfff020(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션]실시간시세 > 해외선물옵션 실시간체결가[실시간-017] -- WS tr_id=HDFFF020.
        필수 파라미터: tr_key. 응답 필드: series_cd, bsns_date, mrkt_open_date, mrkt_open_time,
        mrkt_close_date, mrkt_close_time, prev_price, recv_date, recv_time, active_flag,
        last_price, last_qntt, prev_diff_price, prev_diff_rate, open_price, high_price,
        low_price, vol, prev_sign, quotsign, recv_time2, psttl_price, psttl_sign,
        psttl_diff_price, psttl_diff_rate.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "HDFFF020", tr_key
        )

        async def _on_frame(raw: str) -> None:
            await callback(raw)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            _on_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def order_notice_hdfff1c0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션]실시간시세 > 해외선물옵션 실시간주문내역통보[실시간-019] -- WS
        tr_id=HDFFF1C0. 필수 파라미터: tr_key. 응답 필드: acct_no, ord_dt, odno, orgn_ord_dt,
        orgn_odno, series, rvse_cncl_dvsn_cd, sll_buy_dvsn_cd, cplx_ord_dvsn_cd, prce_tp,
        fm_excg_rcit_dvsn_cd, ord_qty, fm_lmt_pric, fm_stop_ord_pric, tot_ccld_qty, tot_ccld_uv,
        ord_remq, fm_ord_grp_dt, ord_grp_stno, ord_dtl_dtime, oprt_dtl_dtime, work_empl,
        crcy_cd, lqd_yn, lqd_lmt_pric, lqd_stop_pric, trd_cond, term_ord_vald_dtime, spec_tp,
        ecis_rsvn_ord_yn, fuop_item_dvsn_cd, auto_ord_dvsn_cd.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "HDFFF1C0", tr_key
        )

        async def _on_frame(raw: str) -> None:
            await callback(raw)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            _on_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def ccnl_notice_hdfff2c0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션]실시간시세 > 해외선물옵션 실시간체결내역통보[실시간-020] -- WS
        tr_id=HDFFF2C0. 필수 파라미터: tr_key. 응답 필드: acct_no, ord_dt, odno, orgn_ord_dt,
        orgn_odno, series, rvse_cncl_dvsn_cd, sll_buy_dvsn_cd, cplx_ord_dvsn_cd, prce_tp,
        fm_excg_rcit_dvsn_cd, ord_qty, fm_lmt_pric, fm_stop_ord_pric, tot_ccld_qty, tot_ccld_uv,
        ord_remq, fm_ord_grp_dt, ord_grp_stno, ord_dtl_dtime, oprt_dtl_dtime, work_empl,
        ccld_dt, ccno, api_ccno, ccld_qty, fm_ccld_pric, crcy_cd, trst_fee, ord_mdia_online_yn,
        fm_ccld_amt, fuop_item_dvsn_cd.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "HDFFF2C0", tr_key
        )

        async def _on_frame(raw: str) -> None:
            await callback(raw)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            _on_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def investor_unpd_trend_hhddb95030000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 미결제추이 [해외선물-029] -- GET
        /uapi/overseas-futureoption/v1/quotations/investor-unpd-trend (tr_id=HHDDB95030000).
        필수: PROD_ISCD, BSOP_DATE, UPMU_GUBUN, CTS_KEY. 선택: 없음. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/investor-unpd-trend",
            "HHDDB95030000",
            params=params or {},
        )

    async def stock_detail_hhdfc55010100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물종목상세[v1_해외선물-008] -- GET
        /uapi/overseas-futureoption/v1/quotations/stock-detail (tr_id=HHDFC55010100). 필수:
        SRS_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/stock-detail",
            "HHDFC55010100",
            params=params or {},
        )

    async def weekly_ccnl_hhdfc55020000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 체결추이(주간)[해외선물-017] -- GET
        /uapi/overseas-futureoption/v1/quotations/weekly-ccnl (tr_id=HHDFC55020000). 필수:
        SRS_CD, EXCH_CD, START_DATE_TIME, CLOSE_DATE_TIME, QRY_TP, QRY_CNT, QRY_GAP, INDEX_KEY.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/weekly-ccnl",
            "HHDFC55020000",
            params=params or {},
        )

    async def daily_ccnl_hhdfc55020100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 체결추이(일간) [해외선물-018] -- GET
        /uapi/overseas-futureoption/v1/quotations/daily-ccnl (tr_id=HHDFC55020100). 필수:
        SRS_CD, EXCH_CD, START_DATE_TIME, CLOSE_DATE_TIME, QRY_TP, QRY_CNT, QRY_GAP, INDEX_KEY.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/daily-ccnl",
            "HHDFC55020100",
            params=params or {},
        )

    async def tick_ccnl_hhdfc55020200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 체결추이(틱)[해외선물-019] -- GET
        /uapi/overseas-futureoption/v1/quotations/tick-ccnl (tr_id=HHDFC55020200). 필수: SRS_CD,
        EXCH_CD, START_DATE_TIME, CLOSE_DATE_TIME, QRY_TP, QRY_CNT, QRY_GAP, INDEX_KEY. 선택:
        없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/tick-ccnl",
            "HHDFC55020200",
            params=params or {},
        )

    async def monthly_ccnl_hhdfc55020300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 체결추이(월간)[해외선물-020] -- GET
        /uapi/overseas-futureoption/v1/quotations/monthly-ccnl (tr_id=HHDFC55020300). 필수:
        SRS_CD, EXCH_CD, START_DATE_TIME, CLOSE_DATE_TIME, QRY_TP, QRY_CNT, QRY_GAP, INDEX_KEY.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/monthly-ccnl",
            "HHDFC55020300",
            params=params or {},
        )

    async def inquire_time_futurechartprice_hhdfc55020400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 분봉조회[해외선물-016] -- GET
        /uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice
        (tr_id=HHDFC55020400). 필수: SRS_CD, EXCH_CD, START_DATE_TIME, CLOSE_DATE_TIME, QRY_TP,
        QRY_CNT, QRY_GAP, INDEX_KEY. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice",
            "HHDFC55020400",
            params=params or {},
        )

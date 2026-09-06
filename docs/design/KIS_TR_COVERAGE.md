# KIS TR 커버리지 매트릭스

BR-1(ADR-2026-09-06-I D2). 생성: `python scripts/kis_tr_coverage.py`(오프라인, 결정적).
기준 목록 갱신(네트워크 필요): `python scripts/kis_tr_fetch.py`.

- 기준 저장소: `koreainvestment/open-trading-api` @ `main`(commit `b4e624971441`)
- 기준 목록 추출 시각: 2026-09-06T14:28:19+00:00
- 기준 TR 수: 375개(`examples_llm/**`, `chk_*.py` 제외, 기계 추출)

## 요약

| 구현됨 | 실전계좌필요 | 범위밖 | 미착수 | 합계 | 구현률 |
|---|---|---|---|---|---|
| 64 | 22 | 0 | 289 | 375 | 17.07% |

완료 정의(ADR D2): `미착수` 0건. `실전계좌필요`·`범위밖`은 남을 수 있으나 각각 사유가 있다.

## 도메인별

| 도메인 | 구현됨 | 전체 | 구현률 |
|---|---|---|---|
| domestic_bond | 4 | 18 | 22.22% |
| domestic_futureoption | 5 | 50 | 10.00% |
| domestic_stock | 32 | 167 | 19.16% |
| elw | 0 | 24 | 0.00% |
| etfetn | 1 | 6 | 16.67% |
| overseas_futureoption | 5 | 35 | 14.29% |
| overseas_stock | 17 | 75 | 22.67% |

## 전체 TR 매트릭스

| TR ID | 도메인 | 상태 | 이름 | 출처 |
|---|---|---|---|---|
| CTFN6118R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > (야간)선물옵션 잔고현황 [국내선물-010] | `examples_llm/domestic_futureoption/inquire_ngt_balance/inquire_ngt_balance.py` |
| CTFN7107R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > (야간)선물옵션 증거금 상세 [국내선물-024] | `examples_llm/domestic_futureoption/ngt_margin_detail/ngt_margin_detail.py` |
| CTFO5139R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 기준일체결내역[v1_국내선물-016] | `examples_llm/domestic_futureoption/inquire_ccnl_bstime/inquire_ccnl_bstime.py` |
| CTFO6117R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 잔고정산손익내역[v1_국내선물-013] | `examples_llm/domestic_futureoption/inquire_balance_settlement_pl/inquire_balance_settlement_pl.py` |
| CTFO6118R | domestic_futureoption | 구현됨 | [국내선물옵션] 주문/계좌 > 선물옵션 잔고현황[v1_국내선물-004] | `examples_llm/domestic_futureoption/inquire_balance/inquire_balance.py` |
| CTFO6119R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션기간약정수수료일별[v1_국내선물-017] | `examples_llm/domestic_futureoption/inquire_daily_amount_fee/inquire_daily_amount_fee.py` |
| CTFO6159R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 잔고평가손익내역[v1_국내선물-015] | `examples_llm/domestic_futureoption/inquire_balance_valuation_pl/inquire_balance_valuation_pl.py` |
| CTLN4050R | overseas_stock | 미착수 | [해외주식] 시세분석 > 당사 해외주식담보대출 가능 종목 [해외주식-051] | `examples_llm/overseas_stock/colable_by_company/colable_by_company.py` |
| CTOS4001R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 일별거래내역 [해외주식-063] | `examples_llm/overseas_stock/inquire_period_trans/inquire_period_trans.py` |
| CTOS5011R | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외결제일자조회[해외주식-017] | `examples_llm/overseas_stock/countries_holiday/countries_holiday.py` |
| CTPF1002R | domestic_stock | 미착수 | [국내주식] 종목정보 - 주식기본조회 | `examples_llm/domestic_stock/search_stock_info/search_stock_info.py` |
| CTPF1101R | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권 발행정보 | `examples_llm/domestic_bond/issue_info/issue_info.py` |
| CTPF1114R | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권 기본조회 | `examples_llm/domestic_bond/search_bond_info/search_bond_info.py` |
| CTPF1604R | domestic_stock | 미착수 | [국내주식] 종목정보 - 상품기본조회 | `examples_llm/domestic_stock/search_info/search_info.py` |
| CTPF1702R | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 상품기본정보[v1_해외주식-034] | `examples_llm/overseas_stock/search_info/search_info.py` |
| CTPF2005R | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권 평균단가조회 | `examples_llm/domestic_bond/avg_unit/avg_unit.py` |
| CTRGA011R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 기간별계좌권리현황조회 [국내주식-211] | `examples_llm/domestic_stock/period_rights/period_rights.py` |
| CTRGT011R | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 기간별권리조회 [해외주식-052] | `examples_llm/overseas_stock/period_rights/period_rights.py` |
| CTRP6010R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 결제기준잔고 [해외주식-064] | `examples_llm/overseas_stock/inquire_paymt_stdr_balance/inquire_paymt_stdr_balance.py` |
| CTRP6504R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 체결기준현재잔고 [v1_해외주식-008] | `examples_llm/overseas_stock/inquire_present_balance/inquire_present_balance.py` |
| CTRP6548R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 투자계좌자산현황조회[v1_국내주식-048] | `examples_llm/domestic_stock/inquire_account_balance/inquire_account_balance.py` |
| CTRP6550R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 총자산현황[v1_국내선물-014] | `examples_llm/domestic_futureoption/inquire_deposit/inquire_deposit.py` |
| CTSC0004R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식예약주문조회[v1_국내주식-020] | `examples_llm/domestic_stock/order_resv_ccnl/order_resv_ccnl.py` |
| CTSC0008U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식예약주문[v1_국내주식-017] | `examples_llm/domestic_stock/order_resv/order_resv.py` |
| CTSC0009U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식예약주문정정취소[v1_국내주식-018,019] | `examples_llm/domestic_stock/order_resv_rvsecncl/order_resv_rvsecncl.py` |
| CTSC0013U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식예약주문정정취소[v1_국내주식-018,019] | `examples_llm/domestic_stock/order_resv_rvsecncl/order_resv_rvsecncl.py` |
| CTSC2702R | domestic_stock | 구현됨 | [국내주식] 종목정보 - 당사 대주가능 종목 | `examples_llm/domestic_stock/lendable_by_company/lendable_by_company.py` |
| CTSC8013R | domestic_bond | 미착수 | [장내채권] 주문/계좌 - 장내채권 주문체결내역 | `examples_llm/domestic_bond/inquire_daily_ccld/inquire_daily_ccld.py` |
| CTSC8035R | domestic_bond | 미착수 | [장내채권] 주문/계좌 - 채권정정취소가능주문조회 | `examples_llm/domestic_bond/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py` |
| CTSC8407R | domestic_bond | 구현됨 | [장내채권] 주문/계좌 - 장내채권 잔고조회 | `examples_llm/domestic_bond/inquire_balance/inquire_balance.py` |
| CTSC9215R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] | `examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py` |
| FHKBJ773400C0 | domestic_bond | 구현됨 | [장내채권] 기본시세 - 장내채권현재가(시세) | `examples_llm/domestic_bond/inquire_price/inquire_price.py` |
| FHKBJ773401C0 | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권현재가(호가) | `examples_llm/domestic_bond/inquire_asking_price/inquire_asking_price.py` |
| FHKBJ773403C0 | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권현재가(체결) | `examples_llm/domestic_bond/inquire_ccnl/inquire_ccnl.py` |
| FHKBJ773404C0 | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권현재가(일별) | `examples_llm/domestic_bond/inquire_daily_price/inquire_daily_price.py` |
| FHKBJ773701C0 | domestic_bond | 미착수 | [장내채권] 기본시세 - 장내채권 기간별시세(일) | `examples_llm/domestic_bond/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py` |
| FHKEW15010000 | domestic_stock | 구현됨 | [국내주식] ELW시세 > ELW 현재가 시세 [v1_국내주식-014] | `examples_llm/domestic_stock/inquire_elw_price/inquire_elw_price.py` |
| FHKEW15100000 | elw | 미착수 | [국내주식] ELW시세 - ELW 종목검색[국내주식-166] | `examples_llm/elw/cond_search/cond_search.py` |
| FHKEW151701C0 | elw | 미착수 | [국내주식] ELW시세 - ELW 비교대상종목조회[국내주식-183] | `examples_llm/elw/compare_stocks/compare_stocks.py` |
| FHKEW154100C0 | elw | 미착수 | [국내주식] ELW시세 - ELW 기초자산 목록조회[국내주식-185] | `examples_llm/elw/udrl_asset_list/udrl_asset_list.py` |
| FHKEW154101C0 | elw | 미착수 | [국내주식] ELW시세 - ELW 기초자산별 종목시세 | `examples_llm/elw/udrl_asset_price/udrl_asset_price.py` |
| FHKEW154700C0 | elw | 미착수 | [국내주식] ELW시세 - ELW 만기예정/만기종목[국내주식-184] | `examples_llm/elw/expiration_stocks/expiration_stocks.py` |
| FHKEW154800C0 | elw | 미착수 | [국내주식] ELW시세 - ELW 신규상장종목[국내주식-181] | `examples_llm/elw/newly_listed/newly_listed.py` |
| FHKIF03020100 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 선물옵션기간별시세(일/주/월/년)[v1_국내선물-008] | `examples_llm/domestic_futureoption/inquire_daily_fuopchartprice/inquire_daily_fuopchartprice.py` |
| FHKIF03020200 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 선물옵션 분봉조회[v1_국내선물-012] | `examples_llm/domestic_futureoption/inquire_time_fuopchartprice/inquire_time_fuopchartprice.py` |
| FHKST01010100 | domestic_stock | 구현됨 | [국내주식] 기본시세 > 주식현재가 시세[v1_국내주식-008] | `examples_llm/domestic_stock/inquire_price/inquire_price.py` |
| FHKST01010200 | domestic_stock | 구현됨 | [국내주식] 기본시세 > 주식현재가 호가/예상체결[v1_국내주식-011] | `examples_llm/domestic_stock/inquire_asking_price_exp_ccn/inquire_asking_price_exp_ccn.py` |
| FHKST01010300 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 체결[v1_국내주식-009] | `examples_llm/domestic_stock/inquire_ccnl/inquire_ccnl.py` |
| FHKST01010400 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 일자별[v1_국내주식-010] | `examples_llm/domestic_stock/inquire_daily_price/inquire_daily_price.py` |
| FHKST01010600 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 회원사[v1_국내주식-013] | `examples_llm/domestic_stock/inquire_member/inquire_member.py` |
| FHKST01010900 | domestic_stock | 구현됨 | [국내주식] 기본시세 > 주식현재가 투자자[v1_국내주식-012] | `examples_llm/domestic_stock/inquire_investor/inquire_investor.py` |
| FHKST01011800 | domestic_stock | 미착수 | [국내주식] 업종/기타 - 종합 시황/공시(제목) | `examples_llm/domestic_stock/news_title/news_title.py` |
| FHKST01011801 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외속보(제목) [해외주식-055] | `examples_llm/overseas_stock/brknews_title/brknews_title.py` |
| FHKST03010100 | domestic_stock | 구현됨 | [국내주식] 기본시세 > 국내주식기간별시세(일/주/월/년)[v1_국내주식-016] | `examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py` |
| FHKST03010200 | domestic_stock | 구현됨 | [국내주식] 기본시세 > 주식당일분봉조회[v1_국내주식-022] | `examples_llm/domestic_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py` |
| FHKST03010230 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식일별분봉조회 [국내주식-213] | `examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py` |
| FHKST03010800 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목별일별매수매도체결량 [v1_국내주식-056] | `examples_llm/domestic_stock/inquire_daily_trade_volume/inquire_daily_trade_volume.py` |
| FHKST03030100 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 종목_지수_환율기간별시세(일_주_월_년)[v1_해외주식-012] | `examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py` |
| FHKST03030200 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외지수분봉조회[v1_해외주식-031] | `examples_llm/overseas_stock/inquire_time_indexchartprice/inquire_time_indexchartprice.py` |
| FHKST111900C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 체결금액별 매매비중 [국내주식-192] | `examples_llm/domestic_stock/tradprt_byamt/tradprt_byamt.py` |
| FHKST11300006 | domestic_stock | 미착수 | [국내주식] 시세분석 > 관심종목(멀티종목) 시세조회 [국내주식-205] | `examples_llm/domestic_stock/intstock_multprice/intstock_multprice.py` |
| FHKST117300C0 | domestic_stock | 미착수 | [국내주식] 기본시세 > 국내주식 장마감 예상체결가[국내주식-120] | `examples_llm/domestic_stock/exp_closing_price/exp_closing_price.py` |
| FHKST11860000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 시간외예상체결등락률 [국내주식-140] | `examples_llm/domestic_stock/overtime_exp_trans_fluct/overtime_exp_trans_fluct.py` |
| FHKST121600C0 | etfetn | 미착수 | [국내주식] 기본시세 > ETF 구성종목시세[국내주식-073] | `examples_llm/etfetn/inquire_component_stock_price/inquire_component_stock_price.py` |
| FHKST130000C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 상하한가 포착 [국내주식-190] | `examples_llm/domestic_stock/capture_uplowprice/capture_uplowprice.py` |
| FHKST17010000 | domestic_stock | 구현됨 | [국내주식] 순위분석 > 국내주식 신용잔고 상위 [국내주식-109] | `examples_llm/domestic_stock/credit_balance/credit_balance.py` |
| FHKST190900C0 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 대량체결건수 상위[국내주식-107] | `examples_llm/domestic_stock/bulk_trans_num/bulk_trans_num.py` |
| FHKST644100C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 외국계 매매종목 가집계 [국내주식-161] | `examples_llm/domestic_stock/frgnmem_trade_estimate/frgnmem_trade_estimate.py` |
| FHKST644400C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목별 외국계 순매수추이 [국내주식-164] | `examples_llm/domestic_stock/frgnmem_pchs_trend/frgnmem_pchs_trend.py` |
| FHKST649100C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내 증시자금 종합 [국내주식-193] | `examples_llm/domestic_stock/mktfunds/mktfunds.py` |
| FHKST663300C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 국내주식 종목투자의견 | `examples_llm/domestic_stock/invest_opinion/invest_opinion.py` |
| FHKST663400C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 국내주식 증권사별 투자의견 | `examples_llm/domestic_stock/invest_opbysec/invest_opbysec.py` |
| FHKST66430100 | domestic_stock | 구현됨 | [국내주식] 종목정보 > 국내주식 대차대조표 [v1_국내주식-078] | `examples_llm/domestic_stock/finance_balance_sheet/finance_balance_sheet.py` |
| FHKST66430200 | domestic_stock | 구현됨 | [국내주식] 종목정보 > 국내주식 손익계산서 [v1_국내주식-079] | `examples_llm/domestic_stock/finance_income_statement/finance_income_statement.py` |
| FHKST66430300 | domestic_stock | 구현됨 | [국내주식] 종목정보 > 국내주식 재무비율 [v1_국내주식-080] | `examples_llm/domestic_stock/finance_financial_ratio/finance_financial_ratio.py` |
| FHKST66430400 | domestic_stock | 미착수 | [국내주식] 종목정보 - 국내주식 수익성비율 | `examples_llm/domestic_stock/finance_profit_ratio/finance_profit_ratio.py` |
| FHKST66430500 | domestic_stock | 미착수 | [국내주식] 종목정보 > 국내주식 기타주요비율[v1_국내주식-082] | `examples_llm/domestic_stock/finance_other_major_ratios/finance_other_major_ratios.py` |
| FHKST66430600 | domestic_stock | 미착수 | [국내주식] 종목정보 > 국내주식 안정성비율[v1_국내주식-083] | `examples_llm/domestic_stock/finance_stability_ratio/finance_stability_ratio.py` |
| FHKST66430800 | domestic_stock | 미착수 | [국내주식] 종목정보 > 국내주식 성장성비율 [v1_국내주식-085] | `examples_llm/domestic_stock/finance_growth_ratio/finance_growth_ratio.py` |
| FHKUP03500100 | domestic_stock | 미착수 | [국내주식] 업종/기타 - 국내주식업종기간별시세(일/주/월/년) | `examples_llm/domestic_stock/inquire_daily_indexchartprice/inquire_daily_indexchartprice.py` |
| FHKUP03500200 | domestic_stock | 미착수 | [국내주식] 업종/기타 - 업종 분봉조회 | `examples_llm/domestic_stock/inquire_time_indexchartprice/inquire_time_indexchartprice.py` |
| FHKUP11750000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내주식 예상체결 전체지수[국내주식-122] | `examples_llm/domestic_stock/exp_total_index/exp_total_index.py` |
| FHMIF10000000 | domestic_futureoption | 구현됨 | [국내선물옵션] 기본시세 > 선물옵션 시세[v1_국내선물-006] | `examples_llm/domestic_futureoption/inquire_price/inquire_price.py` |
| FHMIF10010000 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 선물옵션 시세호가[v1_국내선물-007] | `examples_llm/domestic_futureoption/inquire_asking_price/inquire_asking_price.py` |
| FHPEW02740100 | elw | 미착수 | [국내주식] ELW시세 - ELW 투자지표추이(체결)[국내주식-172] | `examples_llm/elw/indicator_trend_ccnl/indicator_trend_ccnl.py` |
| FHPEW02740200 | elw | 미착수 | [국내주식] ELW시세 - ELW 투자지표추이(일별)[국내주식-173] | `examples_llm/elw/indicator_trend_daily/indicator_trend_daily.py` |
| FHPEW02740300 | elw | 미착수 | [국내주식] ELW시세 - ELW 투자지표추이(분별)[국내주식-174] | `examples_llm/elw/indicator_trend_minute/indicator_trend_minute.py` |
| FHPEW02770000 | elw | 미착수 | [국내주식] ELW시세 - ELW 상승률순위[국내주식-167] | `examples_llm/elw/updown_rate/updown_rate.py` |
| FHPEW02780000 | elw | 미착수 | [국내주식] ELW시세 - ELW 거래량순위[국내주식-168] | `examples_llm/elw/volume_rank/volume_rank.py` |
| FHPEW02790000 | elw | 미착수 | [국내주식] ELW시세 - ELW 지표순위[국내주식-169] | `examples_llm/elw/indicator/indicator.py` |
| FHPEW02830100 | elw | 미착수 | [국내주식] ELW시세 - ELW 민감도 추이(체결)[국내주식-175] | `examples_llm/elw/sensitivity_trend_ccnl/sensitivity_trend_ccnl.py` |
| FHPEW02830200 | elw | 미착수 | [국내주식] ELW시세 - ELW 민감도 추이(일별)[국내주식-176] | `examples_llm/elw/sensitivity_trend_daily/sensitivity_trend_daily.py` |
| FHPEW02840100 | elw | 미착수 | [국내주식] ELW시세 - ELW 변동성추이(체결)[국내주식-177] | `examples_llm/elw/volatility_trend_ccnl/volatility_trend_ccnl.py` |
| FHPEW02840200 | elw | 미착수 | [국내주식] ELW시세 - ELW 변동성추이(일별)[국내주식-178] | `examples_llm/elw/volatility_trend_daily/volatility_trend_daily.py` |
| FHPEW02840300 | elw | 미착수 | [국내주식] ELW시세 - ELW 변동성 추이(분별) | `examples_llm/elw/volatility_trend_minute/volatility_trend_minute.py` |
| FHPEW02840400 | elw | 미착수 | [국내주식] ELW시세 - ELW 변동성추이(틱)[국내주식-180] | `examples_llm/elw/volatility_trend_tick/volatility_trend_tick.py` |
| FHPEW02850000 | elw | 미착수 | [국내주식] ELW시세 - ELW 민감도 순위[국내주식-170] | `examples_llm/elw/sensitivity/sensitivity.py` |
| FHPEW02870000 | elw | 미착수 | [국내주식] ELW시세 - ELW 당일급변종목[국내주식-171] | `examples_llm/elw/quick_change/quick_change.py` |
| FHPEW03760000 | elw | 미착수 | [국내주식] ELW시세 - ELW LP매매추이[국내주식-182] | `examples_llm/elw/lp_trade_trend/lp_trade_trend.py` |
| FHPIF05030000 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 국내선물 기초자산 시세[국내선물-021] | `examples_llm/domestic_futureoption/display_board_top/display_board_top.py` |
| FHPIF05030100 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 국내옵션전광판_콜풋[국내선물-022] | `examples_llm/domestic_futureoption/display_board_callput/display_board_callput.py` |
| FHPIF05030200 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 국내옵션전광판_선물[국내선물-023] | `examples_llm/domestic_futureoption/display_board_futures/display_board_futures.py` |
| FHPIF05110100 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 선물옵션 일중예상체결추이[국내선물-018] | `examples_llm/domestic_futureoption/exp_price_trend/exp_price_trend.py` |
| FHPIO056104C0 | domestic_futureoption | 미착수 | [국내선물옵션] 기본시세 > 국내옵션전광판_옵션월물리스트[국내선물-020] | `examples_llm/domestic_futureoption/display_board_option_list/display_board_option_list.py` |
| FHPPG04600001 | domestic_stock | 구현됨 | [국내주식] 시세분석 > 프로그램매매 종합현황(일별)[국내주식-115] | `examples_llm/domestic_stock/comp_program_trade_daily/comp_program_trade_daily.py` |
| FHPPG04600101 | domestic_stock | 미착수 | [국내주식] 시세분석 > 프로그램매매 종합현황(시간) [국내주식-114] | `examples_llm/domestic_stock/comp_program_trade_today/comp_program_trade_today.py` |
| FHPPG04650101 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목별 프로그램매매추이(체결)[v1_국내주식-044] | `examples_llm/domestic_stock/program_trade_by_stock/program_trade_by_stock.py` |
| FHPPG04650201 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목별 프로그램매매추이(일별) [국내주식-113] | `examples_llm/domestic_stock/program_trade_by_stock_daily/program_trade_by_stock_daily.py` |
| FHPST01010000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 시세2[v1_국내주식-054] | `examples_llm/domestic_stock/inquire_price_2/inquire_price_2.py` |
| FHPST01060000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 당일시간대별체결[v1_국내주식-023] | `examples_llm/domestic_stock/inquire_time_itemconclusion/inquire_time_itemconclusion.py` |
| FHPST01130000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 매물대/거래비중 [국내주식-196] | `examples_llm/domestic_stock/pbar_tratio/pbar_tratio.py` |
| FHPST01390000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 변동성완화장치(VI) 현황[v1_국내주식-055] | `examples_llm/domestic_stock/inquire_vi_status/inquire_vi_status.py` |
| FHPST01680000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 체결강도 상위[v1_국내주식-101] | `examples_llm/domestic_stock/volume_power/volume_power.py` |
| FHPST01700000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 등락률 순위[v1_국내주식-088] | `examples_llm/domestic_stock/fluctuation/fluctuation.py` |
| FHPST01710000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 거래량순위[v1_국내주식-047] | `examples_llm/domestic_stock/volume_rank/volume_rank.py` |
| FHPST01720000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 국내주식 호가잔량 순위[국내주식-089] | `examples_llm/domestic_stock/quote_balance/quote_balance.py` |
| FHPST01730000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 수익자산지표 순위[v1_국내주식-090] | `examples_llm/domestic_stock/profit_asset_index/profit_asset_index.py` |
| FHPST01740000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 시가총액 상위 [v1_국내주식-091] | `examples_llm/domestic_stock/market_cap/market_cap.py` |
| FHPST01750000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 재무비율 순위[v1_국내주식-092] | `examples_llm/domestic_stock/finance_ratio/finance_ratio.py` |
| FHPST01760000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 국내주식 시간외잔량 순위[v1_국내주식-093] | `examples_llm/domestic_stock/after_hour_balance/after_hour_balance.py` |
| FHPST01770000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 우선주_괴리율 상위[v1_국내주식-094] | `examples_llm/domestic_stock/prefer_disparate_ratio/prefer_disparate_ratio.py` |
| FHPST01780000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 이격도 순위 [v1_국내주식-095] | `examples_llm/domestic_stock/disparity/disparity.py` |
| FHPST01790000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 시장가치 순위[v1_국내주식-096] | `examples_llm/domestic_stock/market_value/market_value.py` |
| FHPST01800000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 관심종목등록 상위[v1_국내주식-102] | `examples_llm/domestic_stock/top_interest_stock/top_interest_stock.py` |
| FHPST01810000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 예상체결가 추이[국내주식-118] | `examples_llm/domestic_stock/exp_price_trend/exp_price_trend.py` |
| FHPST01820000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 예상체결 상승_하락상위[v1_국내주식-103] | `examples_llm/domestic_stock/exp_trans_updown/exp_trans_updown.py` |
| FHPST01840000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내주식 예상체결지수 추이[국내주식-121] | `examples_llm/domestic_stock/exp_index_trend/exp_index_trend.py` |
| FHPST01860000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 당사매매종목 상위[v1_국내주식-104] | `examples_llm/domestic_stock/traded_by_company/traded_by_company.py` |
| FHPST01870000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 신고_신저근접종목 상위[v1_국내주식-105] | `examples_llm/domestic_stock/near_new_highlow/near_new_highlow.py` |
| FHPST02300000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 국내주식 시간외현재가[국내주식-076] | `examples_llm/domestic_stock/inquire_overtime_price/inquire_overtime_price.py` |
| FHPST02300400 | domestic_stock | 미착수 | [국내주식] 기본시세 > 국내주식 시간외호가[국내주식-077] | `examples_llm/domestic_stock/inquire_overtime_asking_price/inquire_overtime_asking_price.py` |
| FHPST02310000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 시간외시간별체결[v1_국내주식-025] | `examples_llm/domestic_stock/inquire_time_overtimeconclusion/inquire_time_overtimeconclusion.py` |
| FHPST02320000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 주식현재가 시간외일자별주가[v1_국내주식-026] | `examples_llm/domestic_stock/inquire_daily_overtimeprice/inquire_daily_overtimeprice.py` |
| FHPST02340000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 시간외등락율순위[국내주식-138] | `examples_llm/domestic_stock/overtime_fluctuation/overtime_fluctuation.py` |
| FHPST02350000 | domestic_stock | 미착수 | [국내주식] 순위분석 - 국내주식 시간외거래량순위 | `examples_llm/domestic_stock/overtime_volume/overtime_volume.py` |
| FHPST02400000 | etfetn | 구현됨 | [국내주식] 기본시세 > ETF/ETN 현재가[v1_국내주식-068] | `examples_llm/etfetn/inquire_price/inquire_price.py` |
| FHPST02440000 | etfetn | 미착수 | [국내주식] 기본시세 > NAV 비교추이(종목)[v1_국내주식-069] | `examples_llm/etfetn/nav_comparison_trend/nav_comparison_trend.py` |
| FHPST02440100 | etfetn | 미착수 | [국내주식] 기본시세 > NAV 비교추이(분)[v1_국내주식-070] | `examples_llm/etfetn/nav_comparison_time_trend/nav_comparison_time_trend.py` |
| FHPST02440200 | etfetn | 미착수 | [국내주식] 기본시세 > NAV 비교추이(일)[v1_국내주식-071] | `examples_llm/etfetn/nav_comparison_daily_trend/nav_comparison_daily_trend.py` |
| FHPST04320000 | domestic_stock | 미착수 | [국내주식] 기본시세 > 회원사 실 시간 매매동향(틱)[국내주식-163] | `examples_llm/domestic_stock/frgnmem_trade_trend/frgnmem_trade_trend.py` |
| FHPST04540000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 주식현재가 회원사 종목매매동향 [국내주식-197] | `examples_llm/domestic_stock/inquire_member_daily/inquire_member_daily.py` |
| FHPST04760000 | domestic_stock | 구현됨 | [국내주식] 시세분석 > 국내주식 신용잔고 일별추이[국내주식-110] | `examples_llm/domestic_stock/daily_credit_balance/daily_credit_balance.py` |
| FHPST04770000 | domestic_stock | 구현됨 | [국내주식] 종목정보 > 국내주식 당사 신용가능종목[국내주식-111] | `examples_llm/domestic_stock/credit_by_company/credit_by_company.py` |
| FHPST04820000 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 공매도 상위종목[국내주식-133] | `examples_llm/domestic_stock/short_sale/short_sale.py` |
| FHPST04830000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내주식 공매도 일별추이[국내주식-134] | `examples_llm/domestic_stock/daily_short_sale/daily_short_sale.py` |
| FHPST07020000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 금리 종합(국내채권_금리)[국내주식-155] | `examples_llm/domestic_stock/comp_interest/comp_interest.py` |
| FHPTJ04030000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 시장별 투자자매매동향(시세)[v1_국내주식-074] | `examples_llm/domestic_stock/inquire_investor_time_by_market/inquire_investor_time_by_market.py` |
| FHPTJ04040000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 시장별 투자자매매동향(일별) [국내주식-075] | `examples_llm/domestic_stock/inquire_investor_daily_by_market/inquire_investor_daily_by_market.py` |
| FHPTJ04160001 | domestic_stock | 미착수 | [국내주식] 시세분석  > 종목별 투자자매매동향(일별)[종목별 투자자매매동향(일별)] | `examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py` |
| FHPTJ04400000 | domestic_stock | 미착수 | [국내주식] 시세분석 > 국내기관_외국인 매매종목가집계[국내주식-037] | `examples_llm/domestic_stock/foreign_institution_total/foreign_institution_total.py` |
| FHPUP02100000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내업종 현재지수 [v1_국내주식-063] | `examples_llm/domestic_stock/inquire_index_price/inquire_index_price.py` |
| FHPUP02110100 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내업종 시간별지수(초)[국내주식-064] | `examples_llm/domestic_stock/inquire_index_tickprice/inquire_index_tickprice.py` |
| FHPUP02110200 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내업종 시간별지수(분)[국내주식-119] | `examples_llm/domestic_stock/inquire_index_timeprice/inquire_index_timeprice.py` |
| FHPUP02120000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내업종 일자별지수 [v1_국내주식-065] | `examples_llm/domestic_stock/inquire_index_daily_price/inquire_index_daily_price.py` |
| FHPUP02140000 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내업종 구분별전체시세[v1_국내주식-066] | `examples_llm/domestic_stock/inquire_index_category_price/inquire_index_category_price.py` |
| H0BICNT0 | domestic_bond | 미착수 | [장내채권] 실시간시세 > 채권지수 실시간체결가 [실시간-060] | `examples_llm/domestic_bond/bond_index_ccnl/bond_index_ccnl.py` |
| H0BJASP0 | domestic_bond | 미착수 | [장내채권] 실시간시세 > 일반채권 실시간호가 [실시간-053] | `examples_llm/domestic_bond/bond_asking_price/bond_asking_price.py` |
| H0BJCNT0 | domestic_bond | 미착수 | [장내채권] 실시간시세 > 일반채권 실시간체결가 [실시간-052] | `examples_llm/domestic_bond/bond_ccnl/bond_ccnl.py` |
| H0CFASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 상품선물 실시간호가[실시간-023] | `examples_llm/domestic_futureoption/commodity_futures_realtime_quote/commodity_futures_realtime_quote.py` |
| H0CFCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 상품선물 실시간체결가[실시간-022] | `examples_llm/domestic_futureoption/commodity_futures_realtime_conclusion/commodity_futures_realtime_conclusion.py` |
| H0EUANC0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간옵션실시간예상체결 [실시간-034] | `examples_llm/domestic_futureoption/krx_ngt_option_exp_ccnl/krx_ngt_option_exp_ccnl.py` |
| H0EUASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간옵션 실시간호가 [실시간-033] | `examples_llm/domestic_futureoption/krx_ngt_option_asking_price/krx_ngt_option_asking_price.py` |
| H0EUCNI0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간옵션실시간체결통보 [실시간-067] | `examples_llm/domestic_futureoption/krx_ngt_option_notice/krx_ngt_option_notice.py` |
| H0EUCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간옵션 실시간체결가 [실시간-032] | `examples_llm/domestic_futureoption/krx_ngt_option_ccnl/krx_ngt_option_ccnl.py` |
| H0EWANC0 | elw | 미착수 | [국내주식] 실시간시세 - ELW 실시간예상체결[실시간-063] | `examples_llm/elw/elw_exp_ccnl/elw_exp_ccnl.py` |
| H0EWASP0 | elw | 미착수 | [국내주식] 실시간시세 - ELW 실시간호가[실시간-062] | `examples_llm/elw/elw_asking_price/elw_asking_price.py` |
| H0EWCNT0 | elw | 미착수 | [국내주식] 실시간시세 - ELW 실시간체결가[실시간-061] | `examples_llm/elw/elw_ccnl/elw_ccnl.py` |
| H0GSCNI0 | overseas_stock | 미착수 | [해외주식] 실시간시세 > 해외주식 실시간체결통보[실시간-009] | `examples_llm/overseas_stock/ccnl_notice/ccnl_notice.py` |
| H0GSCNI9 | overseas_stock | 미착수 | [해외주식] 실시간시세 > 해외주식 실시간체결통보[실시간-009] | `examples_llm/overseas_stock/ccnl_notice/ccnl_notice.py` |
| H0IFASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 지수선물 실시간호가[실시간-011] | `examples_llm/domestic_futureoption/index_futures_realtime_quote/index_futures_realtime_quote.py` |
| H0IFCNI0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 선물옵션 실시간체결통보[실시간-012] | `examples_llm/domestic_futureoption/fuopt_ccnl_notice/fuopt_ccnl_notice.py` |
| H0IFCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 지수선물 실시간체결가[실시간-010] | `examples_llm/domestic_futureoption/index_futures_realtime_conclusion/index_futures_realtime_conclusion.py` |
| H0IOASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 지수옵션 실시간호가[실시간-015] | `examples_llm/domestic_futureoption/index_option_realtime_quote/index_option_realtime_quote.py` |
| H0IOCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 지수옵션 실시간체결가[실시간-014] | `examples_llm/domestic_futureoption/index_option_realtime_conclusion/index_option_realtime_conclusion.py` |
| H0MFASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간선물 실시간호가 [실시간-065] | `examples_llm/domestic_futureoption/krx_ngt_futures_asking_price/krx_ngt_futures_asking_price.py` |
| H0MFCNI0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간선물 실시간체결통보 [실시간-066] | `examples_llm/domestic_futureoption/krx_ngt_futures_ccnl_notice/krx_ngt_futures_ccnl_notice.py` |
| H0MFCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > KRX야간선물 실시간종목체결 [실시간-064] | `examples_llm/domestic_futureoption/krx_ngt_futures_ccnl/krx_ngt_futures_ccnl.py` |
| H0NXANC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간예상체결 (NXT) | `examples_llm/domestic_stock/exp_ccnl_nxt/exp_ccnl_nxt.py` |
| H0NXASP0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간호가 (NXT) | `examples_llm/domestic_stock/asking_price_nxt/asking_price_nxt.py` |
| H0NXCNT0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간체결가 (NXT) | `examples_llm/domestic_stock/ccnl_nxt/ccnl_nxt.py` |
| H0NXMBC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간회원사 (NXT) | `examples_llm/domestic_stock/member_nxt/member_nxt.py` |
| H0NXMKO0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 장운영정보(NXT) | `examples_llm/domestic_stock/market_status_nxt/market_status_nxt.py` |
| H0NXPGM0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간프로그램매매 (NXT) | `examples_llm/domestic_stock/program_trade_nxt/program_trade_nxt.py` |
| H0STANC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간예상체결 (KRX) [실시간-041] | `examples_llm/domestic_stock/exp_ccnl_krx/exp_ccnl_krx.py` |
| H0STASP0 | domestic_stock | 구현됨 | [국내주식] 실시간시세 > 국내주식 실시간호가 (KRX) [실시간-004] | `examples_llm/domestic_stock/asking_price_krx/asking_price_krx.py` |
| H0STCNI0 | domestic_stock | 구현됨 | [국내주식] 실시간시세 > 국내주식 주식체결통보 [실시간-005] | `examples_llm/domestic_stock/ccnl_notice/ccnl_notice.py` |
| H0STCNI9 | domestic_stock | 구현됨 | [국내주식] 실시간시세 > 국내주식 주식체결통보 [실시간-005] | `examples_llm/domestic_stock/ccnl_notice/ccnl_notice.py` |
| H0STCNT0 | domestic_stock | 구현됨 | [국내주식] 실시간시세 > 국내주식 실시간체결가(KRX) [실시간-003] | `examples_llm/domestic_stock/ccnl_krx/ccnl_krx.py` |
| H0STMBC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간회원사 (KRX) [실시간-047] | `examples_llm/domestic_stock/member_krx/member_krx.py` |
| H0STMKO0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 장운영정보 (KRX) [실시간-049] | `examples_llm/domestic_stock/market_status_krx/market_status_krx.py` |
| H0STNAV0 | etfetn | 미착수 | [국내주식] 실시간시세 > 국내ETF NAV추이[실시간-051] | `examples_llm/etfetn/etf_nav_trend/etf_nav_trend.py` |
| H0STOAA0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 시간외 실시간호가 (KRX) [실시간-025] | `examples_llm/domestic_stock/overtime_asking_price_krx/overtime_asking_price_krx.py` |
| H0STOAC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 시간외 실시간예상체결 (KRX) [실시간-024] | `examples_llm/domestic_stock/overtime_exp_ccnl_krx/overtime_exp_ccnl_krx.py` |
| H0STOUP0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 시간외 실시간체결가 (KRX) [실시간-042] | `examples_llm/domestic_stock/overtime_ccnl_krx/overtime_ccnl_krx.py` |
| H0STPGM0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간프로그램매매 (KRX)  [실시간-048] | `examples_llm/domestic_stock/program_trade_krx/program_trade_krx.py` |
| H0UNANC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간예상체결(통합) | `examples_llm/domestic_stock/exp_ccnl_total/exp_ccnl_total.py` |
| H0UNASP0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간호가 (통합) | `examples_llm/domestic_stock/asking_price_total/asking_price_total.py` |
| H0UNCNT0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간체결가 (통합) | `examples_llm/domestic_stock/ccnl_total/ccnl_total.py` |
| H0UNMBC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간회원사 (통합) | `examples_llm/domestic_stock/member_total/member_total.py` |
| H0UNMKO0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 장운영정보(통합) | `examples_llm/domestic_stock/market_status_total/market_status_total.py` |
| H0UNPGM0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내주식 실시간프로그램매매 (통합) | `examples_llm/domestic_stock/program_trade_total/program_trade_total.py` |
| H0UPANC0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내지수 실시간예상체결 [실시간-027] | `examples_llm/domestic_stock/index_exp_ccnl/index_exp_ccnl.py` |
| H0UPCNT0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내지수 실시간체결 [실시간-026] | `examples_llm/domestic_stock/index_ccnl/index_ccnl.py` |
| H0UPPGM0 | domestic_stock | 미착수 | [국내주식] 실시간시세 > 국내지수 실시간프로그램매매 [실시간-028] | `examples_llm/domestic_stock/index_program_trade/index_program_trade.py` |
| H0ZFANC0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식선물 실시간예상체결 [실시간-031] | `examples_llm/domestic_futureoption/futures_exp_ccnl/futures_exp_ccnl.py` |
| H0ZFASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식선물 실시간호가 [실시간-030] | `examples_llm/domestic_futureoption/stock_futures_realtime_quote/stock_futures_realtime_quote.py` |
| H0ZFCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식선물 실시간체결가 [실시간-029] | `examples_llm/domestic_futureoption/stock_futures_realtime_conclusion/stock_futures_realtime_conclusion.py` |
| H0ZOANC0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식옵션 실시간예상체결 [실시간-046] | `examples_llm/domestic_futureoption/option_exp_ccnl/option_exp_ccnl.py` |
| H0ZOASP0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식옵션 실시간호가 [실시간-045] | `examples_llm/domestic_futureoption/stock_option_asking_price/stock_option_asking_price.py` |
| H0ZOCNT0 | domestic_futureoption | 미착수 | [국내선물옵션] 실시간시세 > 주식옵션 실시간체결가 [실시간-044] | `examples_llm/domestic_futureoption/stock_option_ccnl/stock_option_ccnl.py` |
| HDFFF010 | overseas_futureoption | 미착수 | [해외선물옵션]실시간시세 > 해외선물옵션 실시간호가[실시간-018] | `examples_llm/overseas_futureoption/asking_price/asking_price.py` |
| HDFFF020 | overseas_futureoption | 미착수 | [해외선물옵션]실시간시세 > 해외선물옵션 실시간체결가[실시간-017] | `examples_llm/overseas_futureoption/ccnl/ccnl.py` |
| HDFFF1C0 | overseas_futureoption | 미착수 | [해외선물옵션]실시간시세 > 해외선물옵션 실시간주문내역통보[실시간-019] | `examples_llm/overseas_futureoption/order_notice/order_notice.py` |
| HDFFF2C0 | overseas_futureoption | 미착수 | [해외선물옵션]실시간시세 > 해외선물옵션 실시간체결내역통보[실시간-020] | `examples_llm/overseas_futureoption/ccnl_notice/ccnl_notice.py` |
| HDFSASP0 | overseas_stock | 구현됨 | [해외주식] 실시간시세 > 해외주식 실시간호가[실시간-021] | `examples_llm/overseas_stock/asking_price/asking_price.py` |
| HDFSASP1 | overseas_stock | 미착수 | [해외주식] 실시간시세 > 해외주식 지연호가(아시아)[실시간-008] | `examples_llm/overseas_stock/delayed_asking_price_asia/delayed_asking_price_asia.py` |
| HDFSCNT0 | overseas_stock | 구현됨 | [해외주식] 실시간시세 > 해외주식 실시간지연체결가[실시간-007] | `examples_llm/overseas_stock/delayed_ccnl/delayed_ccnl.py` |
| HHDDB95030000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 미결제추이 [해외선물-029] | `examples_llm/overseas_futureoption/investor_unpd_trend/investor_unpd_trend.py` |
| HHDFC55010000 | overseas_futureoption | 구현됨 | [해외선물옵션] 기본시세 > 해외선물종목현재가 [v1_해외선물-009] | `examples_llm/overseas_futureoption/inquire_price/inquire_price.py` |
| HHDFC55010100 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물종목상세[v1_해외선물-008] | `examples_llm/overseas_futureoption/stock_detail/stock_detail.py` |
| HHDFC55020000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 체결추이(주간)[해외선물-017] | `examples_llm/overseas_futureoption/weekly_ccnl/weekly_ccnl.py` |
| HHDFC55020100 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 체결추이(일간) [해외선물-018] | `examples_llm/overseas_futureoption/daily_ccnl/daily_ccnl.py` |
| HHDFC55020200 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 체결추이(틱)[해외선물-019] | `examples_llm/overseas_futureoption/tick_ccnl/tick_ccnl.py` |
| HHDFC55020300 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 체결추이(월간)[해외선물-020] | `examples_llm/overseas_futureoption/monthly_ccnl/monthly_ccnl.py` |
| HHDFC55020400 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 분봉조회[해외선물-016] | `examples_llm/overseas_futureoption/inquire_time_futurechartprice/inquire_time_futurechartprice.py` |
| HHDFC55200000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 상품기본정보[해외선물-023] | `examples_llm/overseas_futureoption/search_contract_detail/search_contract_detail.py` |
| HHDFC86000000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물 호가 [해외선물-031] | `examples_llm/overseas_futureoption/inquire_asking_price/inquire_asking_price.py` |
| HHDFO55010000 | overseas_futureoption | 구현됨 | [해외선물옵션] 기본시세 > 해외옵션종목현재가 [해외선물-035] | `examples_llm/overseas_futureoption/opt_price/opt_price.py` |
| HHDFO55010100 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션종목상세 [해외선물-034] | `examples_llm/overseas_futureoption/opt_detail/opt_detail.py` |
| HHDFO55020000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 체결추이(주간) [해외선물-036] | `examples_llm/overseas_futureoption/opt_weekly_ccnl/opt_weekly_ccnl.py` |
| HHDFO55020100 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 분봉조회 [해외선물-040] | `examples_llm/overseas_futureoption/inquire_time_optchartprice/inquire_time_optchartprice.py` |
| HHDFO55020200 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 체결추이(틱) [해외선물-038] | `examples_llm/overseas_futureoption/opt_tick_ccnl/opt_tick_ccnl.py` |
| HHDFO55020300 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 체결추이(월간) [해외선물-039] | `examples_llm/overseas_futureoption/opt_monthly_ccnl/opt_monthly_ccnl.py` |
| HHDFO55200000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 상품기본정보 [해외선물-041] | `examples_llm/overseas_futureoption/search_opt_detail/search_opt_detail.py` |
| HHDFO86000000 | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외옵션 호가 [해외선물-033] | `examples_llm/overseas_futureoption/opt_asking_price/opt_asking_price.py` |
| HHDFS00000300 | overseas_stock | 구현됨 | [해외주식] 기본시세 - 해외주식 현재체결가 | `examples_llm/overseas_stock/price/price.py` |
| HHDFS76200100 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 현재가 1호가[해외주식-033] | `examples_llm/overseas_stock/inquire_asking_price/inquire_asking_price.py` |
| HHDFS76200200 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 현재가상세[v1_해외주식-029] | `examples_llm/overseas_stock/price_detail/price_detail.py` |
| HHDFS76200300 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 체결추이[해외주식-037] | `examples_llm/overseas_stock/quot_inquire_ccnl/quot_inquire_ccnl.py` |
| HHDFS76240000 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 기간별시세[v1_해외주식-010] | `examples_llm/overseas_stock/dailyprice/dailyprice.py` |
| HHDFS76260000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 가격급등락[해외주식-038] | `examples_llm/overseas_stock/price_fluct/price_fluct.py` |
| HHDFS76270000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 거래량급증[해외주식-039] | `examples_llm/overseas_stock/volume_surge/volume_surge.py` |
| HHDFS76280000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 매수체결강도상위[해외주식-040] | `examples_llm/overseas_stock/volume_power/volume_power.py` |
| HHDFS76290000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 상승률/하락률[해외주식-041] | `examples_llm/overseas_stock/updown_rate/updown_rate.py` |
| HHDFS76300000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 신고/신저가[해외주식-042] | `examples_llm/overseas_stock/new_highlow/new_highlow.py` |
| HHDFS76310010 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 거래량순위[해외주식-043] | `examples_llm/overseas_stock/trade_vol/trade_vol.py` |
| HHDFS76320010 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 거래대금순위[해외주식-044] | `examples_llm/overseas_stock/trade_pbmn/trade_pbmn.py` |
| HHDFS76330000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 거래증가율순위[해외주식-045] | `examples_llm/overseas_stock/trade_growth/trade_growth.py` |
| HHDFS76340000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 거래회전율순위[해외주식-046] | `examples_llm/overseas_stock/trade_turnover/trade_turnover.py` |
| HHDFS76350100 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 시가총액순위[해외주식-047] | `examples_llm/overseas_stock/market_cap/market_cap.py` |
| HHDFS76370000 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 업종별시세[해외주식-048] | `examples_llm/overseas_stock/industry_theme/industry_theme.py` |
| HHDFS76370100 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식 업종별코드조회[해외주식-049] | `examples_llm/overseas_stock/industry_price/industry_price.py` |
| HHDFS76410000 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식조건검색[v1_해외주식-015] | `examples_llm/overseas_stock/inquire_search/inquire_search.py` |
| HHDFS76950200 | overseas_stock | 미착수 | [해외주식] 기본시세 > 해외주식분봉조회[v1_해외주식-030] | `examples_llm/overseas_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py` |
| HHDFS78330900 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외주식 권리종합 [해외주식-050] | `examples_llm/overseas_stock/rights_by_ice/rights_by_ice.py` |
| HHKCM113004C6 | domestic_stock | 미착수 | [국내주식] 시세분석 > 관심종목 그룹별 종목조회 [국내주식-203] | `examples_llm/domestic_stock/intstock_stocklist_by_group/intstock_stocklist_by_group.py` |
| HHKCM113004C7 | domestic_stock | 미착수 | [국내주식] 시세분석 > 관심종목 그룹조회 [국내주식-204] | `examples_llm/domestic_stock/intstock_grouplist/intstock_grouplist.py` |
| HHKDB13470100 | domestic_stock | 미착수 | [국내주식] 순위분석 > 국내주식 배당률 상위[국내주식-106] | `examples_llm/domestic_stock/dividend_rate/dividend_rate.py` |
| HHKDB669100C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(유상증자일정)[국내주식-143] | `examples_llm/domestic_stock/ksdinfo_paidin_capin/ksdinfo_paidin_capin.py` |
| HHKDB669101C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 예탁원정보(무상증자일정) | `examples_llm/domestic_stock/ksdinfo_bonus_issue/ksdinfo_bonus_issue.py` |
| HHKDB669102C0 | domestic_stock | 구현됨 | [국내주식] 종목정보 - 예탁원정보(배당일정) | `examples_llm/domestic_stock/ksdinfo_dividend/ksdinfo_dividend.py` |
| HHKDB669103C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 예탁원정보(주식매수청구일정) | `examples_llm/domestic_stock/ksdinfo_purreq/ksdinfo_purreq.py` |
| HHKDB669104C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(합병_분할일정)[국내주식-147] | `examples_llm/domestic_stock/ksdinfo_merger_split/ksdinfo_merger_split.py` |
| HHKDB669105C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 예탁원정보(액면교체일정) | `examples_llm/domestic_stock/ksdinfo_rev_split/ksdinfo_rev_split.py` |
| HHKDB669106C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(자본감소일정) [국내주식-149] | `examples_llm/domestic_stock/ksdinfo_cap_dcrs/ksdinfo_cap_dcrs.py` |
| HHKDB669107C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(상장정보일정)[국내주식-150] | `examples_llm/domestic_stock/ksdinfo_list_info/ksdinfo_list_info.py` |
| HHKDB669108C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 예탁원정보(공모주청약일정) | `examples_llm/domestic_stock/ksdinfo_pub_offer/ksdinfo_pub_offer.py` |
| HHKDB669109C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(실권주일정)[국내주식-152] | `examples_llm/domestic_stock/ksdinfo_forfeit/ksdinfo_forfeit.py` |
| HHKDB669110C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 예탁원정보(의무예치일정) [국내주식-153] | `examples_llm/domestic_stock/ksdinfo_mand_deposit/ksdinfo_mand_deposit.py` |
| HHKDB669111C0 | domestic_stock | 미착수 | [국내주식] 종목정보 - 예탁원정보(주주총회일정) | `examples_llm/domestic_stock/ksdinfo_sharehld_meet/ksdinfo_sharehld_meet.py` |
| HHKST03900300 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목조건검색 목록조회[국내주식-038] | `examples_llm/domestic_stock/psearch_title/psearch_title.py` |
| HHKST03900400 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목조건검색조회 [국내주식-039] | `examples_llm/domestic_stock/psearch_result/psearch_result.py` |
| HHKST668300C0 | domestic_stock | 미착수 | [국내주식] 종목정보 > 국내주식 종목추정실적[국내주식-187] | `examples_llm/domestic_stock/estimate_perform/estimate_perform.py` |
| HHMCM000002C0 | domestic_stock | 미착수 | [국내주식] 업종/기타 > 국내선물 영업일조회 [국내주식-160] | `examples_llm/domestic_stock/market_time/market_time.py` |
| HHMCM000100C0 | domestic_stock | 미착수 | [국내주식] 순위분석 > HTS조회상위20종목[국내주식-214] | `examples_llm/domestic_stock/hts_top_view/hts_top_view.py` |
| HHPPG046600C1 | domestic_stock | 미착수 | [국내주식] 시세분석 > 프로그램매매 투자자매매동향(당일) [국내주식-116] | `examples_llm/domestic_stock/investor_program_trade_today/investor_program_trade_today.py` |
| HHPST074500C0 | domestic_stock | 미착수 | [국내주식] 시세분석 > 종목별 일별 대차거래추이 [국내주식-135] | `examples_llm/domestic_stock/daily_loan_trans/daily_loan_trans.py` |
| HHPSTH60100C1 | overseas_stock | 미착수 | [해외주식] 시세분석 > 해외뉴스종합(제목) [해외주식-053] | `examples_llm/overseas_stock/news_title/news_title.py` |
| HHPTJ04160200 | domestic_stock | 구현됨 | [국내주식] 시세분석 > 종목별 외인기관 추정가집계[v1_국내주식-046] | `examples_llm/domestic_stock/investor_trend_estimate/investor_trend_estimate.py` |
| OTFM1411R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 예수금현황 [해외선물-012] | `examples_llm/overseas_futureoption/inquire_deposit/inquire_deposit.py` |
| OTFM1412R | overseas_futureoption | 구현됨 | [해외선물옵션] 주문/계좌 > 해외선물옵션 미결제내역조회(잔고) [v1_해외선물-005] | `examples_llm/overseas_futureoption/inquire_unpd/inquire_unpd.py` |
| OTFM2229R | overseas_futureoption | 미착수 | [해외선물옵션] 기본시세 > 해외선물옵션 장운영시간 [해외선물-030] | `examples_llm/overseas_futureoption/market_time/market_time.py` |
| OTFM3001U | overseas_futureoption | 구현됨 | [해외선물옵션] 주문/계좌 > 해외선물옵션 주문[v1_해외선물-001] | `examples_llm/overseas_futureoption/order/order.py` |
| OTFM3002U | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 정정취소주문[v1_해외선물-002, 003] | `examples_llm/overseas_futureoption/order_rvsecncl/order_rvsecncl.py` |
| OTFM3003U | overseas_futureoption | 구현됨 | [해외선물옵션] 주문/계좌 > 해외선물옵션 정정취소주문[v1_해외선물-002, 003] | `examples_llm/overseas_futureoption/order_rvsecncl/order_rvsecncl.py` |
| OTFM3114R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 기간계좌거래내역 [해외선물-014] | `examples_llm/overseas_futureoption/inquire_period_trans/inquire_period_trans.py` |
| OTFM3115R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 증거금상세 [해외선물-032] | `examples_llm/overseas_futureoption/margin_detail/margin_detail.py` |
| OTFM3116R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 당일주문내역조회 [v1_해외선물-004] | `examples_llm/overseas_futureoption/inquire_ccld/inquire_ccld.py` |
| OTFM3118R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 기간계좌손익 일별 [해외선물-010] | `examples_llm/overseas_futureoption/inquire_period_ccld/inquire_period_ccld.py` |
| OTFM3120R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 일별 주문내역 [해외선물-013] | `examples_llm/overseas_futureoption/inquire_daily_order/inquire_daily_order.py` |
| OTFM3122R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 일별체결내역[해외선물-011] | `examples_llm/overseas_futureoption/inquire_daily_ccld/inquire_daily_ccld.py` |
| OTFM3304R | overseas_futureoption | 미착수 | [해외선물옵션] 주문/계좌 > 해외선물옵션 주문가능조회 [v1_해외선물-006] | `examples_llm/overseas_futureoption/inquire_psamount/inquire_psamount.py` |
| STTN1101U | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문[v1_국내선물-001] | `examples_llm/domestic_futureoption/order/order.py` |
| STTN5105R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > (야간)선물옵션 주문가능 조회 [국내선물-011] | `examples_llm/domestic_futureoption/inquire_psbl_ngt_order/inquire_psbl_ngt_order.py` |
| STTN5201R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > (야간)선물옵션 주문체결 내역조회 [국내선물-009] | `examples_llm/domestic_futureoption/inquire_ngt_ccnl/inquire_ngt_ccnl.py` |
| TTTC0011U | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] | `examples_llm/domestic_stock/order_cash/order_cash.py` |
| TTTC0012U | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] | `examples_llm/domestic_stock/order_cash/order_cash.py` |
| TTTC0013U | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식주문(정정취소)[v1_국내주식-003] | `examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py` |
| TTTC0051U | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 주식주문(신용)[v1_국내주식-002] | `examples_llm/domestic_stock/order_credit/order_credit.py` |
| TTTC0052U | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 주식주문(신용)[v1_국내주식-002] | `examples_llm/domestic_stock/order_credit/order_credit.py` |
| TTTC0081R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] | `examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py` |
| TTTC0084R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식정정취소가능주문조회[v1_국내주식-004] | `examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py` |
| TTTC0503R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 퇴직연금 매수가능조회[v1_국내주식-034] | `examples_llm/domestic_stock/pension_inquire_psbl_order/pension_inquire_psbl_order.py` |
| TTTC0506R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 퇴직연금 예수금조회[v1_국내주식-035] | `examples_llm/domestic_stock/pension_inquire_deposit/pension_inquire_deposit.py` |
| TTTC0869R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 주식통합증거금 현황 [국내주식-191] | `examples_llm/domestic_stock/intgr_margin/intgr_margin.py` |
| TTTC0952U | domestic_bond | 구현됨 | [장내채권] 주문/계좌 - 장내채권 매수주문 | `examples_llm/domestic_bond/buy/buy.py` |
| TTTC0953U | domestic_bond | 실전계좌필요 | [장내채권] 주문/계좌 - 장내채권 정정취소주문 | `examples_llm/domestic_bond/order_rvsecncl/order_rvsecncl.py` |
| TTTC0958U | domestic_bond | 구현됨 | [장내채권] 주문/계좌 - 장내채권 매도주문 | `examples_llm/domestic_bond/sell/sell.py` |
| TTTC2101R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 - 해외증거금 통화별조회 [해외주식-035] | `examples_llm/overseas_stock/foreign_margin/foreign_margin.py` |
| TTTC2201R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 퇴직연금 미체결내역[v1_국내주식-033] | `examples_llm/domestic_stock/pension_inquire_daily_ccld/pension_inquire_daily_ccld.py` |
| TTTC2202R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 퇴직연금 체결기준잔고[v1_국내주식-032] | `examples_llm/domestic_stock/pension_inquire_present_balance/pension_inquire_present_balance.py` |
| TTTC2208R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 퇴직연금 잔고조회[v1_국내주식-036] | `examples_llm/domestic_stock/pension_inquire_balance/pension_inquire_balance.py` |
| TTTC8408R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 - 매도가능수량조회 | `examples_llm/domestic_stock/inquire_psbl_sell/inquire_psbl_sell.py` |
| TTTC8434R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식잔고조회[v1_국내주식-006] | `examples_llm/domestic_stock/inquire_balance/inquire_balance.py` |
| TTTC8494R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식잔고조회_실현손익[v1_국내주식-041] | `examples_llm/domestic_stock/inquire_balance_rlz_pl/inquire_balance_rlz_pl.py` |
| TTTC8708R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 기간별손익일별합산조회[v1_국내주식-052] | `examples_llm/domestic_stock/inquire_period_profit/inquire_period_profit.py` |
| TTTC8715R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 기간별매매손익현황조회[v1_국내주식-060] | `examples_llm/domestic_stock/inquire_period_trade_profit/inquire_period_trade_profit.py` |
| TTTC8908R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 매수가능조회[v1_국내주식-007] | `examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py` |
| TTTC8909R | domestic_stock | 실전계좌필요 | [국내주식] 주문/계좌 > 신용매수가능조회[v1_국내주식-042] | `examples_llm/domestic_stock/inquire_credit_psamount/inquire_credit_psamount.py` |
| TTTC8910R | domestic_bond | 실전계좌필요 | [장내채권] 주문/계좌 - 장내채권 매수가능조회 | `examples_llm/domestic_bond/inquire_psbl_order/inquire_psbl_order.py` |
| TTTN1103U | domestic_futureoption | 실전계좌필요 | [국내선물옵션] 주문/계좌 > 선물옵션 정정취소주문[v1_국내선물-002] | `examples_llm/domestic_futureoption/order_rvsecncl/order_rvsecncl.py` |
| TTTO1101U | domestic_futureoption | 구현됨 | [국내선물옵션] 주문/계좌 > 선물옵션 주문[v1_국내선물-001] | `examples_llm/domestic_futureoption/order/order.py` |
| TTTO1103U | domestic_futureoption | 구현됨 | [국내선물옵션] 주문/계좌 > 선물옵션 정정취소주문[v1_국내선물-002] | `examples_llm/domestic_futureoption/order_rvsecncl/order_rvsecncl.py` |
| TTTO5105R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문가능[v1_국내선물-005] | `examples_llm/domestic_futureoption/inquire_psbl_order/inquire_psbl_order.py` |
| TTTO5201R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문체결내역조회[v1_국내선물-003] | `examples_llm/domestic_futureoption/inquire_ccnl/inquire_ccnl.py` |
| TTTS0202U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0304U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0305U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0307U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0308U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0310U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS0311U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS1001U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS1002U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS1005U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTS3007R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 매수가능금액조회 [v1_해외주식-014] | `examples_llm/overseas_stock/inquire_psamount/inquire_psamount.py` |
| TTTS3012R | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 잔고 [v1_해외주식-006] | `examples_llm/overseas_stock/inquire_balance/inquire_balance.py` |
| TTTS3013U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| TTTS3014R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 예약주문조회[v1_해외주식-013] | `examples_llm/overseas_stock/order_resv_list/order_resv_list.py` |
| TTTS3018R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 미체결내역 [v1_해외주식-005] | `examples_llm/overseas_stock/inquire_nccs/inquire_nccs.py` |
| TTTS3035R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 주문체결내역 [v1_해외주식-007] | `examples_llm/overseas_stock/inquire_ccnl/inquire_ccnl.py` |
| TTTS3039R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 기간손익 [v1_해외주식-032] | `examples_llm/overseas_stock/inquire_period_profit/inquire_period_profit.py` |
| TTTS6036U | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 미국주간주문 [v1_해외주식-026] | `examples_llm/overseas_stock/daytime_order/daytime_order.py` |
| TTTS6037U | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 미국주간주문 [v1_해외주식-026] | `examples_llm/overseas_stock/daytime_order/daytime_order.py` |
| TTTS6038U | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 미국주간정정취소 [v1_해외주식-027] | `examples_llm/overseas_stock/daytime_order_rvsecncl/daytime_order_rvsecncl.py` |
| TTTS6058R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 지정가주문번호조회 [해외주식-071] | `examples_llm/overseas_stock/algo_ordno/algo_ordno.py` |
| TTTS6059R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 지정가체결내역조회 [해외주식-070] | `examples_llm/overseas_stock/inquire_algo_ccnl/inquire_algo_ccnl.py` |
| TTTT1002U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTT1004U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 정정취소주문[v1_해외주식-003] | `examples_llm/overseas_stock/order_rvsecncl/order_rvsecncl.py` |
| TTTT1006U | overseas_stock | 구현됨 | [해외주식] 주문/계좌 > 해외주식 주문 [v1_해외주식-001] | `examples_llm/overseas_stock/order/order.py` |
| TTTT3014U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| TTTT3016U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| TTTT3017U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수취소[v1_해외주식-004] | `examples_llm/overseas_stock/order_resv_ccnl/order_resv_ccnl.py` |
| TTTT3039R | overseas_stock | 실전계좌필요 | [해외주식] 주문/계좌 > 해외주식 예약주문조회[v1_해외주식-013] | `examples_llm/overseas_stock/order_resv_list/order_resv_list.py` |
| VTFO6118R | domestic_futureoption | 구현됨 | [국내선물옵션] 주문/계좌 > 선물옵션 잔고현황[v1_국내선물-004] | `examples_llm/domestic_futureoption/inquire_balance/inquire_balance.py` |
| VTRP6504R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 체결기준현재잔고 [v1_해외주식-008] | `examples_llm/overseas_stock/inquire_present_balance/inquire_present_balance.py` |
| VTSC9215R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] | `examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py` |
| VTTC0011U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] | `examples_llm/domestic_stock/order_cash/order_cash.py` |
| VTTC0012U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] | `examples_llm/domestic_stock/order_cash/order_cash.py` |
| VTTC0013U | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식주문(정정취소)[v1_국내주식-003] | `examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py` |
| VTTC0081R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] | `examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py` |
| VTTC8434R | domestic_stock | 구현됨 | [국내주식] 주문/계좌 > 주식잔고조회[v1_국내주식-006] | `examples_llm/domestic_stock/inquire_balance/inquire_balance.py` |
| VTTC8908R | domestic_stock | 미착수 | [국내주식] 주문/계좌 > 매수가능조회[v1_국내주식-007] | `examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py` |
| VTTO1101U | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문[v1_국내선물-001] | `examples_llm/domestic_futureoption/order/order.py` |
| VTTO1103U | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 정정취소주문[v1_국내선물-002] | `examples_llm/domestic_futureoption/order_rvsecncl/order_rvsecncl.py` |
| VTTO5105R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문가능[v1_국내선물-005] | `examples_llm/domestic_futureoption/inquire_psbl_order/inquire_psbl_order.py` |
| VTTO5201R | domestic_futureoption | 미착수 | [국내선물옵션] 주문/계좌 > 선물옵션 주문체결내역조회[v1_국내선물-003] | `examples_llm/domestic_futureoption/inquire_ccnl/inquire_ccnl.py` |
| VTTS3007R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 매수가능금액조회 [v1_해외주식-014] | `examples_llm/overseas_stock/inquire_psamount/inquire_psamount.py` |
| VTTS3012R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 잔고 [v1_해외주식-006] | `examples_llm/overseas_stock/inquire_balance/inquire_balance.py` |
| VTTS3013U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| VTTS3035R | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 주문체결내역 [v1_해외주식-007] | `examples_llm/overseas_stock/inquire_ccnl/inquire_ccnl.py` |
| VTTT1004U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 정정취소주문[v1_해외주식-003] | `examples_llm/overseas_stock/order_rvsecncl/order_rvsecncl.py` |
| VTTT3014U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| VTTT3016U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] | `examples_llm/overseas_stock/order_resv/order_resv.py` |
| VTTT3017U | overseas_stock | 미착수 | [해외주식] 주문/계좌 > 해외주식 예약주문접수취소[v1_해외주식-004] | `examples_llm/overseas_stock/order_resv_ccnl/order_resv_ccnl.py` |

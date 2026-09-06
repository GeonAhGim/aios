"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- 도메인별 생성 mixin을 하나로 묶는다.

`scripts/kis_generate_adapters.py`가 생성한다 -- 손으로 수정하지 말 것. adapter.py
는 `KISGeneratedMixin` 하나만 상속하면 된다(신규 청크가 생겨도 이 파일 하나만
재생성되고 adapter.py는 바뀌지 않는다).
"""
from __future__ import annotations

from src.exchanges.kis.generated.domestic_bond_01_mixin import (
    KISGeneratedDomesticBond01Mixin,
)
from src.exchanges.kis.generated.domestic_futureoption_01_mixin import (
    KISGeneratedDomesticFutureoption01Mixin,
)
from src.exchanges.kis.generated.domestic_futureoption_02_mixin import (
    KISGeneratedDomesticFutureoption02Mixin,
)
from src.exchanges.kis.generated.domestic_futureoption_03_mixin import (
    KISGeneratedDomesticFutureoption03Mixin,
)
from src.exchanges.kis.generated.domestic_futureoption_04_mixin import (
    KISGeneratedDomesticFutureoption04Mixin,
)
from src.exchanges.kis.generated.domestic_futureoption_05_mixin import (
    KISGeneratedDomesticFutureoption05Mixin,
)
from src.exchanges.kis.generated.domestic_stock_01_mixin import (
    KISGeneratedDomesticStock01Mixin,
)
from src.exchanges.kis.generated.domestic_stock_02_mixin import (
    KISGeneratedDomesticStock02Mixin,
)
from src.exchanges.kis.generated.domestic_stock_03_mixin import (
    KISGeneratedDomesticStock03Mixin,
)
from src.exchanges.kis.generated.domestic_stock_04_mixin import (
    KISGeneratedDomesticStock04Mixin,
)
from src.exchanges.kis.generated.domestic_stock_05_mixin import (
    KISGeneratedDomesticStock05Mixin,
)
from src.exchanges.kis.generated.domestic_stock_06_mixin import (
    KISGeneratedDomesticStock06Mixin,
)
from src.exchanges.kis.generated.domestic_stock_07_mixin import (
    KISGeneratedDomesticStock07Mixin,
)
from src.exchanges.kis.generated.domestic_stock_08_mixin import (
    KISGeneratedDomesticStock08Mixin,
)
from src.exchanges.kis.generated.domestic_stock_09_mixin import (
    KISGeneratedDomesticStock09Mixin,
)
from src.exchanges.kis.generated.domestic_stock_10_mixin import (
    KISGeneratedDomesticStock10Mixin,
)
from src.exchanges.kis.generated.domestic_stock_11_mixin import (
    KISGeneratedDomesticStock11Mixin,
)
from src.exchanges.kis.generated.elw_01_mixin import (
    KISGeneratedElw01Mixin,
)
from src.exchanges.kis.generated.elw_02_mixin import (
    KISGeneratedElw02Mixin,
)
from src.exchanges.kis.generated.etfetn_01_mixin import (
    KISGeneratedEtfetn01Mixin,
)
from src.exchanges.kis.generated.overseas_futureoption_01_mixin import (
    KISGeneratedOverseasFutureoption01Mixin,
)
from src.exchanges.kis.generated.overseas_futureoption_02_mixin import (
    KISGeneratedOverseasFutureoption02Mixin,
)
from src.exchanges.kis.generated.overseas_futureoption_03_mixin import (
    KISGeneratedOverseasFutureoption03Mixin,
)
from src.exchanges.kis.generated.overseas_stock_01_mixin import (
    KISGeneratedOverseasStock01Mixin,
)
from src.exchanges.kis.generated.overseas_stock_02_mixin import (
    KISGeneratedOverseasStock02Mixin,
)
from src.exchanges.kis.generated.overseas_stock_03_mixin import (
    KISGeneratedOverseasStock03Mixin,
)
from src.exchanges.kis.generated.overseas_stock_04_mixin import (
    KISGeneratedOverseasStock04Mixin,
)


class KISGeneratedMixin(
    KISGeneratedDomesticBond01Mixin,
    KISGeneratedDomesticFutureoption01Mixin,
    KISGeneratedDomesticFutureoption02Mixin,
    KISGeneratedDomesticFutureoption03Mixin,
    KISGeneratedDomesticFutureoption04Mixin,
    KISGeneratedDomesticFutureoption05Mixin,
    KISGeneratedDomesticStock01Mixin,
    KISGeneratedDomesticStock02Mixin,
    KISGeneratedDomesticStock03Mixin,
    KISGeneratedDomesticStock04Mixin,
    KISGeneratedDomesticStock05Mixin,
    KISGeneratedDomesticStock06Mixin,
    KISGeneratedDomesticStock07Mixin,
    KISGeneratedDomesticStock08Mixin,
    KISGeneratedDomesticStock09Mixin,
    KISGeneratedDomesticStock10Mixin,
    KISGeneratedDomesticStock11Mixin,
    KISGeneratedElw01Mixin,
    KISGeneratedElw02Mixin,
    KISGeneratedEtfetn01Mixin,
    KISGeneratedOverseasFutureoption01Mixin,
    KISGeneratedOverseasFutureoption02Mixin,
    KISGeneratedOverseasFutureoption03Mixin,
    KISGeneratedOverseasStock01Mixin,
    KISGeneratedOverseasStock02Mixin,
    KISGeneratedOverseasStock03Mixin,
    KISGeneratedOverseasStock04Mixin,
):
    """BR-12 생성 mixin 전체(도메인별 청크)를 결합한 파사드."""

# 의존성 잠금 (task-3577)

`requirements-lock.txt`는 `pyproject.toml`의 `[project.dependencies]`·`test`·`dev` extras를
어느 날 pip이 우연히 골라준 최신판이 아니라, 검증된 정확한 버전 조합으로 고정한다 — TA-Lib
0.8.0/0.6.8이 `tests/unit/core/indicators/test_generate_specs.py`를 깨고 0.7.1만 통과하거나,
pytest-timeout·tzdata가 아예 안 깔리는 사고가 새 PC/새 venv를 만들 때마다 반복됐기 때문이다.
갱신 절차: 깨끗한 3.10 venv를 하나 만들고 `pip install -e "C:\aios\aios[dev,test]"`로 원하는
새 버전 조합을 실제로 설치한 뒤, `pip freeze | grep -v '^-e '`로 `requirements-lock.txt`를
다시 쓰고, **그 파일만으로**(`pip install -r requirements-lock.txt && pip install -e . --no-deps`)
새 venv를 하나 더 만들어 `TEST_DATABASE_URL`을 잡고 `pytest tests/unit -q`가 그대로 녹색인지
확인한 다음 커밋한다 — 이 마지막 재현 확인 없이 잠금 파일만 손으로 고치지 않는다.
`C:\aios\pm\scripts\bootstrap_new_pc.ps1` §4도 이 파일로 설치하므로, 새 PC 이전 때마다 같은
환경이 재현된다.

`setuptools`·`pip`은 3.10 venv가 `ensurepip`로 깔아주는 65.5.0/23.0.1(각각 PYSEC-2022-43012 등,
PYSEC-2026-196 등 다건 취약)보다 낮은 버전으로 새 venv가 시작하는 걸 막기 위해, 다른 패키지와
동일하게 이 잠금에 명시로 고정한다 — 둘 다 `pip freeze`가 기본으로 빼먹는 부트스트랩 도구라
잊기 쉽다(task-3622).

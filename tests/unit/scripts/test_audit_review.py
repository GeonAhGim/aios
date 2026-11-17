"""scripts/audit_review: 감사 지적 수집·상태 규칙·증거 인용 검증(2026-11-18)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.audit_review import evidence, ingest, status
from scripts.audit_review.schema import (
    new_record,
    quote_problems,
    record_problems,
    transition_problems,
)

CHECKPOINT = """# 감사 체크포인트

## A-12 — USER 승인 처리의 RLS 문맥 불일치

심각도: 높음(정상 승인 차단).

`approve_request`가 system_transaction을 쓴다.

## 확인한 정상 보호

지적이 아닌 섹션이다.

## A-13: 세션 MFA 시각 전파

본문이다.
"""


def _rec(**over):
    rec = new_record("004", "A-12", "제목", "본문", None)
    rec.update(over)
    return rec


def _read(files: dict[str, str]):
    return lambda rel: files.get(rel)


# --------------------------------------------------------------------------- 수집


def test_parse_checkpoint_extracts_findings_and_skips_other_sections():
    recs = ingest.parse_checkpoint(CHECKPOINT, "004")
    assert [(r["id"], r["severity_label"]) for r in recs] == [("A-12", "높음"), ("A-13", None)]
    assert recs[0]["title"] == "USER 승인 처리의 RLS 문맥 불일치"
    assert all(r["state"] == "DISCOVERED" for r in recs)


def test_ingest_is_incremental_and_never_overwrites_review_fields(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "AIOS_ADVERSARIAL_AUDIT_CHECKPOINT_004.md").write_text(CHECKPOINT, encoding="utf-8")
    out = tmp_path / "findings.jsonl"
    assert ingest.ingest(src, out)["new"] == 2

    records = ingest.load(out)
    records[0].update(state="TRIAGED", category="auth", note="검토함")
    ingest.write(out, records)

    stats = ingest.ingest(src, out)  # 같은 원문 재수집
    assert stats["new"] == 0 and stats["kept"] == 2 and stats["total"] == 2
    kept = ingest.load(out)[0]
    assert kept["state"] == "TRIAGED" and kept["category"] == "auth" and kept["note"] == "검토함"

    # 원문이 바뀌면 검토된 기록은 덮어쓰지 않고 표시만 한다
    changed = CHECKPOINT.replace("system_transaction", "tenant_transaction")
    (src / "AIOS_ADVERSARIAL_AUDIT_CHECKPOINT_004.md").write_text(changed, encoding="utf-8")
    stats = ingest.ingest(src, out)
    assert stats["source_changed"] == 1
    after = ingest.load(out)[0]
    assert after["source_changed"] is True and "system_transaction" in after["body"]


# --------------------------------------------------------------------------- 상태 규칙


def test_states_advance_one_step_at_a_time_and_rejected_only_from_verified():
    assert transition_problems("DISCOVERED", "TRIAGED") == []
    assert transition_problems("DISCOVERED", "VERIFIED") != []  # 건너뛰기
    assert transition_problems("CONFIRMED", "TRIAGED") != []  # 되돌리기
    assert transition_problems("VERIFIED", "REJECTED") == []
    assert transition_problems("TRIAGED", "REJECTED") != []


def test_verified_needs_a_verdict_and_evidence_that_really_exists():
    files = {"src/a.py": "def approve_request():\n    return system_transaction()\n"}
    good = _rec(
        state="VERIFIED",
        verdict="still_valid",
        evidence=[{"file": "src/a.py", "line": 2, "quote": "return system_transaction()"}],
    )
    assert record_problems(good, _read(files)) == []

    no_evidence = _rec(state="VERIFIED", verdict="still_valid")
    assert any("증거" in p for p in record_problems(no_evidence, _read(files)))

    hallucinated = _rec(
        state="VERIFIED",
        verdict="still_valid",
        evidence=[{"file": "src/a.py", "line": 2, "quote": "return tenant_transaction()"}],
    )
    assert any("인용이 파일에 없다" in p for p in quote_problems(hallucinated, _read(files)))

    missing_file = _rec(
        state="VERIFIED",
        verdict="still_valid",
        evidence=[{"file": "src/gone.py", "quote": "x"}],
    )
    assert any("저장소에 없는 파일" in p for p in record_problems(missing_file, _read(files)))


def test_confirmed_and_fixed_need_severity_priority_and_fix_task():
    files = {"a.py": "x = 1\n"}
    ev = [{"file": "a.py", "line": 1, "quote": "x = 1"}]
    confirmed = _rec(state="CONFIRMED", verdict="still_valid", evidence=ev)
    assert any("severity/priority" in p for p in record_problems(confirmed, _read(files)))
    confirmed.update(severity="high", priority="A")
    assert record_problems(confirmed, _read(files)) == []
    fixed = dict(confirmed, state="FIXED")
    assert any("fix_task" in p for p in record_problems(fixed, _read(files)))
    assert record_problems(dict(fixed, fix_task=25500), _read(files)) == []


def test_unknown_enum_values_are_rejected():
    assert any("priority" in p for p in record_problems(_rec(priority="Z"), _read({})))
    assert record_problems(_rec(state="WAT"), _read({}))


# --------------------------------------------------------------------------- 갱신 명령


def _args(**over) -> argparse.Namespace:
    base = dict(
        state=None,
        verdict=None,
        category=None,
        severity=None,
        priority=None,
        fix_task=None,
        note=None,
        evidence=[],
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_set_command_writes_only_when_every_rule_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    out = tmp_path / "findings.jsonl"
    ingest.write(out, [_rec()])

    # TRIAGED로 한 단계
    assert (
        status.main(
            [
                "--findings",
                str(out),
                "--repo-root",
                str(tmp_path),
                "set",
                "004",
                "A-12",
                "--state",
                "TRIAGED",
                "--category",
                "auth",
            ]
        )
        == 0
    )
    # 증거 없이 VERIFIED는 거부 -- 파일은 그대로
    before = out.read_text(encoding="utf-8")
    rc = status.main(
        [
            "--findings",
            str(out),
            "--repo-root",
            str(tmp_path),
            "set",
            "004",
            "A-12",
            "--state",
            "VERIFIED",
            "--verdict",
            "still_valid",
        ]
    )
    assert rc == 1 and out.read_text(encoding="utf-8") == before
    # 증거 인용이 실재하면 통과
    rc = status.main(
        [
            "--findings",
            str(out),
            "--repo-root",
            str(tmp_path),
            "set",
            "004",
            "A-12",
            "--state",
            "VERIFIED",
            "--verdict",
            "still_valid",
            "--evidence",
            "src/a.py|2|return 1",
        ]
    )
    assert rc == 0
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["state"] == "VERIFIED" and rec["evidence"][0]["line"] == 2


def test_check_flags_a_quote_that_stopped_existing_after_the_code_changed(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = tmp_path / "findings.jsonl"
    rec = _rec(
        state="VERIFIED",
        verdict="still_valid",
        evidence=[{"file": "a.py", "line": 1, "quote": "x = 1"}],
    )
    ingest.write(out, [rec])
    assert status.main(["--findings", str(out), "--repo-root", str(tmp_path), "--check"]) == 0
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")  # 코드가 바뀌어 증거가 낡음
    assert status.main(["--findings", str(out), "--repo-root", str(tmp_path), "--check"]) == 1


# --------------------------------------------------------------------------- 증거 묶음


def test_rank_files_prefers_rare_identifiers_and_ignores_common_ones():
    index = {
        "rare_name": ["a.py"],
        "mid_name": ["a.py", "b.py"],
        "common_name": [f"f{i}.py" for i in range(200)],
    }
    ranked = evidence.rank_files(["rare_name", "mid_name", "common_name"], lambda t: index[t])
    assert ranked[0][0] == "a.py" and ranked[0][1] == ["rare_name", "mid_name"]
    assert all(not path.startswith("f") for path, _ in ranked)  # 흔한 이름은 단서가 아니다


def test_window_centers_on_the_definition_line():
    text = "\n".join([*(f"a = {i}" for i in range(30)), "def target_fn():", "    return 1"])
    out = evidence.window(text, ["target_fn"], width=5)
    assert "def target_fn():" in out and out.splitlines()[0].startswith("29:")


def test_defined_in_separates_a_definition_from_an_import():
    definition = "def handle_check(x):\n    return x\n"
    importer = "from a.b import handle_check\nhandle_check(1)\n"
    assert evidence.defined_in(definition, ["handle_check"]) == ["handle_check"]
    assert evidence.defined_in(importer, ["handle_check"]) == []

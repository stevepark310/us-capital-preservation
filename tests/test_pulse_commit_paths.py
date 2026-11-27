"""10분 펄스의 커밋 범위 — config 누락이 Safety Regression 하드락을 깨뜨렸다(2026-11-28)."""
import importlib

pulse = importlib.import_module("scripts.run_10m_pulse")


def test_pulse_stages_config_together_with_data():
    """스크리너가 config/swing_approval.json 유니버스를 갱신하므로 config도 같이 커밋한다.

    data만 커밋하면 '승인 정본(과거 유니버스) vs 현재 스캔'이 커밋 상태에서 어긋나
    `hard_lock_report`가 universe.screened 위반을 내고 CI Safety Regression이 8건 실패한다.
    """
    assert "config" in pulse.STAGE_PATHS
    assert "data" in pulse.STAGE_PATHS
    assert "docs" in pulse.STAGE_PATHS


def test_pulse_uses_stage_paths_and_never_add_all():
    """`git add`는 STAGE_PATHS 목록으로만 — `git add -A`는 다른 작업 파일까지 쓸어담는다."""
    src = open(pulse.__file__, encoding="utf-8").read()
    assert 'git("add", *STAGE_PATHS)' in src
    assert '"add", "-A"' not in src

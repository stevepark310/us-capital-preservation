"""백테스트 결과 요약 — 업무보고 고정 게시글(2026-11-21 동훈님 요청)."""
from core import backtest_digest as bd


def test_digest_reads_all_four_backtest_families():
    d = bd.collect()
    keys = [s["key"] for s in d["sections"]]
    assert keys == ["m8", "m8_v2", "premarket", "inverse"]
    for s in d["sections"]:
        assert s["rows"], f"{s['key']} 수치가 비어 있다"
        assert s["source"] and s["verdict"]


def test_digest_numbers_come_from_result_files():
    d = bd.collect()
    secs = {s["key"]: s for s in d["sections"]}
    m8 = " ".join(r["value"] for r in secs["m8"]["rows"])
    assert "0.291" in m8 and "2.57" in m8                 # OOS 20일 IC (SPY)
    assert "47.6%" in m8                                 # 부호 안정성
    v2 = " ".join(r["value"] for r in secs["m8_v2"]["rows"])
    assert "36.34%" in v2 and "25.74%" in v2 and "8.67%" in v2
    pre = " ".join(r["value"] for r in secs["premarket"]["rows"])
    assert "+2.018%" in pre and "-1.934%" in pre         # 추격금지 OOS / 헤지 OOS
    inv = " ".join(r["value"] for r in secs["inverse"]["rows"])
    assert "+0.657%" in inv and "2.46" in inv            # profit_lock EV / PF
    assert "-8.0%" in inv                                # 다일 보유


def test_digest_reports_live_switch_state_separately():
    d = bd.collect()
    st = d["policy_state"]
    assert st["macro8"]["mode"] == "shadow"
    assert st["macro8"]["enabled_for_sizing"] is False
    # 스위치 값은 정본 파일을 그대로 반영한다(2026-11-26 H-0009 승인으로 enabled=true).
    import json as _json
    import os as _os
    from core import backtest_digest as _bd
    _pol = _json.loads(open(_os.path.join(_bd.BASE_DIR, "config", "regime_policy.json"),
                            encoding="utf-8").read())
    assert st["regime_v2"]["enabled"] is _pol.get("enabled")
    assert st["premarket"]["enabled"] is True
    assert st["premarket"]["manual_intervention_only"] is True
    text = bd.summary_text(d)
    assert "현재 실전 반영 상태" in text and "manual_intervention_only=True" in text
    assert f"sell_only={st['live_gate']['sell_only']}" in text


def test_digest_skips_missing_files_without_fake_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "M8_BACKTEST", str(tmp_path / "none.json"))
    monkeypatch.setattr(bd, "M8_REGIME_COMBINE", str(tmp_path / "none2.json"))
    monkeypatch.setattr(bd, "PREMARKET", str(tmp_path / "none3.json"))
    monkeypatch.setattr(bd, "EXIT_INFLECTION", str(tmp_path / "none4.json"))
    monkeypatch.setattr(bd, "BEAR_INVERSE", str(tmp_path / "none5.json"))
    d = bd.collect()
    assert d["sections"] == [] and d["source_files"] == []
    assert "현재 실전 반영 상태" in bd.summary_text(d)


def test_feed_post_is_pinned_and_rebuilt_by_update_feed():
    from scripts import update_feed
    clock = {"now_kst": "2026-11-21 19:10:00", "phase": "PRE_MARKET"}
    post = update_feed.backtest_post(clock, bd.collect())
    assert post["id"] == "post-backtest-digest"
    assert post["pinned"] is True and post["category"] == "BACKTEST"
    assert "백테스트" in post["badge"]
    assert "0.291" in post["title"]
    assert "OOS 20일 IC (SPY)" in post["content"]
    assert any(m["label"] == "M8 반영" for m in post["metrics"])

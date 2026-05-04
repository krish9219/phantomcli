"""Tests for cost_tracker — pricing, session accumulation, history, alerts."""
from __future__ import annotations

import json
import os
import time

import pytest

from omnicli import cost_tracker as ct


@pytest.fixture(autouse=True)
def _isolate_spend_log(tmp_path, monkeypatch):
    """Point the spend log at a tmp file and reset session state per test."""
    monkeypatch.setenv("PHANTOM_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    ct.reset_session()
    yield tmp_path


class TestPriceLookup:
    def test_exact_match_opus(self):
        p = ct.price_for("claude-opus-4-7")
        assert p.vendor == "anthropic"
        assert p.input_per_million == 15.0

    def test_exact_match_haiku(self):
        assert ct.price_for("claude-haiku-4-5").input_per_million == 0.8

    def test_loose_match_path_prefix(self):
        p = ct.price_for("meta/llama-3.3-70b-instruct")
        assert p.vendor == "nvidia"

    def test_unknown_model_falls_back_to_default(self):
        p = ct.price_for("made-up-future-model-v99")
        # Default is the (unknown) safe guess
        assert p.model == "(unknown)"

    def test_empty_model_returns_default(self):
        assert ct.price_for("").model == "(unknown)"

    def test_register_custom_price(self):
        ct.register_price(ct.PriceEntry("my-custom", 1.0, 3.0, vendor="self-host"))
        p = ct.price_for("my-custom")
        assert p.vendor == "self-host"
        assert p.output_per_million == 3.0


class TestComputeUsd:
    def test_opus_known_rate(self):
        # 1000 prompt + 500 output on Opus
        cost = ct.compute_usd("claude-opus-4-7",
                              prompt_tokens=1000, completion_tokens=500)
        # 1000 * 15 / 1M + 500 * 75 / 1M = 0.015 + 0.0375 = 0.0525
        assert cost == pytest.approx(0.0525, rel=1e-6)

    def test_cached_tokens_discount(self):
        regular = ct.compute_usd("claude-opus-4-7", 2000, 0)
        cached  = ct.compute_usd("claude-opus-4-7", 2000, 0, cached_tokens=1500)
        # With 1500/2000 cached at 1/10 of the normal rate, cached should be cheaper
        assert cached < regular

    def test_zero_tokens_is_zero(self):
        assert ct.compute_usd("gpt-4o", 0, 0) == 0.0

    def test_no_negative_regular_prompt(self):
        """cached_tokens > prompt_tokens shouldn't produce a negative bill."""
        cost = ct.compute_usd("claude-opus-4-7", 100, 0, cached_tokens=500)
        # Normal prompt clamped to 0; only cached is billed
        assert cost >= 0


class TestRecordAndSession:
    def test_record_returns_cost(self):
        cost = ct.record("gpt-4o", 1000, 500)
        assert cost > 0
        assert ct.total_session() == cost

    def test_multiple_records_accumulate(self):
        ct.record("gpt-4o", 500, 250)
        ct.record("gpt-4o", 500, 250)
        s = ct.session_summary()
        assert s.calls == 2
        assert s.prompt_tokens == 1000
        assert s.completion_tokens == 500

    def test_per_model_breakdown(self):
        ct.record("gpt-4o", 100, 50)
        ct.record("claude-haiku-4-5", 200, 100)
        ct.record("gpt-4o", 100, 50)
        s = ct.session_summary()
        assert s.by_model["gpt-4o"]["calls"] == 2
        assert s.by_model["claude-haiku-4-5"]["calls"] == 1

    def test_reset_session_clears(self):
        ct.record("gpt-4o", 1000, 500)
        assert ct.total_session() > 0
        ct.reset_session()
        assert ct.total_session() == 0
        assert ct.session_summary().calls == 0


class TestSpendLog:
    def test_writes_to_log(self, _isolate_spend_log):
        ct.record("gpt-4o", 100, 50)
        path = _isolate_spend_log / "spend.jsonl"
        assert path.is_file()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["model"] == "gpt-4o"
        assert rows[0]["p_tok"] == 100
        assert rows[0]["c_tok"] == 50
        assert rows[0]["usd"] > 0

    def test_history_includes_recent(self):
        ct.record("gpt-4o", 100, 50)
        ct.record("gpt-4o", 200, 100)
        rows = ct.history(days=7)
        assert len(rows) == 2

    def test_history_filters_by_cutoff(self, _isolate_spend_log):
        # Manually write an old entry (8 days ago)
        old_ts = time.time() - 8 * 86400
        with open(_isolate_spend_log / "spend.jsonl", "w") as f:
            f.write(json.dumps({"ts": old_ts, "model": "x", "p_tok": 1,
                                "c_tok": 1, "usd": 0.01,
                                "iso": "2020-01-01T00:00:00+00:00"}) + "\n")
        ct.record("gpt-4o", 100, 50)  # within cutoff
        rows = ct.history(days=7)
        assert len(rows) == 1
        assert rows[0]["model"] == "gpt-4o"

    def test_malformed_log_line_skipped(self, _isolate_spend_log):
        path = _isolate_spend_log / "spend.jsonl"
        with open(path, "w") as f:
            f.write("{not json}\n")
            f.write(json.dumps({"ts": time.time(), "model": "gpt-4o",
                                "p_tok": 1, "c_tok": 1, "usd": 0.01,
                                "iso": "2099-01-01T00:00:00+00:00"}) + "\n")
        rows = ct.history(days=7)
        assert len(rows) == 1  # broken line ignored

    def test_no_log_file_returns_empty(self, _isolate_spend_log):
        # Don't record anything
        assert ct.history() == []


class TestAlertHook:
    def test_alert_fires_when_over_threshold(self, isolated_hooks_config, tmp_path):
        marker = tmp_path / "alert.fired"
        isolated_hooks_config.write_text(json.dumps({
            "Notification": [{"match": "warn", "cmd": f"touch {marker}"}],
        }))
        ct.set_daily_alert_threshold(0.0001)  # tiny — will trip after one call
        ct.record("claude-opus-4-7", 10_000, 5_000)
        assert marker.is_file()

    def test_alert_fires_only_once_per_day(self, isolated_hooks_config, tmp_path):
        counter = tmp_path / "count.txt"
        # Hook appends to counter on every fire
        isolated_hooks_config.write_text(json.dumps({
            "Notification": [{"match": "warn", "cmd": f"echo 1 >> {counter}"}],
        }))
        ct.set_daily_alert_threshold(0.0001)
        ct.record("claude-opus-4-7", 10_000, 5_000)
        ct.record("claude-opus-4-7", 10_000, 5_000)
        ct.record("claude-opus-4-7", 10_000, 5_000)
        # Three spending records but only one alert
        fires = len(counter.read_text().splitlines()) if counter.exists() else 0
        assert fires == 1

    def test_under_threshold_no_alert(self, isolated_hooks_config, tmp_path):
        marker = tmp_path / "alert.fired"
        isolated_hooks_config.write_text(json.dumps({
            "Notification": [{"match": "*", "cmd": f"touch {marker}"}],
        }))
        ct.set_daily_alert_threshold(1000.0)  # huge
        ct.record("gpt-4o-mini", 1, 1)  # nominal spend
        assert not marker.exists()

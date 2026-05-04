"""v3.0.12: /update must return fatal=True on a successful upgrade so the
REPL exits cleanly and the user relaunches on freshly-loaded bytes.

Prior behaviour (≤3.0.11) left the user inside the REPL after an update —
but Python had already cached the OLD modules in sys.modules, so every
subsequent command silently ran stale code. We saw this in production when
a user updated to 3.0.11 and still got 3.0.10's `/web` output because the
code hadn't been reimported."""
from __future__ import annotations


class TestUpdateExitsOnSuccess:
    def test_successful_update_is_fatal(self, monkeypatch):
        from omnicli import commands as _c

        monkeypatch.setattr(
            "omnicli.cli._check_for_update",
            lambda: {"version": "9.9.9", "downloadUrl": "http://x/y.zip"},
        )
        monkeypatch.setattr("omnicli.cli._do_update", lambda: True)

        r = _c._update()

        assert r.handled is True
        assert r.fatal is True, "successful /update must exit REPL — else user runs stale in-memory code"
        assert "9.9.9" in r.reply
        assert "phantom chat" in r.reply.lower()

    def test_already_latest_is_not_fatal(self, monkeypatch):
        from omnicli import commands as _c

        monkeypatch.setattr("omnicli.cli._check_for_update", lambda: None)

        r = _c._update()

        assert r.handled is True
        assert r.fatal is False
        assert "latest" in r.reply.lower()

    def test_failed_update_is_not_fatal(self, monkeypatch):
        from omnicli import commands as _c

        monkeypatch.setattr(
            "omnicli.cli._check_for_update",
            lambda: {"version": "9.9.9", "downloadUrl": "http://x/y.zip"},
        )
        monkeypatch.setattr("omnicli.cli._do_update", lambda: False)

        r = _c._update()

        assert r.handled is True
        assert r.fatal is False, "failed update should leave user in REPL to retry"
        assert "failed" in r.reply.lower()

    def test_exception_is_not_fatal(self, monkeypatch):
        from omnicli import commands as _c

        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr("omnicli.cli._check_for_update", _boom)

        r = _c._update()

        assert r.handled is True
        assert r.fatal is False
        assert "boom" in r.reply

"""Static checks on the PyInstaller spec — does not actually run pyinstaller."""

from __future__ import annotations

from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent.parent / "phantomcli.spec"


def test_spec_file_exists():
    assert SPEC.exists()


def test_spec_targets_v1_binary_name():
    text = SPEC.read_text()
    assert "name='phantom'" in text
    assert "phantomcli'" not in text or text.count("name='phantom'") >= 1


def test_spec_bundles_dashboard_static():
    text = SPEC.read_text()
    assert "phantom/dashboard/static" in text


def test_spec_bundles_builtin_plugins():
    text = SPEC.read_text()
    for plugin in (
        "phantom.plugins.builtin.github_pr",
        "phantom.plugins.builtin.web_screenshot",
        "phantom.plugins.builtin.code_review",
    ):
        assert plugin in text, f"missing hiddenimport: {plugin}"


def test_spec_bundles_v1_subpackages():
    text = SPEC.read_text()
    for module in (
        "phantom.daemon",
        "phantom.swarm",
        "phantom.selfdev",
        "phantom.memory.importers",
        "phantom.config.providers",
    ):
        assert module in text, f"missing hiddenimport: {module}"


def test_spec_excludes_test_runtime():
    text = SPEC.read_text()
    assert "'pytest'" in text or '"pytest"' in text

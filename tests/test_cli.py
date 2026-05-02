"""Tests for command-line wiring."""

import sys
import types

from click.testing import CliRunner

from heron.cli import cli


class FakeApp:
    def __init__(self):
        self.run_kwargs = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs


def install_fake_dashboard(monkeypatch):
    app = FakeApp()
    module = types.ModuleType("heron.dashboard")
    module.create_app = lambda: app
    monkeypatch.setitem(sys.modules, "heron.dashboard", module)
    return app


def test_dashboard_defaults_to_localhost(monkeypatch):
    app = install_fake_dashboard(monkeypatch)

    result = CliRunner().invoke(cli, ["dashboard"])

    assert result.exit_code == 0
    assert app.run_kwargs == {"host": "127.0.0.1", "port": 5001, "debug": False}
    assert "http://127.0.0.1:5001" in result.output
    assert "Phone/LAN access needs `--lan`" in result.output


def test_dashboard_lan_binds_all_interfaces(monkeypatch):
    app = install_fake_dashboard(monkeypatch)
    monkeypatch.setattr("heron.cli._dashboard_lan_urls", lambda port: [f"http://192.168.1.23:{port}"])

    result = CliRunner().invoke(cli, ["dashboard", "--lan"])

    assert result.exit_code == 0
    assert app.run_kwargs == {"host": "0.0.0.0", "port": 5001, "debug": False}
    assert "Accessible locally via: http://127.0.0.1:5001" in result.output
    assert "Accessible via: http://192.168.1.23:5001" in result.output


def test_dashboard_host_all_interfaces_prints_lan_url(monkeypatch):
    app = install_fake_dashboard(monkeypatch)
    monkeypatch.setattr("heron.cli._dashboard_lan_urls", lambda port: [f"http://10.0.0.42:{port}"])

    result = CliRunner().invoke(cli, ["dashboard", "--host", "0.0.0.0", "--port", "5002"])

    assert result.exit_code == 0
    assert app.run_kwargs == {"host": "0.0.0.0", "port": 5002, "debug": False}
    assert "http://10.0.0.42:5002" in result.output

from __future__ import annotations

import sys
from types import SimpleNamespace

from piuda import cli


def test_run_cli_uses_host_and_port_from_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "PIUDA_HOST=127.0.0.2\nPIUDA_PORT=18123\nPIUDA_DEMO_MODE=0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("PIUDA_HOST", "PIUDA_PORT", "PIUDA_DEMO_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["piuda", "run"])
    monkeypatch.setattr(
        cli,
        "create_app",
        lambda: SimpleNamespace(config={"DATABASE": "test.db", "DEMO_MODE": False}),
    )
    served = {}

    def fake_serve(app, *, host, port, threads):
        served.update(host=host, port=port, threads=threads)

    monkeypatch.setattr(cli, "serve", fake_serve)

    cli.main()

    assert served == {"host": "127.0.0.2", "port": 18123, "threads": 8}

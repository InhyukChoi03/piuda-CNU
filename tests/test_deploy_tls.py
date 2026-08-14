from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_tls_certificate_is_stable_across_restarts(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "deploy" / "ensure-tls.sh"
    environment = {**os.environ, "PIUDA_DATA_DIR": str(tmp_path)}

    subprocess.run([str(script)], check=True, env=environment, capture_output=True, text=True)
    certificate = tmp_path / "tls" / "piuda-server.crt"
    first_certificate = certificate.read_bytes()

    subprocess.run([str(script)], check=True, env=environment, capture_output=True, text=True)

    assert certificate.read_bytes() == first_certificate

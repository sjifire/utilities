"""Fixtures for end-to-end browser tests.

Starts the ops server as a subprocess in dev mode (no auth, in-memory stores)
and provides a fresh Playwright browser context per test.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

STARTUP_TIMEOUT = 15  # seconds

# Root of the source tree — needed for PYTHONPATH so the subprocess
# can resolve the ``sjifire`` package (editable install via .pth files
# may not work in a subprocess with a stripped env).
_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")


def _find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_port() -> int:
    """Return a free TCP port for the test server."""
    return _find_free_port()


@pytest.fixture(scope="session")
def base_url(server_port: int) -> str:
    """Base URL for the test server."""
    return f"http://127.0.0.1:{server_port}"


@pytest.fixture(scope="session", autouse=True)
def _server_process(server_port: int, base_url: str):
    """Start uvicorn subprocess in dev mode and wait for it to be ready."""
    env = {
        # Inherit minimal env for Python resolution
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        # Ensure the subprocess can resolve the sjifire package
        "PYTHONPATH": _SRC_DIR,
        # Dev mode: empty client ID disables auth, injects dev@localhost user
        "ENTRA_MCP_API_CLIENT_ID": "",
        "ENTRA_MCP_API_CLIENT_SECRET": "",
        # In-memory stores: empty endpoints skip Cosmos/Blob connections
        "COSMOS_ENDPOINT": "",
        "AZURE_STORAGE_ACCOUNT_URL": "",
        # Empty service credentials: graceful no-ops
        "MS_GRAPH_TENANT_ID": "",
        "MS_GRAPH_CLIENT_ID": "",
        "MS_GRAPH_CLIENT_SECRET": "",
        "ALADTEC_URL": "",
        "ALADTEC_USERNAME": "",
        "ALADTEC_PASSWORD": "",
        "ISPYFIRE_URL": "",
        "ISPYFIRE_USERNAME": "",
        "ISPYFIRE_PASSWORD": "",
        "MCP_SERVER_URL": f"http://127.0.0.1:{server_port}",
        "TESTING": "1",
    }

    # Inherit VIRTUAL_ENV if set (helps uv/Python resolution)
    if venv := os.environ.get("VIRTUAL_ENV"):
        env["VIRTUAL_ENV"] = venv

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "sjifire.ops.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Poll /health until the server is ready
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1)
            if resp.status_code == 200:
                break
        except httpx.ConnectError:
            pass
        time.sleep(0.3)
    else:
        proc.terminate()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        pytest.fail(
            f"Server did not start within {STARTUP_TIMEOUT}s.\nstdout: {stdout}\nstderr: {stderr}"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def _seeded(_server_process, base_url: str):
    """Seed the server's in-memory stores with test fixture data (once per session)."""
    from tests.e2e.seed_data import seed_payload

    resp = httpx.post(f"{base_url}/test/seed", json=seed_payload(), timeout=5)
    assert resp.status_code == 200, f"Seed failed: {resp.text}"
    return resp.json()


@pytest.fixture(scope="session")
def kiosk_token():
    """Generate a valid kiosk token using the dev signing key."""
    from itsdangerous import URLSafeSerializer

    dev_key = "kiosk-dev-signing-key-not-for-production"
    s = URLSafeSerializer(dev_key, salt="kiosk-token")
    return s.dumps({"label": "e2e-test"})


@pytest.fixture
def page(browser, base_url: str):
    """Fresh browser context and page per test, with base_url set."""
    context = browser.new_context(base_url=base_url)
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def seeded_page(browser, base_url: str, _seeded):
    """Fresh browser context with seeded data available."""
    context = browser.new_context(base_url=base_url)
    pg = context.new_page()
    yield pg
    context.close()

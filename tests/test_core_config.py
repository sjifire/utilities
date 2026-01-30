"""Tests for sjifire.core.config."""

import json
from unittest.mock import patch

import pytest

from sjifire.core.config import (
    get_aladtec_credentials,
    get_graph_credentials,
    load_entra_sync_config,
)


class TestGetAladtecCredentials:
    """Tests for get_aladtec_credentials function."""

    def test_returns_credentials_when_set(self, mock_env_vars):
        # mock_env_vars sets env vars, but load_dotenv may override them
        # Patch load_dotenv to do nothing so our env vars are used
        with patch("sjifire.core.config.load_dotenv"):
            url, username, password = get_aladtec_credentials()

        assert url == "https://test.aladtec.com"
        assert username == "testuser"
        assert password == "testpass"

    def test_raises_when_url_missing(self, monkeypatch):
        monkeypatch.delenv("ALADTEC_URL", raising=False)
        monkeypatch.setenv("ALADTEC_USERNAME", "user")
        monkeypatch.setenv("ALADTEC_PASSWORD", "pass")

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="ALADTEC_URL"),
        ):
            get_aladtec_credentials()

    def test_raises_when_username_missing(self, monkeypatch):
        monkeypatch.setenv("ALADTEC_URL", "https://test.com")
        monkeypatch.delenv("ALADTEC_USERNAME", raising=False)
        monkeypatch.setenv("ALADTEC_PASSWORD", "pass")

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="ALADTEC_USERNAME"),
        ):
            get_aladtec_credentials()

    def test_raises_when_password_missing(self, monkeypatch):
        monkeypatch.setenv("ALADTEC_URL", "https://test.com")
        monkeypatch.setenv("ALADTEC_USERNAME", "user")
        monkeypatch.delenv("ALADTEC_PASSWORD", raising=False)

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="ALADTEC_PASSWORD"),
        ):
            get_aladtec_credentials()

    def test_raises_when_all_missing(self, monkeypatch):
        monkeypatch.delenv("ALADTEC_URL", raising=False)
        monkeypatch.delenv("ALADTEC_USERNAME", raising=False)
        monkeypatch.delenv("ALADTEC_PASSWORD", raising=False)

        with patch("sjifire.core.config.load_dotenv"), pytest.raises(ValueError):
            get_aladtec_credentials()


class TestGetGraphCredentials:
    """Tests for get_graph_credentials function."""

    def test_returns_credentials_when_set(self, mock_env_vars):
        with patch("sjifire.core.config.load_dotenv"):
            tenant_id, client_id, client_secret = get_graph_credentials()

        assert tenant_id == "test-tenant-id"
        assert client_id == "test-client-id"
        assert client_secret == "test-client-secret"

    def test_raises_when_tenant_id_missing(self, monkeypatch):
        monkeypatch.delenv("MS_GRAPH_TENANT_ID", raising=False)
        monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "client")
        monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "secret")

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="MS_GRAPH_TENANT_ID"),
        ):
            get_graph_credentials()

    def test_raises_when_client_id_missing(self, monkeypatch):
        monkeypatch.setenv("MS_GRAPH_TENANT_ID", "tenant")
        monkeypatch.delenv("MS_GRAPH_CLIENT_ID", raising=False)
        monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "secret")

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="MS_GRAPH_CLIENT_ID"),
        ):
            get_graph_credentials()

    def test_raises_when_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("MS_GRAPH_TENANT_ID", "tenant")
        monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "client")
        monkeypatch.delenv("MS_GRAPH_CLIENT_SECRET", raising=False)

        with (
            patch("sjifire.core.config.load_dotenv"),
            pytest.raises(ValueError, match="MS_GRAPH_CLIENT_SECRET"),
        ):
            get_graph_credentials()


class TestLoadEntraSyncConfig:
    """Tests for load_entra_sync_config function."""

    def test_loads_company_name(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "entra_sync.json"
        config_file.write_text(json.dumps({"company_name": "Test Fire Dept", "domain": "test.org"}))

        with patch("sjifire.core.config.get_project_root", return_value=tmp_path):
            config = load_entra_sync_config()

        assert config.company_name == "Test Fire Dept"

    def test_loads_domain(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "entra_sync.json"
        config_file.write_text(
            json.dumps({"company_name": "Test Fire Dept", "domain": "testfire.org"})
        )

        with patch("sjifire.core.config.get_project_root", return_value=tmp_path):
            config = load_entra_sync_config()

        assert config.domain == "testfire.org"

    def test_default_domain_when_missing(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "entra_sync.json"
        config_file.write_text(json.dumps({"company_name": "Test Fire Dept"}))

        with patch("sjifire.core.config.get_project_root", return_value=tmp_path):
            config = load_entra_sync_config()

        assert config.domain == "sjifire.org"

    def test_raises_when_file_missing(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Don't create the file

        with (
            patch("sjifire.core.config.get_project_root", return_value=tmp_path),
            pytest.raises(FileNotFoundError),
        ):
            load_entra_sync_config()

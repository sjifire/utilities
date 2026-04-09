"""Tests for core/msgraph_client.py - Microsoft Graph API client wrapper."""

from unittest.mock import MagicMock, patch

from msgraph import GraphServiceClient

from sjifire.core.msgraph_client import get_graph_client

# Test credentials (not real secrets)
TEST_TENANT_ID = "test-tenant-id"
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"


class TestGetGraphClient:
    """Tests for get_graph_client function."""

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_creates_client(self, mock_get_creds, mock_credential_class, mock_client_class):
        """Should create and return a GraphServiceClient."""
        mock_get_creds.return_value = ("tenant-id", "client-id", "client-secret")
        mock_credential = MagicMock()
        mock_credential_class.return_value = mock_credential
        mock_client = MagicMock(spec=GraphServiceClient)
        mock_client_class.return_value = mock_client

        result = get_graph_client()

        assert result is mock_client

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_uses_credentials_from_config(
        self, mock_get_creds, mock_credential_class, mock_client_class
    ):
        """Should use credentials from get_graph_credentials."""
        mock_get_creds.return_value = (TEST_TENANT_ID, TEST_CLIENT_ID, TEST_CLIENT_SECRET)
        mock_credential = MagicMock()
        mock_credential_class.return_value = mock_credential

        get_graph_client()

        mock_credential_class.assert_called_once_with(
            tenant_id=TEST_TENANT_ID,
            client_id=TEST_CLIENT_ID,
            client_secret=TEST_CLIENT_SECRET,
        )

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_uses_default_scope(self, mock_get_creds, mock_credential_class, mock_client_class):
        """Should use the default Graph API scope."""
        mock_get_creds.return_value = ("tenant-id", "client-id", "client-secret")
        mock_credential = MagicMock()
        mock_credential_class.return_value = mock_credential

        get_graph_client()

        mock_client_class.assert_called_once()
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["scopes"] == ["https://graph.microsoft.com/.default"]

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_passes_credential_to_client(
        self, mock_get_creds, mock_credential_class, mock_client_class
    ):
        """Should pass the credential to the GraphServiceClient."""
        mock_get_creds.return_value = ("tenant-id", "client-id", "client-secret")
        mock_credential = MagicMock()
        mock_credential_class.return_value = mock_credential

        get_graph_client()

        mock_client_class.assert_called_once()
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["credentials"] is mock_credential

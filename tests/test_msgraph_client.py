"""Tests for core/msgraph_client.py - Microsoft Graph API client wrapper."""

from unittest.mock import MagicMock, patch

from msgraph import GraphServiceClient

from sjifire.core.msgraph_client import (
    GRAPH_MAX_RETRIES,
    GRAPH_RETRY_DELAY,
    create_graph_client,
    get_graph_client,
)

# Test credentials (not real secrets)
TEST_TENANT_ID = "test-tenant-id"
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"


class TestGetGraphClient:
    """Tests for get_graph_client function."""

    @patch("sjifire.core.msgraph_client.create_graph_client")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_creates_client(self, mock_get_creds, mock_credential_class, mock_create):
        """Should create and return a GraphServiceClient."""
        mock_get_creds.return_value = ("tenant-id", "client-id", "client-secret")
        mock_credential = MagicMock()
        mock_credential_class.return_value = mock_credential
        mock_client = MagicMock(spec=GraphServiceClient)
        mock_create.return_value = mock_client

        result = get_graph_client()

        assert result is mock_client
        mock_create.assert_called_once_with(mock_credential)

    @patch("sjifire.core.msgraph_client.create_graph_client")
    @patch("sjifire.core.msgraph_client.ClientSecretCredential")
    @patch("sjifire.core.msgraph_client.get_graph_credentials")
    def test_uses_credentials_from_config(self, mock_get_creds, mock_credential_class, mock_create):
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


class TestCreateGraphClient:
    """Tests for create_graph_client function."""

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.GraphRequestAdapter")
    @patch("sjifire.core.msgraph_client.AzureIdentityAuthenticationProvider")
    @patch("sjifire.core.msgraph_client.GraphClientFactory")
    def test_returns_graph_service_client(
        self, mock_factory, mock_auth_provider_class, mock_adapter_class, mock_client_class
    ):
        """Should return a GraphServiceClient with retry middleware."""
        credential = MagicMock()
        mock_client = MagicMock(spec=GraphServiceClient)
        mock_client_class.return_value = mock_client

        result = create_graph_client(credential)

        assert result is mock_client

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.GraphRequestAdapter")
    @patch("sjifire.core.msgraph_client.AzureIdentityAuthenticationProvider")
    @patch("sjifire.core.msgraph_client.GraphClientFactory")
    def test_configures_retry_middleware(
        self, mock_factory, mock_auth_provider_class, mock_adapter_class, mock_client_class
    ):
        """Should configure retry middleware via GraphClientFactory."""
        credential = MagicMock()

        create_graph_client(credential)

        mock_factory.create_with_default_middleware.assert_called_once()
        call_kwargs = mock_factory.create_with_default_middleware.call_args[1]
        options = call_kwargs["options"]
        assert "RetryHandlerOption" in options

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.GraphRequestAdapter")
    @patch("sjifire.core.msgraph_client.AzureIdentityAuthenticationProvider")
    @patch("sjifire.core.msgraph_client.GraphClientFactory")
    def test_passes_http_client_to_adapter(
        self, mock_factory, mock_auth_provider_class, mock_adapter_class, mock_client_class
    ):
        """Should pass the middleware-configured http_client to the adapter."""
        credential = MagicMock()
        mock_http_client = MagicMock()
        mock_factory.create_with_default_middleware.return_value = mock_http_client

        create_graph_client(credential)

        mock_adapter_class.assert_called_once()
        call_kwargs = mock_adapter_class.call_args[1]
        assert call_kwargs["client"] is mock_http_client

    @patch("sjifire.core.msgraph_client.GraphServiceClient")
    @patch("sjifire.core.msgraph_client.GraphRequestAdapter")
    @patch("sjifire.core.msgraph_client.AzureIdentityAuthenticationProvider")
    @patch("sjifire.core.msgraph_client.GraphClientFactory")
    def test_uses_request_adapter(
        self, mock_factory, mock_auth_provider_class, mock_adapter_class, mock_client_class
    ):
        """Should create GraphServiceClient with request_adapter."""
        credential = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter_class.return_value = mock_adapter

        create_graph_client(credential)

        mock_client_class.assert_called_once_with(request_adapter=mock_adapter)

    def test_retry_constants(self):
        """Retry constants should be reasonable values."""
        assert GRAPH_MAX_RETRIES == 5
        assert GRAPH_RETRY_DELAY == 3.0

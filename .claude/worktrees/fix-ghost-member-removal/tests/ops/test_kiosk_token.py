"""Tests for kiosk token signing and verification."""

import os
from unittest.mock import patch

from sjifire.ops.kiosk.store import create_token, validate_token


class TestCreateToken:
    def test_returns_string(self):
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            token = create_token(label="Bay TV")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_different_labels_produce_different_tokens(self):
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            t1 = create_token(label="TV 1")
            t2 = create_token(label="TV 2")
        assert t1 != t2

    def test_same_label_same_key_produces_same_token(self):
        """Deterministic: same payload + key produces same token."""
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            t1 = create_token(label="TV 1")
            t2 = create_token(label="TV 1")
        assert t1 == t2


class TestValidateToken:
    def test_valid_token(self):
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            token = create_token(label="Bay TV")
            result = validate_token(token)
        assert result is not None
        assert result["label"] == "Bay TV"

    def test_invalid_token_returns_none(self):
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            result = validate_token("totally-bogus-token")
        assert result is None

    def test_empty_token_returns_none(self):
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "test-key"}):
            result = validate_token("")
        assert result is None

    def test_rotated_key_invalidates_token(self):
        """Token signed with old key should fail with new key."""
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "old-key"}):
            token = create_token(label="Bay TV")

        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": "new-key"}):
            result = validate_token(token)
        assert result is None

    def test_dev_fallback_when_no_key(self):
        """Should work with dev fallback key when KIOSK_SIGNING_KEY is empty."""
        with patch.dict(os.environ, {"KIOSK_SIGNING_KEY": ""}):
            token = create_token(label="Dev TV")
            result = validate_token(token)
        assert result is not None
        assert result["label"] == "Dev TV"

"""Unit tests for crates.io API client."""

import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from pathlib import Path
import httpx

from app.services.crates_api import (
    CratesAPI,
    CrateNotFoundError,
    VersionNotFoundError,
)


@pytest.mark.asyncio
async def test_get_latest_version_success():
    """Test successfully fetching latest version."""
    api = CratesAPI()

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"crate": {"max_version": "1.70.0"}})
    mock_response.raise_for_status = Mock()

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        version = await api.get_latest_version("serde")

        assert version == "1.70.0"
        mock_get.assert_called_once_with(
            "https://crates.io/api/v1/crates/serde",
            headers={"User-Agent": "experiment-platform"},
        )

    await api.close()


@pytest.mark.asyncio
async def test_get_latest_version_not_found():
    """Test fetching version for non-existent crate."""
    api = CratesAPI()

    mock_response = Mock()
    mock_response.status_code = 404

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                message="Not Found", request=Mock(), response=mock_response
            )
        )

        with pytest.raises(CrateNotFoundError, match="Crate 'nonexistent' not found"):
            await api.get_latest_version("nonexistent")

    await api.close()


@pytest.mark.asyncio
async def test_verify_version_exists_success():
    """Test verifying an existing version."""
    api = CratesAPI()

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(
        return_value={
            "versions": [
                {"num": "1.70.0"},
                {"num": "1.69.0"},
            ]
        }
    )
    mock_response.raise_for_status = Mock()

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        exists = await api.verify_version_exists("serde", "1.70.0")

        assert exists is True

    await api.close()


@pytest.mark.asyncio
async def test_verify_version_not_exists():
    """Test verifying a non-existent version."""
    api = CratesAPI()

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(
        return_value={
            "versions": [
                {"num": "1.70.0"},
                {"num": "1.69.0"},
            ]
        }
    )
    mock_response.raise_for_status = Mock()

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        exists = await api.verify_version_exists("serde", "999.0.0")

        assert exists is False

    await api.close()


@pytest.mark.asyncio
async def test_download_crate_success():
    """Test successfully downloading a crate."""
    api = CratesAPI()

    mock_content = b"fake crate content"

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = mock_content
    mock_response.raise_for_status = Mock()

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        output_path = Path("/tmp/test_crate.tar.gz")

        with patch("builtins.open", create=True) as mock_open:
            await api.download_crate("serde", "1.70.0", output_path)

            mock_open.assert_called_once_with(output_path, "wb")
            mock_open.return_value.__enter__.return_value.write.assert_called_once_with(
                mock_content
            )

    await api.close()


@pytest.mark.asyncio
async def test_api_retry_on_failure():
    """Test retry logic on transient failures."""
    api = CratesAPI()

    call_count = 0

    async def mock_get_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        mock_resp = Mock()
        if call_count < 3:
            # Fail first 2 times
            mock_resp.raise_for_status = Mock(side_effect=httpx.HTTPError("Error"))
        else:
            # Succeed on 3rd try
            mock_resp.status_code = 200
            mock_resp.json = Mock(return_value={"crate": {"max_version": "1.70.0"}})
            mock_resp.raise_for_status = Mock()

        return mock_resp

    with patch.object(api.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = mock_get_side_effect

        with patch("asyncio.sleep", return_value=None):
            version = await api.get_latest_version("serde")

        assert version == "1.70.0"
        assert call_count == 3

    await api.close()

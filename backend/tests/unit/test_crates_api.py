"""Unit tests for crates.io API client."""

import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from pathlib import Path
import aiohttp

from app.services.crates_api import (
    CratesAPI,
    CrateNotFoundError,
    VersionNotFoundError,
)


@pytest.mark.asyncio
async def test_get_latest_version_success():
    """Test successfully fetching latest version."""
    api = CratesAPI()

    mock_response = {
        "crate": {
            "max_version": "1.70.0"
        }
    }

    with patch.object(api.session, 'get') as mock_get:
        mock_get.return_value.__aenter__.return_value.json = AsyncMock(
            return_value=mock_response
        )
        mock_get.return_value.__aenter__.return_value.raise_for_status = Mock()

        version = await api.get_latest_version("serde")

        assert version == "1.70.0"
        mock_get.assert_called_once_with(
            "https://crates.io/api/v1/crates/serde",
            headers={"User-Agent": "experiment-platform"}
        )

    await api.close()


@pytest.mark.asyncio
async def test_get_latest_version_not_found():
    """Test fetching version for non-existent crate."""
    api = CratesAPI()

    with patch.object(api.session, 'get') as mock_get:
        mock_resp = Mock()
        mock_resp.status = 404
        mock_resp.raise_for_status = Mock(
            side_effect=aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=404
            )
        )
        mock_get.return_value.__aenter__.return_value = mock_resp

        with pytest.raises(CrateNotFoundError, match="Crate 'nonexistent' not found"):
            await api.get_latest_version("nonexistent")

    await api.close()


@pytest.mark.asyncio
async def test_verify_version_exists_success():
    """Test verifying an existing version."""
    api = CratesAPI()

    mock_response = {
        "versions": [
            {"num": "1.70.0"},
            {"num": "1.69.0"},
        ]
    }

    with patch.object(api.session, 'get') as mock_get:
        mock_get.return_value.__aenter__.return_value.json = AsyncMock(
            return_value=mock_response
        )
        mock_get.return_value.__aenter__.return_value.raise_for_status = Mock()

        exists = await api.verify_version_exists("serde", "1.70.0")

        assert exists is True

    await api.close()


@pytest.mark.asyncio
async def test_verify_version_not_exists():
    """Test verifying a non-existent version."""
    api = CratesAPI()

    mock_response = {
        "versions": [
            {"num": "1.70.0"},
            {"num": "1.69.0"},
        ]
    }

    with patch.object(api.session, 'get') as mock_get:
        mock_get.return_value.__aenter__.return_value.json = AsyncMock(
            return_value=mock_response
        )
        mock_get.return_value.__aenter__.return_value.raise_for_status = Mock()

        exists = await api.verify_version_exists("serde", "999.0.0")

        assert exists is False

    await api.close()


@pytest.mark.asyncio
async def test_download_crate_success():
    """Test successfully downloading a crate."""
    api = CratesAPI()

    mock_content = b"fake crate content"

    with patch.object(api.session, 'get') as mock_get:
        mock_get.return_value.__aenter__.return_value.read = AsyncMock(
            return_value=mock_content
        )
        mock_get.return_value.__aenter__.return_value.raise_for_status = Mock()

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

    mock_response = {
        "crate": {
            "max_version": "1.70.0"
        }
    }

    call_count = 0

    def mock_get_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        mock_resp = Mock()
        if call_count < 3:
            # Fail first 2 times
            mock_resp.raise_for_status = Mock(side_effect=aiohttp.ClientError())
        else:
            # Succeed on 3rd try
            mock_resp.json = AsyncMock(return_value=mock_response)
            mock_resp.raise_for_status = Mock()

        mock_cm = MagicMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        return mock_cm

    with patch.object(api.session, 'get') as mock_get:
        mock_get.side_effect = mock_get_side_effect

        with patch('asyncio.sleep', return_value=None):
            version = await api.get_latest_version("serde")

        assert version == "1.70.0"
        assert call_count == 3

    await api.close()

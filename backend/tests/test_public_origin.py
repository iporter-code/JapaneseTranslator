import pytest
from fastapi.testclient import TestClient

from app.config import Settings, validate_public_origin
from app.main import create_app

TOKEN = 'test-public-origin-token-123456789012345'


def test_https_origin_is_used_behind_plain_http_proxy():
    with TestClient(create_app(Settings(TOKEN, public_base_url='https://test.example/'))) as client:
        response = client.post('/v1/sessions', json={}, headers={
            'Authorization': 'Bearer ' + TOKEN, 'Host': 'untrusted.invalid'})
        assert response.status_code == 200
        assert response.json()['stream_url'].startswith('wss://test.example/v1/sessions/')


@pytest.mark.parametrize('value', ['http://host', 'https://u:p@host', 'https://host/path',
    'https://host?token=secret', 'https://host#part', 'https://host:0',
    'https://host:65536', 'https://host:invalid', 'https://host name', 'https://'])
def test_invalid_public_origins_fail_before_startup(value):
    with pytest.raises(ValueError):
        validate_public_origin(value)

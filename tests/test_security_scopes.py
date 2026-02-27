from src.security import (
    SCOPE_APP_WRITE,
    SCOPE_OPS_WRITE,
    SCOPE_WEB_READ,
    SCOPE_WEB_WRITE,
    create_access_token,
    create_refresh_token,
    decode_token,
)


def test_access_token_contains_scopes_and_client_type() -> None:
    token, _ = create_access_token(
        "user-1",
        scopes=[SCOPE_APP_WRITE],
        client_type="app",
    )
    payload = decode_token(token)
    assert payload["sub"] == "user-1"
    assert payload["type"] == "access"
    assert payload["client_type"] == "app"
    assert payload["scopes"] == [SCOPE_APP_WRITE]


def test_refresh_token_contains_web_scopes() -> None:
    token, _ = create_refresh_token(
        "user-2",
        scopes=[SCOPE_WEB_READ, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE],
        client_type="web",
    )
    payload = decode_token(token)
    assert payload["sub"] == "user-2"
    assert payload["type"] == "refresh"
    assert payload["client_type"] == "web"
    assert payload["scopes"] == [SCOPE_WEB_READ, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE]

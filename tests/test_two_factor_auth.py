"""2FA(TOTP)端到端测试。

覆盖路径:
1. setup → 拿 secret
2. confirm → 启用 + 拿 10 个 recovery codes
3. login(已启用 2FA)→ 拿 challenge
4. verify(正确 TOTP)→ 拿 access token
5. verify(recovery code)→ 拿 access token + 该 code 一次性消费
6. verify(已用过的 recovery code)→ 401
7. disable → 清掉 secret + recovery codes
8. regenerate → 旧 codes 失效,新 10 个返回
9. setup 重复(已启用)→ 409
10. login(2FA 关闭)→ 跟以前一样直接拿 token,requires_2fa=False
"""

from __future__ import annotations

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _register(client: TestClient, email: str = "u@example.com") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "pw123456",
            "client_type": "web",
            "device_name": "test",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _setup_2fa(client: TestClient, access_token: str) -> dict:
    res = client.post(
        "/api/v1/auth/2fa/setup",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _confirm_2fa(client: TestClient, access_token: str, secret: str) -> dict:
    code = pyotp.TOTP(secret).now()
    res = client.post(
        "/api/v1/auth/2fa/confirm",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"code": code},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------- Tests ----------------


def test_setup_returns_secret_and_qr() -> None:
    client = _make_client()
    try:
        token = _register(client)
        body = _setup_2fa(client, token["access_token"])
        assert "secret" in body and len(body["secret"]) >= 16
        assert body["qr_code_uri"].startswith("otpauth://totp/")
        assert "BeeCount" in body["qr_code_uri"]
        assert body["expires_in"] == 300
    finally:
        app.dependency_overrides.clear()


def test_confirm_with_invalid_code_fails() -> None:
    client = _make_client()
    try:
        token = _register(client)
        _setup_2fa(client, token["access_token"])
        res = client.post(
            "/api/v1/auth/2fa/confirm",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            json={"code": "000000"},
        )
        assert res.status_code == 400
        assert "Invalid TOTP" in res.json().get("detail", "")
    finally:
        app.dependency_overrides.clear()


def test_confirm_with_valid_code_enables_2fa() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        result = _confirm_2fa(client, token["access_token"], setup["secret"])
        assert result["enabled"] is True
        assert len(result["recovery_codes"]) == 10
        # 每个 recovery code 是 xxxx-xxxx 形式
        for code in result["recovery_codes"]:
            assert "-" in code and len(code) == 9

        # status 端点应反映启用状态
        status_res = client.get(
            "/api/v1/auth/2fa/status",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        assert status_res.status_code == 200
        assert status_res.json()["enabled"] is True
        assert status_res.json()["enabled_at"] is not None
    finally:
        app.dependency_overrides.clear()


def test_login_with_2fa_returns_challenge() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        # 重新 login
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["requires_2fa"] is True
        assert body["challenge_token"] is not None
        assert body["access_token"] is None
        assert "totp" in body["available_methods"]
        assert "recovery_code" in body["available_methods"]
    finally:
        app.dependency_overrides.clear()


def test_verify_with_correct_totp_returns_token() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        # login → challenge
        login_res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        challenge = login_res.json()["challenge_token"]

        # verify with TOTP
        code = pyotp.TOTP(setup["secret"]).now()
        verify_res = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge,
                "method": "totp",
                "code": code,
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert verify_res.status_code == 200, verify_res.text
        body = verify_res.json()
        assert body["requires_2fa"] is False
        assert body["access_token"] is not None
        assert body["refresh_token"] is not None
    finally:
        app.dependency_overrides.clear()


def test_verify_with_recovery_code_consumes_it() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        confirm = _confirm_2fa(client, token["access_token"], setup["secret"])
        recovery_code = confirm["recovery_codes"][0]

        # login → challenge
        login_res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        challenge = login_res.json()["challenge_token"]

        # 第一次用 recovery code → 成功
        verify_res = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge,
                "method": "recovery_code",
                "code": recovery_code,
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert verify_res.status_code == 200
        assert verify_res.json()["access_token"] is not None

        # 第二次重新 login + 用同一 code → 失败(已消费)
        login_res2 = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        challenge2 = login_res2.json()["challenge_token"]
        verify_res2 = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge2,
                "method": "recovery_code",
                "code": recovery_code,
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert verify_res2.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_disable_clears_2fa() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        code = pyotp.TOTP(setup["secret"]).now()
        res = client.post(
            "/api/v1/auth/2fa/disable",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            json={"password": "pw123456", "code": code},
        )
        assert res.status_code == 200
        assert res.json()["disabled"] is True

        # status 反映未启用
        status_res = client.get(
            "/api/v1/auth/2fa/status",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        assert status_res.json()["enabled"] is False

        # login 又恢复直接发 token,无 challenge
        login_res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert login_res.json()["requires_2fa"] is False
        assert login_res.json()["access_token"] is not None
    finally:
        app.dependency_overrides.clear()


def test_regenerate_invalidates_old_codes() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        confirm = _confirm_2fa(client, token["access_token"], setup["secret"])
        old_code = confirm["recovery_codes"][0]

        # regenerate
        new_totp = pyotp.TOTP(setup["secret"]).now()
        res = client.post(
            "/api/v1/auth/2fa/recovery-codes/regenerate",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            json={"code": new_totp},
        )
        assert res.status_code == 200
        new_codes = res.json()["recovery_codes"]
        assert len(new_codes) == 10
        assert old_code not in new_codes

        # 旧 code 不能用了
        login_res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        challenge = login_res.json()["challenge_token"]
        verify_res = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge,
                "method": "recovery_code",
                "code": old_code,
                "client_type": "web",
            },
        )
        assert verify_res.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_setup_when_already_enabled_returns_409() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        res = client.post(
            "/api/v1/auth/2fa/setup",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        assert res.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_login_without_2fa_returns_token_directly() -> None:
    """backward compat:未启用 2FA 用户的 login 仍直接拿 token,requires_2fa=False。"""
    client = _make_client()
    try:
        _register(client)
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "u@example.com",
                "password": "pw123456",
                "client_type": "web",
                "device_name": "test",
                "platform": "web",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["requires_2fa"] is False
        assert body["access_token"] is not None
        assert body["refresh_token"] is not None
    finally:
        app.dependency_overrides.clear()


def test_disable_with_wrong_password_fails() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        code = pyotp.TOTP(setup["secret"]).now()
        res = client.post(
            "/api/v1/auth/2fa/disable",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            json={"password": "wrongpass", "code": code},
        )
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_verify_with_invalid_challenge_token_fails() -> None:
    client = _make_client()
    try:
        token = _register(client)
        setup = _setup_2fa(client, token["access_token"])
        _confirm_2fa(client, token["access_token"], setup["secret"])

        code = pyotp.TOTP(setup["secret"]).now()
        res = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": "not.a.valid.jwt",
                "method": "totp",
                "code": code,
                "client_type": "web",
            },
        )
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()

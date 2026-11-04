from httpx import AsyncClient

from tests.conftest import register_and_login


async def test_register_returns_user_without_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "Ana@Example.com", "full_name": "Ana", "password": "Secret123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@example.com"  # normalized
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "ana@example.com", "full_name": "Ana", "password": "Secret123"}
    await client.post("/auth/register", json=payload)
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_register_rejects_weak_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "a@example.com", "full_name": "Ana", "password": "password"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_login_with_wrong_password_fails(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await client.post(
        "/auth/login", json={"email": "ana@example.com", "password": "Wrong1234"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    bad = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401


async def test_me_returns_current_user(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "ana@example.com"


async def _login(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={"email": "bob@example.com", "full_name": "Bob", "password": "Secret123"},
    )
    response = await client.post(
        "/auth/login", json={"email": "bob@example.com", "password": "Secret123"}
    )
    tokens: dict[str, str] = response.json()
    return tokens


async def test_refresh_rotates_tokens(client: AsyncClient) -> None:
    tokens = await _login(client)
    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]


async def test_refresh_token_reuse_revokes_family(client: AsyncClient) -> None:
    tokens = await _login(client)
    first = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    rotated = first.json()["refresh_token"]

    # The old token is replayed (e.g. it was stolen) -> rejected, and the family is revoked.
    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
    after = await client.post("/auth/refresh", json={"refresh_token": rotated})
    assert after.status_code == 401


async def test_access_token_cannot_be_used_as_refresh(client: AsyncClient) -> None:
    tokens = await _login(client)
    response = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    tokens = await _login(client)
    assert (
        await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 204
    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


async def test_api_key_lifecycle(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post("/auth/api-keys", json={"name": "Zapier"}, headers=auth_headers)
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith(created.json()["prefix"])

    me = await client.get("/auth/me", headers={"X-API-Key": key})
    assert me.status_code == 200

    listed = await client.get("/auth/api-keys", headers=auth_headers)
    assert [k["name"] for k in listed.json()] == ["Zapier"]
    assert "key" not in listed.json()[0]  # the secret is never shown again

    key_id = created.json()["id"]
    assert (
        await client.delete(f"/auth/api-keys/{key_id}", headers=auth_headers)
    ).status_code == 204
    assert (await client.get("/auth/me", headers={"X-API-Key": key})).status_code == 401

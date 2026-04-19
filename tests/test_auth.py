"""
Auth tests.
Covers: login, token validation, role checks, RBAC.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.fixture
async def client():
    await app.router.startup()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    await app.router.shutdown()


class TestLogin:

    @pytest.mark.asyncio
    async def test_login_success(self, client):
        """Default admin user can log in."""
        response = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["role"] == "super_admin"
        assert body["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        """Wrong password returns 401."""
        response = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_unknown_user(self, client):
        """Unknown user returns 401."""
        response = await client.post(
            "/v1/auth/login",
            json={"username": "nonexistent", "password": "password"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_returns_all_fields(self, client):
        """Token response has all required fields."""
        response = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        body = response.json()
        assert "access_token" in body
        assert "role" in body
        assert "username" in body
        assert "expires_in" in body
        assert body["expires_in"] > 0


class TestProtectedRoutes:

    @pytest.mark.asyncio
    async def test_me_without_token_returns_401(self, client):
        """GET /auth/me without a token returns 401."""
        response = await client.get("/v1/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_valid_token(self, client):
        """GET /auth/me with valid token returns user profile."""
        login = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        token = login.json()["access_token"]

        response = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "admin"
        assert body["role"] == "super_admin"

    @pytest.mark.asyncio
    async def test_me_with_invalid_token_returns_401(self, client):
        """Tampered token returns 401."""
        response = await client.get(
            "/v1/auth/me",
            headers={"Authorization": "Bearer this.is.not.valid"},
        )
        assert response.status_code == 401


class TestRBAC:

    @pytest.mark.asyncio
    async def test_create_user_requires_super_admin(self, client):
        """Creating a user without auth returns 401."""
        response = await client.post(
            "/v1/auth/users",
            json={"username": "newuser", "password": "password123", "role": "tenant_viewer"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_super_admin_can_create_tenant_user(self, client):
        """Super admin can create a tenant-scoped user."""
        login = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        token = login.json()["access_token"]

        response = await client.post(
            "/v1/auth/users",
            json={
                "username":  "test_tenant_viewer",
                "password":  "securepass123",
                "role":      "tenant_viewer",
                "tenant_id": "enterprise_corp",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["role"]      == "tenant_viewer"
        assert body["tenant_id"] == "enterprise_corp"

    @pytest.mark.asyncio
    async def test_tenant_viewer_can_see_own_tenant(self):
        """CurrentUser.can_access_tenant returns True for own tenant."""
        from modules.auth.schemas import CurrentUser
        user = CurrentUser(
            id="1", username="test",
            role="tenant_viewer", tenant_id="enterprise_corp"
        )
        assert user.can_access_tenant("enterprise_corp") is True
        assert user.can_access_tenant("startup_ai")      is False

    @pytest.mark.asyncio
    async def test_super_admin_can_access_any_tenant(self):
        """Super admin bypasses all tenant restrictions."""
        from modules.auth.schemas import CurrentUser
        user = CurrentUser(
            id="1", username="admin",
            role="super_admin", tenant_id=None
        )
        assert user.can_access_tenant("enterprise_corp") is True
        assert user.can_access_tenant("startup_ai")      is True
        assert user.can_access_tenant("any_tenant")      is True
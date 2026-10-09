from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .test_events import moment

User = get_user_model()


class AuthTests(APITestCase):
    """US-11 — Autenticación mínima (login local)."""

    def setUp(self):
        self.a = User.objects.create_user("org_a", password="Pass1234!")
        self.b = User.objects.create_user("org_b", password="Pass1234!")

    def _login(self, username, password):
        return self.client.post(
            "/api/auth/login",
            {"username": username, "password": password},
            format="json",
        )

    def test_login_ok(self):
        res = self._login("org_a", "Pass1234!")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["success"])
        self.assertIn("token", res.data["data"])
        self.assertEqual(res.data["data"]["user"]["username"], "org_a")

    def test_login_invalid_credentials(self):
        res = self._login("org_a", "bad")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data["error"]["message"], "Credenciales inválidas")

    def test_login_nonexistent_user_same_message(self):
        """Mismo mensaje exista o no el usuario (US-11, Escenario 2)."""
        res = self._login("ghost", "x")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data["error"]["message"], "Credenciales inválidas")

    def test_protected_route_requires_auth(self):
        res = self.client.get("/api/events")
        self.assertIn(res.status_code, (401, 403))

    def test_me_endpoint(self):
        token = self._login("org_a", "Pass1234!").data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        res = self.client.get("/api/auth/me")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["data"]["username"], "org_a")

    def test_logout_invalidates_token(self):
        token = self._login("org_a", "Pass1234!").data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        res = self.client.get("/api/events")
        self.assertEqual(res.status_code, 401)

    def test_user_isolation(self):
        """US-11, Escenario 4 — B no ve/edita/borra nada de A."""
        token_a = self._login("org_a", "Pass1234!").data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token_a}")
        res = self.client.post(
            "/api/events",
            {"name": "Boda A", "type": "BODA", "event_datetime": moment(60)},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        event_id = res.data["data"]["id"]

        token_b = self._login("org_b", "Pass1234!").data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token_b}")

        self.assertEqual(self.client.get("/api/events").data["data"], [])
        self.assertEqual(self.client.get(f"/api/events/{event_id}").status_code, 404)
        self.assertEqual(
            self.client.patch(f"/api/events/{event_id}", {"name": "Hack"}, format="json").status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/api/events/{event_id}").status_code, 404)

        # A sigue viendo su evento intacto
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token_a}")
        res = self.client.get(f"/api/events/{event_id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["data"]["name"], "Boda A")

    def test_password_is_hashed(self):
        u = User.objects.get(username="org_a")
        self.assertTrue(u.password.startswith("pbkdf2_"))

    def test_register_creates_user_without_token(self):
        """Registro NO auto-loguea — el FE redirige a /login."""
        res = self.client.post("/api/auth/register", {
            "username": "nuevo",
            "email": "nuevo@example.com",
            "password": "Pass1234!",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("token", res.data["data"])
        self.assertEqual(res.data["data"]["user"]["username"], "nuevo")
        self.assertTrue(User.objects.filter(username="nuevo").exists())

    def test_register_rejects_duplicate_username(self):
        User.objects.create_user("dup", email="dup@x.com", password="Pass1234!")
        res = self.client.post("/api/auth/register", {
            "username": "dup", "email": "otro@x.com", "password": "Pass1234!",
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("username", res.data["error"]["details"])

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user("existente", email="ya@x.com", password="Pass1234!")
        res = self.client.post("/api/auth/register", {
            "username": "nuevo", "email": "ya@x.com", "password": "Pass1234!",
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data["error"]["details"])

    def test_register_rejects_empty_email(self):
        res = self.client.post("/api/auth/register", {
            "username": "nuevo", "email": "", "password": "Pass1234!",
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data["error"]["details"])

    def test_register_rejects_missing_email(self):
        res = self.client.post("/api/auth/register", {
            "username": "nuevo", "password": "Pass1234!",
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data["error"]["details"])

    def test_register_rejects_weak_password(self):
        res = self.client.post("/api/auth/register", {
            "username": "nuevo", "email": "nuevo@x.com", "password": "123",
        }, format="json")
        self.assertEqual(res.status_code, 400)
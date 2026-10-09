"""Sprint 3 — US-06, US-07, US-08, US-12 y la preferencia allow_overload."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from ..models import Event, Subtask, UserSettings
from ..overload import day_load
from .test_events import AuthMixin

User = get_user_model()


class Sprint3Base(AuthMixin, APITestCase):
    def setUp(self):
        self.user = self.authenticate()
        self.today = timezone.localdate()
        self.day_x = self.today + timedelta(days=3)
        self.day_free = self.today + timedelta(days=4)
        self.event = Event.objects.create(
            name="Boda",
            type="BODA",
            event_datetime=timezone.now() + timedelta(days=60),
            user=self.user,
        )

    def task(self, hours, day=None, status=Subtask.Status.PENDIENTE, event=None):
        return Subtask.objects.create(
            event=event or self.event,
            name=f"Gestión {hours}h",
            target_date=day or self.day_x,
            estimated_hours=Decimal(str(hours)),
            status=status,
        )

    def set_limit(self, hours, allow_overload=False, user=None):
        settings, _ = UserSettings.objects.get_or_create(user=user or self.user)
        settings.daily_limit_hours = Decimal(str(hours))
        settings.allow_overload = allow_overload
        settings.save()


class DayLoadTests(Sprint3Base):
    """Test mínimo del cálculo de horas por día (US-07)."""

    def test_suma_excluye_ejecutadas(self):
        self.task(3)
        self.task(2, status=Subtask.Status.POSPUESTA)
        self.task(4, status=Subtask.Status.EJECUTADA)
        self.task(5, day=self.day_free)
        self.assertEqual(day_load(self.user, self.day_x), Decimal("5"))

    def test_dia_vacio_parte_de_cero(self):
        self.assertEqual(day_load(self.user, self.day_x), Decimal("0"))

    def test_solo_cuenta_gestiones_del_usuario(self):
        other = User.objects.create_user("otro", password="Test1234!")
        other_event = Event.objects.create(
            name="Otro", type="OTRO", event_datetime=timezone.now() + timedelta(days=60), user=other
        )
        self.task(8, event=other_event)
        self.task(1)
        self.assertEqual(day_load(self.user, self.day_x), Decimal("1"))


class OverloadCheckEndpointTests(Sprint3Base):
    URL = "/api/conflicts/overload"

    def test_conflicto_5h_mas_2h(self):
        self.task(5)
        res = self.client.post(
            self.URL, {"target_date": self.day_x.isoformat(), "estimated_hours": 2}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.data["data"]
        self.assertEqual(data["planned_hours"], 7)
        self.assertEqual(data["limit_hours"], 6)
        self.assertEqual(data["exceeds_by"], 1)
        self.assertTrue(data["has_conflict"])
        self.assertEqual(data["message"], "Quedarías con 7h de gestión planificadas (límite 6h)")

    def test_sin_conflicto_4h_mas_2h(self):
        self.task(4)
        res = self.client.post(
            self.URL, {"target_date": self.day_x.isoformat(), "estimated_hours": 2}, format="json"
        )
        self.assertFalse(res.data["data"]["has_conflict"])
        self.assertEqual(res.data["data"]["exceeds_by"], 0)

    def test_limite_exacto_no_es_conflicto(self):
        self.task(5)
        res = self.client.post(
            self.URL, {"target_date": self.day_x.isoformat(), "estimated_hours": 1}, format="json"
        )
        self.assertEqual(res.data["data"]["planned_hours"], 6)
        self.assertFalse(res.data["data"]["has_conflict"])

    def test_ejecutada_no_suma(self):
        self.task(5)
        self.task(3, status=Subtask.Status.EJECUTADA)
        res = self.client.post(
            self.URL, {"target_date": self.day_x.isoformat(), "estimated_hours": 1}, format="json"
        )
        self.assertFalse(res.data["data"]["has_conflict"])

    def test_con_subtask_id_no_cuenta_doble(self):
        moving = self.task(2)
        self.task(4)
        res = self.client.post(
            self.URL,
            {"target_date": self.day_x.isoformat(), "subtask_id": moving.id},
            format="json",
        )
        self.assertEqual(res.data["data"]["planned_hours"], 6)
        self.assertFalse(res.data["data"]["has_conflict"])

    def test_fecha_invalida(self):
        res = self.client.post(
            self.URL, {"target_date": "31-02-2026", "estimated_hours": 1}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["details"]["target_date"], ["Ingresa una fecha válida."])

    def test_usa_limite_del_usuario_actual(self):
        self.set_limit(4)
        self.task(3)
        res = self.client.post(
            self.URL, {"target_date": self.day_x.isoformat(), "estimated_hours": 2}, format="json"
        )
        self.assertTrue(res.data["data"]["has_conflict"])
        self.assertEqual(
            res.data["data"]["message"], "Quedarías con 5h de gestión planificadas (límite 4h)"
        )


class RescheduleTests(Sprint3Base):
    """US-06 + US-07 integrados en PATCH /subtasks/:id."""

    def url(self, task):
        return f"/api/subtasks/{task.id}"

    def test_reprogramar_a_fecha_valida(self):
        task = self.task(2, day=self.today - timedelta(days=2))
        res = self.client.patch(
            self.url(task), {"target_date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["message"], "Fecha actualizada.")
        task.refresh_from_db()
        self.assertEqual(task.target_date, self.today)
        groups = {item["id"]: item["group"] for item in self.client.get("/api/today").data["data"]}
        self.assertEqual(groups[task.id], "hoy")

    def test_fecha_pasada_solo_con_preferencia(self):
        task = self.task(2)
        payload = {"target_date": (self.today - timedelta(days=5)).isoformat()}
        res = self.client.patch(self.url(task), payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_in_past")

        UserSettings.objects.filter(user=self.user).update(allow_overdue_subtasks=True)
        res = self.client.patch(self.url(task), payload, format="json")
        self.assertEqual(res.status_code, 200)

    def test_fecha_invalida_o_vacia(self):
        task = self.task(2)
        for bad in ("2026-13-40", "", None, "mañana"):
            res = self.client.patch(self.url(task), {"target_date": bad}, format="json")
            self.assertEqual(res.status_code, 400, bad)
            self.assertEqual(
                res.data["error"]["details"]["target_date"], ["Ingresa una fecha válida."]
            )
        task.refresh_from_db()
        self.assertEqual(task.target_date, self.day_x)

    def test_fecha_posterior_al_evento(self):
        task = self.task(2)
        after = (self.event.event_datetime + timedelta(days=2)).date()
        res = self.client.patch(self.url(task), {"target_date": after.isoformat()}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_after_event")

    def test_gestion_de_otro_usuario_404(self):
        other = User.objects.create_user("otro", password="Test1234!")
        other_event = Event.objects.create(
            name="Otro", type="OTRO", event_datetime=timezone.now() + timedelta(days=60), user=other
        )
        foreign = self.task(1, event=other_event)
        res = self.client.patch(
            self.url(foreign), {"target_date": self.today.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 404)

    def test_conflicto_bloquea_por_defecto(self):
        self.task(5)
        moving = self.task(2, day=self.day_free)
        res = self.client.patch(
            self.url(moving), {"target_date": self.day_x.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["code"], "overload_conflict")
        self.assertEqual(
            res.data["error"]["message"], "Quedarías con 7h de gestión planificadas (límite 6h)"
        )
        conflict = res.data["error"]["details"]["conflict"]
        self.assertEqual(
            (conflict["planned_hours"], conflict["limit_hours"], conflict["exceeds_by"]), (7, 6, 1)
        )
        moving.refresh_from_db()
        self.assertEqual(moving.target_date, self.day_free)

    def test_sin_conflicto_guarda(self):
        self.task(4)
        moving = self.task(2, day=self.day_free)
        res = self.client.patch(
            self.url(moving), {"target_date": self.day_x.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["data"]["conflict"]["has_conflict"])
        self.assertTrue(res.data["data"]["conflict"]["resolved"])
        self.assertEqual(res.data["data"]["previous_day"]["date"], self.day_free.isoformat())

    def test_allow_overload_guarda_con_advertencia(self):
        self.set_limit(6, allow_overload=True)
        self.task(5)
        moving = self.task(2, day=self.day_free)
        res = self.client.patch(
            self.url(moving), {"target_date": self.day_x.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["data"]["conflict"]["has_conflict"])
        self.assertFalse(res.data["data"]["conflict"]["resolved"])
        moving.refresh_from_db()
        self.assertEqual(moving.target_date, self.day_x)

    def test_nuevo_limite_se_usa(self):
        self.set_limit(4)
        self.task(3)
        moving = self.task(2, day=self.day_free)
        res = self.client.patch(
            self.url(moving), {"target_date": self.day_x.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 409)

    def test_marcar_ejecutada_no_revisa_conflicto(self):
        self.task(5)
        task = self.task(4)  # el día ya está excedido (por datos previos)
        res = self.client.patch(self.url(task), {"status": "EJECUTADA"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["data"]["conflict"])

    def test_reactivar_ejecutada_revisa_conflicto(self):
        self.task(5)
        task = self.task(2, status=Subtask.Status.EJECUTADA)
        res = self.client.patch(self.url(task), {"status": "PENDIENTE"}, format="json")
        self.assertEqual(res.status_code, 409)
        task.refresh_from_db()
        self.assertEqual(task.status, Subtask.Status.EJECUTADA)

    def test_editar_nota_no_revisa_conflicto(self):
        self.set_limit(6, allow_overload=True)
        self.task(5)
        task = self.task(2)
        self.set_limit(6, allow_overload=False)
        res = self.client.patch(self.url(task), {"note": "llamar"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["data"]["conflict"])


class ResolveConflictTests(Sprint3Base):
    """US-08 — resolver moviendo o reduciendo horas."""

    def setUp(self):
        super().setUp()
        # Día X excedido: 5h + 3h = 8h con límite 6h (guardado con allow_overload).
        self.base = self.task(5)
        self.extra = self.task(3)

    def test_mover_a_dia_libre_resuelve(self):
        res = self.client.patch(
            f"/api/subtasks/{self.extra.id}",
            {"target_date": self.day_free.isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["data"]["conflict"]["resolved"])
        self.assertEqual(res.data["data"]["previous_day"]["planned_hours"], 5)
        self.assertFalse(res.data["data"]["previous_day"]["has_conflict"])

    def test_reducir_pero_aun_excede(self):
        res = self.client.patch(
            f"/api/subtasks/{self.extra.id}", {"estimated_hours": 2}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        conflict = res.data["data"]["conflict"]
        self.assertFalse(conflict["resolved"])
        self.assertEqual(conflict["planned_hours"], 7)
        self.assertEqual(
            conflict["message"], "Aún quedarías con 7h de gestión planificadas (límite 6h)"
        )
        self.extra.refresh_from_db()
        self.assertEqual(self.extra.estimated_hours, Decimal("2"))

    def test_reducir_lo_suficiente_resuelve(self):
        res = self.client.patch(
            f"/api/subtasks/{self.extra.id}", {"estimated_hours": 1}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["data"]["conflict"]["resolved"])

    def test_reducir_a_cero_rechazado(self):
        res = self.client.patch(
            f"/api/subtasks/{self.extra.id}", {"estimated_hours": 0}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.extra.refresh_from_db()
        self.assertEqual(self.extra.estimated_hours, Decimal("3"))

    def test_aumentar_horas_bloquea(self):
        res = self.client.patch(
            f"/api/subtasks/{self.extra.id}", {"estimated_hours": 4}, format="json"
        )
        self.assertEqual(res.status_code, 409)

    def test_sugerencias_saltan_dias_llenos(self):
        self.task(6, day=self.today + timedelta(days=1))
        res = self.client.get(
            "/api/conflicts/suggestions",
            {
                "subtask_id": self.extra.id,
                "from": (self.today + timedelta(days=1)).isoformat(),
                "limit": 2,
            },
        )
        self.assertEqual(res.status_code, 200)
        dates = [d["date"] for d in res.data["data"]]
        self.assertEqual(
            dates, [(self.today + timedelta(days=2)).isoformat(), self.day_free.isoformat()]
        )

    def test_sugerencias_vacias_si_no_hay_capacidad(self):
        self.event.event_datetime = timezone.now() + timedelta(days=4)
        self.event.save()
        res = self.client.get(
            "/api/conflicts/suggestions", {"subtask_id": self.extra.id, "hours": 20}
        )
        self.assertEqual(res.data["data"], [])


class CreateSubtaskConflictTests(Sprint3Base):
    def test_crear_gestion_con_conflicto_409(self):
        self.task(5)
        res = self.client.post(
            f"/api/events/{self.event.id}/subtasks",
            {"name": "Nueva", "target_date": self.day_x.isoformat(), "estimated_hours": 2},
            format="json",
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(Subtask.objects.count(), 1)

    def test_crear_gestion_sin_conflicto(self):
        res = self.client.post(
            f"/api/events/{self.event.id}/subtasks",
            {"name": "Nueva", "target_date": self.day_x.isoformat(), "estimated_hours": 2},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data["data"]["conflict"])

    def test_crear_evento_con_gestiones_que_exceden_el_mismo_dia(self):
        payload = {
            "name": "Feria",
            "type": "CORPORATIVO",
            "event_datetime": (timezone.now() + timedelta(days=30)).isoformat(),
            "subtasks": [
                {"name": "A", "target_date": self.day_x.isoformat(), "estimated_hours": 4},
                {"name": "B", "target_date": self.day_x.isoformat(), "estimated_hours": 3},
            ],
        }
        res = self.client.post("/api/events", payload, format="json")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["details"]["conflicts"][0]["planned_hours"], 7)
        self.assertFalse(Event.objects.filter(name="Feria").exists())

        self.set_limit(6, allow_overload=True)
        res = self.client.post("/api/events", payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(res.data["data"]["conflicts"]), 1)


class SettingsTests(Sprint3Base):
    def test_default_y_actualizacion(self):
        res = self.client.get("/api/settings")
        self.assertEqual(
            res.data["data"],
            {
                "daily_limit_hours": 6,
                "allow_overload": False,
                "allow_subtasks_after_event": False,
                "allow_overdue_subtasks": False,
            },
        )
        res = self.client.patch("/api/settings", {"allow_overload": True}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["data"]["allow_overload"])
        self.assertTrue(UserSettings.objects.get(user=self.user).allow_overload)

    def test_validaciones(self):
        for payload in (
            {"daily_limit_hours": 0},
            {"daily_limit_hours": 20},
            {"daily_limit_hours": 4.25},
            {"allow_overload": "quizás"},
        ):
            res = self.client.patch("/api/settings", payload, format="json")
            self.assertEqual(res.status_code, 400, payload)
        self.assertEqual(self.client.get("/api/settings").data["data"]["daily_limit_hours"], 6)

    def test_put_daily_limit(self):
        res = self.client.put("/api/settings/daily-limit", {"daily_limit_hours": 4}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.client.get("/api/settings/daily-limit").data["data"]["daily_limit_hours"], 4
        )
        for bad in (0, 20, 4.25):
            res = self.client.put(
                "/api/settings/daily-limit", {"daily_limit_hours": bad}, format="json"
            )
            self.assertEqual(res.status_code, 400)
            self.assertEqual(
                res.data["error"]["message"], "El límite debe estar entre 1 y 16 horas."
            )

    def test_persistencia_por_usuario(self):
        self.set_limit(6)
        user_b = User.objects.create_user("org_b", password="Test1234!")
        self.set_limit(4, user=user_b)
        self.client.credentials()
        self.authenticate_existing("org_b")
        self.assertEqual(
            self.client.get("/api/settings/daily-limit").data["data"]["daily_limit_hours"], 4
        )

    def authenticate_existing(self, username, password="Test1234!"):
        token = self.client.post(
            "/api/auth/login", {"username": username, "password": password}, format="json"
        ).data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

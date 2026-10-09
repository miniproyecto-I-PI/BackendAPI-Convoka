"""Corrección de bugs del Sprint 3 (design/BUGS.md)."""

from datetime import datetime, time, timedelta

from django.utils import timezone

from ..models import Event, Subtask, UserSettings
from .test_sprint3 import Sprint3Base


def set_prefs(user, **prefs):
    UserSettings.objects.update_or_create(user=user, defaults=prefs)


class SubtasksAfterEventTests(Sprint3Base):
    """Bug 1: gestiones con fecha posterior al evento (allow_subtasks_after_event)."""

    def setUp(self):
        super().setUp()
        self.late_task = self.task(2, day=self.today + timedelta(days=20))

    def move_event(self, days):
        return self.client.patch(
            f"/api/events/{self.event.id}",
            {"event_datetime": (timezone.now() + timedelta(days=days)).isoformat()},
            format="json",
        )

    def test_reprogramar_evento_antes_de_sus_gestiones_bloquea(self):
        old_datetime = self.event.event_datetime
        res = self.move_event(10)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "subtasks_after_event")
        self.assertEqual(
            [s["id"] for s in res.data["error"]["details"]["subtasks"]], [self.late_task.id]
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.event_datetime, old_datetime)

    def test_reprogramar_evento_despues_de_sus_gestiones_ok(self):
        self.assertEqual(self.move_event(30).status_code, 200)

    def test_con_preferencia_se_permite(self):
        set_prefs(self.user, allow_subtasks_after_event=True)
        self.assertEqual(self.move_event(10).status_code, 200)

        after = (self.today + timedelta(days=40)).isoformat()
        res = self.client.patch(
            f"/api/subtasks/{self.late_task.id}", {"target_date": after}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        res = self.client.post(
            f"/api/events/{self.event.id}/subtasks",
            {"name": "Tardía", "target_date": after, "estimated_hours": 1},
            format="json",
        )
        self.assertEqual(res.status_code, 201)

    def test_gestiones_iniciales_posteriores_al_evento(self):
        payload = {
            "name": "Feria",
            "type": "CORPORATIVO",
            "event_datetime": (timezone.now() + timedelta(days=5)).isoformat(),
            "subtasks": [
                {
                    "name": "A",
                    "target_date": (self.today + timedelta(days=9)).isoformat(),
                    "estimated_hours": 1,
                }
            ],
        }
        res = self.client.post("/api/events", payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_after_event")
        self.assertFalse(Event.objects.filter(name="Feria").exists())

    def test_fecha_del_evento_en_hora_local(self):
        # Evento a las 21:00 en Bogotá = 02:00 UTC del día siguiente.
        event_day = self.today + timedelta(days=5)
        local_night = timezone.make_aware(datetime.combine(event_day, time(21, 0)))
        self.event.event_datetime = local_night
        self.event.save()
        self.late_task.delete()
        res = self.client.post(
            f"/api/events/{self.event.id}/subtasks",
            {
                "name": "X",
                "target_date": (event_day + timedelta(days=1)).isoformat(),
                "estimated_hours": 1,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_after_event")


class EventInPastTests(Sprint3Base):
    """Bug 2: no se puede guardar un evento con fecha anterior a hoy."""

    def test_crear_evento_vencido(self):
        res = self.client.post(
            "/api/events",
            {
                "name": "Viejo",
                "type": "OTRO",
                "event_datetime": (timezone.now() - timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "event_date_in_past")

    def test_crear_evento_hoy_ok(self):
        res = self.client.post(
            "/api/events",
            {"name": "Hoy", "type": "OTRO", "event_datetime": timezone.now().isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 201)

    def test_mover_evento_al_pasado(self):
        res = self.client.patch(
            f"/api/events/{self.event.id}",
            {"event_datetime": (timezone.now() - timedelta(days=2)).isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "event_date_in_past")

    def test_editar_otro_campo_de_evento_pasado_ok(self):
        self.event.event_datetime = timezone.now() - timedelta(days=10)
        self.event.save()
        res = self.client.patch(
            f"/api/events/{self.event.id}",
            {"name": "Renombrado", "event_datetime": self.event.event_datetime.isoformat()},
            format="json",
        )
        self.assertEqual(res.status_code, 200)


class TodayOrderTests(Sprint3Base):
    """Bug 3: dentro del día se ordena por hora; sin hora cuenta como 00:00."""

    def test_orden_por_hora(self):
        def make(name, at, hours=1):
            return Subtask.objects.create(
                event=self.event,
                name=name,
                target_date=self.today,
                target_time=at,
                estimated_hours=hours,
            )

        make("tarde", time(15, 0), hours=1)
        make("mañana", time(8, 30), hours=5)
        make("sin hora", None, hours=3)
        make("mañana corta", time(8, 30), hours=1)
        names = [item["name"] for item in self.client.get("/api/today").data["data"]]
        self.assertEqual(names, ["sin hora", "mañana corta", "mañana", "tarde"])


class ConflictResolvedFlagTests(Sprint3Base):
    """Bug 4: conflict_resolved solo cuando de verdad había un conflicto."""

    def test_reprogramar_sin_conflicto_previo(self):
        task = self.task(2)
        res = self.client.patch(
            f"/api/subtasks/{task.id}", {"target_date": self.day_free.isoformat()}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["data"]["conflict_resolved"])

    def test_mover_desde_dia_excedido(self):
        self.task(5)
        extra = self.task(3)
        res = self.client.patch(
            f"/api/subtasks/{extra.id}", {"target_date": self.day_free.isoformat()}, format="json"
        )
        self.assertTrue(res.data["data"]["conflict_resolved"])

    def test_reducir_horas_resuelve_o_no(self):
        self.task(5)
        extra = self.task(3)
        res = self.client.patch(f"/api/subtasks/{extra.id}", {"estimated_hours": 2}, format="json")
        self.assertFalse(res.data["data"]["conflict_resolved"])
        res = self.client.patch(f"/api/subtasks/{extra.id}", {"estimated_hours": 1}, format="json")
        self.assertTrue(res.data["data"]["conflict_resolved"])


class ReduceDailyLimitTests(Sprint3Base):
    """Bug 5: no se puede bajar el límite por debajo de lo planificado."""

    def test_bloquea_y_lista_dias(self):
        self.task(5)
        self.task(4, day=self.day_free)
        res = self.client.patch(
            "/api/settings/daily-limit", {"daily_limit_hours": 3}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "daily_limit_below_planned")
        self.assertEqual(
            res.data["error"]["message"],
            "No puedes reducir: tienes días con más horas planificadas "
            f"({self.day_x.isoformat()}, {self.day_free.isoformat()})",
        )
        self.assertEqual(
            self.client.get("/api/settings/daily-limit").data["data"]["daily_limit_hours"], 6
        )

    def test_tambien_en_patch_settings(self):
        self.task(5)
        res = self.client.patch("/api/settings", {"daily_limit_hours": 3}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_dias_pasados_y_ejecutadas_no_cuentan(self):
        self.task(5, day=self.today - timedelta(days=1))
        self.task(5, status=Subtask.Status.EJECUTADA)
        res = self.client.patch(
            "/api/settings/daily-limit", {"daily_limit_hours": 3}, format="json"
        )
        self.assertEqual(res.status_code, 200)

    def test_con_allow_overload_guarda_con_advertencia(self):
        self.task(5)
        res = self.client.patch(
            "/api/settings", {"daily_limit_hours": 3, "allow_overload": True}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["data"]["days_over_limit"][0]["date"], self.day_x.isoformat())


class OverdueSubtasksTests(Sprint3Base):
    """Bug 6: gestiones vencidas solo con allow_overdue_subtasks."""

    def create(self, offset):
        return self.client.post(
            f"/api/events/{self.event.id}/subtasks",
            {
                "name": "X",
                "target_date": (self.today + timedelta(days=offset)).isoformat(),
                "estimated_hours": 1,
            },
            format="json",
        )

    def test_crear_vencida_bloquea_por_defecto(self):
        res = self.create(-1)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_in_past")
        self.assertEqual(self.create(0).status_code, 201)

    def test_crear_vencida_con_preferencia(self):
        set_prefs(self.user, allow_overdue_subtasks=True)
        self.assertEqual(self.create(-1).status_code, 201)

    def test_gestiones_iniciales_vencidas(self):
        payload = {
            "name": "Feria",
            "type": "CORPORATIVO",
            "event_datetime": (timezone.now() + timedelta(days=5)).isoformat(),
            "subtasks": [
                {
                    "name": "A",
                    "target_date": (self.today - timedelta(days=1)).isoformat(),
                    "estimated_hours": 1,
                }
            ],
        }
        res = self.client.post("/api/events", payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "target_date_in_past")

    def test_editar_gestion_vencida_sin_cambiar_fecha_ok(self):
        task = self.task(2, day=self.today - timedelta(days=3))
        res = self.client.patch(f"/api/subtasks/{task.id}", {"note": "pendiente"}, format="json")
        self.assertEqual(res.status_code, 200)

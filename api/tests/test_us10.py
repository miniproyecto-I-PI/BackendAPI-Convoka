from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Event, Subtask

User = get_user_model()


class EventProgressUS10Tests(APITestCase):
    """
    Pruebas para US-10: Barra de progreso de preparación del evento.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="organizador_progreso",
            email="progreso@example.com",
            password="Password123*",
        )
        self.other_user = User.objects.create_user(
            username="otro_organizador",
            email="otro@example.com",
            password="Password123*",
        )
        self.client.force_authenticate(user=self.user)

        self.today = timezone.localdate()
        self.event = Event.objects.create(
            user=self.user,
            name="Boda Sofía y Mateo",
            type=Event.EventType.BODA,
            event_datetime=timezone.now() + timedelta(days=20),
        )

    def test_progreso_con_distintos_estados_de_gestiones(self):
        """
        Escenario 1: Evento con 4 gestiones (2 ejecutadas, 1 pendiente, 1 pospuesta)
        debe retornar 50% ('2 de 4').
        """
        # 2 ejecutadas
        Subtask.objects.create(
            event=self.event,
            name="Separar capilla",
            target_date=self.today,
            estimated_hours=Decimal("2.0"),
            status=Subtask.Status.EJECUTADA,
            done_at=timezone.now(),
        )
        Subtask.objects.create(
            event=self.event,
            name="Contratar fotógrafo",
            target_date=self.today,
            estimated_hours=Decimal("3.0"),
            status=Subtask.Status.EJECUTADA,
            done_at=timezone.now(),
        )
        # 1 pendiente
        Subtask.objects.create(
            event=self.event,
            name="Prueba de vestuario",
            target_date=self.today + timedelta(days=2),
            estimated_hours=Decimal("1.5"),
            status=Subtask.Status.PENDIENTE,
        )
        # 1 pospuesta
        Subtask.objects.create(
            event=self.event,
            name="Degustación de pastel",
            target_date=self.today + timedelta(days=3),
            estimated_hours=Decimal("2.0"),
            status=Subtask.Status.POSPUESTA,
            note="Repostería de vacaciones hasta el lunes",
        )

        response = self.client.get(f"/api/events/{self.event.id}/progress")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        data = response.data["data"]
        self.assertEqual(data["event_id"], self.event.id)
        self.assertEqual(data["event_name"], "Boda Sofía y Mateo")
        self.assertEqual(data["done"], 2)
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["percent"], 50.0)

        # Verificar que el detalle del evento (GET /events/:id) también incluye percent
        detail_response = self.client.get(f"/api/events/{self.event.id}")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        progress = detail_response.data["data"]["progress"]
        self.assertEqual(progress["done"], 2)
        self.assertEqual(progress["total"], 4)
        self.assertEqual(progress["percent"], 50.0)

    def test_marcar_gestion_actualiza_barra_de_progreso(self):
        """Criterio QA: Probar marcar una gestión y ver que la barra se actualiza."""
        subtask = Subtask.objects.create(
            event=self.event,
            name="Confirmar lista de invitados",
            target_date=self.today,
            estimated_hours=Decimal("2.0"),
            status=Subtask.Status.PENDIENTE,
        )
        Subtask.objects.create(
            event=self.event,
            name="Enviar invitaciones",
            target_date=self.today,
            estimated_hours=Decimal("2.0"),
            status=Subtask.Status.PENDIENTE,
        )

        # Inicialmente 0 de 2 -> 0.0%
        res_init = self.client.get(f"/api/events/{self.event.id}/progress")
        self.assertEqual(res_init.data["data"]["done"], 0)
        self.assertEqual(res_init.data["data"]["total"], 2)
        self.assertEqual(res_init.data["data"]["percent"], 0.0)

        # Marcamos una gestión como ejecutada
        patch_res = self.client.patch(
            f"/api/subtasks/{subtask.id}", {"status": "EJECUTADA"}, format="json"
        )
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        # Ahora 1 de 2 -> 50.0%
        res_updated = self.client.get(f"/api/events/{self.event.id}/progress")
        self.assertEqual(res_updated.data["data"]["done"], 1)
        self.assertEqual(res_updated.data["data"]["total"], 2)
        self.assertEqual(res_updated.data["data"]["percent"], 50.0)

    def test_evento_sin_gestiones_estado_vacio(self):
        """Criterio QA: Probar en un evento sin gestiones: vacío (0 de 0 y 0%)."""
        empty_event = Event.objects.create(
            user=self.user,
            name="Evento Vacío",
            type=Event.EventType.SOCIAL,
            event_datetime=timezone.now() + timedelta(days=15),
        )

        response = self.client.get(f"/api/events/{empty_event.id}/progress")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        data = response.data["data"]
        self.assertEqual(data["done"], 0)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["percent"], 0.0)

    def test_evento_inexistente_o_ajeno_retorna_404(self):
        """Seguridad: no se puede ver el progreso de eventos de otros usuarios ni inexistentes."""
        # Inexistente
        res_404 = self.client.get("/api/events/999999/progress")
        self.assertEqual(res_404.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_404.data["success"])

        # Evento perteneciente a otro usuario
        other_event = Event.objects.create(
            user=self.other_user,
            name="Evento Ajeno",
            type=Event.EventType.CORPORATIVO,
            event_datetime=timezone.now() + timedelta(days=5),
        )
        res_other = self.client.get(f"/api/events/{other_event.id}/progress")
        self.assertEqual(res_other.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_other.data["success"])

    def test_usuario_no_autenticado_retorna_401(self):
        """Llamada sin token debe retornar 401 Unauthorized."""
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/api/events/{self.event.id}/progress")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_endpoint_global_progress_todos_los_eventos(self):
        """
        Endpoint opcional GET /api/progress para todos los eventos del usuario:
        resume el total y desglosa cada evento.
        """
        # Evento 1 (self.event): creamos 1 ejecutada y 1 pendiente (50%)
        Subtask.objects.create(
            event=self.event,
            name="Gestión 1",
            target_date=self.today,
            estimated_hours=Decimal("1.0"),
            status=Subtask.Status.EJECUTADA,
        )
        Subtask.objects.create(
            event=self.event,
            name="Gestión 2",
            target_date=self.today,
            estimated_hours=Decimal("1.0"),
            status=Subtask.Status.PENDIENTE,
        )

        # Evento 2: creamos 2 ejecutadas de 2 (100%)
        event2 = Event.objects.create(
            user=self.user,
            name="Cumpleaños 50",
            type=Event.EventType.CUMPLEANOS,
            event_datetime=timezone.now() + timedelta(days=12),
        )
        Subtask.objects.create(
            event=event2,
            name="Comprar torta",
            target_date=self.today,
            estimated_hours=Decimal("1.0"),
            status=Subtask.Status.EJECUTADA,
        )
        Subtask.objects.create(
            event=event2,
            name="Reservar salón",
            target_date=self.today,
            estimated_hours=Decimal("1.0"),
            status=Subtask.Status.EJECUTADA,
        )

        # Evento 3: sin gestiones (0%)
        Event.objects.create(
            user=self.user,
            name="Reunión equipo",
            type=Event.EventType.CORPORATIVO,
            event_datetime=timezone.now() + timedelta(days=8),
        )

        response = self.client.get("/api/progress")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        data = response.data["data"]

        # Resumen global:
        # 3 eventos en total.
        # Subtareas: 2 en evento 1 + 2 en evento 2 + 0 en evento 3 = 4 total.
        # Ejecutadas: 1 en evento 1 + 2 en evento 2 = 3 ejecutadas.
        # Porcentaje global = (3 / 4) * 100 = 75.0%
        summary = data["summary"]
        self.assertEqual(summary["total_events"], 3)
        self.assertEqual(summary["total_subtasks"], 4)
        self.assertEqual(summary["total_done"], 3)
        self.assertEqual(summary["global_percent"], 75.0)

        # Desglose de eventos
        events = data["events"]
        self.assertEqual(len(events), 3)
        event_map = {e["event_id"]: e for e in events}
        self.assertEqual(event_map[self.event.id]["percent"], 50.0)
        self.assertEqual(event_map[event2.id]["percent"], 100.0)

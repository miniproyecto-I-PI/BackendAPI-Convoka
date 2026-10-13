from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Event, Subtask, UserSettings

User = get_user_model()


class SubtaskStatusUS09Tests(APITestCase):
    """
    Pruebas para US-09: Registrar tareas ejecutadas y pospuestas con nota.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="organizador1",
            email="org1@example.com",
            password="Password123*",
        )
        self.other_user = User.objects.create_user(
            username="organizador2",
            email="org2@example.com",
            password="Password123*",
        )
        self.client.force_authenticate(user=self.user)

        self.today = timezone.localdate()
        self.event = Event.objects.create(
            user=self.user,
            name="Conferencia Anual",
            type=Event.EventType.CORPORATIVO,
            event_datetime=timezone.now() + timedelta(days=10),
        )
        self.subtask = Subtask.objects.create(
            event=self.event,
            name="Contratar catering",
            target_date=self.today,
            estimated_hours=Decimal("3.0"),
            status=Subtask.Status.PENDIENTE,
        )

    def test_marcar_ejecutada_guarda_estado_y_done_at(self):
        """Escenario 1: Marcar como EJECUTADA persiste status y done_at."""
        url = f"/api/subtasks/{self.subtask.id}"
        response = self.client.patch(url, {"status": "EJECUTADA"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["status"], "EJECUTADA")
        self.assertIsNotNone(response.data["data"]["done_at"])

        # Verificar en base de datos
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, Subtask.Status.EJECUTADA)
        self.assertIsNotNone(self.subtask.done_at)

    def test_posponer_con_nota_explicativa(self):
        """Escenario 2: Posponer con nota explicativa opcional guarda estado y nota."""
        url = f"/api/subtasks/{self.subtask.id}"
        nota = "Esperando confirmación del proveedor de salones"
        response = self.client.patch(
            url,
            {"status": "POSPUESTA", "note": nota},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["status"], "POSPUESTA")
        self.assertEqual(response.data["data"]["note"], nota)
        self.assertIsNone(response.data["data"]["done_at"])

        # Verificar en base de datos
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, Subtask.Status.POSPUESTA)
        self.assertEqual(self.subtask.note, nota)
        self.assertIsNone(self.subtask.done_at)

    def test_cambiar_de_ejecutada_a_pendiente_limpia_done_at(self):
        """Si una tarea pasa de EJECUTADA a PENDIENTE, done_at debe ser None."""
        self.subtask.status = Subtask.Status.EJECUTADA
        self.subtask.done_at = timezone.now()
        self.subtask.save()

        url = f"/api/subtasks/{self.subtask.id}"
        response = self.client.patch(url, {"status": "PENDIENTE"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["status"], "PENDIENTE")
        self.assertIsNone(response.data["data"]["done_at"])

        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, Subtask.Status.PENDIENTE)
        self.assertIsNone(self.subtask.done_at)

    def test_tarea_ejecutada_no_aparece_en_today_por_defecto(self):
        """Criterio QA: EJECUTADA se guarda y ya no aparece en /today (trabajo pendiente)."""
        # Primero aseguramos que en PENDIENTE sí aparece
        res_before = self.client.get("/api/today")
        self.assertEqual(res_before.status_code, status.HTTP_200_OK)
        task_ids_before = [item["id"] for item in res_before.data["data"]]
        self.assertIn(self.subtask.id, task_ids_before)

        # La marcamos como EJECUTADA
        self.client.patch(f"/api/subtasks/{self.subtask.id}", {"status": "EJECUTADA"}, format="json")

        # Ahora en /today ya NO debe aparecer
        res_after = self.client.get("/api/today")
        self.assertEqual(res_after.status_code, status.HTTP_200_OK)
        task_ids_after = [item["id"] for item in res_after.data["data"]]
        self.assertNotIn(self.subtask.id, task_ids_after)

        # Pero si filtramos con ?status=EJECUTADA sí debe aparecer
        res_filter = self.client.get("/api/today?status=EJECUTADA")
        self.assertEqual(res_filter.status_code, status.HTTP_200_OK)
        filter_ids = [item["id"] for item in res_filter.data["data"]]
        self.assertIn(self.subtask.id, filter_ids)
        self.assertEqual(res_filter.data["data"][0]["group"], "ejecutadas")

    def test_status_invalido_retorna_400(self):
        """Criterio QA: Probar status inválido vía API: error 400."""
        url = f"/api/subtasks/{self.subtask.id}"
        response = self.client.patch(url, {"status": "ESTADO_INVALIDO"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("status", response.data["error"]["details"])

    def test_permisos_usuario_no_puede_modificar_gestion_ajena(self):
        """Criterio BE: Permisos por usuario (no se puede editar gestión de otro)."""
        # Cambiamos la autenticación al otro usuario
        self.client.force_authenticate(user=self.other_user)

        url = f"/api/subtasks/{self.subtask.id}"
        response = self.client.patch(url, {"status": "EJECUTADA"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "not_found")

        # Verificar que en BD no cambió
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, Subtask.Status.PENDIENTE)

    def test_usuario_no_autenticado_retorna_401(self):
        """Llamada sin token debe retornar 401."""
        self.client.force_authenticate(user=None)
        url = f"/api/subtasks/{self.subtask.id}"
        response = self.client.patch(url, {"status": "EJECUTADA"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_ejecutada_deja_de_contar_en_conflicto_sobrecarga(self):
        """Criterio QA: Verificar que EJECUTADA deja de contar en el cálculo de conflicto (US-07)."""
        UserSettings.objects.create(user=self.user, daily_limit_hours=Decimal("6.0"))

        # Creamos una segunda gestión para hoy de 4h (total 3h + 4h = 7h > 6h)
        subtask2 = Subtask.objects.create(
            event=self.event,
            name="Degustación menú",
            target_date=self.today,
            estimated_hours=Decimal("4.0"),
            status=Subtask.Status.PENDIENTE,
        )

        # Verificamos sobrecarga actual del día
        check_res = self.client.post(
            "/api/conflicts/overload",
            {"target_date": self.today.isoformat(), "estimated_hours": 0.5},
            format="json",
        )
        self.assertEqual(check_res.status_code, status.HTTP_200_OK)
        # Horas actuales = 7.0, con 0.5 planificadas = 7.5 > 6.0
        self.assertEqual(check_res.data["data"]["current_hours"], 7.0)

        # Marcamos self.subtask (3.0h) como EJECUTADA
        patch_res = self.client.patch(f"/api/subtasks/{self.subtask.id}", {"status": "EJECUTADA"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        # Ahora las horas actuales del día deben ser solo 4.0h (excluye las 3.0h de la ejecutada)
        check_res2 = self.client.post(
            "/api/conflicts/overload",
            {"target_date": self.today.isoformat(), "estimated_hours": 0.5},
            format="json",
        )
        self.assertEqual(check_res2.status_code, status.HTTP_200_OK)
        self.assertEqual(check_res2.data["data"]["current_hours"], 4.0)
        self.assertFalse(check_res2.data["data"]["has_conflict"])

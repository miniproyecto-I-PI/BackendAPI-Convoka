from rest_framework import status
from rest_framework.test import APITestCase

from .models import Event, Subtask


class EventTests(APITestCase):
    def _valid_payload(self):
        return {
            "name": "Boda de Juan y María",
            "type": "BODA",
            "client_contact": "Juan Pérez",
            "event_datetime": "2026-12-05T18:00:00Z",
            "place": "Salón Los Almendros",
        }

    def test_crear_evento_valido(self):
        response = self.client.post("/api/events", self._valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(Event.objects.count(), 1)

    def test_payload_invalido_no_crea_evento(self):
        payload = self._valid_payload()
        payload["name"] = ""
        response = self.client.post("/api/events", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(Event.objects.count(), 0)

    def test_campos_obligatorios(self):
        response = self.client.post("/api/events", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        details = response.data["error"]["details"]
        for field in ("name", "type", "event_datetime"):
            self.assertIn(field, details)

    def test_evento_persiste_y_se_puede_leer(self):
        self.client.post("/api/events", self._valid_payload(), format="json")
        event = Event.objects.get()
        response = self.client.get(f"/api/events/{event.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["name"], "Boda de Juan y María")


class SubtaskTests(APITestCase):
    def setUp(self):
        response = self.client.post(
            "/api/events",
            {"name": "Boda de Juan y María", "type": "BODA", "event_datetime": "2026-12-05T18:00:00Z"},
            format="json",
        )
        self.event_id = response.data["data"]["id"]

    def _valid_payload(self):
        return {"name": "Reservar salón", "target_date": "2026-10-01", "estimated_hours": 4}

    def test_crear_subtarea_valida(self):
        response = self.client.post(
            f"/api/events/{self.event_id}/subtasks", self._valid_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["status"], "PENDIENTE")

    def test_nombre_vacio_no_guarda(self):
        payload = self._valid_payload()
        payload["name"] = ""
        response = self.client.post(f"/api/events/{self.event_id}/subtasks", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Subtask.objects.count(), 0)

    def test_estimated_hours_mayor_a_cero(self):
        """Test explícitamente exigido por el backlog (US-02)."""
        payload = self._valid_payload()
        payload["estimated_hours"] = 0
        response = self.client.post(f"/api/events/{self.event_id}/subtasks", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Subtask.objects.count(), 0)

    def test_estimated_hours_negativo_no_guarda(self):
        payload = self._valid_payload()
        payload["estimated_hours"] = -2
        response = self.client.post(f"/api/events/{self.event_id}/subtasks", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_event_id_inexistente_devuelve_404(self):
        response = self.client.post("/api/events/9999/subtasks", self._valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_subtarea_queda_asociada_al_evento_correcto(self):
        self.client.post(f"/api/events/{self.event_id}/subtasks", self._valid_payload(), format="json")
        subtask = Subtask.objects.get()
        self.assertEqual(subtask.event_id, self.event_id)


class EditDeleteTests(APITestCase):
    def setUp(self):
        response = self.client.post(
            "/api/events",
            {"name": "Cumpleaños de Ana", "type": "CUMPLEANOS", "event_datetime": "2026-11-01T20:00:00Z"},
            format="json",
        )
        self.event_id = response.data["data"]["id"]
        response = self.client.post(
            f"/api/events/{self.event_id}/subtasks",
            {"name": "Enviar invitaciones", "target_date": "2026-10-15", "estimated_hours": 2},
            format="json",
        )
        self.subtask_id = response.data["data"]["id"]

    def test_editar_evento(self):
        response = self.client.patch(f"/api/events/{self.event_id}", {"place": "Nuevo lugar"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Event.objects.get(pk=self.event_id).place, "Nuevo lugar")

    def test_editar_subtarea(self):
        response = self.client.patch(
            f"/api/subtasks/{self.subtask_id}", {"estimated_hours": 3}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(Subtask.objects.get(pk=self.subtask_id).estimated_hours), 3)

    def test_eliminar_subtarea(self):
        response = self.client.delete(f"/api/subtasks/{self.subtask_id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Subtask.objects.count(), 0)

    def test_eliminar_evento_borra_subtareas_por_cascade(self):
        response = self.client.delete(f"/api/events/{self.event_id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(Subtask.objects.count(), 0)

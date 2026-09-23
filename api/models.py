from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Event(models.Model):
    """Evento que el organizador está planificando (US-01)."""

    class EventType(models.TextChoices):
        BODA = "BODA", "Boda"
        SOCIAL = "SOCIAL", "Social"
        CORPORATIVO = "CORPORATIVO", "Corporativo"
        CUMPLEANOS = "CUMPLEANOS", "Cumpleaños"
        OTRO = "OTRO", "Otro"

    # Obligatorios según el backlog (US-01, "campos obligatorios incompletos":
    # título, tipo, fecha del evento)
    name = models.CharField("nombre", max_length=200)
    type = models.CharField("tipo", max_length=20, choices=EventType.choices)
    event_datetime = models.DateTimeField("fecha y hora del evento")

    # Opcionales
    client_contact = models.CharField("cliente/contacto", max_length=200, blank=True)
    place = models.CharField("lugar/plazo", max_length=200, blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="Usuario demo mientras no exista autenticación real (US-11).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-event_datetime"]

    def __str__(self):
        return self.name


class Subtask(models.Model):
    """Gestión logística asociada a un evento (US-02)."""

    class Status(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        EJECUTADA = "EJECUTADA", "Ejecutada"
        POSPUESTA = "POSPUESTA", "Pospuesta"

    # Al eliminar el evento se eliminan sus subtareas (decisión acordada: CASCADE).
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="subtasks")

    name = models.CharField("nombre de la gestión", max_length=200)
    target_date = models.DateField("fecha objetivo")
    estimated_hours = models.DecimalField(
        "horas estimadas",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0.01, message="Las horas deben ser mayores a 0.")],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDIENTE)
    note = models.CharField("nota", max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["target_date"]

    def __str__(self):
        return f"{self.name} ({self.event.name})"

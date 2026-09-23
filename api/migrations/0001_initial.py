# Generated for the T1 event planning models.
import django.core.validators
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Event",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="nombre")),
                ("type", models.CharField(choices=[("BODA", "Boda"), ("SOCIAL", "Social"), ("CORPORATIVO", "Corporativo"), ("CUMPLEANOS", "Cumpleaños"), ("OTRO", "Otro")], max_length=20, verbose_name="tipo")),
                ("event_datetime", models.DateTimeField(verbose_name="fecha y hora del evento")),
                ("client_contact", models.CharField(blank=True, max_length=200, verbose_name="cliente/contacto")),
                ("place", models.CharField(blank=True, max_length=200, verbose_name="lugar/plazo")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(help_text="Usuario demo mientras no exista autenticación real (US-11).", on_delete=django.db.models.deletion.CASCADE, related_name="events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-event_datetime"]},
        ),
        migrations.CreateModel(
            name="Subtask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="nombre de la gestión")),
                ("target_date", models.DateField(verbose_name="fecha objetivo")),
                ("estimated_hours", models.DecimalField(decimal_places=2, max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal("0.01"), message="Las horas deben ser mayores a 0.")], verbose_name="horas estimadas")),
                ("status", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("EJECUTADA", "Ejecutada"), ("POSPUESTA", "Pospuesta")], default="PENDIENTE", max_length=20)),
                ("note", models.CharField(blank=True, max_length=500, verbose_name="nota")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subtasks", to="api.event")),
            ],
            options={"ordering": ["target_date"]},
        ),
    ]

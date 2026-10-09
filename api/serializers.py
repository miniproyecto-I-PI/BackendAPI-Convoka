from rest_framework import serializers
from django.contrib.auth import get_user_model

from decimal import Decimal

from .models import Event, Subtask, UserSettings

User = get_user_model()

DAILY_LIMIT_MESSAGE = "El límite debe estar entre 1 y 16 horas."


class UserSettingsSerializer(serializers.ModelSerializer):
    """Preferencias del usuario: límite diario (US-12) y permitir sobrecarga."""

    daily_limit_hours = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        coerce_to_string=False,
        required=False,
        error_messages={
            "invalid": DAILY_LIMIT_MESSAGE,
            "null": DAILY_LIMIT_MESSAGE,
            "max_digits": DAILY_LIMIT_MESSAGE,
            "max_decimal_places": "Usa como máximo un decimal (ej. 4.5).",
            "max_whole_digits": DAILY_LIMIT_MESSAGE,
        },
    )
    allow_overload = serializers.BooleanField(
        required=False,
        error_messages={"invalid": "Debe ser true o false."},
    )
    allow_subtasks_after_event = serializers.BooleanField(
        required=False,
        error_messages={"invalid": "Debe ser true o false."},
    )
    allow_overdue_subtasks = serializers.BooleanField(
        required=False,
        error_messages={"invalid": "Debe ser true o false."},
    )

    class Meta:
        model = UserSettings
        fields = [
            "daily_limit_hours",
            "allow_overload",
            "allow_subtasks_after_event",
            "allow_overdue_subtasks",
        ]

    def validate_daily_limit_hours(self, value):
        if not Decimal("1") <= value <= Decimal("16"):
            raise serializers.ValidationError(DAILY_LIMIT_MESSAGE)
        return value

class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True, allow_blank=False)
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ese usuario ya está en uso.")
        return value

    def validate_email(self, value):
        # Ya es required y allow_blank=False, así que aquí value nunca llega vacío.
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ese correo ya está registrado.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)  # hashea la password

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(required=True, write_only=True, allow_blank=False)

    def validate(self, attrs):
        identifier = (attrs.get("username") or attrs.get("email") or "").strip()
        password = attrs.get("password") or ""
        if not identifier or not password:
            raise serializers.ValidationError({
                "username": ["Ingresa tu usuario o correo."],
                "password": ["Ingresa tu contraseña."],
            })
        attrs["identifier"] = identifier
        return attrs



class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]


INVALID_DATE_MESSAGE = "Ingresa una fecha válida."


class SubtaskSerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(read_only=True)

    # US-06: fecha obligatoria y con formato YYYY-MM-DD; mismo mensaje en todos los casos.
    target_date = serializers.DateField(
        error_messages={
            "required": INVALID_DATE_MESSAGE,
            "null": INVALID_DATE_MESSAGE,
            "invalid": INVALID_DATE_MESSAGE,
            "datetime": INVALID_DATE_MESSAGE,
        },
    )

    time = serializers.TimeField(
        source="target_time",
        format="%H:%M",
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Subtask
        fields = [
            "id",
            "event",
            "name",
            "target_date",
            "time", 
            "estimated_hours",
            "status",
            "note",
            "provider", 
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "event", "created_at", "updated_at"]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("El nombre es obligatorio.")
        return value

    def validate_estimated_hours(self, value):
        # El validador del modelo (MinValueValidator) también cubre esto;
        # se repite aquí para que el mensaje de US-02 salga tal cual lo pide
        # el backlog ("Las horas deben ser mayores a 0").
        if value <= 0:
            raise serializers.ValidationError("Las horas deben ser mayores a 0.")
        return value


class EventSerializer(serializers.ModelSerializer):
    subtasks = SubtaskSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()

    def get_progress(self, obj):
        return {
            "done": obj.progress_done if hasattr(obj, "progress_done") else obj.subtasks.filter(status=Subtask.Status.EJECUTADA).count(),
            "total": obj.progress_total if hasattr(obj, "progress_total") else obj.subtasks.count(),
        }

    class Meta:
        model = Event
        fields = [
            "id",
            "name",
            "type",
            "client_contact",
            "event_datetime",
            "place",
            "user",
            "subtasks",
            "progress",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "subtasks", "created_at", "updated_at"]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("El nombre del evento es obligatorio.")
        return value

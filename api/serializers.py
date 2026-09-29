from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Event, Subtask

User = get_user_model()

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ese usuario ya está en uso.")
        return value

    def validate_email(self, value):
        if not value:
            return value
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


class SubtaskSerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(read_only=True)

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

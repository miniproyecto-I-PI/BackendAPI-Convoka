from rest_framework import serializers

from .models import Event, Subtask


class SubtaskSerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Subtask
        fields = [
            "id",
            "event",
            "name",
            "target_date",
            "estimated_hours",
            "status",
            "note",
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "subtasks", "created_at", "updated_at"]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("El nombre del evento es obligatorio.")
        return value

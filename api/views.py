from django.db import connection, transaction
from django.db.models import Count, Q
from decimal import Decimal, InvalidOperation
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.contrib.auth import authenticate, get_user_model
from rest_framework.authtoken.models import Token

from .serializers import LoginSerializer, UserSerializer

from .demo import get_demo_user
from .models import Event, Subtask, UserSettings
from .serializers import EventSerializer, SubtaskSerializer
from .utils import error_response, success_response, validation_details

User = get_user_model()

class LoginView(APIView):
    """US-11 — Login local con token. Mismo mensaje exista o no el usuario."""
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(summary="Iniciar sesión", request=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa los campos del formulario.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )

        identifier = serializer.validated_data["identifier"]
        password = serializer.validated_data["password"]

        # Permitir username o email, sin revelar si existe.
        login_username = identifier
        if "@" in identifier:
            user_obj = User.objects.filter(email__iexact=identifier).first()
            if user_obj:
                login_username = user_obj.username

        user = authenticate(request, username=login_username, password=password)
        if user is None:
            return error_response(
                "invalid_credentials",
                "Credenciales inválidas",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        token, _ = Token.objects.get_or_create(user=user)
        return success_response(
            {"token": token.key, "user": UserSerializer(user).data},
            "Inicio de sesión exitoso.",
        )


class LogoutView(APIView):
    """US-11 — Invalida el token actual."""
    @extend_schema(summary="Cerrar sesión")
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return success_response(message="Sesión cerrada.")


class MeView(APIView):
    """US-11 — Usuario autenticado (útil para restaurar sesión al recargar)."""
    @extend_schema(summary="Usuario autenticado", responses={200: UserSerializer})
    def get(self, request):
        return success_response(UserSerializer(request.user).data)


def events_with_progress(queryset):
    """Annotate counts once so event list/detail responses avoid N+1 counts."""
    return queryset.annotate(
        progress_done=Count("subtasks", filter=Q(subtasks__status=Subtask.Status.EJECUTADA)),
        progress_total=Count("subtasks"),
    ).prefetch_related("subtasks")


class HealthCheckView(APIView):
    """
    Endpoint de salud del sistema para verificar el estado del servidor y la base de datos.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Health Check del sistema",
        description=(
            "Verifica que el servicio de Django y la base de datos "
            "PostgreSQL estén activos y respondiendo."
        ),
        responses={
            200: inline_serializer(
                name="HealthCheckSuccessResponse",
                fields={
                    "status": serializers.CharField(default="healthy"),
                    "database": serializers.CharField(default="connected"),
                },
            ),
            503: inline_serializer(
                name="HealthCheckErrorResponse",
                fields={
                    "status": serializers.CharField(default="unhealthy"),
                    "database": serializers.CharField(default="disconnected"),
                    "error": serializers.CharField(),
                },
            ),
        },
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
        except Exception as e:
            return Response(
                {"status": "unhealthy", "database": "disconnected", "error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"status": "healthy", "database": "connected"}, status=status.HTTP_200_OK)


class EventListCreateView(APIView):
    """
    GET  /api/events   Lista los eventos del usuario demo.
    POST /api/events   Crea un evento (US-01).
    """

    @extend_schema(summary="Listar eventos", responses={200: EventSerializer(many=True)})
    def get(self, request):
        events = events_with_progress(Event.objects.filter(user=request.user))
        return success_response(EventSerializer(events, many=True).data)

    @extend_schema(
        summary="Crear evento", request=EventSerializer, responses={201: EventSerializer}
    )
    @transaction.atomic
    def post(self, request):
        serializer = EventSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa los campos del evento.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )

        initial_subtasks = request.data.get("subtasks", [])
        if not isinstance(initial_subtasks, list):
            return error_response(
                "validation_error",
                "Revisa las gestiones logísticas.",
                {"subtasks": ["Envía una lista de gestiones."]},
                status.HTTP_400_BAD_REQUEST,
            )

        subtasks_serializer = SubtaskSerializer(data=initial_subtasks, many=True)
        if not subtasks_serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa las gestiones logísticas.",
                {"subtasks": subtasks_serializer.errors},
                status.HTTP_400_BAD_REQUEST,
            )

        event = serializer.save(user=request.user)
        for subtask_data in subtasks_serializer.validated_data:
            Subtask.objects.create(event=event, **subtask_data)
        return success_response(
            EventSerializer(event).data, "Evento creado.", status.HTTP_201_CREATED
        )


class EventDetailView(APIView):
    """
    GET    /api/events/:id
    PATCH  /api/events/:id   Editar evento (US-03).
    DELETE /api/events/:id   Eliminar evento; CASCADE elimina también sus subtareas.
    """

    def _get_event(self, pk, user):
        return events_with_progress(Event.objects.filter(pk=pk, user=user)).first()

    @extend_schema(summary="Detalle de evento", responses={200: EventSerializer})
    def get(self, request, pk):
        event = self._get_event(pk, request.user)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(EventSerializer(event).data)

    @extend_schema(
        summary="Editar evento", request=EventSerializer, responses={200: EventSerializer}
    )
    def patch(self, request, pk):
        event = self._get_event(pk, request.user)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        serializer = EventSerializer(event, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "No se pudo actualizar el evento.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return success_response(serializer.data, "Cambios guardados.")

    @extend_schema(summary="Eliminar evento")
    def delete(self, request, pk):
        event = self._get_event(pk, request.user)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        event.delete()
        return success_response(message="Evento eliminado.")


class TodayView(APIView):
    UPCOMING_DAYS = 7  # Decisión UX para "Próximas"

    @extend_schema(summary="Listar gestiones para Hoy")
    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone

        today = timezone.localdate()
        qs = Subtask.objects.filter(
            event__user=request.user,
        ).exclude(
            status=Subtask.Status.EJECUTADA,   # US-04: excluir ejecutadas
        ).select_related("event")

        # US-05 — filtros opcionales
        event_id = request.query_params.get("event_id")
        status_param = request.query_params.get("status")
        if event_id:
            qs = qs.filter(event_id=event_id)
        if status_param:
            qs = qs.filter(status=status_param)

        qs = qs.filter(
            target_date__lte=today + timedelta(days=self.UPCOMING_DAYS)
        ).order_by("target_date", "estimated_hours", "id")

        data = []
        for task in qs:
            item = SubtaskSerializer(task).data
            item["event_name"] = task.event.name
            item["event_type"] = task.event.type

            # US-04 — el grupo viaja también desde el BE (FE puede ignorarlo
            # si sigue agrupando con sortGestiones.js)
            if task.target_date < today:
                item["group"] = "vencidas"
            elif task.target_date == today:
                item["group"] = "hoy"
            else:
                item["group"] = "proximas"

            data.append(item)

        return success_response(data)


class DailyLimitView(APIView):
    """Lee o actualiza el límite diario de horas del usuario actual/demo."""

    @staticmethod
    def _settings(request):
        return UserSettings.objects.get_or_create(user=request.user)[0]

    @extend_schema(summary="Consultar límite diario")
    def get(self, request):
        return success_response({"daily_limit_hours": self._settings(request).daily_limit_hours})

    @extend_schema(summary="Actualizar límite diario")
    def patch(self, request):
        value = request.data.get("daily_limit_hours")
        try:
            value = Decimal(str(value))
        except (TypeError, ValueError, InvalidOperation):
            return error_response("validation_error", "El límite debe ser numérico.", {"daily_limit_hours": ["Ingresa un número entre 1 y 16."]}, status.HTTP_400_BAD_REQUEST)
        if not value.is_finite() or not Decimal("1") <= value <= Decimal("16") or value.as_tuple().exponent < -1:
            return error_response("validation_error", "El límite debe estar entre 1 y 16 horas.", {"daily_limit_hours": ["Ingresa un número entre 1 y 16."]}, status.HTTP_400_BAD_REQUEST)
        settings = self._settings(request)
        settings.daily_limit_hours = value
        settings.save(update_fields=["daily_limit_hours"])
        return success_response({"daily_limit_hours": value}, "Límite diario actualizado.")


class SubtaskListCreateView(APIView):
    """
    GET  /api/events/:id/subtasks
    POST /api/events/:id/subtasks   Crea subtarea logística (US-02).
    """

    @extend_schema(
        summary="Listar subtareas de un evento", responses={200: SubtaskSerializer(many=True)}
    )
    def get(self, request, event_id):
        event = Event.objects.filter(pk=event_id, user=request.user).first()
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(SubtaskSerializer(event.subtasks.all(), many=True).data)

    @extend_schema(
        summary="Crear subtarea logística",
        request=SubtaskSerializer,
        responses={201: SubtaskSerializer},
    )
    def post(self, request, event_id):
        event = Event.objects.filter(pk=event_id, user=request.user).first()
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        serializer = SubtaskSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa los campos de la gestión.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        # status siempre arranca en PENDIENTE, sin importar lo que llegue en el body
        subtask = serializer.save(event=event, status=Subtask.Status.PENDIENTE)
        return success_response(
            SubtaskSerializer(subtask).data, "Gestión agregada.", status.HTTP_201_CREATED
        )


class SubtaskDetailView(APIView):
    """
    PATCH  /api/subtasks/:id   Editar subtarea (US-03).
    DELETE /api/subtasks/:id   Eliminar subtarea (US-03).
    """

    def _get_subtask(self, pk, user):
        return Subtask.objects.filter(pk=pk, event__user=user).first()

    @extend_schema(
        summary="Editar subtarea", request=SubtaskSerializer, responses={200: SubtaskSerializer}
    )
    def patch(self, request, pk):
        subtask = self._get_subtask(pk, request.user)
        if subtask is None:
            return error_response("not_found", "Gestión no encontrada.", status_code=404)
        serializer = SubtaskSerializer(subtask, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "No se pudo actualizar la gestión.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return success_response(serializer.data, "Cambios guardados.")

    @extend_schema(summary="Eliminar subtarea")
    def delete(self, request, pk):
        subtask = self._get_subtask(pk, request.user)
        if subtask is None:
            return error_response("not_found", "Gestión no encontrada.", status_code=404)
        subtask.delete()
        return success_response(message="Gestión eliminada.")

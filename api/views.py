from django.db import connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from datetime import date
from decimal import Decimal, InvalidOperation
from drf_spectacular.utils import (
    extend_schema,
    inline_serializer,
    OpenApiParameter,
    OpenApiResponse,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.contrib.auth import authenticate, get_user_model
from rest_framework.authtoken.models import Token

from .serializers import LoginSerializer, UserSerializer
from .serializers import RegisterSerializer

from .demo import get_demo_user
from .models import Event, Subtask
from .overload import (
    check_day,
    day_load,
    days_over_limit,
    evaluate_new_subtasks,
    evaluate_subtask_change,
    get_user_settings,
    suggest_days,
)
from .serializers import EventSerializer, SubtaskSerializer, UserSettingsSerializer
from .utils import error_response, success_response, validation_details

User = get_user_model()


# -----------------------------------------------------------------------------
# Autenticación (US-11)
# -----------------------------------------------------------------------------

class RegisterView(APIView):
    """
    Registro de organizador nuevo.
    NO auto-loguea: el usuario debe ir a /login para iniciar sesión.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Crear cuenta (US-11)",
        description=(
            "Registra un organizador nuevo. **No auto-loguea**: la respuesta "
            "no incluye token, el cliente debe llevar al usuario a `/login`.\n\n"
            "**Reglas:**\n"
            "- `username` y `email` son únicos; si ya existen → `400`.\n"
            "- `email` es obligatorio.\n"
            "- La contraseña se guarda hasheada con `create_user` (pbkdf2).\n"
            "- La contraseña debe tener al menos 8 caracteres.\n\n"
            "**Usado por el FE:** pantalla de registro. Tras un `201`, la app "
            "redirige a `/login` para que el usuario inicie sesión."
        ),
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(description="Cuenta creada. La respuesta incluye el usuario pero **no** un token."),
            400: OpenApiResponse(description="Validación fallida (username/email duplicado, email vacío, contraseña corta)."),
        },
        tags=["Autenticación"],
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa los campos del formulario.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        user = serializer.save()
        return success_response(
            {"user": UserSerializer(user).data},
            "Cuenta creada correctamente. Ahora puedes iniciar sesión.",
            status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """US-11 — Login local con token. Mismo mensaje exista o no el usuario."""
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Iniciar sesión (US-11)",
        description=(
            "Inicia sesión con `username` **o** `email` y `password`. "
            "Si las credenciales son válidas, devuelve el token de DRF del usuario "
            "(lo crea si no existía).\n\n"
            "**Seguridad:** el mensaje de error es siempre `'Credenciales inválidas'`, "
            "exista o no el usuario. Así no se filtra información sensible.\n\n"
            "**Uso del token en requests posteriores:**\n"
            "```\nAuthorization: Token <key>\n```\n\n"
            "**Usado por el FE:** formulario de `/login`. Tras un `200`, el FE "
            "guarda el token en `localStorage` (o `sessionStorage`) y navega a `/hoy`."
        ),
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Login exitoso. Devuelve `{ token, user }`."),
            400: OpenApiResponse(description="Faltan campos obligatorios (`username`/`email` y `password`)."),
            401: OpenApiResponse(description="Credenciales inválidas."),
        },
        tags=["Autenticación"],
    )
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
    @extend_schema(
        summary="Cerrar sesión (US-11)",
        description=(
            "Invalida el token actual del usuario autenticado eliminándolo "
            "de la base de datos. Cualquier intento posterior de usar ese token "
            "responderá `401 Invalid token`.\n\n"
            "El cliente debe además limpiar su almacenamiento local "
            "(`localStorage` / `sessionStorage`).\n\n"
            "**Usado por el FE:** botón Cerrar sesión en el header. El FE "
            "limpia el token de storage y redirige a `/login`."
        ),
        responses={
            200: OpenApiResponse(description="Sesión cerrada. Token eliminado."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Autenticación"],
    )
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return success_response(message="Sesión cerrada.")


class MeView(APIView):
    """US-11 — Usuario autenticado (útil para restaurar sesión al recargar)."""
    @extend_schema(
        summary="Usuario autenticado (US-11)",
        description=(
            "Devuelve los datos del usuario identificado por el token. "
            "Se usa al recargar la SPA para restaurar la sesión sin pedir login de nuevo.\n\n"
            "Si responde `401`, el frontend debe limpiar el token y redirigir a `/login`.\n\n"
            "**Usado por el FE:** al montar el `AuthProvider`, el FE llama a este "
            "endpoint para restaurar la sesión desde el token guardado."
        ),
        responses={
            200: OpenApiResponse(
                response=UserSerializer,
                description="Datos del usuario autenticado.",
            ),
            401: OpenApiResponse(description="Sin token o token inválido/expirado."),
        },
        tags=["Autenticación"],
    )
    def get(self, request):
        return success_response(UserSerializer(request.user).data)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def events_with_progress(queryset):
    """Annotate counts once so event list/detail responses avoid N+1 counts."""
    return queryset.annotate(
        progress_done=Count("subtasks", filter=Q(subtasks__status=Subtask.Status.EJECUTADA)),
        progress_total=Count("subtasks"),
    ).prefetch_related("subtasks")


def overload_conflict_response(conflicts):
    """409 de US-07: el cambio supera el límite diario y el usuario no permite sobrecarga."""
    details = {"conflicts": conflicts}
    if len(conflicts) == 1:
        details["conflict"] = conflicts[0]
    return error_response(
        "overload_conflict",
        conflicts[0]["message"],
        details,
        status.HTTP_409_CONFLICT,
    )


def target_date_after_event_response(event_date):
    return error_response(
        "target_date_after_event",
        "La fecha objetivo no puede ser posterior a la fecha del evento.",
        {"target_date": [f"Debe ser igual o anterior al {event_date.isoformat()}."]},
        status.HTTP_400_BAD_REQUEST,
    )


def target_date_in_past_response():
    return error_response(
        "target_date_in_past",
        "La fecha objetivo no puede ser anterior a hoy.",
        {"target_date": [f"Debe ser igual o posterior al {timezone.localdate().isoformat()}."]},
        status.HTTP_400_BAD_REQUEST,
    )


def event_date_in_past_response():
    return error_response(
        "event_date_in_past",
        "La fecha del evento no puede ser anterior a hoy.",
        {"event_datetime": [f"Debe ser igual o posterior al {timezone.localdate().isoformat()}."]},
        status.HTTP_400_BAD_REQUEST,
    )


def subtasks_after_event_response(event_date, subtasks):
    return error_response(
        "subtasks_after_event",
        "Hay gestiones programadas después de la nueva fecha del evento.",
        {
            "event_datetime": [
                f"Mueve primero estas gestiones al {event_date.isoformat()} o antes."
            ],
            "subtasks": [
                {"id": s.id, "name": s.name, "target_date": s.target_date.isoformat()}
                for s in subtasks
            ],
        },
        status.HTTP_400_BAD_REQUEST,
    )


def event_local_date(event_datetime):
    """Fecha del evento en hora local (America/Bogota), no en UTC."""
    return timezone.localtime(event_datetime).date()


def subtask_date_error(target_date, event_date, settings):
    """
    Reglas de fecha de una gestión que se crea o reprograma, según las
    preferencias del usuario. Devuelve la respuesta de error o None.
    """
    if not settings.allow_overdue_subtasks and target_date < timezone.localdate():
        return target_date_in_past_response()
    if not settings.allow_subtasks_after_event and target_date > event_date:
        return target_date_after_event_response(event_date)
    return None


def apply_daily_limit(settings, new_limit, allow_overload):
    """
    Bajar el límite diario por debajo de lo ya planificado (de hoy en adelante)
    se rechaza, salvo que el usuario permita sobrecarga. Devuelve
    (respuesta_de_error, días_excedidos).
    """
    days = days_over_limit(settings.user, new_limit)
    if days and not allow_overload:
        listed = ", ".join(day["date"] for day in days)
        return (
            error_response(
                "daily_limit_below_planned",
                f"No puedes reducir: tienes días con más horas planificadas ({listed})",
                {"daily_limit_hours": ["Hay días con más horas planificadas."], "days": days},
                status.HTTP_400_BAD_REQUEST,
            ),
            days,
        )
    return None, days


CONFLICT_REPORT_SCHEMA = inline_serializer(
    name="OverloadReport",
    fields={
        "date": serializers.DateField(),
        "current_hours": serializers.FloatField(help_text="Horas ya planificadas ese día (sin esta gestión)."),
        "added_hours": serializers.FloatField(help_text="Horas que aporta la gestión evaluada."),
        "planned_hours": serializers.FloatField(help_text="Total resultante del día."),
        "limit_hours": serializers.FloatField(help_text="Límite diario del usuario (US-12)."),
        "exceeds_by": serializers.FloatField(help_text="Horas por encima del límite (0 si no excede)."),
        "has_conflict": serializers.BooleanField(),
        "allow_overload": serializers.BooleanField(help_text="Preferencia del usuario."),
        "message": serializers.CharField(help_text="Texto listo para mostrar; vacío si no hay conflicto."),
    },
)

CONFLICT_409_DESCRIPTION = (
    "Conflicto de sobrecarga diaria (`code: overload_conflict`). Solo ocurre si el "
    "usuario tiene `allow_overload=false`. No se guarda nada. `error.message` trae el "
    "texto listo (\"Quedarías con 7h de gestión planificadas (límite 6h)\") y "
    "`error.details.conflict` / `error.details.conflicts` el reporte con "
    "`planned_hours`, `limit_hours`, `exceeds_by`, `has_conflict`."
)


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------

class HealthCheckView(APIView):
    """
    Endpoint de salud del sistema para verificar el estado del servidor y la base de datos.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Health Check del sistema",
        description=(
            "Verifica que el servicio de Django y la base de datos estén activos "
            "ejecutando un `SELECT 1` contra la BD.\n\n"
            "**No requiere autenticación.** Útil para UptimeRobot o para que Render "
            "no apague la instancia por inactividad.\n\n"
            "**Usado por el FE:** no directamente. Se usa para monitoreo externo "
            "(UptimeRobot) o para el botón Comprobar estado del servicio del FE."
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
        tags=["Sistema"],
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


# -----------------------------------------------------------------------------
# Eventos
# -----------------------------------------------------------------------------

class EventListCreateView(APIView):
    """
    GET  /api/events   Lista los eventos del usuario.
    POST /api/events   Crea un evento (US-01).
    """

    @extend_schema(
        summary="Listar eventos del organizador",
        description=(
            "Devuelve todos los eventos del usuario autenticado, ordenados por "
            "`event_datetime` descendente (los más próximos primero).\n\n"
            "Cada evento incluye sus `subtasks` anidadas y un objeto `progress` "
            "con `{done, total}` ya calculado por Django en una sola query.\n\n"
            "**Usado por el FE:** vista `/eventos` (listado completo) y dashboard "
            "principal. El FE lo llama una sola vez al montar el hook `useEvents`."
        ),
        responses={
            200: OpenApiResponse(
                response=EventSerializer(many=True),
                description="Lista de eventos con subtasks y progress.",
            ),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Eventos"],
    )
    def get(self, request):
        events = events_with_progress(Event.objects.filter(user=request.user))
        return success_response(EventSerializer(events, many=True).data)

    @extend_schema(
        summary="Crear evento (US-01)",
        description=(
            "Crea un evento y opcionalmente sus gestiones iniciales en la misma request.\n\n"
            "**Campos obligatorios:** `name`, `type`, `event_datetime`.\n"
            "**Opcionales:** `client_contact`, `place`, `subtasks`.\n\n"
            "**Reglas:**\n"
            "- La fecha del evento (hora de Bogotá) no puede ser anterior a hoy → "
            "`400 event_date_in_past`.\n"
            "- Si se envían `subtasks`, cada una debe tener `target_date` **≤** la fecha "
            "del evento (`400 target_date_after_event`), salvo que el usuario tenga "
            "`allow_subtasks_after_event=true`; y **≥** hoy (`400 target_date_in_past`), "
            "salvo que tenga `allow_overdue_subtasks=true`.\n"
            "- Todo corre en una transacción atómica: si falla una subtask, no se crea nada.\n"
            "- El evento queda asociado al usuario del token.\n"
            "- **Sobrecarga diaria (US-07):** las gestiones iniciales se suman (por día) a "
            "las que el usuario ya tiene planificadas. Si algún día supera el límite y el "
            "usuario no permite sobrecarga → `409` con `error.details.conflicts` (una "
            "entrada por día excedido). Si la permite, se crea y la respuesta trae "
            "`data.conflicts` como advertencia.\n\n"
            "**Usado por el FE:** vista `/crear`. Tras un `201`, el FE navega al "
            "detalle del evento o a `/hoy` con un toast de confirmación."
        ),
        request=EventSerializer,
        responses={
            201: OpenApiResponse(response=EventSerializer, description="Evento creado con sus subtasks."),
            400: OpenApiResponse(description="Validación fallida (campos obligatorios, formato de fecha, evento en el pasado, subtask vencida o con fecha posterior al evento)."),
            401: OpenApiResponse(description="Sin token o token inválido."),
            409: OpenApiResponse(description=CONFLICT_409_DESCRIPTION),
        },
        tags=["Eventos"],
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

        event_date = event_local_date(serializer.validated_data["event_datetime"])
        if event_date < timezone.localdate():
            return event_date_in_past_response()

        settings = get_user_settings(request.user)
        for subtask_data in subtasks_serializer.validated_data:
            date_error = subtask_date_error(subtask_data["target_date"], event_date, settings)
            if date_error:
                return date_error

        conflicts, blocked = evaluate_new_subtasks(
            request.user,
            [(s["target_date"], s["estimated_hours"]) for s in subtasks_serializer.validated_data],
        )
        if blocked:
            return overload_conflict_response(conflicts)

        event = serializer.save(user=request.user)
        for subtask_data in subtasks_serializer.validated_data:
            Subtask.objects.create(event=event, **subtask_data)
        data = EventSerializer(event).data
        if conflicts:
            data["conflicts"] = conflicts
        return success_response(data, "Evento creado.", status.HTTP_201_CREATED)


class EventDetailView(APIView):
    """
    GET    /api/events/:id
    PATCH  /api/events/:id   Editar evento (US-03).
    DELETE /api/events/:id   Eliminar evento; CASCADE elimina también sus subtareas.
    """

    def _get_event(self, pk, user):
        return events_with_progress(Event.objects.filter(pk=pk, user=user)).first()

    @extend_schema(
        summary="Detalle de evento",
        description=(
            "Devuelve un evento del usuario autenticado, con sus subtasks y progress.\n\n"
            "Si el evento no existe **o pertenece a otro usuario** → `404` "
            "(no se revela la existencia de eventos ajenos).\n\n"
            "**Usado por el FE:** vista `/evento/:id`. El hook `useEventSubtasks` "
            "lo llama al montar y también tras cada edición del evento."
        ),
        responses={
            200: OpenApiResponse(response=EventSerializer, description="Evento con subtasks y progress."),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Eventos"],
    )
    def get(self, request, pk):
        event = self._get_event(pk, request.user)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(EventSerializer(event).data)

    @extend_schema(
        summary="Editar evento (US-03)",
        description=(
            "Actualiza **parcialmente** un evento. Solo se modifican los campos "
            "que se envíen en el body.\n\n"
            "Si se cambia `event_datetime`:\n"
            "- La nueva fecha (hora de Bogotá) no puede ser anterior a hoy → "
            "`400 event_date_in_past`.\n"
            "- Si alguna gestión del evento queda con `target_date` posterior a la nueva "
            "fecha → `400 subtasks_after_event` con `error.details.subtasks` "
            "(`id`, `name`, `target_date` de cada una). No aplica si el usuario tiene "
            "`allow_subtasks_after_event=true`.\n\n"
            "**Usado por el FE:** modal Editar ficha de evento en `/evento/:id`. "
            "Tras un `200`, el FE muestra el modal de éxito y refresca la vista."
        ),
        request=EventSerializer,
        responses={
            200: OpenApiResponse(response=EventSerializer, description="Evento actualizado."),
            400: OpenApiResponse(
                description=(
                    "Validación fallida, evento en el pasado o gestiones posteriores "
                    "a la nueva fecha."
                )
            ),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
        },
        tags=["Eventos"],
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

        new_datetime = serializer.validated_data.get("event_datetime")
        if new_datetime is not None and new_datetime != event.event_datetime:
            new_date = event_local_date(new_datetime)
            if new_date < timezone.localdate():
                return event_date_in_past_response()
            if not get_user_settings(request.user).allow_subtasks_after_event:
                late = event.subtasks.filter(target_date__gt=new_date).order_by("target_date", "id")
                if late:
                    return subtasks_after_event_response(new_date, late)

        serializer.save()
        return success_response(serializer.data, "Cambios guardados.")

    @extend_schema(
        summary="Eliminar evento",
        description=(
            "Elimina un evento y **todas sus gestiones** (CASCADE). "
            "La operación es permanente; no hay soft-delete.\n\n"
            "El frontend debe pedir confirmación al usuario antes de llamar este endpoint.\n\n"
            "**Usado por el FE:** modal `DeleteEventModal`. Tras confirmar, el FE "
            "redirige a `/hoy` con un toast de Evento eliminado."
        ),
        responses={
            200: OpenApiResponse(description="Evento y sus gestiones eliminados."),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
        },
        tags=["Eventos"],
    )
    def delete(self, request, pk):
        event = self._get_event(pk, request.user)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        event.delete()
        return success_response(message="Evento eliminado.")


# -----------------------------------------------------------------------------
# Vista Hoy (US-04 + US-05)
# -----------------------------------------------------------------------------

class TodayView(APIView):
    UPCOMING_DAYS = 7  # Decisión UX para "Próximas"

    @extend_schema(
        summary="Listar gestiones para Hoy (US-04 + US-05)",
        description=(
            "Devuelve las gestiones logísticas del organizador autenticado.\n\n"
            "**Por defecto** se devuelven solo gestiones **no ejecutadas**, "
            "agrupadas y ordenadas según la regla de prioridad de US-04.\n\n"
            "**Agrupación (campo `group`):**\n"
            "- `vencidas`: `target_date` anterior a hoy (más antigua primero).\n"
            "- `hoy`: `target_date` igual a hoy.\n"
            "- `proximas`: `target_date` posterior a hoy, hasta 7 días.\n"
            "- `ejecutadas`: solo aparece cuando se filtra explícitamente con "
            "`?status=EJECUTADA`. Devuelve el histórico completo de gestiones "
            "completadas, sin límite de fecha.\n\n"
            "**Orden dentro de cada grupo:** `target_date` ascendente, luego `time` "
            "ascendente (una gestión sin hora cuenta como 00:00); en caso de empate, "
            "`estimated_hours` ascendente (menor esfuerzo primero).\n\n"
            "**Filtros opcionales (US-05):** combinables con `&`.\n\n"
            "**Usado por el FE:** vista `/hoy`. El hook `useTodayGestiones` lo llama "
            "al montar y cada vez que cambian los filtros de estado o evento."
        ),
        parameters=[
            OpenApiParameter(
                name="event_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtra por id de evento. Solo eventos del usuario autenticado.",
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["PENDIENTE", "POSPUESTA", "EJECUTADA"],
                description=(
                    "Filtra por estado. `EJECUTADA` habilita el grupo `ejecutadas` "
                    "con el histórico completo. Sin este parámetro, se excluyen "
                    "las ejecutadas (regla de US-04)."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="TodayItem",
                    fields={
                        "id": serializers.IntegerField(),
                        "name": serializers.CharField(),
                        "target_date": serializers.DateField(),
                        "time": serializers.TimeField(allow_null=True),
                        "estimated_hours": serializers.DecimalField(max_digits=5, decimal_places=2),
                        "status": serializers.ChoiceField(
                            choices=["PENDIENTE", "POSPUESTA", "EJECUTADA"]
                        ),
                        "note": serializers.CharField(),
                        "provider": serializers.CharField(),
                        "event_name": serializers.CharField(),
                        "event_type": serializers.CharField(),
                        "group": serializers.ChoiceField(
                            choices=["vencidas", "hoy", "proximas", "ejecutadas"]
                        ),
                    },
                    many=True,
                ),
                description=(
                    "Lista de gestiones del usuario. Cada item incluye `group` "
                    "para que el cliente pueda separar en las cuatro secciones."
                ),
            ),
            401: OpenApiResponse(description="Token ausente, inválido o expirado."),
        },
        tags=["Vista Hoy"],
    )
    def get(self, request):
        from datetime import timedelta

        today = timezone.localdate()
        qs = Subtask.objects.filter(
            event__user=request.user,
        ).select_related("event")

        event_id = request.query_params.get("event_id")
        status_param = request.query_params.get("status")

        if event_id:
            qs = qs.filter(event_id=event_id)

        if status_param:
            # Estado explícito → respétalo tal cual (habilita EJECUTADA).
            qs = qs.filter(status=status_param)
        else:
            # Sin filtro: /hoy muestra solo trabajo pendiente (US-04).
            qs = qs.exclude(status=Subtask.Status.EJECUTADA)

        # El límite de 7 días no aplica al histórico de ejecutadas.
        if status_param != Subtask.Status.EJECUTADA:
            qs = qs.filter(target_date__lte=today + timedelta(days=self.UPCOMING_DAYS))

        # Sin hora cuenta como 00:00: va antes que las que sí tienen hora ese día.
        qs = qs.order_by(
            "target_date", F("target_time").asc(nulls_first=True), "estimated_hours", "id"
        )

        data = []
        for task in qs:
            item = SubtaskSerializer(task).data
            item["event_name"] = task.event.name
            item["event_type"] = task.event.type

            # Grupo: las ejecutadas van a un bloque propio, no se mezclan
            # con la clasificación temporal de US-04.
            if task.status == Subtask.Status.EJECUTADA:
                item["group"] = "ejecutadas"
            elif task.target_date < today:
                item["group"] = "vencidas"
            elif task.target_date == today:
                item["group"] = "hoy"
            else:
                item["group"] = "proximas"

            data.append(item)

        return success_response(data)


# -----------------------------------------------------------------------------
# Configuración del usuario
# -----------------------------------------------------------------------------

class DailyLimitView(APIView):
    """Lee o actualiza el límite diario de horas del usuario autenticado (US-12)."""

    @extend_schema(
        summary="Consultar límite diario de horas (US-12)",
        description=(
            "Devuelve el límite diario de horas de gestión configurado por el usuario "
            "(por defecto **6** si no lo ha modificado).\n\n"
            "Este valor se usa en **US-07** para detectar sobrecarga cuando se "
            "crean o reprograman gestiones.\n\n"
            "**Usado por el FE:** pantalla de configuración. El FE lo llama al "
            "montar la vista para mostrar el valor actual."
        ),
        responses={
            200: OpenApiResponse(description="Valor actual del límite diario: `{ daily_limit_hours }`."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Configuración"],
    )
    def get(self, request):
        return success_response({"daily_limit_hours": get_user_settings(request.user).daily_limit_hours})

    @extend_schema(
        summary="Actualizar límite diario de horas (US-12)",
        description=(
            "Actualiza el límite diario de horas de gestión del usuario. `PUT` y `PATCH` "
            "se comportan igual (el backlog pide `PUT`; el FE actual usa `PATCH`).\n\n"
            "**Rango válido:** entre 1 y 16 horas, con un decimal como máximo.\n"
            "Valores fuera de rango → `400` con el mensaje "
            "\"El límite debe estar entre 1 y 16 horas.\"\n\n"
            "**No se puede bajar por debajo de lo planificado:** si algún día desde hoy "
            "(sin contar `EJECUTADA`) supera el nuevo límite → `400 daily_limit_below_planned` "
            "con el mensaje \"No puedes reducir: tienes días con más horas planificadas "
            "(2026-10-12, 2026-10-14)\" y `error.details.days` (`date`, `planned_hours`). "
            "Si el usuario tiene `allow_overload=true` se guarda igual y `data.days_over_limit` "
            "trae esos días como advertencia.\n\n"
            "A partir de este cambio, la detección de conflicto (US-07) usa el nuevo valor.\n\n"
            "**Usado por el FE:** input numérico en la pantalla de configuración. "
            "Se llama al guardar y muestra un toast de confirmación."
        ),
        request=inline_serializer(
            name="DailyLimitRequest",
            fields={"daily_limit_hours": serializers.DecimalField(max_digits=4, decimal_places=1)},
        ),
        responses={
            200: OpenApiResponse(description="Límite actualizado."),
            400: OpenApiResponse(description="Valor no numérico, fuera de rango, con demasiados decimales, o por debajo de lo ya planificado."),
        },
        tags=["Configuración"],
    )
    def put(self, request):
        value = request.data.get("daily_limit_hours")
        try:
            value = Decimal(str(value))
        except (TypeError, ValueError, InvalidOperation):
            return error_response("validation_error", "El límite debe ser numérico.", {"daily_limit_hours": ["Ingresa un número entre 1 y 16."]}, status.HTTP_400_BAD_REQUEST)
        if not value.is_finite() or not Decimal("1") <= value <= Decimal("16") or value.as_tuple().exponent < -1:
            return error_response("validation_error", "El límite debe estar entre 1 y 16 horas.", {"daily_limit_hours": ["Ingresa un número entre 1 y 16."]}, status.HTTP_400_BAD_REQUEST)
        settings = get_user_settings(request.user)
        limit_error, days = apply_daily_limit(settings, value, settings.allow_overload)
        if limit_error:
            return limit_error
        settings.daily_limit_hours = value
        settings.save(update_fields=["daily_limit_hours"])
        data = {"daily_limit_hours": value}
        if days:
            data["days_over_limit"] = days
        return success_response(data, "Límite diario actualizado.")

    @extend_schema(
        summary="Actualizar límite diario de horas (US-12, alias de PUT)",
        description="Igual que `PUT /settings/daily-limit`. Se mantiene porque el frontend actual lo usa.",
        request=inline_serializer(
            name="DailyLimitPatchRequest",
            fields={"daily_limit_hours": serializers.DecimalField(max_digits=4, decimal_places=1)},
        ),
        responses={
            200: OpenApiResponse(description="Límite actualizado."),
            400: OpenApiResponse(description="Valor no numérico, fuera de rango, o con demasiados decimales."),
        },
        tags=["Configuración"],
    )
    def patch(self, request):
        return self.put(request)


class UserSettingsView(APIView):
    """GET/PATCH /api/settings — todas las preferencias del usuario."""

    @extend_schema(
        summary="Consultar preferencias del usuario",
        description=(
            "Devuelve todas las preferencias del usuario autenticado:\n"
            "- `daily_limit_hours`: límite diario de horas de gestión (US-12, default 6).\n"
            "- `allow_overload`: si es `true`, se permite programar gestiones en un día "
            "aunque se supere el límite (el conflicto llega como advertencia en la "
            "respuesta en vez de un `409`). **Por defecto `false`.**\n"
            "- `allow_subtasks_after_event`: si es `true`, una gestión puede quedar con "
            "fecha posterior a la de su evento (al crearla, reprogramarla o al mover el "
            "evento). **Por defecto `false`.**\n"
            "- `allow_overdue_subtasks`: si es `true`, se pueden crear o reprogramar "
            "gestiones con fecha anterior a hoy (pensado para pruebas). **Por defecto `false`.**\n\n"
            "**Usado por el FE:** pantalla de configuración/perfil."
        ),
        responses={
            200: OpenApiResponse(response=UserSettingsSerializer, description="Preferencias actuales."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Configuración"],
    )
    def get(self, request):
        return success_response(UserSettingsSerializer(get_user_settings(request.user)).data)

    @extend_schema(
        summary="Actualizar preferencias del usuario",
        description=(
            "Actualiza **parcialmente** las preferencias: se puede enviar cualquier "
            "combinación de campos.\n\n"
            "**Validaciones:**\n"
            "- `daily_limit_hours`: entre 1 y 16, máximo un decimal → si no, `400`. "
            "Tampoco puede quedar por debajo de lo planificado desde hoy "
            "(`400 daily_limit_below_planned`, igual que en `/settings/daily-limit`), salvo "
            "con `allow_overload=true` (el enviado o el guardado), y entonces "
            "`data.days_over_limit` trae los días como advertencia.\n"
            "- `allow_overload`, `allow_subtasks_after_event`, `allow_overdue_subtasks`: "
            "booleanos → si no, `400`.\n\n"
            "Ejemplo: `{ \"allow_overload\": true }`."
        ),
        request=UserSettingsSerializer,
        responses={
            200: OpenApiResponse(response=UserSettingsSerializer, description="Preferencias actualizadas."),
            400: OpenApiResponse(description="Validación fallida."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Configuración"],
    )
    def patch(self, request):
        settings = get_user_settings(request.user)
        serializer = UserSettingsSerializer(settings, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa tus preferencias.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        days = []
        validated = serializer.validated_data
        if "daily_limit_hours" in validated:
            limit_error, days = apply_daily_limit(
                settings,
                validated["daily_limit_hours"],
                validated.get("allow_overload", settings.allow_overload),
            )
            if limit_error:
                return limit_error
        serializer.save()
        data = serializer.data
        if days:
            data["days_over_limit"] = days
        return success_response(data, "Preferencias actualizadas.")


# -----------------------------------------------------------------------------
# Conflictos de sobrecarga (US-07 / US-08)
# -----------------------------------------------------------------------------

class OverloadCheckView(APIView):
    """POST /api/conflicts/overload — simula una gestión en un día sin guardar nada."""

    class InputSerializer(serializers.Serializer):
        target_date = serializers.DateField(
            error_messages={
                "required": "Ingresa una fecha válida.",
                "null": "Ingresa una fecha válida.",
                "invalid": "Ingresa una fecha válida.",
            }
        )
        estimated_hours = serializers.DecimalField(
            max_digits=5, decimal_places=2, required=False,
            min_value=Decimal("0.01"),
            error_messages={"min_value": "Las horas deben ser mayores a 0."},
        )
        subtask_id = serializers.IntegerField(required=False)

    @extend_schema(
        summary="Verificar sobrecarga diaria (US-07)",
        description=(
            "Calcula, **sin guardar nada**, cómo quedaría la carga de un día si se "
            "programa ahí una gestión. Pensado para llamarlo antes de confirmar una "
            "reprogramación (o en vivo mientras el usuario ajusta horas, US-08).\n\n"
            "**Body:**\n"
            "- `target_date` (obligatorio): día a evaluar, `YYYY-MM-DD`.\n"
            "- `subtask_id` (opcional): gestión existente que se movería/editaría. "
            "Sus horas actuales no se cuentan dos veces.\n"
            "- `estimated_hours` (opcional si se envía `subtask_id`): horas a sumar. "
            "Si se omite, se usan las de la gestión.\n\n"
            "**Cálculo:** `planned_hours = SUM(estimated_hours)` de las gestiones del "
            "usuario en ese día (excluyendo `EJECUTADA` y la propia `subtask_id`) + "
            "`estimated_hours`. `has_conflict = planned_hours > limit_hours` "
            "(llegar exactamente al límite **no** es conflicto).\n\n"
            "Ejemplo: límite 6h, día con 5h, mover gestión de 2h → "
            "`planned_hours: 7, exceeds_by: 1, has_conflict: true, "
            "message: \"Quedarías con 7h de gestión planificadas (límite 6h)\"`."
        ),
        request=InputSerializer,
        responses={
            200: OpenApiResponse(response=CONFLICT_REPORT_SCHEMA, description="Reporte de carga del día."),
            400: OpenApiResponse(description="Fecha inválida, horas ≤ 0, o falta `estimated_hours` sin `subtask_id`."),
            404: OpenApiResponse(description="`subtask_id` no existe o no pertenece al usuario."),
        },
        tags=["Conflictos"],
    )
    def post(self, request):
        serializer = self.InputSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "No pudimos verificar tu carga del día.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        subtask = None
        if "subtask_id" in data:
            subtask = Subtask.objects.filter(pk=data["subtask_id"], event__user=request.user).first()
            if subtask is None:
                return error_response("not_found", "Gestión no encontrada.", status_code=404)
        hours = data.get("estimated_hours", subtask.estimated_hours if subtask else None)
        if hours is None:
            return error_response(
                "validation_error",
                "No pudimos verificar tu carga del día.",
                {"estimated_hours": ["Envía las horas estimadas o el id de la gestión."]},
                status.HTTP_400_BAD_REQUEST,
            )
        report = check_day(
            request.user,
            data["target_date"],
            hours,
            exclude_subtask_id=subtask.pk if subtask else None,
        )
        return success_response(report)


class OverloadSuggestionsView(APIView):
    """GET /api/conflicts/suggestions — próximos días con capacidad libre (US-08)."""

    @extend_schema(
        summary="Sugerir días con capacidad libre (US-08)",
        description=(
            "Devuelve los próximos días en los que cabe una gestión sin superar el "
            "límite diario del usuario. Sirve para la opción \"mover a otro día\" "
            "del modal de conflicto.\n\n"
            "**Reglas:**\n"
            "- Se busca desde `from` (por defecto hoy) día por día.\n"
            "- Si se envía `subtask_id`, la búsqueda termina en la fecha de su evento "
            "(no se puede programar después) y sus horas actuales no cuentan.\n"
            "- Sin `subtask_id`, la búsqueda cubre 30 días.\n"
            "- Lista vacía = no hay días sugeridos → el FE debe permitir selección manual."
        ),
        parameters=[
            OpenApiParameter("subtask_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
                             description="Gestión a mover. Si se omite `hours` se usan sus horas."),
            OpenApiParameter("hours", OpenApiTypes.NUMBER, OpenApiParameter.QUERY, required=False,
                             description="Horas a ubicar (obligatorio si no hay `subtask_id`)."),
            OpenApiParameter("from", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False,
                             description="Primer día a evaluar (`YYYY-MM-DD`). Default: hoy."),
            OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
                             description="Cantidad máxima de sugerencias (1–10, default 3)."),
        ],
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="DaySuggestion",
                    fields={
                        "date": serializers.DateField(),
                        "current_hours": serializers.FloatField(),
                        "planned_hours": serializers.FloatField(),
                        "available_hours": serializers.FloatField(),
                        "limit_hours": serializers.FloatField(),
                    },
                    many=True,
                ),
                description="Días sugeridos (puede ser lista vacía).",
            ),
            400: OpenApiResponse(description="Parámetros inválidos."),
            404: OpenApiResponse(description="`subtask_id` no existe o no pertenece al usuario."),
        },
        tags=["Conflictos"],
    )
    def get(self, request):
        params = request.query_params
        try:
            hours = Decimal(params["hours"]) if params.get("hours") else None
            start = date.fromisoformat(params["from"]) if params.get("from") else None
            limit = min(max(int(params.get("limit", 3)), 1), 10)
            subtask_id = int(params["subtask_id"]) if params.get("subtask_id") else None
        except (ValueError, InvalidOperation):
            return error_response("validation_error", "Parámetros inválidos.", status_code=400)
        if hours is not None and (not hours.is_finite() or hours <= 0):
            return error_response(
                "validation_error",
                "Parámetros inválidos.",
                {"hours": ["Las horas deben ser mayores a 0."]},
                status.HTTP_400_BAD_REQUEST,
            )

        end = None
        if subtask_id is not None:
            subtask = Subtask.objects.filter(pk=subtask_id, event__user=request.user).select_related("event").first()
            if subtask is None:
                return error_response("not_found", "Gestión no encontrada.", status_code=404)
            hours = hours if hours is not None else subtask.estimated_hours
            end = event_local_date(subtask.event.event_datetime)
        if hours is None:
            return error_response(
                "validation_error",
                "Parámetros inválidos.",
                {"hours": ["Envía las horas o el id de la gestión."]},
                status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            suggest_days(request.user, hours, start=start, end=end, exclude_subtask_id=subtask_id, limit=limit)
        )


# -----------------------------------------------------------------------------
# Gestiones (Subtask)
# -----------------------------------------------------------------------------

class SubtaskListCreateView(APIView):
    """
    GET  /api/events/:id/subtasks
    POST /api/events/:id/subtasks   Crea subtarea logística (US-02).
    """

    @extend_schema(
        summary="Listar gestiones de un evento",
        description=(
            "Devuelve todas las gestiones (`Subtask`) asociadas a un evento del usuario.\n\n"
            "Si el evento no existe **o pertenece a otro usuario** → `404`.\n\n"
            "**Usado por el FE:** el hook `useEventSubtasks` lo llama al abrir "
            "`/evento/:id`. Sin embargo, el detalle del evento (`GET /events/:id`) "
            "ya trae las subtasks anidadas, así que este endpoint suele usarse solo "
            "para refrescos puntuales."
        ),
        responses={
            200: OpenApiResponse(
                response=SubtaskSerializer(many=True),
                description="Lista de gestiones del evento.",
            ),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
        },
        tags=["Gestiones"],
    )
    def get(self, request, event_id):
        event = Event.objects.filter(pk=event_id, user=request.user).first()
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(SubtaskSerializer(event.subtasks.all(), many=True).data)

    @extend_schema(
        summary="Crear gestión logística (US-02)",
        description=(
            "Crea una gestión asociada a un evento del usuario.\n\n"
            "**Campos obligatorios:** `name`, `target_date`, `estimated_hours`.\n"
            "**Opcionales:** `time`, `provider`, `note`.\n\n"
            "**Reglas:**\n"
            "- El evento debe existir y ser del usuario → `404` si no.\n"
            "- `name` no puede estar vacío.\n"
            "- `estimated_hours` debe ser mayor a 0.\n"
            "- `target_date` **no puede ser posterior** a la fecha del evento → "
            "`400 target_date_after_event` (salvo `allow_subtasks_after_event=true`).\n"
            "- `target_date` **no puede ser anterior a hoy** → `400 target_date_in_past` "
            "(salvo `allow_overdue_subtasks=true`).\n"
            "- El `status` siempre arranca en `PENDIENTE`, ignorando lo que llegue en el body.\n"
            "- **Sobrecarga diaria (US-07):** si sumar `estimated_hours` al día "
            "`target_date` supera el límite del usuario y este no permite sobrecarga → "
            "`409 overload_conflict`. Si la permite, se crea y `data.conflict` trae el "
            "reporte como advertencia (`null` si no hay conflicto).\n\n"
            "**Usado por el FE:** vista `/evento/:id/gestiones/crear`. Tras un `201`, "
            "el FE muestra el modal `CreateSubtaskSuccessModal` y refresca el detalle."
        ),
        request=SubtaskSerializer,
        responses={
            201: OpenApiResponse(response=SubtaskSerializer, description="Gestión creada. Incluye `conflict` (reporte o `null`)."),
            400: OpenApiResponse(description="Validación fallida (nombre vacío, horas ≤ 0, fecha posterior al evento)."),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
            409: OpenApiResponse(description=CONFLICT_409_DESCRIPTION),
        },
        tags=["Gestiones"],
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

        # Fecha vencida o posterior al evento, según las preferencias del usuario.
        target_date = serializer.validated_data["target_date"]
        date_error = subtask_date_error(
            target_date, event_local_date(event.event_datetime), get_user_settings(request.user)
        )
        if date_error:
            return date_error

        # US-07: la gestión nueva suma horas a su día.
        conflicts, blocked = evaluate_new_subtasks(
            request.user, [(target_date, serializer.validated_data["estimated_hours"])]
        )
        if blocked:
            return overload_conflict_response(conflicts)

        # status siempre arranca en PENDIENTE, sin importar lo que llegue en el body
        subtask = serializer.save(event=event, status=Subtask.Status.PENDIENTE)
        data = SubtaskSerializer(subtask).data
        data["conflict"] = conflicts[0] if conflicts else None
        return success_response(data, "Gestión agregada.", status.HTTP_201_CREATED)


class SubtaskDetailView(APIView):
    """
    PATCH  /api/subtasks/:id   Editar subtarea (US-03).
    DELETE /api/subtasks/:id   Eliminar subtarea (US-03).
    """

    def _get_subtask(self, pk, user):
        return Subtask.objects.filter(pk=pk, event__user=user).first()

    @extend_schema(
        summary="Editar / reprogramar gestión (US-03, US-06, US-07, US-08)",
        description=(
            "Actualiza **parcialmente** una gestión. Solo se modifican los campos "
            "enviados en el body.\n\n"
            "**Uso típico:**\n"
            "- Marcar como ejecutada: `{ \"status\": \"EJECUTADA\" }` (US-09).\n"
            "- Posponer con nota: `{ \"status\": \"POSPUESTA\", \"note\": \"...\" }` (US-09).\n"
            "- Reprogramar: `{ \"target_date\": \"YYYY-MM-DD\" }` (US-06 / US-08 mover).\n"
            "- Cambiar horas: `{ \"estimated_hours\": 2.5 }` (US-08 reducir).\n\n"
            "**Validación de fecha (US-06):** si se envía `target_date` debe ser una fecha "
            "`YYYY-MM-DD` válida (vacía, `null` o con otro formato → `400` con "
            "`details.target_date = [\"Ingresa una fecha válida.\"]`). Si la fecha **cambia**:\n"
            "- No puede ser posterior a la fecha del evento → `400 target_date_after_event` "
            "(salvo `allow_subtasks_after_event=true`).\n"
            "- No puede ser anterior a hoy → `400 target_date_in_past` "
            "(salvo `allow_overdue_subtasks=true`).\n\n"
            "**Sobrecarga diaria (US-07/08):** si cambian `target_date`, `estimated_hours` "
            "o `status`, se recalcula la carga del día destino "
            "(SUM de `estimated_hours` del usuario ese día, sin las `EJECUTADA`).\n"
            "- Si el cambio **aumenta** la carga del día, el total supera el límite y el "
            "usuario tiene `allow_overload=false` → `409 overload_conflict`, no se guarda.\n"
            "- Reducir horas o sacar la gestión de un día **nunca se bloquea**; si el día "
            "sigue excedido se guarda y `data.conflict.message` dice "
            "\"Aún quedarías con Xh (límite Yh)\".\n"
            "- La respuesta `200` incluye `data.conflict` (reporte del día destino con "
            "`resolved: true/false` y los nuevos totales, o `null` si no aplica) y, si "
            "cambió la fecha, `data.previous_day` con los totales del día que se liberó.\n"
            "- `data.conflict_resolved` es `true` **solo** si el día de origen estaba "
            "excedido antes del cambio, dejó de estarlo y el día destino no quedó en "
            "conflicto. Úsalo para decidir si mostrar \"Conflicto resuelto\".\n\n"
            "**Usado por el FE:** botones Hecha, Posponer y Reprogramar en "
            "`/hoy` y `/evento/:id`; también el modal `EditSubtaskModal`."
        ),
        request=SubtaskSerializer,
        responses={
            200: OpenApiResponse(
                response=SubtaskSerializer,
                description=(
                    "Gestión actualizada. `message` es \"Fecha actualizada.\" si cambió "
                    "`target_date`, si no \"Cambios guardados.\". Incluye `conflict`, "
                    "`conflict_resolved` y, si cambió la fecha, `previous_day`."
                ),
            ),
            400: OpenApiResponse(description="Validación fallida (fecha inválida, vencida o posterior al evento, horas ≤ 0)."),
            404: OpenApiResponse(description="Gestión no encontrada o no pertenece al usuario."),
            409: OpenApiResponse(description=CONFLICT_409_DESCRIPTION),
        },
        tags=["Gestiones"],
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

        validated = serializer.validated_data
        old_date = subtask.target_date
        new_date = validated.get("target_date", subtask.target_date)
        new_hours = validated.get("estimated_hours", subtask.estimated_hours)
        new_status = validated.get("status", subtask.status)

        settings = get_user_settings(request.user)
        date_changed = new_date != old_date
        if date_changed:
            date_error = subtask_date_error(
                new_date, event_local_date(subtask.event.event_datetime), settings
            )
            if date_error:
                return date_error

        report = None
        load_changed = bool({"target_date", "estimated_hours", "status"} & validated.keys())
        if load_changed:
            report, blocked = evaluate_subtask_change(subtask, new_date, new_hours, new_status)
            if blocked:
                return overload_conflict_response([report])
            old_day_before = day_load(request.user, old_date)

        serializer.save()
        data = serializer.data
        data["conflict"] = report

        # "Conflicto resuelto" solo si el día de origen estaba excedido, dejó de
        # estarlo y el día destino no quedó en conflicto.
        conflict_resolved = False
        old_day_after = day_load(request.user, old_date)
        if load_changed:
            conflict_resolved = (
                old_day_before > settings.daily_limit_hours
                and old_day_after <= settings.daily_limit_hours
                and not (report and report["has_conflict"])
            )
        data["conflict_resolved"] = conflict_resolved

        if date_changed:
            data["previous_day"] = {
                "date": old_date.isoformat(),
                "planned_hours": float(old_day_after),
                "limit_hours": float(settings.daily_limit_hours),
                "has_conflict": old_day_after > settings.daily_limit_hours,
            }
        message = "Fecha actualizada." if date_changed else "Cambios guardados."
        return success_response(data, message)

    @extend_schema(
        summary="Eliminar gestión (US-03)",
        description=(
            "Elimina una gestión. La operación es permanente; no hay soft-delete.\n\n"
            "El frontend debe pedir confirmación antes de llamar este endpoint.\n\n"
            "**Usado por el FE:** modal `DeleteSubtaskModal` en `/evento/:id`. "
            "Tras confirmar, el FE muestra un toast de Gestión eliminada."
        ),
        responses={
            200: OpenApiResponse(description="Gestión eliminada."),
            404: OpenApiResponse(description="Gestión no encontrada o no pertenece al usuario."),
        },
        tags=["Gestiones"],
    )
    def delete(self, request, pk):
        subtask = self._get_subtask(pk, request.user)
        if subtask is None:
            return error_response("not_found", "Gestión no encontrada.", status_code=404)
        subtask.delete()
        return success_response(message="Gestión eliminada.")
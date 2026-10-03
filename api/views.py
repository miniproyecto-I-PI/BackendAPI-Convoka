from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone
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
from .models import Event, Subtask, UserSettings
from .serializers import EventSerializer, SubtaskSerializer
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
            "- Si se envían `subtasks`, cada una debe tener `target_date` **≤** "
            "`event_datetime.date()`. Si alguna es posterior → `400`.\n"
            "- Todo corre en una transacción atómica: si falla una subtask, no se crea nada.\n"
            "- El evento queda asociado al usuario del token.\n\n"
            "**Usado por el FE:** vista `/crear`. Tras un `201`, el FE navega al "
            "detalle del evento o a `/hoy` con un toast de confirmación."
        ),
        request=EventSerializer,
        responses={
            201: OpenApiResponse(response=EventSerializer, description="Evento creado con sus subtasks."),
            400: OpenApiResponse(description="Validación fallida (campos obligatorios, formato de fecha, subtask con fecha posterior al evento)."),
            401: OpenApiResponse(description="Sin token o token inválido."),
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
            "Si envías `event_datetime`, ten en cuenta que puedes dejar gestiones "
            "con `target_date` posterior. No se valida ese cruce al editar el evento.\n\n"
            "**Usado por el FE:** modal Editar ficha de evento en `/evento/:id`. "
            "Tras un `200`, el FE muestra el modal de éxito y refresca la vista."
        ),
        request=EventSerializer,
        responses={
            200: OpenApiResponse(response=EventSerializer, description="Evento actualizado."),
            400: OpenApiResponse(description="Validación fallida."),
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
            "**Orden dentro de cada grupo:** `target_date` ascendente; en caso de empate, "
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

        qs = qs.order_by("target_date", "estimated_hours", "id")

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
    """Lee o actualiza el límite diario de horas del usuario actual/demo."""

    @staticmethod
    def _settings(request):
        return UserSettings.objects.get_or_create(user=request.user)[0]

    @extend_schema(
        summary="Consultar límite diario de horas",
        description=(
            "Devuelve el límite diario de horas de gestión configurado por el usuario "
            "(por defecto **6** si no lo ha modificado).\n\n"
            "Este valor se usa en **US-07** para detectar sobrecarga cuando se "
            "reprograman gestiones.\n\n"
            "**Usado por el FE:** pantalla de configuración. El FE lo llama al "
            "montar la vista para mostrar el valor actual."
        ),
        responses={
            200: OpenApiResponse(description="Valor actual del límite diario."),
            401: OpenApiResponse(description="Sin token o token inválido."),
        },
        tags=["Configuración"],
    )
    def get(self, request):
        return success_response({"daily_limit_hours": self._settings(request).daily_limit_hours})

    @extend_schema(
        summary="Actualizar límite diario de horas",
        description=(
            "Actualiza el límite diario de horas de gestión del usuario.\n\n"
            "**Rango válido:** entre 1 y 16 horas, con un decimal como máximo.\n"
            "Valores fuera de rango → `400`.\n\n"
            "**Usado por el FE:** input numérico en la pantalla de configuración. "
            "Se llama al guardar y muestra un toast de confirmación."
        ),
        request=inline_serializer(
            name="DailyLimitRequest",
            fields={"daily_limit_hours": serializers.DecimalField(max_digits=4, decimal_places=1)},
        ),
        responses={
            200: OpenApiResponse(description="Límite actualizado."),
            400: OpenApiResponse(description="Valor no numérico, fuera de rango, o con demasiados decimales."),
        },
        tags=["Configuración"],
    )
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
            "- `target_date` **no puede ser posterior** a la fecha del evento. "
            "Si lo es → `400 target_date_after_event`.\n"
            "- El `status` siempre arranca en `PENDIENTE`, ignorando lo que llegue en el body.\n\n"
            "**Usado por el FE:** vista `/evento/:id/gestiones/crear`. Tras un `201`, "
            "el FE muestra el modal `CreateSubtaskSuccessModal` y refresca el detalle."
        ),
        request=SubtaskSerializer,
        responses={
            201: OpenApiResponse(response=SubtaskSerializer, description="Gestión creada."),
            400: OpenApiResponse(description="Validación fallida (nombre vacío, horas ≤ 0, fecha posterior al evento)."),
            404: OpenApiResponse(description="Evento no encontrado o no pertenece al usuario."),
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

        # La fecha objetivo no puede ser posterior a la fecha del evento.
        target_date = serializer.validated_data["target_date"]
        event_date = event.event_datetime.date()
        if target_date > event_date:
            return error_response(
                "target_date_after_event",
                "La fecha objetivo no puede ser posterior a la fecha del evento.",
                {"target_date": [f"Debe ser igual o anterior al {event_date.isoformat()}."]},
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
        summary="Editar gestión (US-03)",
        description=(
            "Actualiza **parcialmente** una gestión. Solo se modifican los campos "
            "enviados en el body.\n\n"
            "**Uso típico:**\n"
            "- Marcar como ejecutada: `{ \"status\": \"EJECUTADA\" }` (US-09).\n"
            "- Posponer con nota: `{ \"status\": \"POSPUESTA\", \"note\": \"...\" }` (US-09).\n"
            "- Reprogramar: `{ \"target_date\": \"YYYY-MM-DD\" }` (US-06).\n"
            "- Cambiar horas: `{ \"estimated_hours\": 2.5 }`.\n\n"
            "**Usado por el FE:** botones Hecha, Posponer y Reprogramar en "
            "`/hoy` y `/evento/:id`; también el modal `EditSubtaskModal`."
        ),
        request=SubtaskSerializer,
        responses={
            200: OpenApiResponse(response=SubtaskSerializer, description="Gestión actualizada."),
            400: OpenApiResponse(description="Validación fallida."),
            404: OpenApiResponse(description="Gestión no encontrada o no pertenece al usuario."),
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
        serializer.save()
        return success_response(serializer.data, "Cambios guardados.")

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
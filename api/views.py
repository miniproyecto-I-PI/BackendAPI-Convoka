from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .demo import get_demo_user
from .models import Event, Subtask
from .serializers import EventSerializer, SubtaskSerializer
from .utils import error_response, success_response, validation_details


class HealthCheckView(APIView):
    """
    Endpoint de salud del sistema para verificar el estado del servidor y la base de datos.
    """

    permission_classes: list = []

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
        events = Event.objects.filter(user=get_demo_user(request))
        return success_response(EventSerializer(events, many=True).data)

    @extend_schema(summary="Crear evento", request=EventSerializer, responses={201: EventSerializer})
    def post(self, request):
        serializer = EventSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "validation_error",
                "Revisa los campos del evento.",
                validation_details(serializer.errors),
                status.HTTP_400_BAD_REQUEST,
            )
        event = serializer.save(user=get_demo_user(request))
        return success_response(
            EventSerializer(event).data, "Evento creado.", status.HTTP_201_CREATED
        )


class EventDetailView(APIView):
    """
    GET    /api/events/:id
    PATCH  /api/events/:id   Editar evento (US-03).
    DELETE /api/events/:id   Eliminar evento; CASCADE elimina también sus subtareas.
    """

    def _get_event(self, pk):
        return Event.objects.filter(pk=pk).first()

    @extend_schema(summary="Detalle de evento", responses={200: EventSerializer})
    def get(self, request, pk):
        event = self._get_event(pk)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(EventSerializer(event).data)

    @extend_schema(summary="Editar evento", request=EventSerializer, responses={200: EventSerializer})
    def patch(self, request, pk):
        event = self._get_event(pk)
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
        event = self._get_event(pk)
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        event.delete()
        return success_response(message="Evento eliminado.")


class SubtaskListCreateView(APIView):
    """
    GET  /api/events/:id/subtasks
    POST /api/events/:id/subtasks   Crea subtarea logística (US-02).
    """

    @extend_schema(summary="Listar subtareas de un evento", responses={200: SubtaskSerializer(many=True)})
    def get(self, request, event_id):
        event = Event.objects.filter(pk=event_id).first()
        if event is None:
            return error_response("not_found", "Evento no encontrado.", status_code=404)
        return success_response(SubtaskSerializer(event.subtasks.all(), many=True).data)

    @extend_schema(
        summary="Crear subtarea logística", request=SubtaskSerializer, responses={201: SubtaskSerializer}
    )
    def post(self, request, event_id):
        event = Event.objects.filter(pk=event_id).first()
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

    def _get_subtask(self, pk):
        return Subtask.objects.filter(pk=pk).first()

    @extend_schema(summary="Editar subtarea", request=SubtaskSerializer, responses={200: SubtaskSerializer})
    def patch(self, request, pk):
        subtask = self._get_subtask(pk)
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
        subtask = self._get_subtask(pk)
        if subtask is None:
            return error_response("not_found", "Gestión no encontrada.", status_code=404)
        subtask.delete()
        return success_response(message="Gestión eliminada.")

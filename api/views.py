from django.db import connection
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, inline_serializer


class HealthCheckView(APIView):
    """
    Endpoint de salud del sistema para verificar el estado del servidor y la base de datos.
    """
    permission_classes = []

    @extend_schema(
        summary="Health Check del sistema",
        description="Verifica que el servicio de Django y la base de datos PostgreSQL estén activos y respondiendo.",
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
                {
                    "status": "unhealthy",
                    "database": "disconnected",
                    "error": str(e),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "status": "healthy",
                "database": "connected",
            },
            status=status.HTTP_200_OK,
        )

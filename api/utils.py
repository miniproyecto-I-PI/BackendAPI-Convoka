"""
Contrato de respuesta estándar (TS-03).

Éxito:  {"success": true,  "data": {...}, "message": "..."}
Error:  {"success": false, "error": {"code": "...", "message": "...", "details": {...}}}

No existía ningún estándar previo en el repo (no hay TS-03 implementado
todavía), así que este es el propuesto en el brief del proyecto.
"""

from rest_framework.response import Response


def success_response(data=None, message="", status_code=200):
    return Response({"success": True, "data": data, "message": message}, status=status_code)


def error_response(code, message, details=None, status_code=400):
    return Response(
        {
            "success": False,
            "error": {"code": code, "message": message, "details": details or {}},
        },
        status=status_code,
    )


def validation_details(errors):
    """Convierte serializer.errors (DRF) en un dict plano de mensajes por campo."""
    return {field: [str(m) for m in msgs] for field, msgs in errors.items()}

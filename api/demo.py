"""
Usuario demo para Sprint 1 / T1.

Mientras no exista autenticación real (US-11, Sprint 2), todo Event se
asocia a un único usuario "demo" fijo, creado la primera vez que se
necesita. `get_demo_user` ya recibe `request` en su firma aunque no lo
use todavía: cuando llegue el login real basta con cambiar el cuerpo de
la función (o las llamadas `get_demo_user(request)` en views.py) por
`request.user`, sin tocar el resto del código.
"""

from django.contrib.auth import get_user_model

DEMO_USERNAME = "demo"
DEMO_EMAIL = "demo@convoka.local"


def get_demo_user(request=None):
    """Devuelve (y crea si hace falta) el usuario demo fijo de Sprint 1."""
    User = get_user_model()
    user, _created = User.objects.get_or_create(
        username=DEMO_USERNAME,
        defaults={"email": DEMO_EMAIL},
    )
    return user

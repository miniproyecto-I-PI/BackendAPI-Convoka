"""
Detección de sobrecarga diaria (US-07 / US-08 / US-12).

Carga de un día = SUM(estimated_hours) de las gestiones del usuario con ese
`target_date`, excluyendo las EJECUTADA. Se compara contra el límite diario
del usuario (`UserSettings.daily_limit_hours`, 6h por defecto).

Regla de bloqueo: un cambio se rechaza solo si **aumenta** la carga de un día
y el total resultante supera el límite, salvo que el usuario tenga
`allow_overload=True`. Reducir horas o sacar una gestión de un día nunca se
bloquea, aunque el día siga excedido (así US-08 puede informar "Aún quedarías
con Xh (límite Yh)" después de guardar).
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Subtask, UserSettings

ZERO = Decimal("0")


def get_user_settings(user):
    return UserSettings.objects.get_or_create(user=user)[0]


def format_hours(value):
    """7.00 -> '7', 6.50 -> '6.5' (para los mensajes al usuario)."""
    normalized = Decimal(value).normalize()
    return f"{normalized:f}"


def day_load(user, target_date, exclude_subtask_id=None):
    """Horas planificadas del usuario en `target_date`, sin contar las EJECUTADA."""
    qs = Subtask.objects.filter(event__user=user, target_date=target_date).exclude(
        status=Subtask.Status.EJECUTADA
    )
    if exclude_subtask_id is not None:
        qs = qs.exclude(pk=exclude_subtask_id)
    return qs.aggregate(total=Sum("estimated_hours"))["total"] or ZERO


def conflict_message(planned_hours, limit_hours, still=False):
    prefix = "Aún quedarías" if still else "Quedarías"
    return (
        f"{prefix} con {format_hours(planned_hours)}h de gestión planificadas "
        f"(límite {format_hours(limit_hours)}h)"
    )


def build_report(target_date, current_hours, added_hours, settings):
    """
    Payload estándar del conflicto (contrato de US-07):
    planned_hours, limit_hours, exceeds_by, has_conflict (+ contexto extra).
    """
    planned = current_hours + added_hours
    limit = settings.daily_limit_hours
    exceeds_by = max(planned - limit, ZERO)
    has_conflict = planned > limit
    return {
        "date": target_date.isoformat(),
        "current_hours": float(current_hours),
        "added_hours": float(added_hours),
        "planned_hours": float(planned),
        "limit_hours": float(limit),
        "exceeds_by": float(exceeds_by),
        "has_conflict": has_conflict,
        "allow_overload": settings.allow_overload,
        "message": conflict_message(planned, limit) if has_conflict else "",
    }


def check_day(user, target_date, hours, exclude_subtask_id=None, settings=None):
    """Simula sumar `hours` a `target_date` (sin la gestión excluida, si se da)."""
    settings = settings or get_user_settings(user)
    current = day_load(user, target_date, exclude_subtask_id)
    return build_report(target_date, current, Decimal(hours), settings)


def counts(status):
    return status != Subtask.Status.EJECUTADA


def evaluate_subtask_change(subtask, new_date, new_hours, new_status):
    """
    Evalúa el estado final de una gestión que se va a editar.

    Devuelve (report, blocked):
    - report: conflicto del día destino con la gestión ya aplicada, o None si
      la gestión queda EJECUTADA (no suma horas).
    - blocked: True si el cambio aumenta la carga del día destino, lo deja por
      encima del límite y el usuario no permite sobrecarga.
    """
    if not counts(new_status):
        return None, False

    user = subtask.event.user
    settings = get_user_settings(user)
    report = check_day(user, new_date, new_hours, exclude_subtask_id=subtask.pk, settings=settings)

    old_contribution = (
        subtask.estimated_hours
        if subtask.target_date == new_date and counts(subtask.status)
        else ZERO
    )
    increases = Decimal(new_hours) > old_contribution
    if report["has_conflict"] and not increases:
        # Bajó la carga pero el día sigue excedido (US-08, "persiste").
        report["message"] = conflict_message(
            Decimal(str(report["planned_hours"])), settings.daily_limit_hours, still=True
        )
    blocked = report["has_conflict"] and increases and not settings.allow_overload
    report["resolved"] = not report["has_conflict"]
    return report, blocked


def evaluate_new_subtasks(user, items):
    """
    Evalúa varias gestiones nuevas a la vez (p. ej. las iniciales de POST /events),
    acumulando las que caen el mismo día. `items` = [(target_date, hours), ...].
    Devuelve (reports_con_conflicto, blocked).
    """
    settings = get_user_settings(user)
    added = {}
    for target_date, hours in items:
        added[target_date] = added.get(target_date, ZERO) + Decimal(hours)

    reports = []
    for target_date, hours in sorted(added.items()):
        report = build_report(target_date, day_load(user, target_date), hours, settings)
        if report["has_conflict"]:
            reports.append(report)
    blocked = bool(reports) and not settings.allow_overload
    return reports, blocked


def suggest_days(user, hours, start=None, end=None, exclude_subtask_id=None, limit=3):
    """
    Próximos días (desde `start`, por defecto hoy) donde `hours` cabe sin
    superar el límite. Se busca hasta `end` (p. ej. la fecha del evento) o 30 días.
    """
    settings = get_user_settings(user)
    start = start or timezone.localdate()
    end = end or start + timedelta(days=30)
    hours = Decimal(hours)

    # Una sola query con la carga de todo el rango.
    qs = Subtask.objects.filter(
        event__user=user, target_date__gte=start, target_date__lte=end
    ).exclude(status=Subtask.Status.EJECUTADA)
    if exclude_subtask_id is not None:
        qs = qs.exclude(pk=exclude_subtask_id)
    loads = {
        row["target_date"]: row["total"]
        for row in qs.values("target_date").annotate(total=Sum("estimated_hours"))
    }

    suggestions = []
    day = start
    while day <= end and len(suggestions) < limit:
        current = loads.get(day, ZERO)
        if current + hours <= settings.daily_limit_hours:
            suggestions.append(
                {
                    "date": day.isoformat(),
                    "current_hours": float(current),
                    "planned_hours": float(current + hours),
                    "available_hours": float(settings.daily_limit_hours - current),
                    "limit_hours": float(settings.daily_limit_hours),
                }
            )
        day += timedelta(days=1)
    return suggestions

from django.urls import path

from .views import (
    EventDetailView,
    EventListCreateView,
    HealthCheckView,
    SubtaskDetailView,
    SubtaskListCreateView,
    TodayView,
    DailyLimitView,
)

# Nota: sin "/" final a propósito, para calzar con el contrato del backlog
# ("POST /events", no "POST /events/") y con services/api.js, que ya llama
# a `${API_URL}/events` sin slash final.
urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("events", EventListCreateView.as_view(), name="event-list-create"),
    path("today", TodayView.as_view(), name="today"),
    path("settings/daily-limit", DailyLimitView.as_view(), name="daily-limit"),
    path("events/<int:pk>", EventDetailView.as_view(), name="event-detail"),
    path(
        "events/<int:event_id>/subtasks",
        SubtaskListCreateView.as_view(),
        name="subtask-list-create",
    ),
    path("subtasks/<int:pk>", SubtaskDetailView.as_view(), name="subtask-detail"),
]

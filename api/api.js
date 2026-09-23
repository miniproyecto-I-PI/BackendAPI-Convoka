/**
 * services/api.js
 * ---------------------------------------------------------------------------
 * SINGLE INTEGRATION POINT with the backend.
 *
 * Real base URL is read from an environment variable so each environment
 * (local, staging, prod) can point to a different API without code changes.
 * See `.env.example` — VITE_API_URL debe incluir el prefijo /api, ej.
 * VITE_API_URL=http://localhost:8000/api
 */

import { mockGestiones } from "../data/mockGestiones";

export const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

/** Simulates realistic network latency for the mock responses below. */
function delay(ms = 400) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Llama al backend real y desempaqueta el contrato estándar TS-03:
 *   éxito -> { success: true,  data, message }   -> devuelve `data`
 *   error -> { success: false, error: { code, message, details } } -> lanza Error
 *
 * `error.details` (errores por campo) y `error.status` (código HTTP)
 * quedan disponibles en el Error lanzado para que el formulario los use.
 */
async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    // respuesta sin body (ej. 204) — se maneja abajo con response.ok
  }

  if (!response.ok || !body?.success) {
    const message = body?.error?.message || "Ocurrió un error inesperado. Intenta de nuevo.";
    const error = new Error(message);
    error.status = response.status;
    error.details = body?.error?.details || {};
    throw error;
  }

  return body.data;
}

/**
 * GET /today  (US-04)
 * Returns the raw list of active/relevant gestiones. Grouping and ordering
 * is applied on the frontend by `utils/sortGestiones.js` (see that file for
 * why). Throws to simulate the error state when `simulateError` is true —
 * used by the QA simulation toolbar (components/dev/SimulationToolbar.jsx)
 * to demo the error state without needing a real backend outage.
 *
 * @param {{ simulateError?: boolean }} [opts]
 * @returns {Promise<import('../utils/sortGestiones').Gestion[]>}
 */
export async function getToday({ simulateError = false } = {}) {
  await delay();
  if (simulateError) {
    throw new Error("No pudimos cargar tus gestiones");
  }
  // TODO(backend, Sprint 2/US-04): reemplazar por apiRequest("/today")
  return structuredClone(mockGestiones);
}

/**
 * PATCH /subtasks/:id  (US-09, escenario "Marcar tarea ejecutada")
 * @param {string} id
 * @returns {Promise<{ id: string, status: 'EJECUTADA', doneAt: string }>}
 */
export async function markGestionAsDone(id) {
  await delay(250);
  // TODO(backend, Sprint 4/US-09): reemplazar por
  //   apiRequest(`/subtasks/${id}`, { method: "PATCH", body: JSON.stringify({ status: "EJECUTADA" }) })
  return { id, status: "EJECUTADA", doneAt: new Date().toISOString() };
}

/**
 * PATCH /subtasks/:id  (US-09, escenario "Posponer con nota explicativa")
 * @param {string} id
 * @param {string} [note]
 */
export async function postponeGestion(id, note = "") {
  await delay(250);
  // TODO(backend, Sprint 4/US-09): PATCH { status: 'POSPUESTA', note }
  return { id, status: "POSPUESTA", note };
}

/**
 * PATCH /subtasks/:id  (US-06, "Reprogramar subtarea logística / gestión")
 * @param {string} id
 * @param {string} newTargetDateISO
 */
export async function rescheduleGestion(id, newTargetDateISO) {
  await delay(300);
  // TODO(backend, Sprint 3/US-06): PATCH { target_date: newTargetDateISO }
  // TODO(backend, US-07): antes de confirmar en el flujo real, llamar primero
  // al endpoint de conflicto (POST /conflicts/overload) y dejar que el
  // usuario lo resuelva (US-08) si `has_conflict` viene en true.
  return { id, targetDate: newTargetDateISO };
}

// ---------------------------------------------------------------------------
// Eventos (US-01, US-03) — YA CONECTADOS AL BACKEND REAL
// ---------------------------------------------------------------------------

/**
 * POST /events  (US-01, "Crear evento")
 * @param {{ name: string, type: string, client_contact?: string, event_datetime: string, place?: string }} payload
 * @returns {Promise<object>} el evento creado
 */
export async function createEvent(payload) {
  return apiRequest("/events", { method: "POST", body: JSON.stringify(payload) });
}

/** GET /events — lista los eventos del usuario demo. */
export async function listEvents() {
  return apiRequest("/events");
}

/** GET /events/:id — detalle de un evento (incluye sus subtareas). */
export async function getEvent(id) {
  return apiRequest(`/events/${id}`);
}

/** PATCH /events/:id — editar evento (US-03). */
export async function updateEvent(id, payload) {
  return apiRequest(`/events/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

/** DELETE /events/:id — eliminar evento; el backend borra en cascada sus subtareas (US-03). */
export async function deleteEvent(id) {
  return apiRequest(`/events/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Subtareas logísticas (US-02, US-03) — YA CONECTADAS AL BACKEND REAL
// ---------------------------------------------------------------------------

/** GET /events/:id/subtasks — lista las subtareas de un evento. */
export async function listSubtasks(eventId) {
  return apiRequest(`/events/${eventId}/subtasks`);
}

/**
 * POST /events/:id/subtasks  (US-02, "Crear plan inicial")
 * @param {string|number} eventId
 * @param {{ name: string, target_date: string, estimated_hours: number }} payload
 */
export async function createSubtask(eventId, payload) {
  return apiRequest(`/events/${eventId}/subtasks`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** PATCH /subtasks/:id — editar subtarea (US-03). */
export async function updateSubtask(id, payload) {
  return apiRequest(`/subtasks/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

/** DELETE /subtasks/:id — eliminar subtarea (US-03). */
export async function deleteSubtask(id) {
  return apiRequest(`/subtasks/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------

/**
 * GET /settings/daily-limit and PUT /settings/daily-limit  (US-12)
 * Grouped in one object because they always change together in the UI
 * (the settings modal reads the current value, then writes a new one).
 */
export const dailyLimitApi = {
  /** @returns {Promise<{ dailyLimitHours: number }>} */
  async get() {
    await delay(200);
    // TODO(backend, Sprint 3/US-12): GET /settings/daily-limit — default 6h.
    return { dailyLimitHours: 6 };
  },
  /**
   * @param {number} hours - must be validated client-side to the 1–16 range
   * before calling this (see components/common/DailyLimitModal.jsx).
   */
  async update(hours) {
    await delay(300);
    // TODO(backend, Sprint 3/US-12): PUT /settings/daily-limit { daily_limit_hours: hours }
    return { dailyLimitHours: hours };
  },
};

/**
 * POST /auth/login  (US-11) — from Sprint 2 onward.
 * @param {{ email: string, password: string }} credentials
 */
export async function login(_credentials) {
  await delay(400);
  // TODO(backend, Sprint 2): call Supabase Auth / DRF token endpoint using
  // the (currently unused) `_credentials` argument: { email, password }.
  throw new Error("Login aún no disponible — se implementa desde el Sprint 2 (US-11).");
}

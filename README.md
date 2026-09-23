# Convoka — Backend API (Sprint 1 / T1)

App de gestión de eventos y su plan logístico. Este documento cubre lo
necesario para levantar el backend, correr los tests y conectarlo con el
frontend React. Estado actual: **US-01, US-02 y US-03 implementadas**
(crear/editar/eliminar eventos y subtareas), con usuario demo fijo — sin
autenticación real todavía (llega en Sprint 2, US-11).

## Requisitos

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) para dependencias y entorno virtual
- Node.js 18+ (para el frontend)
- Una base de datos PostgreSQL (Supabase) accesible, o SQLite local para
  desarrollo rápido (fallback automático si no defines `DATABASE_URL`)

## Instalación (PowerShell, Windows)

```powershell
cd BackendAPI-Convoka
uv sync
```

Esto crea el entorno virtual e instala todas las dependencias declaradas en
`pyproject.toml` (Django, DRF, drf-spectacular, corsheaders, psycopg, etc.).

## Variables de entorno

Copia `.env.example` a `.env` en la raíz del backend y completa:

```
DATABASE_URL=postgresql://usuario:password@host:puerto/nombre_bd?sslmode=require
```

- `DATABASE_URL` es el **connection string de Supabase** (Project Settings →
  Database → Connection string, modo "URI"). Si no está definida, el
  proyecto cae automáticamente a un `db.sqlite3` local — útil para probar
  rápido, pero para evidencia de persistencia real usa Supabase.
- CORS y CSRF **ya están configurados** en `config/settings.py` (no
  necesitas variable de entorno para esto en T1): permiten
  `http://localhost:3000`, `http://localhost:5173` y el dominio de Vercel
  `https://front-end-api-sigma.vercel.app`. Si el frontend cambia de
  dominio, añade el nuevo origen a `CORS_ALLOWED_ORIGINS` y
  `CSRF_TRUSTED_ORIGINS` en ese archivo.

### Un cambio pendiente en `config/settings.py`

Cambia `TIME_ZONE = "UTC"` por:

```python
TIME_ZONE = "America/Bogota"
```

Para T1 no se usa todavía `/today` (US-04), pero fijar la zona horaria ahora
evita que las fechas de subtareas se interpreten distinto más adelante.

## Migraciones

Los modelos `Event` y `Subtask` son nuevos en este sprint. Genera y aplica
las migraciones (no vienen incluidas — Django las genera a partir de
`api/models.py`, así que ejecútalo tú para que queden exactas a tu entorno):

```powershell
uv run python manage.py makemigrations api
uv run python manage.py migrate
```

Verifica que quedaron aplicadas:

```powershell
uv run python manage.py showmigrations api
```

## Ejecutar el backend

```powershell
uv run python manage.py runserver
```

Health check: `GET http://localhost:8000/api/health/` debe devolver
`{"status": "healthy", "database": "connected"}`.

## Endpoints (Sprint 1 / T1)

Prefijo base: `/api`. Todas las respuestas siguen el contrato estándar
**TS-03**:

```jsonc
// éxito
{ "success": true, "data": { /* ... */ }, "message": "Evento creado." }

// error (400/404)
{
  "success": false,
  "error": {
    "code": "validation_error",
    "message": "Revisa los campos del evento.",
    "details": { "name": ["El nombre del evento es obligatorio."] }
  }
}
```

| Método | Ruta                         | Descripción                                   |
| ------ | ---------------------------- | ---------------------------------------------- |
| GET    | `/events`                    | Lista eventos del usuario demo                 |
| POST   | `/events`                    | Crea evento (US-01)                            |
| GET    | `/events/:id`                | Detalle de evento (incluye sus subtareas)      |
| PATCH  | `/events/:id`                | Edita evento (US-03)                           |
| DELETE | `/events/:id`                | Elimina evento — CASCADE elimina sus subtareas |
| GET    | `/events/:id/subtasks`       | Lista subtareas del evento                     |
| POST   | `/events/:id/subtasks`       | Crea subtarea logística (US-02)                |
| PATCH  | `/subtasks/:id`               | Edita subtarea (US-03)                         |
| DELETE | `/subtasks/:id`               | Elimina subtarea (US-03)                       |

**Campos de `Event`** — obligatorios: `name`, `type`
(`BODA|SOCIAL|CORPORATIVO|CUMPLEANOS|OTRO`), `event_datetime` (ISO 8601).
Opcionales: `client_contact`, `place`.

**Campos de `Subtask`** — obligatorios: `name`, `target_date` (fecha ISO),
`estimated_hours` (decimal > 0). `status` se fuerza a `PENDIENTE` al crear
sin importar lo que se envíe; se puede cambiar después vía `PATCH`.

**Reglas de integridad:** eliminar un evento elimina en cascada (`CASCADE`)
todas sus subtareas — es la estrategia acordada para T1.

**Usuario demo:** todo evento se asocia a un usuario fijo `demo`, creado
automáticamente la primera vez que se usa (`api/demo.py`). En Sprint 2
(US-11) se reemplaza por `request.user`.

## Documentación OpenAPI / Swagger

Con el servidor corriendo:

- Swagger UI: `http://localhost:8000/api/docs/`
- Redoc: `http://localhost:8000/api/redoc/`
- Schema crudo: `http://localhost:8000/api/schema/`

## Tests

```powershell
uv run python manage.py test api
```

Cubre US-01 (creación válida/inválida, campos obligatorios, persistencia),
US-02 (creación válida, nombre vacío, `estimated_hours` en 0 y negativo,
`event_id` inexistente, asociación correcta al evento) y US-03 (editar y
eliminar evento/subtarea, verificación de CASCADE).

## Conexión con el frontend (React + Vite)

En el frontend, `.env` (o `.env.local`) debe tener:

```
VITE_API_URL=http://localhost:8000/api
```

y, una vez desplegado el backend, la URL pública equivalente (ej. en las
variables de entorno del proyecto en Vercel). `services/api.js` ya lee esta
variable — no hay URLs quemadas en el código, y el resto de la app siempre
importa las funciones de `services/api.js`, nunca hace `fetch` directo.

### Formularios del frontend (aún no implementados) — cómo deben quedar configurados

Todavía no existen los componentes de formulario, así que aquí queda la
configuración esperada para que, cuando se creen, se integren directo con
el backend sin sorpresas.

> **Nota sobre rutas:** el backlog oficial (US-01/02/03) usa `/crear` y
> `/evento/:id`. La tabla de rutas SPA que manejan usa en cambio
> `/actividad/:id` y campos como `course`/`due_date`, que no calzan con el
> modelo `Event` de este sprint (evento con cliente/contacto y lugar, no una
> actividad académica con curso). Asumí la nomenclatura del backlog oficial
> (`/evento/:id`) para esta guía — si el equipo ya estandarizó
> `/actividad/:id` en otro documento, es solo cuestión de renombrar la ruta;
> el contrato del API (`/api/events/...`) no cambia.

**`/crear` — formulario "Crear evento" (US-01)**

| Campo del formulario | Atributo del payload | Obligatorio | Validación en cliente |
| --- | --- | --- | --- |
| Nombre del evento | `name` | Sí | No vacío |
| Tipo | `type` | Sí | Un valor de `BODA \| SOCIAL \| CORPORATIVO \| CUMPLEANOS \| OTRO` (select) |
| Fecha y hora | `event_datetime` | Sí | Fecha válida, formato ISO 8601 al enviar |
| Cliente/contacto | `client_contact` | No | — |
| Lugar/plazo | `place` | No | — |

Al enviar: `await createEvent(payload)` desde `services/api.js`. Si la
promesa se rechaza, `error.details` trae los mensajes por campo (ej.
`{ name: ["El nombre del evento es obligatorio."] }`) para mostrarlos junto
a cada input; `error.message` es el mensaje general. Al resolver
correctamente, navegar a `/evento/:id` con el `id` del evento devuelto.

**`/evento/:id` — bloque "Subtareas logísticas" (US-02)**

| Campo del formulario | Atributo del payload | Obligatorio | Validación en cliente |
| --- | --- | --- | --- |
| Nombre de la gestión | `name` | Sí | No vacío (placeholders sugeridos: "Reservar salón", "Enviar invitaciones", "Confirmar catering") |
| Fecha objetivo | `target_date` | Sí | Fecha válida |
| Horas estimadas | `estimated_hours` | Sí | Numérico, > 0, acepta decimales simples (ej. `2.5`) |

Al enviar: `await createSubtask(eventId, payload)`. Tras éxito, refrescar la
lista con `listSubtasks(eventId)` (o anexar el resultado directamente, que
ya viene con `status: "PENDIENTE"`).

**Edición/eliminación (US-03)** — reutilizar el mismo formulario de
creación en modo edición, precargado con los datos actuales, llamando a
`updateEvent(id, payload)` / `updateSubtask(id, payload)` con solo los
campos que cambiaron (son `PATCH`, no requieren el objeto completo).
Eliminar siempre detrás de un modal de confirmación, llamando a
`deleteEvent(id)` / `deleteSubtask(id)`.

**Accesibilidad mínima (TS-06):** cada input con su `<label htmlFor>`
asociado, foco visible, mensaje de error asociado vía `aria-describedby`, y
el modal de confirmación con foco atrapado + cierre por `Escape`.

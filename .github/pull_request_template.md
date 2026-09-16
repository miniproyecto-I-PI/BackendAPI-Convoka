## 📋 Descripción
<!-- Describe brevemente qué hace este PR, la funcionalidad que añade o el problema que resuelve. -->
<!-- Incluye referencias a Historias de Usuario (HU), tareas o Criterios de Aceptación si aplica. -->


## 🛠️ Tipo de PR (PR Type)
- [ ] 🚀 **Feature** (Nueva funcionalidad)
- [ ] 🐛 **Fix** (Corrección de error)
- [ ] 🧪 **Test** (Pruebas añadidas o actualizadas)
- [ ] ♻️ **Refactor** (Reestructuración de código sin alterar comportamiento)
- [ ] 📝 **Docs** (Documentación / Swagger)
- [ ] 🔧 **Chore** (Configuración, CI, dependencias, tooling)


## 🔍 Cambios realizados
<!-- Detalla los archivos o módulos modificados según las capas o estructura del proyecto -->

### Modelos / Base de Datos
- `api/models.py` — <!-- Descripción de nuevos modelos, campos o migraciones -->

### Serializadores / DTOs
- `api/serializers.py` — <!-- Serializadores creados o modificados, validaciones de entrada/salida -->

### Vistas / Endpoints (API)
- `api/views.py` — <!-- Nuevas vistas, ViewSets, validaciones de negocio, permisos -->
- `api/urls.py` — <!-- Rutas expuestas -->

### Configuración / Otros
- <!-- Cambios en settings, dependencias en pyproject.toml, CI, etc. -->


## 🧪 Calidad y Validaciones
<!-- Marca las validaciones ejecutadas antes de abrir el PR -->
- [ ] `uv run ruff check .` (Linter sin errores)
- [ ] `uv run ruff format --check .` (Formato de código verificado)
- [ ] `uv run mypy api config` (Type checking pasando sin errores)
- [ ] `uv run python manage.py check` (Validaciones del sistema Django OK)
- [ ] `uv run python manage.py spectacular --validate` (Esquema Swagger válido)
- [ ] Tests unitarios / de integración (si aplica en esta etapa)


## 💡 Decisiones técnicas
<!-- Justifica decisiones de diseño, patrones adoptados, alternativas descartadas o manejo de casos borde -->
- 


## 🚀 Cómo probar
<!-- Proporciona pasos claros para que el revisor reproduzca y pruebe los cambios localmente -->

```bash
# 1. Ejemplo de comando o petición curl
curl -X GET http://localhost:8000/api/health/
```

### Pruebas desde Swagger UI:
- [ ] Probado interactivamente en `http://localhost:8000/api/docs/`

<!-- Adjunta aquí capturas de pantalla de Swagger, respuestas JSON o evidencias si aplica -->

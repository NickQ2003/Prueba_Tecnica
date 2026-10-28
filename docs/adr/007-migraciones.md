# ADR-007: migraciones secuenciales

- **Estado:** aceptada.
- **Contexto:** los evaluadores necesitan construir una base vacía y reproducir su esquema.
- **Decisión:** Alembic es autoridad con IDs numéricos `001`, `002`, cadena lineal y helper que asigna el siguiente ID.
- **Alternativa descartada:** `create_all`, scripts SQL aplicados al ordenar nombres o editar migraciones aplicadas.
- **Consecuencia:** cada cambio añade archivo revisado; el helper valida el checkout local, pero no coordina ramas/repositorios separados.

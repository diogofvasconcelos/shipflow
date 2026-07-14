# frontend/

Server-rendered UI — there is no separate SPA. The backend renders these
Jinja2 templates and HTMX drives interactivity over HTML fragments (see
docs/ARCHITECTURE.md §2). This folder holds:

- `templates/` — Jinja2 pages and partials (pt-BR operator screens).
  The backend locates them via the `TEMPLATES_DIR` setting
  (default `../frontend/templates`, resolved from `backend/`).
- `static/` — CSS/JS assets, added when the first screen needs them.

Screens are built task by task (T1, T8, T9, T13… in docs/ORCHESTRATION.md).

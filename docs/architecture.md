# Архитектура

Каноническое описание v2:

- [C4 System Context, Container и логические компоненты](architecture/overview.md)
- [Интеграционная архитектура и outbox](architecture/integration.md)
- [Реестр интеграционных рисков](architecture/integration-risks.md)
- [Архитектурные решения](architecture/decisions.md)
- [Физическая модель SQLite](data/physical-model.md)
- [Внутренний HTTP API](api/internal-api.md)

Ранние упоминания `Approval` и `SyncJob` удалены: таких таблиц или runtime-компонентов нет. Решение представлено состоянием `requests` и `history`, а техническая работа — записью `integration_events`.

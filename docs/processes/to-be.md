# Процесс TO-BE

![BPMN 2.0 TO-BE](../diagrams/rendered/to-be.svg)

[Исходник BPMN 2.0 XML](../diagrams/src/to-be.bpmn)

## Collaboration

- pool «LeaveOps / организация» с lanes сотрудника, приложения и руководителя;
- отдельный pool «Ручная синхронизация / Integration Worker» с lanes оператора и внутреннего worker;
- внешний pool «Bitrix24 Calendar».

Технический процесс выделен в отдельный participant, чтобы показать независимое выполнение. В текущем codebase это не отдельный deployable-сервис: руководитель запускает обработку из демо-UI, а администратор — командой CLI. Возможный production scheduler отмечен только как не реализованный вариант.

## Бизнес-поток

1. Сотрудник создаёт draft и отправляет заявку.
2. LeaveOps валидирует даты/права и рассчитывает preview.
3. Руководитель изучает контекст и выбирает approve/reject.
4. При approve LeaveOps повторяет правила внутри `BEGIN IMMEDIATE`.
5. Статус, history и outbox записываются атомарно, затем выполняется commit.
6. Сотрудник получает бизнес-результат. Успех Calendar API для этого не требуется.

При конфликте заявка остаётся `submitted`; при отказе сохраняются `rejected` и причина без интеграционного события. Commit не отправляет message flow worker: запись outbox показана как persisted data store, а бизнес-процесс завершается самостоятельно.

## Технический поток

1. Руководитель в текущем demo UI либо администратор через CLI явно запускает синхронизацию.
2. Внутренний worker читает доступные записи из persisted outbox.
3. Worker выполняет claim с token и lease.
4. Адаптер вызывает Bitrix24 через message flow между pools.
5. Успех сохраняет `delivered` и `external_id`.
6. Ошибка сохраняет `pending` и `last_error`; следующий ручной запуск не меняет бизнес-статус.

Разделение подтверждается [SQ-02](../diagrams/rendered/sq-02-transactional-outbox.svg) и ADR-001/ADR-002.

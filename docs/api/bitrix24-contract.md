# Контракт Bitrix24 Calendar

## Назначение границы

Bitrix24 хранит календарную проекцию согласованного отсутствия. Он не хранит решение, историю, баланс или правила покрытия и не определяет `requests.status`.

## Методы

| LeaveOps command | Bitrix24 methods | Условие | Результат |
| --- | --- | --- | --- |
| Probe | `profile`, `scope`, `methods` | До настройки | ID webhook-пользователя, scope и доступность методов |
| Ensure section | `calendar.section.get`, `calendar.section.add` | Для каждого mapped owner | Личный `section_id` |
| `upsert` | `calendar.event.get`, при отсутствии `calendar.event.add` | Согласование | Новый или найденный event ID |
| `update` | `calendar.event.update` | Согласованный перенос после доставленного create | Тот же event ID |
| `cancel` | `calendar.event.get`, при наличии `calendar.event.delete` | Отмена approved-заявки | Удалённый ID или идемпотентный absent marker |

## Отображение полей

| LeaveOps | Bitrix24 |
| --- | --- |
| `request_id` | `[LeaveOps:<request_id>]` в `description` |
| `employee_id` | Ключ выбора `owner_id`/`section_id`; часть `name` |
| `start_date`, `end_date` | `from`, `to` |
| Целый день | `skip_time = Y` |
| Состояние доступности | `accessibility = absent` |
| `external_id` | ID события из add или поиска |

Дополнительные фиксированные поля: `importance=normal`, `private_event=N`, `is_meeting=N`. Даты включительны в LeaveOps; визуальная интерпретация конечной границы в UI Bitrix24 остаётся ограничением живого доказательства.

## Идемпотентность и ошибки

Перед `add` адаптер ищет служебную метку в выделенном разделе за период заявки. Найденный объект возвращается без повторного создания. `update` требует числовой ID последнего доставленного create/update. `cancel` считается успешным, если цель уже отсутствует.

HTTP, transport и Bitrix error превращаются в `Bitrix24Error`. Worker записывает текст в `last_error`, возвращает событие в `pending` и не меняет заявку.

## Карта

`B24_MAPPING_FILE` содержит `schema_version=1` и объект `employees`. Для каждой записи обязательны уникальный числовой `owner_id` и, для sync, числовой `section_id`. Webhook и карта остаются runtime-конфигурацией и не коммитятся.

## Проверенная граница

На собственном тестовом портале проверены scope `calendar`, шесть методов адаптера, создание/поиск/обновление/удаление и маршрутизация по трём владельцам. Полная карта шести синтетических сотрудников, уведомления, attendee semantics, rate limits и длительные отказы не проверены.

Подробная фиксация живых smoke-проверок сохранена в [отчёте о проверке](../verification.md). Официальные методы: [add](https://apidocs.bitrix24.ru/api-reference/calendar/calendar-event/calendar-event-add.html), [update](https://apidocs.bitrix24.ru/api-reference/calendar/calendar-event/calendar-event-update.html), [get](https://apidocs.bitrix24.ru/api-reference/calendar/calendar-event/calendar-event-get.html), [delete](https://apidocs.bitrix24.ru/api-reference/calendar/calendar-event/calendar-event-delete.html), [section.get](https://apidocs.bitrix24.ru/api-reference/calendar/calendar-section-get.html).

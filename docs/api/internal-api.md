# Внутренний HTTP API

Документ выведен из `leaveops/web.py`. API предназначен для loopback-демо и не является публичным production-контрактом.

## Общие условия

- Base URL: `http://127.0.0.1:<port>`.
- Закрытые операции используют `X-Demo-Actor`; это демонстрационная идентичность.
- Каждый POST требует точный `Content-Type: application/json`, `X-LeaveOps: local-demo` и JSON object размером 1–8192 bytes.
- Допустимый `Host`: `127.0.0.1:<port>` или `localhost:<port>`.
- `Origin` может отсутствовать либо совпадать с одним из локальных адресов.

## GET

| Path | Назначение | Доступ | Параметры | Успешный ответ | Связь |
| --- | --- | --- | --- | --- | --- |
| `/api/actors` | Список активных синтетических акторов | Без actor header | — | `200`, массив `{id,name}` | Demo transport |
| `/api/config` | Имя выбранной интеграции | Без actor header | — | `200 {integration_name}` | `UC-06` |
| `/api/calendar` | Календарь и покрытие своего отдела | Активный сотрудник | `start`, `end`; ISO, максимум 31 день | `200 {employees,absences,coverage,manager_id,start,end}` | `FR-01`, `UC-05` |
| `/api/balance` | Баланс сотрудника на дату | Сам сотрудник или руководитель отдела | `id`, `as_of` | `200` с параметрами и расчётом баланса | `FR-07`, `UC-05` |
| `/api/hr-summary` | Сводная по отделу | Только руководитель | `as_of` | `200 {employees,pending_requests,low_balance_employee_ids,upcoming_absences}` | `FR-08`, `UC-05` |
| `/api/requests` | Доступные заявки | Автор и/или руководитель отдела | — | `200`, массив заявок | `FR-02`, `FR-04` |
| `/api/request` | Деталь, history, integration и preview | Автор или руководитель отдела | `id` | `200` с `conflicts` для draft/submitted | `FR-03`, `FR-06` |

Статические GET доступны только для `/`, `/app.js`, `/style.css`; другие пути возвращают `404`.

## POST

| Path | Назначение | Актор | JSON body | Успешный ответ | Связь |
| --- | --- | --- | --- | --- | --- |
| `/api/requests` | Создать draft | Активный сотрудник | `{start,end}` | `201`, полная заявка | `FR-02`, `UC-01` |
| `/api/transition` | Submit / approve / reject / cancel | Владелец или руководитель по target | `{id,target,reason?}` | `200`, полная заявка | `FR-02`, `FR-04`, `FR-05`; `UC-01/02/04` |
| `/api/reschedule` | Предложить новые даты | Автор approved-заявки | `{id,start,end,reason?}` | `200`, заявка с proposal | `FR-09`, `UC-03` |
| `/api/reschedule/decision` | Согласовать/отклонить перенос | Руководитель отдела | `{id,target,reason?}` | `200`, заявка с результатом | `FR-09`, `UC-03` |
| `/api/sync` | Обработать outbox до ошибки или опустошения | Только руководитель демо | `{}` | `200`, массив результатов worker | `FR-11`, `UC-06` |

`target` для обычного перехода ограничен `submitted`, `approved`, `rejected`, `cancelled`; для решения по переносу — `approved` или `rejected`.

## Ошибки

| HTTP | Условие | Тело |
| --- | --- | --- |
| `400` | Невалидные даты, причина, размер/JSON/body; любой `WorkflowError`, кроме перечисленных ниже | `{error?,message,conflicts?}` |
| `403` | `forbidden`, неверный Host/Origin или POST headers | `{error?,message,conflicts?}` |
| `404` | `not_found` или неизвестный route | `{error?,message,conflicts?}` |
| `409` | `conflict` или `invalid_transition` | `{error,message,conflicts}` |
| `503` | `sqlite3.OperationalError` | `{message}` |

Транспорт не раскрывает traceback. CSP, `nosniff`, `no-store` и отсутствие CORS уменьшают риск локального демо, но не заменяют аутентификацию.

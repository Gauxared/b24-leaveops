# Нефункциональные требования

| ID | Проверяемое требование | Приоритет | Способ проверки |
| --- | --- | --- | --- |
| `NFR-01` | Проверки, бизнес-изменение, история и outbox фиксируются одной транзакцией SQLite; ошибка не оставляет частичный результат | Must | `test_approval_enqueues_event_in_same_transaction`, `test_blocked_approval_does_not_enqueue` |
| `NFR-02` | Повтор после потерянного ответа использует существующий календарный объект и тот же `external_id` | Must | `test_real_adapter_contract_recovers_lost_add_response`, `test_update_retries_after_lost_response_without_new_external_absence` |
| `NFR-03` | Одно outbox-событие одновременно принадлежит одному worker; просроченный lease допускает восстановление | Must | `test_concurrent_workers_claim_once`, `test_expired_lease_is_recovered` |
| `NFR-04` | Закрытая операция проверяет активного актора, владение заявкой и границу отдела | Must | `test_owner_and_visibility_permissions`, `test_balance_and_hr_summary_keep_role_boundaries` |
| `NFR-05` | Демо слушает только `127.0.0.1`, проверяет `Host`, `Origin`, JSON и фиксированный список static path | Must | `test_wrong_origin_host_and_content_type_rejected`, `test_validation_and_static_allowlist` |
| `NFR-06` | Локальный сценарий и тесты выполняются на Python 3.12 без сторонних runtime-пакетов | Should | `test_cli_real_process`, `python -m leaveops.demo` |
| `NFR-07` | Webhook, рабочая карта и SQLite-файлы исключены из Git | Must | `.gitignore`, проверка tracked files и secret scan |
| `NFR-08` | Для доставки сохраняются `attempts`, `last_error`, `external_id`, `delivered_at` | Must | `test_delivery_is_idempotent_after_lost_response`, чтение `/api/request` |

Числовые SLA, нагрузочная ёмкость и RTO/RPO отсутствуют, потому что для них нет production-контекста и измерений.

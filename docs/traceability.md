# Трассировка v2

Матрицы разделяют происхождение бизнес-требований и технических мер. Подробные формулировки находятся в [BR](requirements/business-requirements.md), [правилах](requirements/business-rules.md), [FR](requirements/functional-requirements.md), [NFR](requirements/non-functional-requirements.md), [acceptance criteria](requirements/acceptance-criteria.md) и [реестре интеграционных рисков](architecture/integration-risks.md).

## Бизнес-трассировка

`Stakeholder → Pain Point → Business Requirement → Rule / FR → Use Case`

| Stakeholder | Pain Point | Business Requirement | Rule / FR | Use Case |
| --- | --- | --- | --- | --- |
| Руководитель | `PP-001` ручная проверка доступности | `BR-01` | `RULE-03`, `RULE-05`, `RULE-06`; `FR-01`, `FR-03` | `UC-02`, `UC-05` |
| HR, руководитель | `PP-002` отдельная проверка баланса | `BR-01` | `RULE-07`, `RULE-08`; `FR-07`, `FR-08` | `UC-02`, `UC-05` |
| Руководитель | `PP-003` нет единого контекста | `BR-01` | `RULE-04`–`RULE-08`; `FR-03`, `FR-04` | `UC-02` |
| Сотрудник, руководитель, HR | `PP-004` ручное ведение календаря | `BR-03` | `FR-10`, `FR-11` | `UC-06` |
| Сотрудник, руководитель | `PP-005` неконтролируемый перенос | `BR-04` | `RULE-09`, `RULE-11`, `RULE-12`; `FR-09` | `UC-03` |
| Все человеческие роли | `PP-006` фрагментированная история | `BR-02` | `RULE-01`, `RULE-02`, `RULE-09`, `RULE-10`; `FR-02`, `FR-04`–`FR-06` | `UC-01`–`UC-04` |

`BR-05` сохраняет идентификатор и объём, но его источник теперь обозначен честно: это требование устойчивости, сформулированное после выявления `RISK-002` и `RISK-003`, а не отдельная AS-IS боль.

## Трассировка технических рисков

`Integration Risk → NFR → ADR → Sequence Diagram → Component → Test`

| Integration Risk | NFR | ADR | Sequence Diagram | Component / implementation | Automated evidence / gap |
| --- | --- | --- | --- | --- | --- |
| `RISK-001` Bitrix24 недоступен | `NFR-01`, `NFR-08` | `ADR-001`, `ADR-002` | `SQ-02` | `integration_events`, `IntegrationWorker.run_once` | `test_delivery_is_idempotent_after_lost_response` |
| `RISK-002` ответ потерян после create | `NFR-02`, `NFR-08` | `ADR-005` | `SQ-03` | marker lookup в `Bitrix24CalendarAdapter` | `test_real_adapter_contract_recovers_lost_add_response` |
| `RISK-003` дубль при повторе | `NFR-02` | `ADR-005` | `SQ-03` | `_find`, Mock receipts, сохранение `external_id` | lost-response и update-retry tests |
| `RISK-004` конкурентные worker | `NFR-03` | `ADR-002`, `ADR-007` | `SQ-02` | atomic claim, `worker_token`, `lease_until` | `test_concurrent_workers_claim_once`, `test_expired_lease_is_recovered` |
| `RISK-005` отсутствует mapping | `NFR-07`, `NFR-08` | `ADR-006` | `SQ-02` | `mapping_preflight`, mapped adapter | mapping preflight and duplicate-owner tests |
| `RISK-006` ручное изменение Bitrix24 | `NFR-08` | `ADR-001`, `ADR-006` | `SQ-04` | update/cancel по известному ID | Контракт update/cancel проверен; silent-drift reconciliation не реализован |

## Функциональные требования → реализация → проверка

| FR | Use Case / AC | Компонент или реализация | Автоматическое доказательство |
| --- | --- | --- | --- |
| `FR-01` | `UC-05`; `AC-AVL-001` | `LeaveOps.team_calendar`, `GET /api/calendar` | `test_team_calendar_shares_availability_not_private_history` |
| `FR-02` | `UC-01`; `AC-REQ-001` | `LeaveOps.create`, `transition`, POST routes | `test_lifecycle_persists_history_after_restart`, `test_full_workflow_updates_calendar_and_history` |
| `FR-03` | `UC-02`/`UC-03`; `AC-APP-002`–`AC-APP-004` | `preview`, `_conflicts`, HTTP 409 | `test_conflict_cannot_be_bypassed_via_http`, `test_low_balance_blocks_approval_without_reserving_or_enqueuing` |
| `FR-04` | `UC-02`; `AC-APP-001`, `AC-APP-005`, `AC-APP-006` | `LeaveOps.transition` | `test_coverage_boundary_and_rollback`, `test_rejection_requires_reason_and_is_terminal` |
| `FR-05` | `UC-04`; `AC-CAN-001` | `transition(...cancelled)`, outbox cancel | `test_cancelled_approved_absence_releases_capacity`, `test_cancel_is_delivered_as_separate_event` |
| `FR-06` | `UC-01`–`UC-06`; `AC-HIS-001` | `LeaveOps.get`, `/api/request` | `test_full_workflow_updates_calendar_and_history` |
| `FR-07` | `UC-05`; `AC-BAL-001` | `_balance`, `_request_balance`, `/api/balance` | `test_balance_counts_accrual_and_completed_absence`, `test_cancelled_approved_absence_releases_balance_forecast` |
| `FR-08` | `UC-05`; `AC-HR-001` | `LeaveOps.hr_summary`, `/api/hr-summary` | `test_balance_and_hr_summary_keep_role_boundaries` |
| `FR-09` | `UC-03`; `AC-RES-001`, `AC-RES-002` | `propose_reschedule`, `decide_reschedule` | `test_reschedule_keeps_approved_period_until_manager_accepts_and_coalesces_pending_create`, `test_reschedule_conflict_does_not_change_approved_period_or_queue` |
| `FR-10` | `UC-02`/`UC-03`/`UC-04`; `AC-APP-001`, `AC-INT-001` | `_enqueue`, `integration_events` | `test_approval_enqueues_event_in_same_transaction`, `test_blocked_approval_does_not_enqueue` |
| `FR-11` | `UC-06`; `AC-INT-001`–`AC-INT-003` | `IntegrationWorker`, Mock и REST adapters | `test_delivery_is_idempotent_after_lost_response`, `test_cancel_deletes_the_marked_event`, `test_real_adapter_contract_updates_existing_marked_event` |
| `FR-12` | `UC-06`; `AC-MAP-001` | `mapping_preflight`, `require_mapping_ready_for_setup` | `test_mapping_preflight_blocks_unknown_and_duplicate_users_before_setup` |
| `FR-13` | `UC-06`; `AC-MAP-002` | `MappedBitrix24CalendarAdapter.ensure_sections` | `test_mapping_creates_distinct_personal_sections_and_routes`, `test_probe_and_section_setup_are_idempotent` |

## Нефункциональные требования → реализация → проверка

| NFR | Связанный риск | Решение / компонент | Проверка |
| --- | --- | --- | --- |
| `NFR-01` атомарность | `RISK-001` | `ADR-002`; `_connection(write=True)`, transition/reschedule | `test_approval_enqueues_event_in_same_transaction`, `test_coverage_boundary_and_rollback` |
| `NFR-02` идемпотентность | `RISK-002`, `RISK-003` | `ADR-005`; marker search, Mock receipts | `test_real_adapter_contract_recovers_lost_add_response`, `test_update_retries_after_lost_response_without_new_external_absence` |
| `NFR-03` конкурентный claim | `RISK-004` | `worker_token`, `lease_until`, `BEGIN IMMEDIATE` | `test_concurrent_workers_claim_once`, `test_expired_lease_is_recovered` |
| `NFR-04` границы доступа | — | `_actor`, `_manager`, `_readable`, `_readable_employee` | `test_owner_and_visibility_permissions`, `test_other_department_manager_is_forbidden` |
| `NFR-05` локальный HTTP | — | loopback bind, Host/Origin/body/static allowlists | `test_wrong_origin_host_and_content_type_rejected`, `test_validation_and_static_allowlist` |
| `NFR-06` воспроизводимость | — | `ADR-007`/`ADR-008`; stdlib + SQLite + CLI | `test_cli_real_process`, `python -m leaveops.demo` |
| `NFR-07` изоляция конфигурации | `RISK-005` | `.gitignore`, env, runtime mapping, preflight | tracked-files audit, secret scan, mapping tests |
| `NFR-08` диагностика | `RISK-001`, `RISK-002`, `RISK-005`, `RISK-006` | attempts/error/external/delivered fields | `test_delivery_is_idempotent_after_lost_response`, `/api/request` |

Все Must-level FR/NFR имеют реализацию и автоматическое либо репозиторное доказательство. `RISK-006` явно сохраняет остаточный пробел: silent drift после ручного изменения внешнего календаря текущими тестами и реализацией не обнаруживается. Это трассировка демонстрационного контура; она не заменяет production UAT.

# LeaveOps

[![Verify](https://github.com/Gauxared/b24-leaveops/actions/workflows/verify.yml/badge.svg)](https://github.com/Gauxared/b24-leaveops/actions/workflows/verify.yml)

Сервис для планирования отсутствий, согласования заявок и контроля доступности команды с интеграцией календаря Bitrix24.

LeaveOps показывает руководителю не только даты отпуска, но и последствия решения: пересечения, доступный остаток и минимальное покрытие критичных ролей. После согласования система сохраняет команду в outbox; отдельный ручной запуск синхронизации создаёт календарное событие, а при переносе обновляет тот же внешний объект.

## Возможности

- календарь команды и заявки со статусами `draft`, `submitted`, `approved`, `rejected`, `cancelled`;
- проверка пересечений, отпускного баланса и покрытия ролей;
- отдельное согласование переноса уже утверждённого периода;
- HR-сводная с использованными, запланированными и доступными днями;
- журнал действий и технический статус синхронизации;
- outbox с безопасным повтором после ошибки или потерянного ответа;
- Mock Bitrix24 для локального запуска и адаптер Calendar REST API для тестового портала.

Приложение использует синтетический отдел и не требует внешних аккаунтов для локального запуска.

## Кейс системного анализа

Проект оформлен как полный путь от бизнес-проблемы до проверяемой реализации: контекст и границы, AS-IS/TO-BE, требования, Use Case, состояния, модель данных, интеграционные последовательности, архитектурные решения и трассировка к тестам.

Начать просмотр: **[LeaveOps — кейс системного анализа](CASE_STUDY.md)**.

## Быстрый старт

Нужен Python 3.12+. Сторонние пакеты и сборка фронтенда не требуются.

```powershell
python -m leaveops init
python -m leaveops.web
```

Откройте http://127.0.0.1:8765. Сервер принимает соединения только с локального компьютера. Данные сохраняются в `runtime/leaveops.db`; повторный `init` не перезаписывает существующую базу.

## Демонстрационный сценарий

1. Выберите «Аналитик Б», создайте заявку на 15–16 октября 2026 года и отправьте её руководителю.
2. Переключитесь на «Руководитель», проверьте покрытие и согласуйте заявку.
3. Вернитесь к сотруднику и предложите перенос на 20–21 октября.
4. Руководитель увидит действующий и предложенный периоды и повторно примет решение.
5. Отправьте ожидающие события в Mock Bitrix24. Статус доставки появится отдельно от статуса заявки.

Заявка на 14–16 октября показывает другой исход: 14 октября нарушается минимальное покрытие аналитиков, поэтому согласование блокируется с конкретной причиной.

## Командная строка

```powershell
python -m unittest discover -s tests -v
python tools/verify_docs.py
node --check leaveops/static/app.js
python -m leaveops.demo
python -m leaveops.integration_demo
python -m leaveops list --actor manager
python -m leaveops create --actor analyst-b --start 2026-10-15 --end 2026-10-16
```

Команда `create` возвращает ID заявки. Подставьте его вместо `REQUEST_ID`:

```powershell
python -m leaveops submit --actor analyst-b REQUEST_ID
python -m leaveops preview --actor manager REQUEST_ID
python -m leaveops approve --actor manager REQUEST_ID
python -m leaveops show --actor analyst-b REQUEST_ID
python -m leaveops cancel --actor analyst-b REQUEST_ID --reason "Изменились планы"
```

После согласования очередь можно отправить в отдельную локальную базу, которая имитирует календарь Bitrix24:

```powershell
python -m leaveops sync
python -m leaveops mock-list
```

`integration_demo` воспроизводит потерю ответа после внешней записи и подтверждает, что повтор не создаёт дубль.

## Подключение Bitrix24

Webhook и карта пользователей передаются только локально. Приложение не читает `.env.example` автоматически.

```powershell
$env:B24_WEBHOOK_URL="https://your-portal.bitrix24.ru/rest/USER_ID/WEBHOOK_TOKEN/"
$env:B24_MAPPING_FILE="runtime/bitrix24-mapping.json"
python -m leaveops b24-mapping-check
python -m leaveops b24-check
python -m leaveops b24-setup
python -m leaveops b24-sync
```

Карта строится по [примеру](data/bitrix24-mapping.example.json). Для каждого сотрудника задаются отдельные `owner_id` и `section_id`. Команда `b24-mapping-check` проверяет карту без сетевых запросов, а `b24-setup` создаёт или находит личные разделы `LeaveOps — отсутствия` и сохраняет их идентификаторы в runtime-файл.

Для использования адаптера в веб-интерфейсе:

```powershell
python -m leaveops.web --integration-provider bitrix24
```

Подробности приведены в [контракте Bitrix24](docs/api/bitrix24-contract.md) и [описании карты пользователей](docs/mapping-master.md).

## Проверка

Workflow [Verify](.github/workflows/verify.yml) запускается на каждый push и pull request: выполняет 52 Python-теста, проверяет документацию и BPMN-структуру, JavaScript-синтаксис и PlantUML-исходники. Он не использует Bitrix24 webhook и не выполняет живые сетевые тесты.

Локально дополнительно выполняются demo-сценарии, рендеринг и визуальная проверка BPMN/UML. Живые smoke-тесты тестового Bitrix24 остаются ручными. Команды, границы и фактические результаты приведены в [отчёте о проверке](docs/verification.md).

## Ограничения

- Переключатель пользователя демонстрирует роли и не заменяет аутентификацию.
- Отпускной баланс использует упрощённые правила и не является кадровым расчётом.
- Не реализованы производственный календарь, правила 1С, массовые переносы и обратная синхронизация из Bitrix24.
- Уведомления целевого пользователя Bitrix24 не проверялись; организатором события остаётся пользователь webhook.
- Локальный HTTP-сервер не предназначен для размещения в интернете.

## Документация

- [Кейс системного анализа](CASE_STUDY.md)
- [Discovery и постановка задачи](docs/discovery/problem-statement.md)
- [BPMN AS-IS](docs/processes/as-is.md) и [BPMN TO-BE](docs/processes/to-be.md)
- [Требования, критерии и decision tables](docs/requirements.md)
- [UML Use Case и спецификации](docs/use-cases/overview.md)
- [Данные и состояния](docs/system-models.md)
- [C4 и компоненты](docs/architecture/overview.md)
- [UML Sequence Diagram](docs/integration-scenarios.md)
- [Архитектурные решения](docs/architecture/decisions.md)
- [Реестр интеграционных рисков](docs/architecture/integration-risks.md)
- [Внутренний HTTP API](docs/api/internal-api.md)
- [Контракт Bitrix24](docs/api/bitrix24-contract.md)
- [Матрица трассировки](docs/traceability.md)
- [Правила календаря](docs/calendar-rules.md)
- [Расчёт отпускного баланса](docs/balance-rules.md)
- [Перенос согласованного периода](docs/rescheduling.md)
- [Демонстрационный сценарий](docs/demo-guide.md)
- [Проверка](docs/verification.md)
- [Карта пользователей Bitrix24](docs/mapping-master.md)
- [Исходники BPMN/PlantUML и SVG](docs/diagrams/README.md)

Исходный код распространяется по [лицензии MIT](LICENSE).

"""Reliable local outbox and a separate SQLite imitation of Bitrix24."""

import json
import os
import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class SimulatedLostResponse(Exception):
    pass


class Bitrix24Error(Exception):
    pass


def _load_mapping_document(mapping_file):
    try:
        document = json.loads(Path(mapping_file).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Bitrix24Error(f'Не удалось прочитать карту Bitrix24: {type(error).__name__}') from None
    if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('employees'), dict):
        raise Bitrix24Error('Карта Bitrix24 должна содержать schema_version=1 и объект employees')
    return document


def mapping_preflight(mapping_file, employee_ids):
    """Check a local mapping before any call or write to Bitrix24."""
    document = _load_mapping_document(mapping_file)
    known = sorted({str(employee_id) for employee_id in employee_ids})
    known_set = set(known)
    mapped = sorted(document['employees'])
    unknown = sorted(set(mapped) - known_set)
    invalid_targets = []
    owner_ids = {}
    missing_sections = []
    for employee_id, target in document['employees'].items():
        if not isinstance(target, dict) or not str(target.get('owner_id', '')).isdigit():
            invalid_targets.append(employee_id)
            continue
        owner_id = int(target['owner_id'])
        owner_ids.setdefault(owner_id, []).append(employee_id)
        if not str(target.get('section_id', '')).isdigit():
            missing_sections.append(employee_id)
    duplicate_owners = {
        str(owner_id): sorted(employee_id for employee_id in employees)
        for owner_id, employees in owner_ids.items() if len(employees) > 1
    }
    issues = bool(unknown or invalid_targets or duplicate_owners or not mapped)
    mapped_known = sorted((set(mapped) & known_set) - set(invalid_targets))
    return {
        'mode': 'local_mapping_preflight',
        'writes_to_bitrix24': False,
        'mapping_file': str(mapping_file),
        'known_employee_ids': known,
        'mapped_employee_ids': mapped_known,
        'unmapped_employee_ids': sorted(known_set - set(mapped_known)),
        'unknown_mapping_employee_ids': unknown,
        'invalid_target_employee_ids': sorted(invalid_targets),
        'duplicate_owner_ids': duplicate_owners,
        'missing_section_employee_ids': sorted(missing_sections),
        'ready_for_section_setup': not issues,
        'complete_for_all_synthetic_employees': not issues and not (known_set - set(mapped_known)),
        'ready_for_full_sync': not issues and not (known_set - set(mapped_known)) and not missing_sections,
    }


def require_mapping_ready_for_setup(mapping_file, employee_ids):
    report = mapping_preflight(mapping_file, employee_ids)
    if not report['ready_for_section_setup']:
        raise Bitrix24Error('Карта Bitrix24 не готова к созданию разделов: проверьте неизвестных сотрудников, owner_id и дубли')
    return report


class Bitrix24CalendarAdapter:
    """Outbound adapter for a dedicated calendar section in a cloud portal."""

    def __init__(self, webhook_url, section_id=None, timeout=15, opener=None, owner_id=None):
        parsed = urlsplit(webhook_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment:
            raise Bitrix24Error('Webhook URL должен быть полным HTTPS-адресом без query и fragment')
        if not re.fullmatch(r'/rest/\d+/[^/]+/?', parsed.path):
            raise Bitrix24Error('Webhook URL не похож на входящий webhook Bitrix24')
        self._base_url = webhook_url.rstrip('/') + '/'
        self.section_id = int(section_id) if section_id not in (None, '') else None
        self._owner_id = int(owner_id) if owner_id not in (None, '') else None
        self.timeout = timeout
        self.opener = opener or build_opener(ProxyHandler({}))
        self._profile = None

    def _call(self, method, params=None):
        if not re.fullmatch(r'[a-z][a-z0-9_.]+', method):
            raise Bitrix24Error('Некорректное имя REST-метода')
        request = Request(self._base_url + method + '.json',
                          data=json.dumps(params or {}).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                   'User-Agent': 'LeaveOps/1.0'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
            except Exception:
                raise Bitrix24Error(f'Bitrix24 вернул HTTP {error.code}') from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise Bitrix24Error(f'Bitrix24 недоступен: {type(error).__name__}') from None
        if not isinstance(payload, dict):
            raise Bitrix24Error('Bitrix24 вернул неожиданный ответ')
        if 'error' in payload:
            code = str(payload.get('error') or 'BITRIX_ERROR')
            description = str(payload.get('error_description') or 'Ошибка REST API')
            raise Bitrix24Error(f'{code}: {description}')
        if 'result' not in payload:
            raise Bitrix24Error('В ответе Bitrix24 отсутствует result')
        return payload['result']

    def profile(self):
        if self._profile is None:
            result = self._call('profile')
            if not isinstance(result, dict) or not str(result.get('ID', '')).isdigit():
                raise Bitrix24Error('Bitrix24 не вернул ID пользователя webhook')
            self._profile = result
        return self._profile

    @property
    def owner_id(self):
        return self._owner_id if self._owner_id is not None else int(self.profile()['ID'])

    def probe(self):
        scopes = self._call('scope')
        methods = self._call('methods')
        required = {'calendar.section.get', 'calendar.section.add', 'calendar.event.get',
                    'calendar.event.add', 'calendar.event.update', 'calendar.event.delete'}
        available = set(methods if isinstance(methods, list) else [])
        return {'connected': True, 'owner_id': self.owner_id,
                'calendar_scope': 'calendar' in (scopes if isinstance(scopes, list) else []),
                'required_methods': {method: method in available for method in sorted(required)}}

    def sections(self):
        result = self._call('calendar.section.get', {'type': 'user', 'ownerId': self.owner_id})
        if not isinstance(result, list):
            raise Bitrix24Error('Bitrix24 вернул неожиданный список календарей')
        return result

    def ensure_section(self, name='LeaveOps Test'):
        for section in self.sections():
            if section.get('NAME') == name:
                self.section_id = int(section['ID'])
                return {'section_id': self.section_id, 'created': False}
        result = self._call('calendar.section.add', {
            'type': 'user', 'ownerId': self.owner_id, 'name': name,
            'description': 'Изолированный календарь для тестов проекта LeaveOps',
            'color': '#17695b', 'text_color': '#ffffff',
            'export': {'ALLOW': False, 'SET': '3_9'},
        })
        self.section_id = int(result)
        return {'section_id': self.section_id, 'created': True}

    @staticmethod
    def _marker(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', request_id):
            raise Bitrix24Error('Некорректный идентификатор заявки')
        return f'[LeaveOps:{request_id}]'

    def _find(self, payload):
        if self.section_id is None:
            raise Bitrix24Error('Не задан B24_SECTION_ID')
        result = self._call('calendar.event.get', {
            'type': 'user', 'ownerId': self.owner_id,
            'from': payload['start_date'], 'to': payload['end_date'],
            'section': [self.section_id],
        })
        marker = self._marker(payload['request_id'])
        for event in result if isinstance(result, list) else []:
            if marker in str(event.get('DESCRIPTION') or event.get('description') or ''):
                value = event.get('ID') or event.get('id')
                if value is not None:
                    return str(value)
        return None

    def deliver(self, event):
        payload = json.loads(event['payload'])
        marker = self._marker(payload['request_id'])
        if event['operation'] == 'cancel':
            existing = self._find(payload)
            external_id = str(event.get('external_id') or existing or '')
            if existing:
                self._call('calendar.event.delete', {'id': int(existing)})
                external_id = existing
            if not external_id:
                external_id = f'absent:{payload["request_id"]}'
            return external_id
        if event['operation'] == 'update':
            external_id = str(event.get('external_id') or '')
            if not external_id.isdigit():
                raise Bitrix24Error('Для обновления не найден ID ранее доставленного события')
            self._call('calendar.event.update', {
                'id': int(external_id), 'type': 'user', 'ownerId': self.owner_id, 'section': self.section_id,
                'name': f'Отсутствие · {payload["employee_id"]}',
                'description': f'{marker}\nСоздано демонстрационным проектом LeaveOps.',
                'from': payload['start_date'], 'to': payload['end_date'],
                'skip_time': 'Y', 'accessibility': 'absent', 'importance': 'normal',
                'private_event': 'N', 'is_meeting': 'N',
            })
            return external_id
        existing = self._find(payload)
        if existing:
            return existing
        result = self._call('calendar.event.add', {
            'type': 'user', 'ownerId': self.owner_id, 'section': self.section_id,
            'name': f'Отсутствие · {payload["employee_id"]}',
            'description': f'{marker}\nСоздано демонстрационным проектом LeaveOps.',
            'from': payload['start_date'], 'to': payload['end_date'],
            'skip_time': 'Y', 'accessibility': 'absent', 'importance': 'normal',
            'private_event': 'N', 'is_meeting': 'N',
        })
        return str(result)


class MappedBitrix24CalendarAdapter:
    """Route each LeaveOps employee to a distinct Bitrix24 user calendar."""

    def __init__(self, webhook_url, mappings, timeout=15, adapter_factory=Bitrix24CalendarAdapter):
        if not isinstance(mappings, dict) or not mappings:
            raise Bitrix24Error('Карта сотрудников Bitrix24 пуста')
        normalized = {}
        owners = set()
        for employee_id, target in mappings.items():
            if not isinstance(employee_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', employee_id):
                raise Bitrix24Error('Некорректный employee_id в карте Bitrix24')
            if not isinstance(target, dict) or not str(target.get('owner_id', '')).isdigit():
                raise Bitrix24Error(f'Для {employee_id} не задан числовой owner_id')
            owner_id = int(target['owner_id'])
            if owner_id in owners:
                raise Bitrix24Error('Один пользователь Bitrix24 не может представлять двух сотрудников LeaveOps')
            owners.add(owner_id)
            section_id = target.get('section_id')
            normalized[employee_id] = {'owner_id': owner_id,
                                       'section_id': int(section_id) if str(section_id or '').isdigit() else None}
        self.webhook_url = webhook_url
        self.mappings = normalized
        self.timeout = timeout
        self.adapter_factory = adapter_factory

    def _adapter(self, target):
        return self.adapter_factory(self.webhook_url, target.get('section_id'), self.timeout,
                                    owner_id=target['owner_id'])

    def probe(self):
        return self.adapter_factory(self.webhook_url, timeout=self.timeout).probe()

    def ensure_sections(self, name='LeaveOps — отсутствия'):
        configured = {}
        for employee_id, target in self.mappings.items():
            adapter = self._adapter(target)
            section = adapter.ensure_section(name)
            target['section_id'] = section['section_id']
            configured[employee_id] = {**target, 'created': section['created']}
        return configured

    def deliver(self, event):
        payload = json.loads(event['payload'])
        target = self.mappings.get(payload.get('employee_id'))
        if target is None:
            raise Bitrix24Error(f'Сотрудник {payload.get("employee_id")} не сопоставлен с Bitrix24 user')
        if target['section_id'] is None:
            raise Bitrix24Error(f'Для {payload["employee_id"]} не настроен личный календарь')
        adapter = self._adapter(target)
        return adapter.deliver(event)


def bitrix_adapter_from_environment():
    webhook = os.environ.get('B24_WEBHOOK_URL', '')
    mapping_file = os.environ.get('B24_MAPPING_FILE')
    if not mapping_file:
        return Bitrix24CalendarAdapter(webhook, os.environ.get('B24_SECTION_ID'))
    document = _load_mapping_document(mapping_file)
    return MappedBitrix24CalendarAdapter(webhook, document['employees'])


class MockBitrix24:
    """External-system stand-in. Event IDs make delivery idempotent."""

    def __init__(self, database, lose_response_once_for=None):
        self.database = str(database)
        self.lose_response_once_for = set(lose_response_once_for or [])
        self._lost = set()
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS absences (
                    external_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    employee_id TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    event_id TEXT PRIMARY KEY, operation TEXT NOT NULL, external_id TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def deliver(self, event):
        payload = json.loads(event['payload'])
        with self._connection() as db:
            receipt = db.execute('SELECT external_id FROM receipts WHERE event_id=?', (event['id'],)).fetchone()
            if receipt:
                external_id = receipt['external_id']
            else:
                current = db.execute('SELECT external_id FROM absences WHERE request_id=?',
                                     (payload['request_id'],)).fetchone()
                external_id = current['external_id'] if current else f"mock-b24-{uuid4()}"
                if event['operation'] in ('upsert', 'update'):
                    db.execute('''INSERT INTO absences VALUES (?,?,?,?,?,1)
                        ON CONFLICT(request_id) DO UPDATE SET employee_id=excluded.employee_id,
                        start_date=excluded.start_date,end_date=excluded.end_date,active=1''',
                               (external_id, payload['request_id'], payload['employee_id'],
                                payload['start_date'], payload['end_date']))
                else:
                    if current:
                        db.execute('UPDATE absences SET active=0 WHERE request_id=?', (payload['request_id'],))
                db.execute('INSERT INTO receipts(event_id,operation,external_id) VALUES (?,?,?)',
                           (event['id'], event['operation'], external_id))
        if event['id'] in self.lose_response_once_for and event['id'] not in self._lost:
            self._lost.add(event['id'])
            raise SimulatedLostResponse('Имитирована потеря ответа после записи во внешнюю систему')
        return external_id

    def list_absences(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM absences ORDER BY request_id')]


class IntegrationWorker:
    def __init__(self, application_database, adapter, lease_seconds=30):
        self.database = str(application_database)
        self.adapter = adapter
        self.lease_seconds = lease_seconds

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def run_once(self):
        token = str(uuid4())
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM integration_events
                WHERE status='pending' OR (status='processing' AND lease_until<=strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ORDER BY rowid LIMIT 1''').fetchone()
            if row is None:
                db.commit()
                return None
            db.execute('''UPDATE integration_events SET status='processing',attempts=attempts+1,
                worker_token=?,lease_until=strftime('%Y-%m-%dT%H:%M:%fZ','now',?) WHERE id=?''',
                       (token, f'+{self.lease_seconds} seconds', row['id']))
            db.commit()
        finally:
            db.close()
        event = dict(row)
        if event['operation'] in {'cancel', 'update'}:
            db = self._connect()
            try:
                previous = db.execute('''SELECT external_id FROM integration_events
                    WHERE request_id=? AND operation IN ('upsert','update') AND status='delivered'
                    ORDER BY rowid DESC LIMIT 1''', (event['request_id'],)).fetchone()
                if previous:
                    event['external_id'] = previous['external_id']
            finally:
                db.close()
        try:
            external_id = self.adapter.deliver(event)
        except Exception as error:
            db = self._connect()
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('''UPDATE integration_events SET status='pending',last_error=?,worker_token=NULL,lease_until=NULL
                    WHERE id=? AND worker_token=?''', (str(error)[:1000], event['id'], token))
                db.commit()
            finally:
                db.close()
            return {'id': event['id'], 'status': 'pending', 'error': str(error)}
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE integration_events SET status='delivered',external_id=?,last_error=NULL,
                worker_token=NULL,lease_until=NULL,delivered_at=? WHERE id=? AND worker_token=?''',
                       (external_id, datetime.now(timezone.utc).isoformat(), event['id'], token))
            db.commit()
        finally:
            db.close()
        return {'id': event['id'], 'status': 'delivered', 'external_id': external_id}

    def drain(self, limit=100):
        results = []
        for _ in range(limit):
            result = self.run_once()
            if result is None:
                break
            results.append(result)
            if result['status'] != 'delivered':
                break
        return results

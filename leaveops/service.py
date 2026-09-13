"""Transactional workflow. All public operations require an explicit demo actor."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4


class WorkflowError(Exception):
    def __init__(self, code, message, conflicts=None):
        super().__init__(message)
        self.code = code
        self.conflicts = conflicts or []


class LeaveOps:
    def __init__(self, database):
        self.database = str(database)
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS employees (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, department_id TEXT NOT NULL,
                    role TEXT NOT NULL, active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS balances (
                    employee_id TEXT PRIMARY KEY REFERENCES employees(id),
                    opening_days REAL NOT NULL CHECK(opening_days >= 0),
                    accrual_start TEXT NOT NULL,
                    monthly_accrual_days REAL NOT NULL CHECK(monthly_accrual_days >= 0));
                CREATE TABLE IF NOT EXISTS departments (
                    id TEXT PRIMARY KEY, manager_id TEXT NOT NULL REFERENCES employees(id));
                CREATE TABLE IF NOT EXISTS rules (
                    department_id TEXT NOT NULL REFERENCES departments(id), role TEXT NOT NULL,
                    minimum_available INTEGER NOT NULL CHECK(minimum_available >= 0),
                    PRIMARY KEY(department_id, role));
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id),
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('draft','submitted','approved','rejected','cancelled')),
                    CHECK(start_date <= end_date));
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
                    actor_id TEXT NOT NULL REFERENCES employees(id), from_status TEXT,
                    to_status TEXT NOT NULL, reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                CREATE TABLE IF NOT EXISTS integration_events (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
                    operation TEXT NOT NULL CHECK(operation IN ('upsert','update','cancel')),
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','delivered')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    external_id TEXT, last_error TEXT,
                    worker_token TEXT, lease_until TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    delivered_at TEXT);
                CREATE INDEX IF NOT EXISTS integration_events_delivery
                    ON integration_events(status, created_at);
                CREATE TABLE IF NOT EXISTS reschedule_proposals (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('submitted','approved','rejected')),
                    requested_by TEXT NOT NULL REFERENCES employees(id), reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    decided_at TEXT, decided_by TEXT REFERENCES employees(id), decision_reason TEXT NOT NULL DEFAULT '',
                    CHECK(start_date <= end_date));
                CREATE INDEX IF NOT EXISTS reschedule_proposals_request
                    ON reschedule_proposals(request_id, created_at);
            """)
            definition = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='integration_events'").fetchone()[0]
            if "'update'" not in definition:
                db.executescript("""
                    ALTER TABLE integration_events RENAME TO integration_events_legacy;
                    CREATE TABLE integration_events (
                        id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
                        operation TEXT NOT NULL CHECK(operation IN ('upsert','update','cancel')),
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','delivered')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        external_id TEXT, last_error TEXT,
                        worker_token TEXT, lease_until TEXT,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                        delivered_at TEXT);
                    INSERT INTO integration_events SELECT * FROM integration_events_legacy;
                    DROP TABLE integration_events_legacy;
                    CREATE INDEX integration_events_delivery ON integration_events(status, created_at);
                """)

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def seed(self, source):
        """Initialize an empty database only; never overwrite existing work."""
        data = json.loads(Path(source).read_text(encoding='utf-8'))
        if data.get('synthetic') is not True or data.get('schema_version') != 1:
            raise WorkflowError('invalid_seed', 'Требуется синтетический набор версии 1')
        if data['calendar'] != {'working_iso_weekdays': [1, 2, 3, 4, 5], 'holidays': [], 'inclusive_dates': True}:
            raise WorkflowError('invalid_calendar', 'Поддерживается только демонстрационный календарь Пн–Пт')
        balances = data.get('balances')
        if not isinstance(balances, list) or {row.get('employee_id') for row in balances} != {row['id'] for row in data['employees']}:
            raise WorkflowError('invalid_seed', 'Для каждого синтетического сотрудника нужен один отпускной баланс')
        for balance in balances:
            self._dates(balance.get('accrual_start'), balance.get('accrual_start'))
            if date.fromisoformat(balance['accrual_start']).day != 1 or any(
                not isinstance(balance.get(field), (int, float)) or isinstance(balance.get(field), bool) or balance[field] < 0
                for field in ('opening_days', 'monthly_accrual_days')
            ):
                raise WorkflowError('invalid_seed', 'Баланс содержит невалидное начальное значение или месячную норму')
        with self._connection(write=True) as db:
            if db.execute('SELECT 1 FROM employees LIMIT 1').fetchone():
                raise WorkflowError('already_initialized', 'База уже заполнена; данные сохранены')
            for e in data['employees']:
                db.execute('INSERT INTO employees VALUES (?,?,?,?,?)',
                           (e['id'], e['name'], e['department_id'], e['role'], e['active']))
            for balance in balances:
                db.execute('INSERT INTO balances VALUES (?,?,?,?)',
                           (balance['employee_id'], balance['opening_days'], balance['accrual_start'],
                            balance['monthly_accrual_days']))
            for d in data['departments']:
                db.execute('INSERT INTO departments VALUES (?,?)', (d['id'], d['manager_id']))
            for r in data['coverage_rules']:
                db.execute('INSERT INTO rules VALUES (?,?,?)',
                           (r['department_id'], r['role'], r['minimum_available']))
            for a in data['absences']:
                self._dates(a['start_date'], a['end_date'])
                db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                           (a['id'], a['employee_id'], a['start_date'], a['end_date'], a['status']))
                self._record(db, a['id'], a['approved_by'], None, a['status'], 'Синтетическое начальное отсутствие')

    @staticmethod
    def _dates(start, end):
        try:
            if not all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in (start, end)):
                raise ValueError()
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first > last:
                raise ValueError()
        except (ValueError, TypeError):
            raise WorkflowError('invalid_dates', 'Нужны существующие даты YYYY-MM-DD: начало не позже конца') from None
        return first, last

    @staticmethod
    def _days(start, end):
        first, last = LeaveOps._dates(start, end)
        return (last - first).days + 1

    @staticmethod
    def _months(accrual_start, as_of):
        first, current = LeaveOps._dates(accrual_start, as_of)
        if current < first:
            return 0
        return (current.year - first.year) * 12 + current.month - first.month + 1

    def _balance(self, db, employee_id, as_of, exclude_request_id=None):
        self._dates(as_of, as_of)
        row = db.execute('SELECT * FROM balances WHERE employee_id=?', (employee_id,)).fetchone()
        if row is None:
            return None
        earned = row['opening_days'] + self._months(row['accrual_start'], as_of) * row['monthly_accrual_days']
        query = '''SELECT start_date,end_date FROM requests
            WHERE employee_id=? AND status='approved' '''
        params = [employee_id]
        if exclude_request_id:
            query += 'AND id<>? '
            params.append(exclude_request_id)
        absences = list(db.execute(query + 'ORDER BY start_date,id', params))
        used = sum(self._days(item['start_date'], item['end_date']) for item in absences if item['end_date'] <= as_of)
        planned = sum(self._days(item['start_date'], item['end_date']) for item in absences if item['start_date'] > as_of)
        return {
            'employee_id': employee_id,
            'as_of': as_of,
            'opening_days': round(row['opening_days'], 2),
            'accrual_start': row['accrual_start'],
            'monthly_accrual_days': round(row['monthly_accrual_days'], 2),
            'accrued_days': round(earned - row['opening_days'], 2),
            'used_days': round(used, 2),
            'planned_days': round(planned, 2),
            'available_days': round(earned - used - planned, 2),
        }

    def _request_balance(self, db, request, replacing_request_id=None):
        balance = self._balance(db, request['employee_id'], request['start_date'], replacing_request_id)
        if balance is None:
            return None
        request_days = self._days(request['start_date'], request['end_date'])
        return {
            'before_approval': balance,
            'request_days': request_days,
            'after_approval_days': round(balance['available_days'] - request_days, 2),
        }

    @staticmethod
    def _actor(db, actor):
        row = db.execute('SELECT * FROM employees WHERE id=? AND active=1', (actor,)).fetchone()
        if row is None:
            raise WorkflowError('forbidden', 'Неизвестный или неактивный сотрудник')
        return row

    @staticmethod
    def _request(db, request_id):
        row = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise WorkflowError('not_found', 'Заявка не найдена')
        return row

    @staticmethod
    def _manager(db, actor, employee_id):
        return db.execute('''SELECT 1 FROM employees e JOIN departments d ON d.id=e.department_id
                             WHERE e.id=? AND d.manager_id=?''', (employee_id, actor)).fetchone() is not None

    def _readable(self, db, actor, request):
        self._actor(db, actor)
        if actor != request['employee_id'] and not self._manager(db, actor, request['employee_id']):
            raise WorkflowError('forbidden', 'Заявка другого сотрудника недоступна')

    def _readable_employee(self, db, actor, employee_id):
        self._actor(db, actor)
        if actor != employee_id and not self._manager(db, actor, employee_id):
            raise WorkflowError('forbidden', 'Баланс другого сотрудника недоступен')

    @staticmethod
    def _record(db, request_id, actor, previous, target, reason):
        cursor = db.execute('INSERT INTO history(request_id,actor_id,from_status,to_status,reason) VALUES (?,?,?,?,?)',
                            (request_id, actor, previous, target, reason))
        return cursor.lastrowid

    @staticmethod
    def _enqueue(db, request, history_id, operation, external_id=None):
        payload = json.dumps({'request_id': request['id'], 'employee_id': request['employee_id'],
                              'start_date': request['start_date'], 'end_date': request['end_date']},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('''INSERT INTO integration_events(id,request_id,operation,payload,external_id)
                      VALUES (?,?,?,?,?)''',
                   (f'leaveops-{history_id}', request['id'], operation, payload, external_id))

    def create(self, actor, start, end):
        self._dates(start, end)
        request_id = str(uuid4())
        with self._connection(write=True) as db:
            self._actor(db, actor)
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (request_id, actor, start, end, 'draft'))
            self._record(db, request_id, actor, None, 'draft', '')
        return self.get(actor, request_id)

    def get(self, actor, request_id):
        with self._connection() as db:
            row = self._request(db, request_id)
            self._readable(db, actor, row)
            result = dict(row)
            result['history'] = [dict(r) for r in db.execute(
                'SELECT * FROM history WHERE request_id=? ORDER BY id', (request_id,))]
            result['integration'] = [dict(r) for r in db.execute('''SELECT id,operation,status,attempts,external_id,last_error,
                created_at,delivered_at FROM integration_events WHERE request_id=? ORDER BY created_at,id''', (request_id,))]
            result['balance_projection'] = self._request_balance(db, row)
            proposal = db.execute('''SELECT * FROM reschedule_proposals WHERE request_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1''', (request_id,)).fetchone()
            if proposal:
                result['reschedule'] = {**dict(proposal), 'current_start_date': row['start_date'],
                                        'current_end_date': row['end_date']}
                if proposal['status'] == 'submitted':
                    candidate = {**dict(row), 'start_date': proposal['start_date'], 'end_date': proposal['end_date']}
                    result['reschedule_conflicts'] = self._conflicts(db, candidate, replacing_request_id=row['id'])
                else:
                    result['reschedule_conflicts'] = []
            else:
                result['reschedule'] = None
                result['reschedule_conflicts'] = []
            return result

    def list_requests(self, actor):
        with self._connection() as db:
            self._actor(db, actor)
            return [dict(r) for r in db.execute('''SELECT r.* FROM requests r
                JOIN employees e ON e.id=r.employee_id JOIN departments d ON d.id=e.department_id
                WHERE r.employee_id=? OR d.manager_id=? ORDER BY r.start_date,r.id''', (actor, actor))]

    def preview(self, actor, request_id):
        with self._connection() as db:
            row = self._request(db, request_id)
            self._readable(db, actor, row)
            return self._conflicts(db, row)

    def demo_actors(self):
        """Public synthetic identities for the local role switcher; not authentication."""
        with self._connection() as db:
            return [dict(r) for r in db.execute('SELECT id,name FROM employees WHERE active=1 ORDER BY id')]

    def active_employee_ids(self):
        """Synthetic identities used for a local integration-map preflight."""
        with self._connection() as db:
            return [row['id'] for row in db.execute('SELECT id FROM employees WHERE active=1 ORDER BY id')]

    def is_manager(self, actor):
        with self._connection() as db:
            self._actor(db, actor)
            return db.execute('SELECT 1 FROM departments WHERE manager_id=?', (actor,)).fetchone() is not None

    def balance(self, actor, employee_id, as_of):
        with self._connection() as db:
            self._readable_employee(db, actor, employee_id)
            balance = self._balance(db, employee_id, as_of)
            if balance is None:
                raise WorkflowError('balance_unavailable', 'Для сотрудника не задан демонстрационный отпускной баланс')
            return balance

    def hr_summary(self, actor, as_of):
        self._dates(as_of, as_of)
        with self._connection() as db:
            employee = self._actor(db, actor)
            department = db.execute('SELECT * FROM departments WHERE id=?', (employee['department_id'],)).fetchone()
            if department['manager_id'] != actor:
                raise WorkflowError('forbidden', 'HR-сводная доступна руководителю отдела')
            team = [dict(row) for row in db.execute('''SELECT id,name,role FROM employees
                WHERE department_id=? AND active=1 ORDER BY role,name,id''', (department['id'],))]
            balances = []
            for member in team:
                balance = self._balance(db, member['id'], as_of)
                if balance is not None:
                    balances.append({**member, **balance})
            pending = db.execute('''SELECT COUNT(*) FROM requests r JOIN employees e ON e.id=r.employee_id
                WHERE e.department_id=? AND r.status='submitted' ''', (department['id'],)).fetchone()[0]
            upcoming = [dict(row) for row in db.execute('''SELECT r.id,r.employee_id,r.start_date,r.end_date,e.name
                FROM requests r JOIN employees e ON e.id=r.employee_id
                WHERE e.department_id=? AND r.status='approved' AND r.start_date>? ORDER BY r.start_date,r.id LIMIT 8''',
                (department['id'], as_of))]
            low_balance = [row['employee_id'] for row in balances if row['available_days'] < 5]
            return {'as_of': as_of, 'employees': balances, 'pending_requests': pending,
                    'low_balance_employee_ids': low_balance, 'upcoming_absences': upcoming}

    def team_calendar(self, actor, start, end):
        first, last = self._dates(start, end)
        if (last - first).days > 30:
            raise WorkflowError('invalid_dates', 'Календарь доступен за период до 31 дня')
        with self._connection() as db:
            employee = self._actor(db, actor)
            department = db.execute('SELECT * FROM departments WHERE id=?', (employee['department_id'],)).fetchone()
            team = [dict(r) for r in db.execute('SELECT id,name,role FROM employees WHERE department_id=? AND active=1 ORDER BY role,id', (department['id'],))]
            # Team availability is shared; request history and reasons stay behind get().
            absences = [dict(r) for r in db.execute('''SELECT r.id,r.employee_id,r.start_date,r.end_date,r.status
                FROM requests r JOIN employees e ON e.id=r.employee_id
                WHERE e.department_id=? AND r.status IN ('approved','submitted')
                AND r.start_date<=? AND r.end_date>=?''', (department['id'], end, start))]
            rules = [dict(r) for r in db.execute('SELECT role,minimum_available FROM rules WHERE department_id=?', (department['id'],))]
            coverage = []
            for offset in range((last-first).days+1):
                day = first + timedelta(days=offset)
                iso = day.isoformat()
                absent = {a['employee_id'] for a in absences if a['status']=='approved' and a['start_date']<=iso<=a['end_date']}
                for rule in rules:
                    available = sum(e['role']==rule['role'] and e['id'] not in absent for e in team)
                    coverage.append({'date': iso, 'role': rule['role'], 'available': available,
                                     'required': rule['minimum_available'], 'working': day.isoweekday()<=5})
            return {'employees': team, 'absences': absences, 'coverage': coverage,
                    'manager_id': department['manager_id'], 'start': start, 'end': end}

    def _conflicts(self, db, request, replacing_request_id=None):
        first, last = self._dates(request['start_date'], request['end_date'])
        employee = db.execute('SELECT * FROM employees WHERE id=?', (request['employee_id'],)).fetchone()
        team = list(db.execute('SELECT * FROM employees WHERE department_id=? AND active=1', (employee['department_id'],)))
        rules = list(db.execute('SELECT * FROM rules WHERE department_id=?', (employee['department_id'],)))
        absences = list(db.execute('''SELECT r.* FROM requests r JOIN employees e ON e.id=r.employee_id
            WHERE e.department_id=? AND r.status='approved' AND r.id<>?
            AND r.start_date<=? AND r.end_date>=?''',
            (employee['department_id'], request['id'], request['end_date'], request['start_date'])))
        conflicts = []
        for a in absences:
            if a['employee_id'] == request['employee_id']:
                conflicts.append({'code': 'overlap', 'absence_id': a['id'],
                                  'start_date': max(a['start_date'], request['start_date']),
                                  'end_date': min(a['end_date'], request['end_date']),
                                  'message': 'Пересечение с согласованным отсутствием сотрудника'})
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            if day.isoweekday() > 5:
                continue
            iso = day.isoformat()
            unavailable = {a['employee_id'] for a in absences if a['start_date'] <= iso <= a['end_date']}
            unavailable.add(request['employee_id'])
            for rule in rules:
                available = sum(e['role'] == rule['role'] and e['id'] not in unavailable for e in team)
                if available < rule['minimum_available']:
                    conflicts.append({'code': 'coverage', 'date': iso, 'role': rule['role'],
                                      'required': rule['minimum_available'], 'available': available,
                                      'message': f"{iso}: роль {rule['role']}, доступно {available}, требуется {rule['minimum_available']}"})
        balance = self._request_balance(db, request, replacing_request_id)
        if balance is not None and balance['after_approval_days'] < 0:
            conflicts.append({'code': 'balance', 'available': balance['before_approval']['available_days'],
                              'requested': balance['request_days'], 'after_approval': balance['after_approval_days'],
                              'message': f"Недостаточно доступных дней: доступно {balance['before_approval']['available_days']}, требуется {balance['request_days']}"})
        return conflicts

    def propose_reschedule(self, actor, request_id, start, end, reason=''):
        self._dates(start, end)
        if not isinstance(reason, str):
            raise WorkflowError('invalid_reason', 'Комментарий должен быть текстом')
        with self._connection(write=True) as db:
            self._actor(db, actor)
            row = self._request(db, request_id)
            if actor != row['employee_id']:
                raise WorkflowError('forbidden', 'Перенос согласованной заявки предлагает её автор')
            if row['status'] != 'approved':
                raise WorkflowError('invalid_transition', 'Перенести можно только согласованную заявку')
            if db.execute("SELECT 1 FROM reschedule_proposals WHERE request_id=? AND status='submitted'", (request_id,)).fetchone():
                raise WorkflowError('reschedule_pending', 'Предложение переноса уже ожидает решения')
            if (start, end) == (row['start_date'], row['end_date']):
                raise WorkflowError('unchanged_dates', 'Укажите период, отличный от текущего')
            proposal_id = f'reschedule-{uuid4()}'
            db.execute('''INSERT INTO reschedule_proposals(id,request_id,start_date,end_date,status,requested_by,reason)
                VALUES (?,?,?,?,?,?,?)''', (proposal_id, request_id, start, end, 'submitted', actor, reason.strip()))
            history_reason = f'Предложен перенос: {start} — {end}'
            if reason.strip():
                history_reason += f'. {reason.strip()}'
            self._record(db, request_id, actor, 'approved', 'reschedule_submitted', history_reason)
        return self.get(actor, request_id)

    def decide_reschedule(self, actor, request_id, target, reason=''):
        if target not in {'approved', 'rejected'}:
            raise WorkflowError('invalid_transition', 'Неизвестное решение по переносу')
        if not isinstance(reason, str) or (target == 'rejected' and not reason.strip()):
            raise WorkflowError('reason_required', 'Для отклонения переноса требуется причина')
        with self._connection(write=True) as db:
            self._actor(db, actor)
            row = self._request(db, request_id)
            if actor == row['employee_id'] or not self._manager(db, actor, row['employee_id']):
                raise WorkflowError('forbidden', 'Решение по переносу принимает руководитель отдела')
            if row['status'] != 'approved':
                raise WorkflowError('invalid_transition', 'Исходная заявка больше не согласована')
            proposal = db.execute("SELECT * FROM reschedule_proposals WHERE request_id=? AND status='submitted' ORDER BY created_at,id LIMIT 1", (request_id,)).fetchone()
            if proposal is None:
                raise WorkflowError('invalid_transition', 'Нет предложения переноса, ожидающего решения')
            candidate = {**dict(row), 'start_date': proposal['start_date'], 'end_date': proposal['end_date']}
            if target == 'approved':
                conflicts = self._conflicts(db, candidate, replacing_request_id=request_id)
                if conflicts:
                    raise WorkflowError('conflict', 'Перенос блокируют конфликты', conflicts)
                db.execute('UPDATE requests SET start_date=?,end_date=? WHERE id=?',
                           (proposal['start_date'], proposal['end_date'], request_id))
            db.execute('''UPDATE reschedule_proposals SET status=?,decided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                decided_by=?,decision_reason=? WHERE id=?''', (target, actor, reason.strip(), proposal['id']))
            old_period = f"{row['start_date']} — {row['end_date']}"
            new_period = f"{proposal['start_date']} — {proposal['end_date']}"
            history_reason = f'{old_period} → {new_period}' if target == 'approved' else reason.strip()
            history_id = self._record(db, request_id, actor, 'approved', f'reschedule_{target}', history_reason)
            if target == 'approved':
                updated = {**dict(row), 'start_date': proposal['start_date'], 'end_date': proposal['end_date']}
                pending = db.execute("SELECT id FROM integration_events WHERE request_id=? AND operation='upsert' AND status='pending' ORDER BY rowid DESC LIMIT 1", (request_id,)).fetchone()
                if pending:
                    payload = json.dumps({'request_id': updated['id'], 'employee_id': updated['employee_id'],
                                          'start_date': updated['start_date'], 'end_date': updated['end_date']},
                                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                    db.execute('UPDATE integration_events SET payload=? WHERE id=?', (payload, pending['id']))
                else:
                    external = db.execute("SELECT external_id FROM integration_events WHERE request_id=? AND operation IN ('upsert','update') AND status='delivered' ORDER BY rowid DESC LIMIT 1", (request_id,)).fetchone()
                    self._enqueue(db, updated, history_id, 'update', external['external_id'] if external else None)
        return self.get(actor, request_id)

    def transition(self, actor, request_id, target, reason=''):
        with self._connection(write=True) as db:
            self._actor(db, actor)
            row = self._request(db, request_id)
            owner = actor == row['employee_id']
            allowed = {'submitted': {'draft'}, 'approved': {'submitted'}, 'rejected': {'submitted'},
                       'cancelled': {'draft', 'submitted', 'approved'}}
            if target not in allowed:
                raise WorkflowError('invalid_transition', 'Неизвестный переход')
            if target in {'submitted', 'cancelled'}:
                if not owner:
                    raise WorkflowError('forbidden', 'Подать или отменить заявку может только её автор')
            elif owner or not self._manager(db, actor, row['employee_id']):
                raise WorkflowError('forbidden', 'Нужен руководитель отдела; собственное согласование запрещено')
            if row['status'] not in allowed[target]:
                raise WorkflowError('invalid_transition', f"Переход {row['status']} → {target} запрещён")
            if not isinstance(reason, str) or (target == 'rejected' and not reason.strip()):
                raise WorkflowError('reason_required', 'Для отклонения требуется причина')
            if target == 'approved':
                if not db.execute('SELECT 1 FROM employees WHERE id=? AND active=1', (row['employee_id'],)).fetchone():
                    raise WorkflowError('inactive_employee', 'Сотрудник неактивен')
                conflicts = self._conflicts(db, row)
                if conflicts:
                    raise WorkflowError('conflict', 'Согласование блокируют конфликты', conflicts)
            db.execute('UPDATE requests SET status=? WHERE id=?', (target, request_id))
            history_id = self._record(db, request_id, actor, row['status'], target, reason.strip())
            updated = dict(row)
            updated['status'] = target
            if target == 'approved':
                self._enqueue(db, updated, history_id, 'upsert')
            elif target == 'cancelled' and row['status'] == 'approved':
                self._enqueue(db, updated, history_id, 'cancel')
        return self.get(actor, request_id)

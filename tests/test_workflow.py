import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from leaveops.service import LeaveOps, WorkflowError


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'demo.db'
        self.app = LeaveOps(self.path)
        self.app.seed(ROOT / 'data/demo.json')

    def request(self, actor='analyst-b', start='2026-10-15', end='2026-10-16'):
        row = self.app.create(actor, start, end)
        self.app.transition(actor, row['id'], 'submitted')
        return row['id']

    def error(self, code, function, *args):
        with self.assertRaises(WorkflowError) as context:
            function(*args)
        self.assertEqual(context.exception.code, code)
        return context.exception

    def test_lifecycle_persists_history_after_restart(self):
        rid = self.request()
        self.app.transition('manager', rid, 'approved')
        self.app = LeaveOps(self.path)
        row = self.app.get('analyst-b', rid)
        self.assertEqual(row['status'], 'approved')
        self.assertEqual([h['to_status'] for h in row['history']], ['draft', 'submitted', 'approved'])
        self.assertEqual(self.app.preview('manager', rid), [])
        self.app.transition('analyst-b', rid, 'cancelled', 'Изменились планы')
        self.assertEqual(self.app.get('manager', rid)['status'], 'cancelled')

    def test_balance_counts_accrual_and_completed_absence(self):
        balance = self.app.balance('analyst-a', 'analyst-a', '2026-10-18')
        self.assertEqual(balance['opening_days'], 5)
        self.assertEqual(balance['accrued_days'], 23.3)
        self.assertEqual(balance['used_days'], 3)
        self.assertEqual(balance['planned_days'], 0)
        self.assertEqual(balance['available_days'], 25.3)
        self.error('forbidden', self.app.balance, 'developer-a', 'analyst-a', '2026-10-18')

    def test_low_balance_blocks_approval_without_reserving_or_enqueuing(self):
        rid = self.request('developer-c', '2026-10-15', '2026-10-16')
        conflicts = self.app.preview('manager', rid)
        self.assertEqual([conflict['code'] for conflict in conflicts], ['balance'])
        self.assertEqual((conflicts[0]['available'], conflicts[0]['requested']), (1, 2))
        self.error('conflict', self.app.transition, 'manager', rid, 'approved')
        row = self.app.get('manager', rid)
        self.assertEqual(row['status'], 'submitted')
        self.assertEqual(row['integration'], [])

    def test_cancelled_approved_absence_releases_balance_forecast(self):
        rid = self.request('analyst-b', '2026-10-15', '2026-10-16')
        before = self.app.get('manager', rid)['balance_projection']
        self.assertEqual((before['request_days'], before['after_approval_days']), (2, 24.3))
        self.app.transition('manager', rid, 'approved')
        planned = self.app.balance('analyst-b', 'analyst-b', '2026-10-14')
        self.assertEqual(planned['planned_days'], 2)
        self.app.transition('analyst-b', rid, 'cancelled')
        released = self.app.balance('analyst-b', 'analyst-b', '2026-10-14')
        self.assertEqual(released['planned_days'], 0)
        self.assertEqual(released['available_days'], 26.3)

    def test_reschedule_keeps_approved_period_until_manager_accepts_and_coalesces_pending_create(self):
        rid = self.request('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('manager', rid, 'approved')
        proposed = self.app.propose_reschedule('analyst-b', rid, '2026-10-20', '2026-10-21', 'Сдвинул планы')
        self.assertEqual((proposed['status'], proposed['start_date'], proposed['reschedule']['status']),
                         ('approved', '2026-10-15', 'submitted'))
        self.assertEqual(proposed['reschedule_conflicts'], [])
        self.error('reschedule_pending', self.app.propose_reschedule, 'analyst-b', rid, '2026-10-22', '2026-10-23')
        accepted = self.app.decide_reschedule('manager', rid, 'approved')
        self.assertEqual((accepted['start_date'], accepted['end_date'], accepted['reschedule']['status']),
                         ('2026-10-20', '2026-10-21', 'approved'))
        self.assertEqual([event['operation'] for event in accepted['integration']], ['upsert'])
        with self.app._connection() as db:
            payload = json.loads(db.execute('SELECT payload FROM integration_events WHERE request_id=?', (rid,)).fetchone()[0])
        self.assertEqual((payload['start_date'], payload['end_date']), ('2026-10-20', '2026-10-21'))

    def test_reschedule_rejection_preserves_approved_period(self):
        rid = self.request('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('manager', rid, 'approved')
        self.app.propose_reschedule('analyst-b', rid, '2026-10-20', '2026-10-21')
        rejected = self.app.decide_reschedule('manager', rid, 'rejected', 'На эти даты нужен аналитик')
        self.assertEqual((rejected['status'], rejected['start_date'], rejected['end_date']),
                         ('approved', '2026-10-15', '2026-10-16'))
        self.assertEqual((rejected['reschedule']['status'], rejected['reschedule']['decision_reason']),
                         ('rejected', 'На эти даты нужен аналитик'))
        self.assertEqual([event['operation'] for event in rejected['integration']], ['upsert'])

    def test_reschedule_conflict_does_not_change_approved_period_or_queue(self):
        rid = self.request('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('manager', rid, 'approved')
        self.app.propose_reschedule('analyst-b', rid, '2026-10-14', '2026-10-16')
        error = self.error('conflict', self.app.decide_reschedule, 'manager', rid, 'approved')
        self.assertEqual(error.conflicts[0]['code'], 'coverage')
        row = self.app.get('manager', rid)
        self.assertEqual((row['start_date'], row['end_date'], row['reschedule']['status']),
                         ('2026-10-15', '2026-10-16', 'submitted'))
        self.assertEqual([event['operation'] for event in row['integration']], ['upsert'])

    def test_only_owner_can_propose_and_only_manager_can_decide_reschedule(self):
        rid = self.request('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('manager', rid, 'approved')
        self.error('forbidden', self.app.propose_reschedule, 'analyst-a', rid, '2026-10-20', '2026-10-21')
        self.error('unchanged_dates', self.app.propose_reschedule, 'analyst-b', rid, '2026-10-15', '2026-10-16')
        self.app.propose_reschedule('analyst-b', rid, '2026-10-20', '2026-10-21')
        self.error('forbidden', self.app.decide_reschedule, 'analyst-b', rid, 'approved')
        self.error('reason_required', self.app.decide_reschedule, 'manager', rid, 'rejected', ' ')

    def test_coverage_boundary_and_rollback(self):
        rid = self.request(start='2026-10-14')
        conflicts = self.app.preview('manager', rid)
        self.assertEqual([(c['date'], c['available'], c['required']) for c in conflicts], [('2026-10-14', 0, 1)])
        self.error('conflict', self.app.transition, 'manager', rid, 'approved')
        row = self.app.get('manager', rid)
        self.assertEqual(row['status'], 'submitted')
        self.assertEqual(len(row['history']), 2)

    def test_overlap_includes_end_date(self):
        rid = self.request('analyst-a', '2026-10-14', '2026-10-15')
        self.assertEqual(self.app.preview('manager', rid)[0]['code'], 'overlap')
        self.error('conflict', self.app.transition, 'manager', rid, 'approved')

    def test_developer_minimum(self):
        rid = self.request('developer-b', '2026-10-13', '2026-10-13')
        conflict = self.app.preview('manager', rid)[0]
        self.assertEqual((conflict['role'], conflict['available'], conflict['required']), ('developer', 1, 2))

    def test_weekends_ignore_coverage_but_not_overlap(self):
        for actor in ('analyst-a', 'analyst-b'):
            rid = self.request(actor, '2026-10-17', '2026-10-18')
            self.assertEqual(self.app.preview('manager', rid), [])
            self.app.transition('manager', rid, 'approved')
        duplicate = self.request('analyst-a', '2026-10-18', '2026-10-18')
        self.assertEqual(self.app.preview('manager', duplicate)[0]['code'], 'overlap')

    def test_manager_cannot_approve_own_request(self):
        rid = self.request('manager')
        self.error('forbidden', self.app.transition, 'manager', rid, 'approved')

    def test_owner_and_visibility_permissions(self):
        rid = self.app.create('analyst-b', '2026-10-15', '2026-10-16')['id']
        for action in ('submitted', 'cancelled', 'approved', 'rejected'):
            self.error('forbidden', self.app.transition, 'developer-a', rid, action)
        for method in (self.app.get, self.app.preview):
            self.error('forbidden', method, 'analyst-a', rid)
        self.assertNotIn(rid, [r['id'] for r in self.app.list_requests('analyst-a')])
        self.error('forbidden', self.app.create, 'unknown', '2026-10-15', '2026-10-16')

    def test_other_department_manager_is_forbidden(self):
        with self.app._connection(write=True) as db:
            db.execute("INSERT INTO employees VALUES ('other','Другой','other-team','manager',1)")
            db.execute("INSERT INTO departments VALUES ('other-team','other')")
        rid = self.request()
        self.error('forbidden', self.app.transition, 'other', rid, 'approved')

    def test_rejection_requires_reason_and_is_terminal(self):
        rid = self.request()
        self.error('reason_required', self.app.transition, 'manager', rid, 'rejected', '  ')
        self.app.transition('manager', rid, 'rejected', 'Изменить даты')
        self.error('invalid_transition', self.app.transition, 'analyst-b', rid, 'submitted')
        self.assertEqual(self.app.get('manager', rid)['history'][-1]['reason'], 'Изменить даты')

    def test_invalid_transitions_and_duplicates(self):
        rid = self.app.create('analyst-b', '2026-10-15', '2026-10-16')['id']
        self.error('invalid_transition', self.app.transition, 'manager', rid, 'approved')
        self.app.transition('analyst-b', rid, 'submitted')
        self.app.transition('manager', rid, 'approved')
        self.error('invalid_transition', self.app.transition, 'manager', rid, 'approved')
        self.assertEqual(len(self.app.get('manager', rid)['history']), 3)

    def test_dates_are_strict(self):
        for start, end in [('2026-10-16', '2026-10-15'), ('2026-02-30', '2026-03-01'),
                           ('20261015', '2026-10-16'), ('2026-10-15T00:00:00', '2026-10-16')]:
            with self.subTest(start=start):
                self.error('invalid_dates', self.app.create, 'analyst-a', start, end)

    def test_cancelled_approved_absence_releases_capacity(self):
        rid = self.request(start='2026-10-14', end='2026-10-14')
        self.app.transition('analyst-a', 'absence-1', 'cancelled')
        self.app.transition('manager', rid, 'approved')

    def test_pending_requests_do_not_reserve_capacity(self):
        a = self.request('analyst-a')
        b = self.request('analyst-b')
        self.assertEqual(self.app.preview('manager', a), [])
        self.assertEqual(self.app.preview('manager', b), [])
        self.app.transition('manager', a, 'approved')
        self.error('conflict', self.app.transition, 'manager', b, 'approved')

    def test_concurrent_approvals_cannot_both_pass(self):
        ids = [self.request('analyst-a'), self.request('analyst-b')]
        services = [LeaveOps(self.path), LeaveOps(self.path)]
        barrier = Barrier(2)

        def approve(pair):
            service, rid = pair
            barrier.wait(timeout=5)
            try:
                service.transition('manager', rid, 'approved')
                return 'approved'
            except WorkflowError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, zip(services, ids)))
        self.assertCountEqual(outcomes, ['approved', 'conflict'])
        self.assertCountEqual([self.app.get('manager', rid)['status'] for rid in ids], ['approved', 'submitted'])

    def test_seed_never_overwrites_work(self):
        rid = self.request()
        self.error('already_initialized', self.app.seed, ROOT / 'data/demo.json')
        self.assertEqual(self.app.get('analyst-b', rid)['status'], 'submitted')

    def test_cancel_draft_and_submitted_are_terminal(self):
        for submit in (False, True):
            rid = self.app.create('analyst-b', '2026-10-15', '2026-10-16')['id']
            if submit:
                self.app.transition('analyst-b', rid, 'submitted')
            self.app.transition('analyst-b', rid, 'cancelled')
            self.error('invalid_transition', self.app.transition, 'analyst-b', rid, 'submitted')

    def test_inactive_employee_cannot_be_approved(self):
        rid = self.request()
        with self.app._connection(write=True) as db:
            db.execute("UPDATE employees SET active=0 WHERE id='analyst-b'")
        self.error('inactive_employee', self.app.transition, 'manager', rid, 'approved')

    def test_cli_real_process(self):
        result = subprocess.run([sys.executable, '-m', 'leaveops', '--db', str(self.path),
                                 'list', '--actor', 'manager'], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 2)
        result = subprocess.run([sys.executable, '-m', 'leaveops', '--db', str(self.path),
                                 'show', '--actor', 'manager', 'missing'], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'not_found')


if __name__ == '__main__':
    unittest.main()

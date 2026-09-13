import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from leaveops.service import LeaveOps
from leaveops.web import make_server

ROOT = Path(__file__).resolve().parents[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'web.db'
        self.service = LeaveOps(self.path)
        self.service.seed(ROOT / 'data/demo.json')
        self.server = make_server(self.path, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, body=None, actor='manager', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {'X-Demo-Actor': actor}
        if body is not None:
            request_headers.update({'Content-Type': 'application/json', 'X-LeaveOps': 'local-demo'})
        request_headers.update(headers or {})
        connection.request('POST' if body is not None else 'GET', path,
                           body=json.dumps(body) if body is not None else None, headers=request_headers)
        response = connection.getresponse()
        status, content_type = response.status, response.getheader('Content-Type')
        payload = response.read()
        connection.close()
        return status, json.loads(payload) if content_type.startswith('application/json') else payload

    def create_submitted(self, start, end):
        status, row = self.call('/api/requests', {'start': start, 'end': end}, actor='analyst-b')
        self.assertEqual(status, 201)
        status, _ = self.call('/api/transition', {'id': row['id'], 'target': 'submitted'}, actor='analyst-b')
        self.assertEqual(status, 200)
        return row['id']

    def test_full_workflow_updates_calendar_and_history(self):
        rid = self.create_submitted('2026-10-15', '2026-10-16')
        status, row = self.call('/api/request?id='+rid)
        self.assertEqual((status, row['conflicts']), (200, []))
        status, _ = self.call('/api/transition', {'id': rid, 'target': 'approved'})
        self.assertEqual(status, 200)
        status, delivered = self.call('/api/sync', {})
        self.assertEqual((status, delivered[0]['status']), (200, 'delivered'))
        _, calendar = self.call('/api/calendar?start=2026-10-12&end=2026-10-18')
        self.assertTrue(any(a['id']==rid and a['status']=='approved' for a in calendar['absences']))
        self.assertEqual(next(c['available'] for c in calendar['coverage'] if c['role']=='analyst' and c['date']=='2026-10-15'), 1)
        status, _ = self.call('/api/transition', {'id': rid, 'target': 'cancelled'}, actor='analyst-b')
        self.assertEqual(status, 200)
        _, calendar = self.call('/api/calendar?start=2026-10-12&end=2026-10-18')
        self.assertFalse(any(a['id']==rid for a in calendar['absences']))
        _, row = self.call('/api/request?id='+rid)
        self.assertEqual([h['to_status'] for h in row['history']], ['draft','submitted','approved','cancelled'])
        self.assertEqual(row['integration'][-1]['status'], 'pending')

    def test_reschedule_requires_second_manager_decision_and_updates_mock_event(self):
        rid = self.create_submitted('2026-10-15', '2026-10-16')
        self.assertEqual(self.call('/api/transition', {'id': rid, 'target': 'approved'})[0], 200)
        self.assertEqual(self.call('/api/sync', {})[0], 200)
        status, proposed = self.call('/api/reschedule', {'id': rid, 'start': '2026-10-20', 'end': '2026-10-21'}, actor='analyst-b')
        self.assertEqual((status, proposed['start_date'], proposed['reschedule']['status']), (200, '2026-10-15', 'submitted'))
        status, detail = self.call('/api/request?id='+rid)
        self.assertEqual((status, detail['reschedule']['start_date'], detail['reschedule_conflicts']), (200, '2026-10-20', []))
        status, changed = self.call('/api/reschedule/decision', {'id': rid, 'target': 'approved'})
        self.assertEqual((status, changed['start_date'], changed['integration'][-1]['operation']), (200, '2026-10-20', 'update'))
        self.assertEqual(self.call('/api/sync', {})[0], 200)
        _, changed = self.call('/api/request?id='+rid)
        self.assertEqual(changed['integration'][-1]['status'], 'delivered')

    def test_reschedule_http_keeps_old_period_when_conflicted_or_rejected(self):
        rid = self.create_submitted('2026-10-15', '2026-10-16')
        self.call('/api/transition', {'id': rid, 'target': 'approved'})
        self.assertEqual(self.call('/api/reschedule', {'id': rid, 'start': '2026-10-14', 'end': '2026-10-16'}, actor='analyst-b')[0], 200)
        status, result = self.call('/api/reschedule/decision', {'id': rid, 'target': 'approved'})
        self.assertEqual((status, result['error']), (409, 'conflict'))
        self.assertEqual(self.call('/api/request?id='+rid)[1]['start_date'], '2026-10-15')
        status, rejected = self.call('/api/reschedule/decision', {'id': rid, 'target': 'rejected', 'reason': 'Нужен аналитик'})
        self.assertEqual((status, rejected['start_date'], rejected['reschedule']['status']), (200, '2026-10-15', 'rejected'))

    def test_conflict_cannot_be_bypassed_via_http(self):
        rid = self.create_submitted('2026-10-14', '2026-10-16')
        status, result = self.call('/api/transition', {'id': rid, 'target': 'approved'})
        self.assertEqual(status, 409)
        self.assertEqual(result['conflicts'][0]['available'], 0)
        _, row = self.call('/api/request?id='+rid)
        self.assertEqual(row['status'], 'submitted')

    def test_team_calendar_shares_availability_not_private_history(self):
        rid = self.create_submitted('2026-10-15', '2026-10-16')
        status, calendar = self.call('/api/calendar?start=2026-10-12&end=2026-10-18', actor='analyst-a')
        self.assertEqual(status, 200)
        self.assertTrue(any(a['id']==rid for a in calendar['absences']))
        self.assertNotIn('history', calendar['absences'][0])
        self.assertEqual(self.call('/api/request?id='+rid, actor='analyst-a')[0], 403)
        self.assertEqual(self.call('/api/calendar?start=2026-10-12&end=2026-10-18', actor='unknown')[0], 403)

    def test_balance_and_hr_summary_keep_role_boundaries(self):
        status, balance = self.call('/api/balance?id=analyst-a&as_of=2026-10-18', actor='analyst-a')
        self.assertEqual((status, balance['used_days'], balance['available_days']), (200, 3, 25.3))
        self.assertEqual(self.call('/api/balance?id=analyst-a&as_of=2026-10-18', actor='developer-a')[0], 403)
        status, summary = self.call('/api/hr-summary?as_of=2026-10-18', actor='manager')
        self.assertEqual((status, summary['as_of'], summary['low_balance_employee_ids']),
                         (200, '2026-10-18', ['developer-c']))
        self.assertEqual(self.call('/api/hr-summary?as_of=2026-10-18', actor='analyst-a')[0], 403)
        self.assertEqual(self.call('/api/balance?id=analyst-a&as_of=bad', actor='analyst-a')[0], 400)

    def test_wrong_origin_host_and_content_type_rejected(self):
        body = {'start':'2026-10-15','end':'2026-10-16'}
        for headers in ({'Origin':'https://example.com'}, {'Host':'evil.example'},
                        {'Content-Type':'text/plain'}, {'X-LeaveOps':''}):
            self.assertEqual(self.call('/api/requests', body, headers=headers)[0], 403)

    def test_validation_and_static_allowlist(self):
        for body in ([], {'start':'bad','end':'2026-10-16'}):
            self.assertEqual(self.call('/api/requests', body)[0], 400)
        self.assertEqual(self.call('/api/transition', {'id': [], 'target': []})[0], 400)
        self.assertEqual(self.call('/api/calendar?start=2026-01-01&end=2026-12-31')[0], 400)
        self.assertEqual(self.call('/../service.py')[0], 404)
        self.assertEqual(self.call('/api/config')[1]['integration_name'], 'Mock Bitrix24')
        for asset in ('/', '/app.js', '/style.css'):
            self.assertEqual(self.call(asset)[0], 200)

    def test_manager_cannot_approve_self_over_http(self):
        _, row = self.call('/api/requests', {'start':'2026-10-15','end':'2026-10-16'})
        self.call('/api/transition', {'id':row['id'],'target':'submitted'})
        self.assertEqual(self.call('/api/transition', {'id':row['id'],'target':'approved'})[0], 403)

    def test_only_manager_can_run_sync(self):
        self.assertEqual(self.call('/api/sync', {}, actor='analyst-a')[0], 403)


if __name__ == '__main__':
    unittest.main()

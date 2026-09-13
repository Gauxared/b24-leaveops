import json
import tempfile
import unittest
from pathlib import Path

from leaveops.integration import (Bitrix24CalendarAdapter, Bitrix24Error, IntegrationWorker,
                                  MappedBitrix24CalendarAdapter, mapping_preflight,
                                  require_mapping_ready_for_setup)
from leaveops.service import LeaveOps

ROOT = Path(__file__).resolve().parents[1]


class FakeBitrix24(Bitrix24CalendarAdapter):
    def __init__(self, lose_add_response=False):
        super().__init__('https://example.bitrix24.ru/rest/1/test-token/')
        self.events = []
        self.sections_data = []
        self.lose_add_response = lose_add_response
        self.add_lost = False

    def _call(self, method, params=None):
        if method == 'profile':
            return {'ID': '1'}
        if method == 'scope':
            return ['calendar']
        if method == 'methods':
            return ['calendar.section.get', 'calendar.section.add', 'calendar.event.get',
                    'calendar.event.add', 'calendar.event.update', 'calendar.event.delete']
        if method == 'calendar.section.get':
            return self.sections_data
        if method == 'calendar.section.add':
            section = {'ID': '71', 'NAME': params['name']}
            self.sections_data.append(section)
            return 71
        if method == 'calendar.event.get':
            return list(self.events)
        if method == 'calendar.event.add':
            event_id = str(100 + len(self.events))
            self.events.append({'ID': event_id, 'DESCRIPTION': params['description']})
            if self.lose_add_response and not self.add_lost:
                self.add_lost = True
                raise Bitrix24Error('Имитирована потеря ответа')
            return int(event_id)
        if method == 'calendar.event.update':
            event = next(event for event in self.events if event['ID'] == str(params['id']))
            event['DESCRIPTION'] = params['description']
            event['FROM'] = params['from']
            event['TO'] = params['to']
            return int(params['id'])
        if method == 'calendar.event.delete':
            self.events = [event for event in self.events if event['ID'] != str(params['id'])]
            return True
        raise AssertionError(method)


class MappingAdapterStub:
    sections = {}
    deliveries = []

    def __init__(self, webhook_url, section_id=None, timeout=15, owner_id=None):
        self.owner_id = owner_id
        self.section_id = section_id

    def probe(self):
        return {'connected': True}

    def ensure_section(self, name):
        created = self.owner_id not in self.sections
        self.sections.setdefault(self.owner_id, self.owner_id + 500)
        self.section_id = self.sections[self.owner_id]
        return {'section_id': self.section_id, 'created': created}

    def deliver(self, event):
        payload = json.loads(event['payload'])
        self.deliveries.append((payload['employee_id'], self.owner_id, self.section_id))
        return str(self.owner_id * 1000)


class Bitrix24AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app_path = Path(self.temp.name) / 'leaveops.db'
        self.app = LeaveOps(self.app_path)
        self.app.seed(ROOT / 'data/demo.json')

    def approve(self):
        row = self.app.create('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('analyst-b', row['id'], 'submitted')
        return self.app.transition('manager', row['id'], 'approved')

    def test_rejects_non_webhook_urls_without_network(self):
        for url in ('', 'http://example.bitrix24.ru/rest/1/token/',
                    'https://example.bitrix24.ru/', 'https://example.bitrix24.ru/rest/1/token/?x=1'):
            with self.subTest(url=url), self.assertRaises(Bitrix24Error):
                Bitrix24CalendarAdapter(url)

    def test_probe_and_section_setup_are_idempotent(self):
        adapter = FakeBitrix24()
        probe = adapter.probe()
        self.assertTrue(probe['connected'])
        self.assertTrue(all(probe['required_methods'].values()))
        self.assertEqual(adapter.ensure_section(), {'section_id': 71, 'created': True})
        self.assertEqual(adapter.ensure_section(), {'section_id': 71, 'created': False})
        self.assertEqual(len(adapter.sections_data), 1)

    def test_real_adapter_contract_recovers_lost_add_response(self):
        approved = self.approve()
        adapter = FakeBitrix24(lose_add_response=True)
        adapter.ensure_section()
        worker = IntegrationWorker(self.app_path, adapter)
        self.assertEqual(worker.run_once()['status'], 'pending')
        self.assertEqual(len(adapter.events), 1)
        self.assertEqual(worker.run_once()['status'], 'delivered')
        self.assertEqual(len(adapter.events), 1)
        event = self.app.get('manager', approved['id'])['integration'][0]
        self.assertEqual((event['status'], event['attempts'], event['external_id']), ('delivered', 2, '100'))

    def test_cancel_deletes_the_marked_event(self):
        approved = self.approve()
        adapter = FakeBitrix24()
        adapter.ensure_section()
        worker = IntegrationWorker(self.app_path, adapter)
        worker.drain()
        self.assertEqual(len(adapter.events), 1)
        self.app.transition('analyst-b', approved['id'], 'cancelled', 'Отмена')
        worker.drain()
        self.assertEqual(adapter.events, [])
        events = self.app.get('manager', approved['id'])['integration']
        self.assertEqual([event['external_id'] for event in events], ['100', '100'])

    def test_real_adapter_contract_updates_existing_marked_event(self):
        adapter = FakeBitrix24()
        adapter.ensure_section()
        event = {'operation': 'upsert', 'payload': json.dumps({
            'request_id': 'request-update', 'employee_id': 'analyst-b',
            'start_date': '2026-10-15', 'end_date': '2026-10-16'})}
        external_id = adapter.deliver(event)
        updated = {**event, 'operation': 'update', 'external_id': external_id,
                   'payload': json.dumps({'request_id': 'request-update', 'employee_id': 'analyst-b',
                                          'start_date': '2026-10-20', 'end_date': '2026-10-21'})}
        self.assertEqual(adapter.deliver(updated), external_id)
        self.assertEqual((len(adapter.events), adapter.events[0]['FROM'], adapter.events[0]['TO']),
                         (1, '2026-10-20', '2026-10-21'))

    def test_mapping_creates_distinct_personal_sections_and_routes(self):
        MappingAdapterStub.sections = {}
        MappingAdapterStub.deliveries = []
        adapter = MappedBitrix24CalendarAdapter(
            'https://example.bitrix24.ru/rest/1/test-token/',
            {'manager': {'owner_id': 1}, 'analyst-b': {'owner_id': 12}},
            adapter_factory=MappingAdapterStub)
        configured = adapter.ensure_sections()
        self.assertEqual(configured['manager']['section_id'], 501)
        self.assertEqual(configured['analyst-b']['section_id'], 512)
        event = {'operation': 'upsert', 'payload': json.dumps({
            'request_id': 'request-1', 'employee_id': 'analyst-b',
            'start_date': '2026-10-15', 'end_date': '2026-10-16'})}
        self.assertEqual(adapter.deliver(event), '12000')
        self.assertEqual(MappingAdapterStub.deliveries, [('analyst-b', 12, 512)])

    def test_mapping_rejects_duplicate_owner_and_unmapped_employee(self):
        with self.assertRaises(Bitrix24Error):
            MappedBitrix24CalendarAdapter('https://example.bitrix24.ru/rest/1/test-token/', {
                'manager': {'owner_id': 1}, 'analyst-b': {'owner_id': 1}})
        adapter = MappedBitrix24CalendarAdapter(
            'https://example.bitrix24.ru/rest/1/test-token/',
            {'manager': {'owner_id': 1, 'section_id': 501}}, adapter_factory=MappingAdapterStub)
        with self.assertRaises(Bitrix24Error):
            adapter.deliver({'operation': 'upsert', 'payload': json.dumps({
                'request_id': 'request-2', 'employee_id': 'analyst-b',
                'start_date': '2026-10-15', 'end_date': '2026-10-16'})})

    def test_mapping_preflight_reports_partial_safe_map_without_portal_access(self):
        mapping = Path(self.temp.name) / 'mapping.json'
        mapping.write_text(json.dumps({'schema_version': 1, 'employees': {
            'manager': {'owner_id': 1}, 'analyst-b': {'owner_id': 12, 'section_id': 6}}}), encoding='utf-8')
        report = mapping_preflight(mapping, self.app.active_employee_ids())
        self.assertFalse(report['writes_to_bitrix24'])
        self.assertTrue(report['ready_for_section_setup'])
        self.assertFalse(report['complete_for_all_synthetic_employees'])
        self.assertEqual(report['missing_section_employee_ids'], ['manager'])
        self.assertEqual(report['unmapped_employee_ids'], ['analyst-a', 'developer-a', 'developer-b', 'developer-c'])

    def test_mapping_preflight_blocks_unknown_and_duplicate_users_before_setup(self):
        mapping = Path(self.temp.name) / 'mapping.json'
        mapping.write_text(json.dumps({'schema_version': 1, 'employees': {
            'manager': {'owner_id': 1}, 'analyst-b': {'owner_id': 1}, 'not-an-employee': {'owner_id': 12}}}),
                           encoding='utf-8')
        report = mapping_preflight(mapping, self.app.active_employee_ids())
        self.assertFalse(report['ready_for_section_setup'])
        self.assertEqual(report['unknown_mapping_employee_ids'], ['not-an-employee'])
        self.assertEqual(report['duplicate_owner_ids'], {'1': ['analyst-b', 'manager']})
        with self.assertRaises(Bitrix24Error):
            require_mapping_ready_for_setup(mapping, self.app.active_employee_ids())


if __name__ == '__main__':
    unittest.main()

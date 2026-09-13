import argparse
import json
import os
import sys
from pathlib import Path

from .service import LeaveOps, WorkflowError
from .integration import (Bitrix24Error, IntegrationWorker, MappedBitrix24CalendarAdapter,
                          MockBitrix24, bitrix_adapter_from_environment, mapping_preflight,
                          require_mapping_ready_for_setup)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='LeaveOps — локальное демо заявок')
    parser.add_argument('--db', default='runtime/leaveops.db')
    parser.add_argument('--external-db', default='runtime/mock-bitrix24.db')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('sync')
    commands.add_parser('mock-list')
    commands.add_parser('b24-check')
    mapping_check = commands.add_parser('b24-mapping-check')
    mapping_check.add_argument('--mapping', default=os.environ.get('B24_MAPPING_FILE'),
                               help='Локальный JSON-файл карты; по умолчанию B24_MAPPING_FILE')
    commands.add_parser('b24-setup')
    commands.add_parser('b24-sync')
    for name in ('create', 'list', 'show', 'preview', 'submit', 'approve', 'reject', 'cancel'):
        sub = commands.add_parser(name)
        sub.add_argument('--actor', required=True, help='Синтетический ID; это не аутентификация')
        if name == 'create':
            sub.add_argument('--start', required=True)
            sub.add_argument('--end', required=True)
        elif name != 'list':
            sub.add_argument('request_id')
        if name in ('reject', 'cancel'):
            sub.add_argument('--reason', default='')
    args = parser.parse_args()
    service = LeaveOps(args.db)
    try:
        if args.command == 'init':
            service.seed(Path(__file__).resolve().parents[1] / 'data' / 'demo.json')
            result = {'initialized': True, 'database': args.db}
        elif args.command == 'sync':
            result = IntegrationWorker(args.db, MockBitrix24(args.external_db)).drain()
        elif args.command == 'mock-list':
            result = MockBitrix24(args.external_db).list_absences()
        elif args.command == 'b24-mapping-check':
            if not args.mapping:
                raise Bitrix24Error('Укажите --mapping или B24_MAPPING_FILE')
            result = mapping_preflight(args.mapping, service.active_employee_ids())
        elif args.command in {'b24-check', 'b24-setup', 'b24-sync'}:
            adapter = bitrix_adapter_from_environment()
            if args.command == 'b24-check':
                result = adapter.probe()
            elif args.command == 'b24-setup':
                if isinstance(adapter, MappedBitrix24CalendarAdapter):
                    require_mapping_ready_for_setup(os.environ['B24_MAPPING_FILE'], service.active_employee_ids())
                    result = adapter.ensure_sections()
                    mapping_path = Path(os.environ['B24_MAPPING_FILE'])
                    saved = {'schema_version': 1, 'employees': {
                        employee_id: {'owner_id': target['owner_id'], 'section_id': target['section_id']}
                        for employee_id, target in adapter.mappings.items()
                    }}
                    temporary = mapping_path.with_suffix(mapping_path.suffix + '.tmp')
                    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    temporary.replace(mapping_path)
                else:
                    result = {'owner_id': adapter.owner_id, **adapter.ensure_section()}
            else:
                result = IntegrationWorker(args.db, adapter).drain()
        elif args.command == 'create':
            result = service.create(args.actor, args.start, args.end)
        elif args.command == 'list':
            result = service.list_requests(args.actor)
        elif args.command in ('show', 'preview'):
            result = getattr(service, 'get' if args.command == 'show' else 'preview')(args.actor, args.request_id)
        else:
            target = {'submit': 'submitted', 'approve': 'approved', 'reject': 'rejected', 'cancel': 'cancelled'}[args.command]
            result = service.transition(args.actor, args.request_id, target, getattr(args, 'reason', ''))
    except (WorkflowError, Bitrix24Error) as error:
        code = error.code if isinstance(error, WorkflowError) else 'bitrix24_error'
        conflicts = error.conflicts if isinstance(error, WorkflowError) else []
        print(json.dumps({'error': code, 'message': str(error), 'conflicts': conflicts}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

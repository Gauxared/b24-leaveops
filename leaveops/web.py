"""Loopback-only demo HTTP transport. Not a production web server."""

import argparse
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import LeaveOps, WorkflowError
from .integration import IntegrationWorker, MockBitrix24, bitrix_adapter_from_environment

STATIC = Path(__file__).parent / 'static'


def make_server(database, port=8765, external_database=None, adapter=None, integration_name='Mock Bitrix24'):
    service = LeaveOps(database)
    external_database = external_database or str(Path(database).with_name('mock-bitrix24.db'))
    worker = IntegrationWorker(database, adapter or MockBitrix24(external_database))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self, write=False):
            try:
                port = self.server.server_port
                hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
                if self.headers.get('Host') not in hosts:
                    return self.send(403, {'message': 'Допустим только локальный адрес'})
                if self.headers.get('Origin') not in (None, *(f'http://{host}' for host in hosts)):
                    return self.send(403, {'message': 'Запрос с другого сайта запрещён'})
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                actor = self.headers.get('X-Demo-Actor', '')
                if write:
                    if self.headers.get('Content-Type') != 'application/json' or self.headers.get('X-LeaveOps') != 'local-demo':
                        return self.send(403, {'message': 'Требуется локальный JSON-запрос'})
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 8192:
                        return self.send(400, {'message': 'Некорректный размер запроса'})
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict) or not all(isinstance(k, str) for k in data):
                        raise ValueError()
                    if url.path == '/api/requests':
                        return self.send(201, service.create(actor, data.get('start'), data.get('end')))
                    if url.path == '/api/transition':
                        if not all(isinstance(data.get(k), str) for k in ('id', 'target')):
                            raise ValueError()
                        return self.send(200, service.transition(actor, data['id'], data['target'], data.get('reason', '')))
                    if url.path == '/api/reschedule':
                        if not all(isinstance(data.get(k), str) for k in ('id', 'start', 'end')):
                            raise ValueError()
                        return self.send(200, service.propose_reschedule(actor, data['id'], data['start'], data['end'],
                                                                          data.get('reason', '')))
                    if url.path == '/api/reschedule/decision':
                        if not all(isinstance(data.get(k), str) for k in ('id', 'target')):
                            raise ValueError()
                        return self.send(200, service.decide_reschedule(actor, data['id'], data['target'], data.get('reason', '')))
                    if url.path == '/api/sync':
                        if not service.is_manager(actor):
                            raise WorkflowError('forbidden', 'Синхронизацию запускает руководитель')
                        return self.send(200, worker.drain())
                else:
                    files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                    if url.path in files:
                        filename, mime = files[url.path]
                        return self.send(200, (STATIC / filename).read_bytes(), mime)
                    if url.path == '/api/actors':
                        return self.send(200, service.demo_actors())
                    if url.path == '/api/config':
                        return self.send(200, {'integration_name': integration_name})
                    if url.path == '/api/calendar':
                        return self.send(200, service.team_calendar(actor, query.get('start', [''])[0], query.get('end', [''])[0]))
                    if url.path == '/api/balance':
                        return self.send(200, service.balance(actor, query.get('id', [''])[0], query.get('as_of', [''])[0]))
                    if url.path == '/api/hr-summary':
                        return self.send(200, service.hr_summary(actor, query.get('as_of', [''])[0]))
                    if url.path == '/api/requests':
                        return self.send(200, service.list_requests(actor))
                    if url.path == '/api/request':
                        rid = query.get('id', [''])[0]
                        result = service.get(actor, rid)
                        result['conflicts'] = service.preview(actor, rid) if result['status'] in ('draft', 'submitted') else []
                        return self.send(200, result)
                self.send(404, {'message': 'Страница не найдена'})
            except WorkflowError as error:
                code = {'forbidden': 403, 'not_found': 404, 'conflict': 409, 'invalid_transition': 409}.get(error.code, 400)
                self.send(code, {'error': error.code, 'message': str(error), 'conflicts': error.conflicts})
            except (ValueError, TypeError, UnicodeError):
                self.send(400, {'message': 'Некорректный формат запроса'})
            except sqlite3.OperationalError:
                self.send(503, {'message': 'Хранилище временно недоступно. Повторите попытку.'})

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(write=True)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='LeaveOps — локальный веб-интерфейс')
    parser.add_argument('--db', default='runtime/leaveops.db')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--external-db', default='runtime/mock-bitrix24.db')
    parser.add_argument('--integration-provider', choices=('mock', 'bitrix24'), default='mock')
    args = parser.parse_args()
    adapter = None
    integration_name = 'Mock Bitrix24'
    if args.integration_provider == 'bitrix24':
        adapter = bitrix_adapter_from_environment()
        integration_name = 'Личные календари Bitrix24'
    server = make_server(args.db, args.port, args.external_db, adapter, integration_name)
    print(f'LeaveOps: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

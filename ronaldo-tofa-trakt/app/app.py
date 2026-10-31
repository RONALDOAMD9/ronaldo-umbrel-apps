#!/usr/bin/env python3
"""Local setup UI, device pairing, durable history and WebSocket live status."""
import base64
import datetime as dt
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tarfile
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlencode

from sync import Bridge, HttpError, iso, request, save
from live import Live

DATA = Path(os.environ.get('DATA_DIR', '/data'))
LOCK = threading.RLock()
WAKE = threading.Event()
REVISION = 0
STATE = {'websocket': False, 'playing': False, 'title': None, 'progress': None,
         'ends_at': None, 'last_sync': None, 'history_error': None, 'live_error': None}
PAIRING = {}
SESSION = secrets.token_urlsafe(32)
VERSION = '1.0.2'


def config():
    return json.loads((DATA / 'config.json').read_text())


def ready():
    return all((DATA / (name + '-tokens.json')).exists() for name in ('tofa', 'trakt'))


def publish(**values):
    with LOCK:
        STATE.update(values)


def error_message(exc):
    if isinstance(exc, HttpError):
        return f'HTTP {exc.status}: confira as conexões e autorizações.'
    if isinstance(exc, (OSError, RuntimeError)):
        return 'Falha de conexão; nova tentativa será feita automaticamente.'
    return str(exc)[:180]


def history_worker():
    while True:
        try:
            with LOCK:
                if ready():
                    b = Bridge(DATA)
                    try:
                        b.run(True)
                        STATE.update(last_sync=iso(dt.datetime.now(dt.timezone.utc)), history_error=None)
                    finally:
                        b.db.close()
        except Exception as exc:
            publish(history_error=error_message(exc))
        WAKE.wait(60)
        WAKE.clear()


def live_worker():
    from websockets.sync.client import connect
    delay = 1
    while True:
        with LOCK:
            enabled = ready()
            revision = REVISION
        if not enabled:
            time.sleep(2)
            continue
        controller = None
        b = None
        connected_at = time.monotonic()
        try:
            with LOCK:
                b = Bridge(DATA)
                me = b.tofa('/api/v1/users/me')
                target = b.config['tofa_url'].rstrip('/')
                token = b.tokens('tofa')
                controller = Live(b, me['id'], publish, profile_id=b.config.get('profile_id'))
            socket_url = ('wss://' if target.startswith('https://') else 'ws://') + target.split('://', 1)[1] + '/api/v1/ws'
            with connect(socket_url, open_timeout=15, ping_interval=20, ping_timeout=20, max_queue=256, proxy=None) as ws:
                ws.send(json.dumps({'type': 'auth', 'token': token}))
                if json.loads(ws.recv(timeout=15)).get('type') != 'auth_ok':
                    raise ValueError('O Tofa recusou a conexão de eventos; reconecte sua conta.')
                publish(websocket=True, live_error=None)
                with LOCK:
                    try:
                        controller.snapshot()
                    except HttpError as exc:
                        if exc.status != 403:
                            raise
                        # Ordinary Tofa users cannot fetch the admin snapshot;
                        # their own subsequent StreamStarted events still work.
                last_snapshot = time.monotonic()
                while True:
                    if revision != REVISION:
                        break
                    try:
                        event = json.loads(ws.recv(timeout=5))
                        with LOCK:
                            try:
                                controller.event(event)
                                if event.get('type') == 'StreamStopped':
                                    WAKE.set()
                            except Exception as exc:
                                STATE['live_error'] = error_message(exc)
                    except TimeoutError:
                        pass
                    with LOCK:
                        controller.tick()
                    # Reconcile disconnected sessions; never replace state with
                    # this lean snapshot, which lacks title and progress.
                    if time.monotonic() - last_snapshot > 180 and me.get('is_admin'):
                        with LOCK:
                            ids = {s['session_id'] for s in b.tofa('/api/v1/system/playback/sessions')}
                            if controller.active and controller.active['sid'] not in ids:
                                controller.event({'type': 'StreamStopped', 'session_id': controller.active['sid']})
                        last_snapshot = time.monotonic()
        except Exception as exc:
            publish(live_error=error_message(exc))
        finally:
            publish(websocket=False)
            if controller:
                try:
                    with LOCK:
                        controller.pause()
                except Exception:
                    pass
            if b:
                b.db.close()
        if time.monotonic() - connected_at > 60:
            delay = 1
        time.sleep(delay)
        delay = min(delay * 2, 30)


def start_pairing(service):
    with LOCK:
        c = config()
        if service == 'tofa':
            status = request(c['tofa_url'].rstrip('/') + '/api/v1/auth/status')
            if not status.get('claimed'):
                raise ValueError('Vincule primeiro seu servidor à conta Tofa.')
            if status.get('connect_url', '').rstrip('/') != 'https://api.tofa.tv':
                raise ValueError('O servidor indicou um serviço de autenticação inesperado.')
            c['server_id'] = status['server_id']
            save(DATA / 'config.json', c)
            code = request('https://api.tofa.tv/device/code', body={
                'server_id': c['server_id'], 'client_name': 'Tofa para Trakt', 'client_type': 'script'})
            visible = {'code': code['user_code'], 'url': code['verification_uri_complete']}
        else:
            if not c.get('trakt_client_id'):
                raise ValueError('Informe o Client ID do seu aplicativo Trakt.')
            code = request('https://auth.trakt.tv/oauth/device/code', body={'client_id': c['trakt_client_id']})
            visible = {'code': code['user_code'], 'url': code['verification_url']}
        ticket = secrets.token_hex(16)
        PAIRING[service] = dict(visible, status='pending', expires_at=time.time() + code['expires_in'], ticket=ticket)
    threading.Thread(target=poll_pairing, args=(service, code, c, ticket), daemon=True).start()


def poll_pairing(service, code, c, ticket):
    global REVISION
    deadline = time.monotonic() + code['expires_in']
    interval = max(5, int(code['interval']))
    while time.monotonic() < deadline:
        time.sleep(interval)
        with LOCK:
            if PAIRING.get(service, {}).get('ticket') != ticket:
                return
            try:
                if service == 'tofa':
                    tokens = request('https://api.tofa.tv/device/token', form=True, body={
                        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': code['device_code']})
                else:
                    tokens = request('https://auth.trakt.tv/oauth/device/token', body={
                        'code': code['device_code'], 'client_id': c['trakt_client_id']})
                if not tokens.get('access_token') or not tokens.get('refresh_token'):
                    raise ValueError('Resposta de autorização incompleta')
                tokens['obtained_at'] = time.time()
                save(DATA / (service + '-tokens.json'), tokens)
                PAIRING[service]['status'] = 'connected'
                REVISION += 1
                WAKE.set()
                return
            except HttpError as exc:
                if exc.status == 429:
                    interval += 5
                elif exc.status != 400:
                    PAIRING[service].update(status='error', error=f'Autorização recusada ou expirada (HTTP {exc.status}).')
                    return
            except Exception as exc:
                PAIRING[service].update(status='error', error=error_message(exc))
                return
    with LOCK:
        if PAIRING.get(service, {}).get('ticket') == ticket:
            PAIRING[service]['status'] = 'expired'


def make_backup():
    with LOCK:
        # SQLite's backup API produces a consistent DB even with WAL enabled.
        memory = sqlite3.connect(':memory:')
        source = sqlite3.connect(DATA / 'state.sqlite')
        source.backup(memory)
        source.close()
        db_bytes = memory.serialize()
        memory.close()
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as tar:
            for name in ('config.json', 'tofa-tokens.json', 'trakt-tokens.json', 'state.sqlite'):
                p = DATA / name
                if name != 'state.sqlite' and not p.exists():
                    continue
                content = db_bytes if name == 'state.sqlite' else p.read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(content), 0o600
                tar.addfile(info, io.BytesIO(content))
        return out.getvalue()


def restore_backup(raw):
    global REVISION
    if len(raw) > 1_500_000:
        raise ValueError('Backup maior que 1,5 MB')
    allowed = {'config.json', 'tofa-tokens.json', 'trakt-tokens.json', 'state.sqlite'}
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        for member in tar:
            if member.name not in allowed or not member.isfile() or member.size > 5_000_000 or member.name in contents:
                raise ValueError('Formato de backup inválido')
            contents[member.name] = tar.extractfile(member).read()
    if 'config.json' not in contents or 'state.sqlite' not in contents:
        raise ValueError('Backup incompleto')
    c = json.loads(contents['config.json'])
    validate_config(c)
    for name in ('tofa-tokens.json', 'trakt-tokens.json'):
        if name in contents:
            token = json.loads(contents[name])
            if not all(token.get(k) for k in ('access_token', 'refresh_token', 'obtained_at')):
                raise ValueError('Autorização incompleta no backup')
    # Validate DB before making any live change.
    check = sqlite3.connect(':memory:')
    check.deserialize(contents['state.sqlite'])
    if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Banco do backup inválido')
    check.close()
    with LOCK:
        for name, data in contents.items():
            target = DATA / name
            temp = DATA / (name + '.restore')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.replace(temp, target)
        # A backup without credentials must not keep unrelated old credentials.
        for name in ('tofa-tokens.json', 'trakt-tokens.json'):
            if name not in contents:
                (DATA / name).unlink(missing_ok=True)
        PAIRING.clear()
        REVISION += 1
        WAKE.set()


def validate_config(c):
    parts = urlsplit(c['tofa_url'])
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Use o endereço direto do Tofa, como http://umbrel.local:33333, sem senha ou caminho.')
    if not isinstance(c.get('trakt_client_id', ''), str) or len(c.get('trakt_client_id', '')) > 200:
        raise ValueError('Client ID inválido')
    dt.datetime.fromisoformat(c['since'].replace('Z', '+00:00'))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # URLs, authorization codes and request bodies never enter logs.

    def send(self, status, body, content_type='application/json', headers=None):
        raw = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def csrf(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except cookies.CookieError:
            return False
        return (jar.get('tofa_trakt') is not None and
                hmac.compare_digest(jar['tofa_trakt'].value, SESSION) and
                hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), SESSION))

    def do_GET(self):
        if self.path == '/':
            page = (Path(__file__).parent / 'index.html').read_text().replace('__CSRF__', SESSION)
            self.send(200, page.encode(), 'text/html; charset=utf-8', {'Set-Cookie': f'tofa_trakt={SESSION}; HttpOnly; SameSite=Strict; Path=/'})
        elif self.path == '/api/status':
            with LOCK:
                c = config()
                counts = {}
                db = sqlite3.connect(DATA / 'state.sqlite')
                try:
                    counts = dict(db.execute('SELECT status,COUNT(*) FROM sent GROUP BY status'))
                except sqlite3.OperationalError:
                    pass
                finally:
                    db.close()
                self.send(200, dict(STATE, version=VERSION,
                    tofa_connected=(DATA / 'tofa-tokens.json').exists(), trakt_connected=(DATA / 'trakt-tokens.json').exists(),
                    settings={k: c.get(k, '') for k in ('tofa_url', 'trakt_client_id', 'since', 'profile_id')},
                    sent=counts.get('sent', 0), pending=counts.get('pending', 0),
                    pairing={k: {a: v for a, v in x.items() if a != 'ticket'} for k, x in PAIRING.items()}))
        elif self.path == '/api/backup':
            # Downloads are a deliberate POST to prevent cross-site navigation
            # from starting a credential-containing backup download.
            self.send(405, {'error': 'Use o botão de backup no aplicativo.'})
        else:
            self.send(404, {'error': 'Não encontrado'})

    def do_POST(self):
        if not self.csrf():
            self.send(403, {'error': 'Recarregue a página para continuar.'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 2_100_000:
                raise ValueError('Solicitação muito grande')
            body = json.loads(self.rfile.read(size) or '{}')
            if self.path == '/api/settings':
                global REVISION
                with LOCK:
                    c = config()
                    old_url, old_client = c['tofa_url'], c.get('trakt_client_id')
                    c.update({k: str(body[k]).strip() for k in ('tofa_url', 'trakt_client_id', 'since', 'profile_id') if k in body})
                    validate_config(c)
                    save(DATA / 'config.json', c)
                    if c['tofa_url'] != old_url:
                        (DATA / 'tofa-tokens.json').unlink(missing_ok=True)
                        c.pop('server_id', None)
                        save(DATA / 'config.json', c)
                    if c.get('trakt_client_id') != old_client:
                        (DATA / 'trakt-tokens.json').unlink(missing_ok=True)
                    REVISION += 1
                    WAKE.set()
            elif self.path in ('/api/connect/tofa', '/api/connect/trakt'):
                start_pairing(self.path.rsplit('/', 1)[1])
            elif self.path == '/api/sync':
                WAKE.set()
            elif self.path == '/api/backup':
                self.send(200, make_backup(), 'application/gzip', {'Content-Disposition': 'attachment; filename="tofa-trakt-backup.tar.gz"'})
                return
            elif self.path == '/api/restore':
                restore_backup(base64.b64decode(body['archive'], validate=True))
            else:
                self.send(404, {'error': 'Não encontrado'})
                return
            self.send(200, {'ok': True})
        except Exception as exc:
            self.send(400, {'error': error_message(exc)})


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    # Umbrel may create bind-mount directories as root. Drop privileges after
    # assigning only this app's data directory and files to the app user.
    if os.geteuid() == 0 and os.environ.get('DROP_PRIVILEGES', '1') == '1':
        # With DAC_OVERRIDE deliberately dropped, root cannot traverse a 700
        # directory owned by uid 1000. Temporarily take ownership using CHOWN
        # before scanning; hand it back before dropping the process uid.
        os.chown(DATA, 0, 0)
        for p in DATA.iterdir():
            if p.is_file() and not p.is_symlink():
                os.chown(p, 1000, 1000)
        os.chown(DATA, 1000, 1000)
        os.setgroups([])
        os.setgid(1000)
        os.setuid(1000)
    os.chmod(DATA, 0o700)
    if not (DATA / 'config.json').exists():
        save(DATA / 'config.json', {'tofa_url': os.environ.get('TOFA_URL', 'http://host.docker.internal:33333'),
            'trakt_client_id': os.environ.get('TRAKT_CLIENT_ID', ''),
            'since': iso(dt.datetime.now(dt.timezone.utc)), 'interval_seconds': 60,
            'redirect_uri': 'http://localhost:8765/callback'})


def main():
    initialize()
    for worker in (history_worker, live_worker):
        threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer((os.environ.get('BIND_HOST', '0.0.0.0'), int(os.environ.get('PORT', '8787'))), Handler).serve_forever()


if __name__ == '__main__':
    main()

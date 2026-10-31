#!/usr/bin/env python3
"""One-way completed playback sync. Requires Python 3.11+ and curl."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.parse

UTC = dt.timezone.utc


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def save(path, value):
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class HttpError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTP {status}')


def request(url, token=None, body=None, form=False, client_id=None, extra_headers=None):
    # Credentials travel through stdin, never command arguments or log output.
    headers = {'Accept': 'application/json'}
    headers.update(extra_headers or {})
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if client_id:
        headers.update({'trakt-api-key': client_id, 'trakt-api-version': '2'})
    cmd = ['curl', '--silent', '--show-error', '--connect-timeout', '10',
           '--max-time', '45', '--config', '-', '--write-out', '\n%{http_code}', url]
    config = ''.join('header = ' + json.dumps(k + ': ' + v) + '\n' for k, v in headers.items())
    if body is not None:
        encoded = urllib.parse.urlencode(body) if form else json.dumps(body)
        content = 'application/x-www-form-urlencoded' if form else 'application/json'
        config += 'header = ' + json.dumps('Content-Type: ' + content) + '\n'
        config += 'request = "POST"\ndata = ' + json.dumps(encoded) + '\n'
    r = subprocess.run(cmd, input=config, text=True, capture_output=True)
    if r.returncode:
        raise RuntimeError('Request failed or timed out; credentials omitted')
    raw, code = r.stdout.rsplit('\n', 1)
    if not 200 <= int(code) < 300:
        raise HttpError(int(code))
    return json.loads(raw) if raw else {}


def payload(item, media):
    if item.get('end_reason') != 'completed' or not item.get('ended_at'):
        return None
    ids = {}
    for provider in media.get('provider_ids', []):
        key, value = provider['provider'], provider['provider_id']
        if key in ('tmdb', 'tvdb') and str(value).isdigit():
            ids[key] = int(value)
        elif key == 'imdb' and str(value).startswith('tt'):
            ids[key] = value
    if not ids and media.get('tmdb_id'):
        ids['tmdb'] = int(media['tmdb_id'])
    if not ids:
        raise ValueError('No reliable external ID; title matching disabled')
    watched = iso(stamp(item['ended_at']).replace(second=0, microsecond=0))
    if item.get('media_type') == 'movie':
        return {'movies': [{'ids': ids, 'watched_at': watched}]}
    if item.get('media_type') == 'tv':
        season, episode = item.get('season_number'), item.get('episode_number')
        if season is None or episode is None:
            raise ValueError('Missing season or episode number')
        return {'shows': [{'ids': ids, 'seasons': [{'number': season, 'episodes': [
            {'number': episode, 'watched_at': watched}]}]}]}
    raise ValueError('Unsupported media type')


class Bridge:
    def __init__(self, folder):
        self.folder = folder
        self.config = json.loads((folder / 'config.json').read_text())
        self.db = sqlite3.connect(folder / 'state.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
        self.cache = {}

    def tokens(self, service):
        path = self.folder / (service + '-tokens.json')
        data = json.loads(path.read_text())
        if 'obtained_at' not in data:
            # Device flows save this timestamp on initial authorization.
            raise ValueError(f'{service} tokens have no obtained_at timestamp')
        if time.time() < data['obtained_at'] + data.get('expires_in', 3600) - 300:
            return data['access_token']
        if service == 'tofa':
            url = 'https://api.tofa.tv/servers/' + self.config['server_id'] + '/device-token/refresh'
            body = {'refresh_token': data['refresh_token']}
        else:
            url = 'https://auth.trakt.tv/oauth/token'
            body = {'refresh_token': data['refresh_token'], 'client_id': self.config['trakt_client_id'],
                    'grant_type': 'refresh_token', 'redirect_uri': self.config.get('redirect_uri', 'urn:ietf:wg:oauth:2.0:oob')}
        new = request(url, body=body)
        new['obtained_at'] = time.time()
        save(path, new)
        return new['access_token']

    def tofa(self, path):
        headers = {'X-Profile-Id': self.config['profile_id']} if self.config.get('profile_id') else {}
        return request(self.config['tofa_url'].rstrip('/') + path, self.tokens('tofa'), extra_headers=headers)

    def trakt(self, path, body=None):
        return request('https://api.trakt.tv' + path, self.tokens('trakt'), body,
                       client_id=self.config['trakt_client_id'])

    def existing(self, item, media):
        watched = stamp(item['ended_at']).replace(second=0, microsecond=0)
        query = {'start_at': iso(watched - dt.timedelta(seconds=2)),
                 'end_at': iso(watched + dt.timedelta(seconds=2)), 'limit': 100, 'page': 1}
        expected = payload(item, media)
        target = (expected.get('movies') or expected['shows'])[0]['ids']
        while True:
            rows = self.trakt('/users/me/history?' + urllib.parse.urlencode(query))
            for row in rows:
                if abs((stamp(row['watched_at']) - watched).total_seconds()) > 2:
                    continue
                other = row.get('movie') if item['media_type'] == 'movie' else row.get('show')
                if not other or not any(other.get('ids', {}).get(k) == v for k, v in target.items()):
                    continue
                if item['media_type'] == 'movie':
                    return True
                ep = row.get('episode', {})
                if ep.get('season') == item['season_number'] and ep.get('number') == item['episode_number']:
                    return True
            if len(rows) < 100:
                return False
            query['page'] += 1

    def run(self, commit=False):
        self.cache.clear()
        cutoff = stamp(self.config['since'])
        query = {'limit': 100}
        visited = set()
        while True:
            page = self.tofa('/api/v1/watch/history?' + urllib.parse.urlencode(query))
            rows = page['items']
            for item in rows:
                if item['id'] in visited:
                    raise ValueError('History pagination did not advance')
                visited.add(item['id'])
                if item.get('end_reason') != 'completed' or not item.get('ended_at') or stamp(item['ended_at']) < cutoff:
                    continue
                state = self.db.execute('SELECT status FROM sent WHERE id=?', (item['id'],)).fetchone()
                if state and state[0] == 'sent':
                    continue
                try:
                    mid = item.get('media_id')
                    if not mid:
                        raise ValueError('Media no longer available in Tofa')
                    if mid not in self.cache:
                        self.cache[mid] = self.tofa('/api/v1/media/' + mid)
                    media = self.cache[mid]
                    data = payload(item, media)
                    print(json.dumps({'session': item['id'], 'payload': data}, ensure_ascii=False), flush=True)
                    if not commit:
                        continue
                    if self.existing(item, media):
                        self.db.execute('INSERT OR REPLACE INTO sent VALUES (?, ?)', (item['id'], 'sent'))
                        self.db.commit()
                        continue
                    if state and state[0] == 'pending':
                        print('Pending: inspect Trakt before retrying this session.', flush=True)
                        continue
                    self.db.execute('INSERT OR REPLACE INTO sent VALUES (?, ?)', (item['id'], 'pending'))
                    self.db.commit()
                    try:
                        result = self.trakt('/sync/history', data)
                    except HttpError as e:
                        if e.status in (400, 401, 403, 404, 420, 429):
                            self.db.execute('DELETE FROM sent WHERE id=?', (item['id'],))
                            self.db.commit()
                        raise
                    added = result.get('added', {})
                    if sum(added.get(k, 0) for k in ('movies', 'episodes')) != 1:
                        # Unexpected responses stay pending to avoid duplicate writes.
                        raise ValueError('Trakt did not confirm exactly one watch; inspect response before retrying')
                    self.db.execute('UPDATE sent SET status=? WHERE id=?', ('sent', item['id']))
                    self.db.commit()
                    time.sleep(2)
                except ValueError as e:
                    print(f'Skipped {item["id"]}: {e}', flush=True)
            if not page.get('has_more'):
                break
            if not rows:
                raise ValueError('Empty history page with has_more=true')
            last = rows[-1]
            query.update(before=last['started_at'], before_id=last['id'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--commit', action='store_true', help='Send watches; default is preview only')
    parser.add_argument('--loop', action='store_true')
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    lock = (args.data / '.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    bridge = Bridge(args.data)
    while True:
        try:
            bridge.run(args.commit)
        except Exception as e:
            print(f'Sync paused for this cycle: {type(e).__name__}: {e}', flush=True)
            if not args.loop:
                raise SystemExit(1)
        if not args.loop:
            break
        time.sleep(max(60, int(bridge.config.get('interval_seconds', 300))))


if __name__ == '__main__':
    main()

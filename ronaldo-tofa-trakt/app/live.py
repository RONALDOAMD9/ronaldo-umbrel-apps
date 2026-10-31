"""Live watching status; completed history has a single writer in sync.py."""
import json
import datetime as dt
import time
import urllib.parse


def external_ids(media):
    ids = {}
    for entry in media.get('provider_ids', []):
        name, value = entry['provider'], entry['provider_id']
        if name in ('tmdb', 'tvdb') and str(value).isdigit():
            ids[name] = int(value)
        elif name == 'imdb' and str(value).startswith('tt'):
            ids[name] = value
    if not ids and media.get('tmdb_id'):
        ids['tmdb'] = int(media['tmdb_id'])
    if not ids:
        raise ValueError('Título sem identificador externo confiável')
    return ids


def identify(bridge, session, media):
    """Resolve the actual media file, never infer episode numbers from a title."""
    if media['media_type'] == 'movie':
        return {'movie': {'ids': external_ids(media)}}
    file = next((f for f in media.get('files', []) if f['id'] == session.get('media_file_id')), None)
    if not file or not file.get('episode_id'):
        raise ValueError('Arquivo sem episódio identificado')
    for season in media.get('seasons') or []:
        for episode in season.get('episodes', []):
            if episode['id'] != file['episode_id']:
                continue
            # TMDB episode identifiers can be passed directly to scrobble.
            if episode.get('tmdb_episode_id'):
                return {'episode': {'ids': {'tmdb': episode['tmdb_episode_id']}}}
            ids = external_ids(media)
            key = next(k for k in ('tmdb', 'imdb', 'tvdb') if k in ids)
            matches = bridge.trakt('/search/' + key + '/' + urllib.parse.quote(str(ids[key])) + '?type=show')
            matches = [m['show'] for m in matches if m.get('type') == 'show']
            if len(matches) != 1:
                raise ValueError('Série não identificada de forma única no Trakt')
            sid = matches[0]['ids']['trakt']
            ep = bridge.trakt(f'/shows/{sid}/seasons/{season["season_number"]}/episodes/{episode["episode_number"]}')
            return {'episode': {'ids': {'trakt': ep['ids']['trakt']}}}
    raise ValueError('Episódio do arquivo não encontrado na série')


class Live:
    def __init__(self, bridge, user_id, publish, now=time.monotonic, profile_id=None):
        self.bridge, self.user_id, self.publish, self.now = bridge, user_id, publish, now
        self.profile_id = profile_id
        self.sessions = {}
        self.active = None
        self.started = False
        self.last_sent = 0
        self.last_progress = 0
        self.last_movement = 0
        self.position = None

    def snapshot(self):
        """Recover an existing session from the playback and personal history snapshots."""
        sessions = self.bridge.tofa('/api/v1/system/playback/sessions')
        recent = self.bridge.tofa('/api/v1/watch/history?limit=100')['items']
        for s in sessions:
            if s['user_id'] != self.user_id:
                continue
            h = next((h for h in recent if h.get('media_file_id') == s['media_file_id'] and not h.get('ended_at')), None)
            if h:
                s.update(media_id=h.get('media_id'), media_title=h.get('title'), profile_id=self.profile_id)
            self.sessions[s['session_id']] = s
            if h and s.get('media_id'):
                progress = self.bridge.tofa('/api/v1/media/' + s['media_file_id'] + '/progress')
                # Seed from recent persisted progress once, rather than waiting
                # for the next event after a restart during playback.
                if progress and progress.get('updated_at'):
                    updated = dt.datetime.fromisoformat(progress['updated_at'].replace('Z', '+00:00'))
                    if 0 <= (dt.datetime.now(dt.timezone.utc) - updated).total_seconds() < 45:
                        media = self.bridge.tofa('/api/v1/media/' + s['media_id'])
                        file = next((f for f in media.get('files', []) if f['id'] == s['media_file_id']), {})
                        duration = file.get('duration_ms') or 0
                        if duration > 0:
                            self.sessions[s['session_id']]['profile_id'] = progress.get('profile_id')
                            self.event({'type': 'StreamProgress', 'session_id': s['session_id'],
                                'user_id': self.user_id, 'position_ticks': progress['position_ms'] * 10000,
                                'progress_pct': progress['position_ms'] / duration * 100})

    def pause(self):
        if self.active and self.started:
            try:
                # /pause clears "watching now" without creating a second history entry.
                self.bridge.trakt('/scrobble/pause', dict(self.active['item'], progress=self.last_progress))
            finally:
                self.started = False
                self.publish(playing=False, ends_at=None)

    def event(self, e):
        kind, sid = e.get('type'), e.get('session_id')
        if not sid:
            return
        if e.get('user_id') not in (None, self.user_id):
            return
        if kind == 'StreamStopped':
            if self.active and self.active['sid'] == sid:
                self.pause()
                self.active = None
                self.publish(title=None, progress=None)
            self.sessions.pop(sid, None)
            return
        if kind not in ('StreamStarted', 'StreamUpdated', 'StreamProgress'):
            return
        # Do not use missing user IDs to adopt an unrelated playback session.
        if sid not in self.sessions and e.get('user_id') != self.user_id:
            return
        session = self.sessions.setdefault(sid, {})
        session.update(e)
        if self.profile_id and session.get('profile_id') != self.profile_id:
            return
        if not session.get('media_id'):
            return
        if not self.active or self.active['sid'] != sid:
            if kind != 'StreamStarted' and self.active:
                return  # One watching status per Trakt account; newest start wins.
            self.pause()
            media = self.bridge.tofa('/api/v1/media/' + session['media_id'])
            file = next((f for f in media.get('files', []) if f['id'] == session.get('media_file_id')), {})
            self.active = {'sid': sid, 'item': identify(self.bridge, session, media), 'title': session.get('media_title', media.get('title')), 'duration': (file.get('duration_ms') or 0) / 1000}
            self.last_movement, self.position = self.now(), None
            self.started = False
        progress = max(0.0, min(100.0, float(e.get('progress_pct', self.last_progress if self.started else 0))))
        position = e.get('position_ticks')
        previous_position = self.position
        elapsed = self.now() - self.last_movement
        moved = position is not None and position != previous_position
        seek = False
        if moved:
            if previous_position is not None:
                seek = abs((position - previous_position) / 10_000_000 - elapsed) > 12
            self.position, self.last_movement = position, self.now()
        self.last_progress = progress
        if not self.started and not moved and kind != 'StreamStarted' and self.position is not None:
            return
        first_progress = position is not None and previous_position is None
        if not self.started or first_progress or (seek and self.now() - self.last_sent > 3):
            self.bridge.trakt('/scrobble/start', dict(self.active['item'], progress=progress))
            self.started, self.last_sent = True, self.now()
        remaining = self.active['duration'] * (1 - progress / 100)
        ends_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=remaining)).isoformat() if remaining else None
        self.publish(playing=True, title=self.active['title'], progress=round(progress, 1), ends_at=ends_at)

    def tick(self):
        # Tofa has no explicit pause event. Infer a pause only after a generous
        # 75s without movement; resume on the next advancing progress event.
        if self.active and self.started and self.now() - self.last_movement > 75:
            self.pause()

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sync import Bridge, payload

MEDIA = {'tmdb_id': 10, 'provider_ids': [{'provider': 'tmdb', 'provider_id': '10'}]}
ITEM = {'id': 'watch1', 'media_id': 'm1', 'media_type': 'tv', 'season_number': 1,
        'episode_number': 2, 'end_reason': 'completed',
        'started_at': '2026-10-31T01:00:00Z', 'ended_at': '2026-10-31T02:00:00.123Z'}


class FakeBridge(Bridge):
    def __init__(self, folder):
        super().__init__(folder)
        self.posts = 0
        self.timeout = False
        self.remote_exists = False

    def tofa(self, path):
        if path.startswith('/api/v1/watch/history'):
            return {'items': [ITEM], 'has_more': False}
        return MEDIA

    def existing(self, item, media):
        return self.remote_exists

    def trakt(self, path, body=None):
        self.posts += 1
        if self.timeout:
            raise RuntimeError('Simulated timeout after possible server write')
        return {'added': {'movies': 0, 'episodes': 1}, 'not_found': {}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'config.json').write_text(json.dumps({'since': '2026-10-30T00:00:00Z'}))
        self.bridge = FakeBridge(self.folder)

    def tearDown(self):
        self.bridge.db.close()
        self.temp.cleanup()

    def test_episode_nested_under_show(self):
        p = payload(ITEM, MEDIA)
        self.assertEqual(p['shows'][0]['seasons'][0]['episodes'],
                         [{'number': 2, 'watched_at': '2026-10-31T02:00:00Z'}])

    def test_watch_time_matches_trakt_minute_precision(self):
        item = dict(ITEM, ended_at='2026-10-31T02:00:32.456Z')
        self.assertEqual(payload(item, MEDIA)['shows'][0]['seasons'][0]['episodes'][0]['watched_at'],
                         '2026-10-31T02:00:00Z')

    def test_interrupted_not_sent(self):
        item = dict(ITEM, end_reason='stopped')
        self.assertIsNone(payload(item, MEDIA))

    def test_missing_episode_rejected(self):
        with self.assertRaises(ValueError):
            payload(dict(ITEM, episode_number=None), MEDIA)

    def test_preview_does_not_post(self):
        self.bridge.run(False)
        self.assertEqual(self.bridge.posts, 0)
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM sent').fetchone()[0], 0)

    @patch('sync.time.sleep')
    def test_restart_does_not_duplicate(self, sleep):
        self.bridge.run(True)
        self.bridge.run(True)
        self.assertEqual(self.bridge.posts, 1)

    def test_ambiguous_timeout_is_not_reposted(self):
        self.bridge.timeout = True
        with self.assertRaises(RuntimeError):
            self.bridge.run(True)
        self.bridge.timeout = False
        self.bridge.run(True)
        self.assertEqual(self.bridge.posts, 1)
        self.assertEqual(self.bridge.db.execute('SELECT status FROM sent').fetchone()[0], 'pending')

    def test_existing_remote_watch_not_resent(self):
        self.bridge.remote_exists = True
        self.bridge.run(True)
        self.assertEqual(self.bridge.posts, 0)
        self.assertEqual(self.bridge.db.execute('SELECT status FROM sent').fetchone()[0], 'sent')


if __name__ == '__main__':
    unittest.main()

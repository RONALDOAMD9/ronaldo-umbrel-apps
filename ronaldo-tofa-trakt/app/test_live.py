import unittest
from live import Live, identify


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.media = {'media_type': 'movie', 'title': 'Movie', 'tmdb_id': 42,
                      'files': [{'id': 'f1', 'duration_ms': 600000}]}

    def tofa(self, path):
        return self.media

    def trakt(self, path, body=None):
        self.calls.append((path, body))
        return {'action': 'start'}


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.b = FakeBridge()
        self.clock = 0
        self.status = {}
        self.live = Live(self.b, 'u1', lambda **x: self.status.update(x), now=lambda: self.clock)
        self.start = {'type': 'StreamStarted', 'user_id': 'u1', 'session_id': 's1',
                      'media_id': 'm1', 'media_file_id': 'f1', 'media_title': 'Movie'}

    def progress(self, pct, ticks):
        self.live.event({'type': 'StreamProgress', 'user_id': 'u1', 'session_id': 's1',
                         'progress_pct': pct, 'position_ticks': ticks})

    def test_start_updates_watching_but_not_history(self):
        self.live.event(self.start)
        self.assertEqual(self.b.calls[0][0], '/scrobble/start')
        self.assertTrue(self.status['playing'])
        self.assertIsNotNone(self.status['ends_at'])

    def test_resumed_playback_reports_actual_first_position(self):
        self.live.event(self.start)
        self.progress(50, 3_000_000_000)
        self.assertEqual(self.b.calls[-1][1]['progress'], 50)
        self.assertEqual(len(self.b.calls), 2)

    def test_pause_is_inferred_and_same_position_does_not_resume(self):
        self.live.event(self.start)
        self.progress(10, 600_000_000)
        self.clock = 76
        self.live.tick()
        self.assertFalse(self.status['playing'])
        self.assertEqual(self.b.calls[-1][0], '/scrobble/pause')
        count = len(self.b.calls)
        self.progress(10, 600_000_000)
        self.assertEqual(len(self.b.calls), count)
        self.clock = 80
        self.progress(11, 660_000_000)
        self.assertTrue(self.status['playing'])
        self.assertEqual(self.b.calls[-1][0], '/scrobble/start')

    def test_stopping_at_high_progress_does_not_double_write_history(self):
        self.live.event(self.start)
        self.progress(99, 5_940_000_000)
        self.live.event({'type': 'StreamStopped', 'session_id': 's1'})
        self.assertEqual(self.b.calls[-1][0], '/scrobble/pause')
        self.assertNotIn('/scrobble/stop', [path for path, _ in self.b.calls])
        self.assertFalse(self.status['playing'])

    def test_other_user_cannot_overwrite_watching(self):
        self.live.event(dict(self.start, user_id='someone-else'))
        self.assertEqual(self.b.calls, [])

    def test_other_profile_is_filtered(self):
        self.live.profile_id = 'p1'
        self.live.event(dict(self.start, profile_id='p2'))
        self.assertEqual(self.b.calls, [])

    def test_episode_uses_file_identity_not_title_guess(self):
        media = {'media_type': 'tv', 'files': [{'id': 'f1', 'episode_id': 'e2'}],
                 'seasons': [{'season_number': 3, 'episodes': [{'id': 'e2', 'episode_number': 4, 'tmdb_episode_id': 123}]}]}
        self.assertEqual(identify(self.b, self.start, media), {'episode': {'ids': {'tmdb': 123}}})
        with self.assertRaises(ValueError):
            identify(self.b, dict(self.start, media_file_id='missing'), media)

    def test_seek_refreshes_prediction(self):
        self.live.event(self.start)
        self.progress(10, 600_000_000)
        self.clock = 10
        self.progress(60, 3_600_000_000)
        self.assertEqual(self.b.calls[-1][1]['progress'], 60)


if __name__ == '__main__':
    unittest.main()

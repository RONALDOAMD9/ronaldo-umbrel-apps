import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import app


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patcher = patch.object(app, 'DATA', self.root)
        self.patcher.start()
        self.config = {'tofa_url': 'http://tofa:33333', 'since': '2026-10-31T00:00:00Z', 'trakt_client_id': 'public-id'}
        (self.root / 'config.json').write_text(json.dumps(self.config))
        (self.root / 'tofa-tokens.json').write_text(json.dumps({'access_token': 'private-token', 'refresh_token': 'refresh', 'obtained_at': 1}))
        db = sqlite3.connect(self.root / 'state.sqlite')
        db.execute('CREATE TABLE sent (id TEXT PRIMARY KEY, status TEXT)')
        db.execute("INSERT INTO sent VALUES ('x','sent')")
        db.commit()
        db.close()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_backup_restores_authorization_and_deduplication_state(self):
        backup = app.make_backup()
        (self.root / 'tofa-tokens.json').unlink()
        app.restore_backup(backup)
        self.assertEqual(json.loads((self.root / 'tofa-tokens.json').read_text())['access_token'], 'private-token')
        db = sqlite3.connect(self.root / 'state.sqlite')
        self.assertEqual(db.execute('SELECT id,status FROM sent').fetchall(), [('x', 'sent')])
        db.close()
        self.assertEqual((self.root / 'tofa-tokens.json').stat().st_mode & 0o777, 0o600)

    def test_restore_rejects_path_traversal_without_changing_config(self):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as tar:
            info = tarfile.TarInfo('../config.json')
            info.size = 2
            tar.addfile(info, io.BytesIO(b'{}'))
        with self.assertRaises(ValueError):
            app.restore_backup(out.getvalue())
        self.assertEqual(json.loads((self.root / 'config.json').read_text()), self.config)

    def test_unexpected_credentials_in_url_rejected(self):
        with self.assertRaises(ValueError):
            app.validate_config(dict(self.config, tofa_url='https://user:secret@server'))

    def test_csrf_rejects_cross_site_request(self):
        fake = type('Request', (), {'headers': {'Cookie': 'tofa_trakt=' + app.SESSION}})()
        self.assertFalse(app.Handler.csrf(fake))
        fake.headers['X-CSRF-Token'] = app.SESSION
        self.assertTrue(app.Handler.csrf(fake))


if __name__ == '__main__':
    unittest.main()

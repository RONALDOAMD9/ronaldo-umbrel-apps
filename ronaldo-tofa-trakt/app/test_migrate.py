import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
from migrate_legacy import migrate, CONTAINER


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / 'legacy'
        self.source = self.old / 'data'
        self.source.mkdir(parents=True)
        self.target = self.root / 'new'
        (self.source / 'config.json').write_text(json.dumps({'server_id': 'sid', 'trakt_client_id': 'client', 'tofa_url': 'http://old:33333'}))
        for name in ('tofa', 'trakt'):
            (self.source / (name + '-tokens.json')).write_text(json.dumps({'access_token':'fixture', 'refresh_token':'fixture', 'obtained_at':1}))
        db = sqlite3.connect(self.source / 'state.sqlite')
        db.execute('CREATE TABLE sent(id TEXT PRIMARY KEY,status TEXT)')
        db.execute("INSERT INTO sent VALUES ('done','sent')")
        db.commit();db.close()
        self.container = {'Config': {'Labels': {'com.docker.compose.project': 'tofa-trakt', 'com.docker.compose.service':'sync'}},
                          'Mounts':[{'Destination':'/data','Source':str(self.source)}], 'State':{'Running':True}}

    def tearDown(self):
        self.temp.cleanup()

    def run_docker(self, args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps([self.container]) if args[1] == 'inspect' else '', '')

    def test_migrates_once_and_keeps_private_backup(self):
        with patch('migrate_legacy.subprocess.run', side_effect=self.run_docker) as docker:
            self.assertTrue(migrate(self.old, self.target))
            self.assertIn(['docker','stop','--time','20',CONTAINER], [c.args[0] for c in docker.call_args_list])
            self.assertFalse(migrate(self.old, self.target))
        self.assertTrue((self.target / 'legacy-backup.tar.gz').exists())
        self.assertEqual((self.target / 'trakt-tokens.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.target / 'config.json').read_text())['tofa_url'],'http://host.docker.internal:33333')
        db = sqlite3.connect(self.target / 'state.sqlite')
        self.assertEqual(db.execute('SELECT id FROM sent').fetchall(), [('done',)])
        db.close()

    def test_unrelated_container_is_never_stopped(self):
        self.container['Config']['Labels']['com.docker.compose.project'] = 'other'
        with patch('migrate_legacy.subprocess.run', side_effect=self.run_docker) as docker:
            with self.assertRaises(ValueError):migrate(self.old, self.target)
            self.assertEqual(docker.call_count, 1)
        self.assertFalse((self.target / 'config.json').exists())

    def test_existing_app_authorization_is_preserved(self):
        self.target.mkdir()
        (self.target / 'trakt-tokens.json').write_text('already connected')
        with patch('migrate_legacy.subprocess.run') as docker:
            self.assertFalse(migrate(self.old, self.target))
            docker.assert_not_called()


if __name__ == '__main__':unittest.main()

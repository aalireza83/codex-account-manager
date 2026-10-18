import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from activity import ActivityStore


class ActivityTests(unittest.TestCase):
    def test_concurrent_counters_survive_reopening(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'activity.json'
            store=ActivityStore(path)
            since=store.snapshot()['tracking_since']
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _:ActivityStore(path).record('account','stable-key','ping',True,25),range(40)))
            state=ActivityStore(path).snapshot()
            self.assertEqual(state['tracking_since'],since)
            self.assertEqual(state['records']['account:stable-key']['ping']['success'],40)
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_corrupt_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'activity.json'
            invalid=json.dumps({'schema_version':1,'records':{'account:x':{'ping':{'success':-1,'failed':0}}}})
            path.write_text(invalid)
            with self.assertRaises(ValueError):ActivityStore(path).record('account','x','ping',True,1)
            self.assertEqual(path.read_text(),invalid)

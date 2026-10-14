#!/usr/bin/env python3
"""Tests for the atomic state file."""
import os
import tempfile
import unittest

import state_store as st


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'sub', 'state.json')   # parent dir doesn't exist yet

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self):
        d = st.load(self.path)
        self.assertEqual(d, {'schedule': {'enabled': False, 'time': '08:00', 'seconds': 60},
                             'guid': None, 'last_run': None})

    def test_defaults_are_fresh_copies(self):
        st.load(self.path)['schedule']['enabled'] = True
        self.assertFalse(st.load(self.path)['schedule']['enabled'])

    def test_round_trip(self):
        d = st.defaults()
        d['schedule'] = {'enabled': True, 'time': '07:30', 'seconds': 45}
        d['guid'] = 'a' * 32
        d['last_run'] = '2026-10-14 08:00'
        st.save(self.path, d)
        self.assertEqual(st.load(self.path), d)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['state.json'])   # no temp left

    def test_corrupt_or_invalid_gives_defaults(self):
        os.makedirs(os.path.dirname(self.path))
        for junk in ('{"schedule": {"enab', '[1, 2]', '{}',
                     '{"schedule": {"enabled": true, "time": "23:00", "seconds": 60}}'):
            with open(self.path, 'w') as f:
                f.write(junk)
            self.assertEqual(st.load(self.path), st.defaults(), junk)


if __name__ == '__main__':
    unittest.main()

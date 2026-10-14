#!/usr/bin/env python3
"""Tests for the pure schedule rule."""
import unittest
from datetime import datetime

import schedule as s

TODAY = 2026286          # 2026-10-14, matches the ESP's year*1000 + 0-based tm_yday
ON = {'enabled': True, 'time': '08:00', 'seconds': 60}


def at(h, m):
    return datetime(2026, 10, 14, h, m)


class TestDayKey(unittest.TestCase):
    def test_day_key_matches_esp(self):
        self.assertEqual(s.day_key(at(13, 0)), TODAY)
        self.assertEqual(s.day_key(datetime(2026, 1, 1)), 2026000)
        self.assertEqual(s.day_key(datetime(2026, 12, 31)), 2026364)


class TestDue(unittest.TestCase):
    def test_fires_at_start_time(self):
        self.assertTrue(s.due(ON, at(8, 0), True, TODAY - 1))

    def test_catch_up_same_day_before_cutoff(self):
        self.assertTrue(s.due(ON, at(21, 59), True, TODAY - 1))
        self.assertTrue(s.due(ON, at(15, 0), True, -1))

    def test_dropped_at_or_after_cutoff(self):
        self.assertFalse(s.due(ON, at(22, 0), True, TODAY - 1))
        self.assertFalse(s.due(ON, at(23, 59), True, TODAY - 1))

    def test_not_before_start(self):
        self.assertFalse(s.due(ON, at(7, 59), True, TODAY - 1))

    def test_not_twice(self):
        self.assertFalse(s.due(ON, at(9, 0), True, TODAY))

    def test_needs_ntp_and_esp_state_and_enabled(self):
        self.assertFalse(s.due(ON, at(8, 0), False, TODAY - 1))
        self.assertFalse(s.due(ON, at(8, 0), True, None))
        self.assertFalse(s.due({**ON, 'enabled': False}, at(8, 0), True, TODAY - 1))


class TestNextRun(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(s.next_run({**ON, 'enabled': False}, at(7, 0), False), 'Schedule off')
        self.assertEqual(s.next_run(ON, at(7, 0), False), 'today 08:00')
        self.assertEqual(s.next_run(ON, at(9, 0), False), 'now (catching up)')
        self.assertEqual(s.next_run(ON, at(9, 0), True), 'tomorrow 08:00')
        self.assertEqual(s.next_run(ON, at(22, 30), False), 'tomorrow 08:00')


class TestValidation(unittest.TestCase):
    def test_validate_seconds(self):
        for ok in (1, 45, 90):
            self.assertEqual(s.validate_seconds(ok), ok)
        for bad in (0, 91, -1, True, '30', None, 30.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                s.validate_seconds(bad)

    def test_validate_schedule(self):
        self.assertEqual(s.validate_schedule({'enabled': True, 'time': '21:59', 'seconds': 90}),
                         {'enabled': True, 'time': '21:59', 'seconds': 90})
        bad = [
            None, [], {},
            {'enabled': 'yes', 'time': '08:00', 'seconds': 60},
            {'enabled': True, 'time': '22:00', 'seconds': 60},   # cutoff
            {'enabled': True, 'time': '8:00', 'seconds': 60},
            {'enabled': True, 'time': '24:00', 'seconds': 60},
            {'enabled': True, 'time': '08:00', 'seconds': 91},
        ]
        for d in bad:
            with self.assertRaises(ValueError, msg=repr(d)):
                s.validate_schedule(d)


if __name__ == '__main__':
    unittest.main()

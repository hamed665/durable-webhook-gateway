import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from gateway import Gateway


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.db')
        self.now = 1000
        self.g = Gateway(self.path, b'x' * 32, clock=lambda: self.now)

    def tearDown(self):
        self.g.close()
        self.tmp.cleanup()

    def add(self, key='evt-1', body=b'{"total":120}'):
        return self.g.ingest(key, int(self.now), body, self.g.signature(key, int(self.now), body))

    def test_signature_tampering(self):
        with self.assertRaises(ValueError):
            self.g.ingest('evt-1', 1000, b'{}', 'bad')

    def test_id_bound_to_signature(self):
        with self.assertRaises(ValueError):
            self.g.ingest('other', 1000, b'{}', self.g.signature('original', 1000, b'{}'))

    def test_expired_signature(self):
        with self.assertRaises(ValueError):
            self.g.ingest('x', 1, b'{}', self.g.signature('x', 1, b'{}'))

    def test_duplicate_and_collision(self):
        self.assertTrue(self.add())
        self.assertFalse(self.add())
        with self.assertRaises(ValueError):
            self.add(body=b'{"total":121}')

    def test_success_survives_reopen(self):
        self.add()
        job = self.g.claim()
        self.g.finish(job['id'], job['lease'], success=True)
        other = Gateway(self.path, b'x'*32, clock=lambda: self.now)
        self.addCleanup(other.close)
        self.assertEqual(other.status()[0]['state'], 'done')
        self.assertIsNone(other.claim())

    def test_retry_backoff_and_dead_letter(self):
        self.add()
        for attempt in range(1, 4):
            job = self.g.claim()
            self.assertEqual(job['attempt'], attempt)
            self.g.finish(job['id'], job['lease'], success=False)
            self.assertIsNone(self.g.claim())
            self.now += 2 ** attempt
        self.assertEqual(self.g.status()[0]['state'], 'dead')

    def test_crash_recovery_and_stale_worker(self):
        self.add()
        first = self.g.claim()
        self.now += 31
        replacement = self.g.claim()
        with self.assertRaises(ValueError):
            self.g.finish(first['id'], first['lease'], success=True)
        self.g.finish(replacement['id'], replacement['lease'], success=True)

    def test_crash_budget_exhaustion(self):
        self.add()
        for _ in range(3):
            self.assertIsNotNone(self.g.claim())
            self.now += 31
        self.assertIsNone(self.g.claim())
        self.assertEqual(self.g.status()[0]['state'], 'dead')

    def test_parallel_workers_claim_once(self):
        self.add()
        def claim(_):
            other = Gateway(self.path, b'x'*32, clock=lambda: 1000)
            try:
                return other.claim()
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(claim, range(4)))
        self.assertEqual(sum(j is not None for j in jobs), 1)

    def test_ingress_bounds_and_json(self):
        with self.assertRaises(ValueError):
            self.add(body=b'x' * 65537)
        with self.assertRaises(ValueError):
            self.add(body=b'{broken')

    def test_short_secret(self):
        with self.assertRaises(ValueError):
            Gateway(':memory:', b'short', clock=lambda: 0)

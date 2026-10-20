"""Signed ingress and durable at-least-once work scheduling. No outbound transport."""
import hashlib
import hmac
import json
import sqlite3
import uuid


class Gateway:
    def __init__(self, path, secret, *, clock, max_attempts=3, lease_seconds=30):
        if len(secret) < 32:
            raise ValueError('Use a secret of at least 32 bytes')
        if max_attempts < 1 or lease_seconds <= 0:
            raise ValueError('Invalid retry configuration')
        self.secret, self.clock = secret, clock
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, body BLOB NOT NULL, digest TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL, due REAL NOT NULL,
            lease TEXT, last_error TEXT)''')

    def close(self):
        self.db.close()

    def signature(self, event_id, timestamp, body):
        return hmac.new(self.secret, json.dumps([event_id, timestamp], separators=(',', ':')).encode() + b'.' + body,
                        hashlib.sha256).hexdigest()

    def ingest(self, event_id, timestamp, body, signature):
        if not isinstance(body, bytes) or len(body) > 65536:
            raise ValueError('Body must be bytes, max 64 KiB')
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise ValueError('Invalid event ID')
        if type(timestamp) is not int or abs(self.clock() - timestamp) > 300:
            raise ValueError('Expired signature')
        if not isinstance(signature, str) or not hmac.compare_digest(
                self.signature(event_id, timestamp, body), signature):
            raise ValueError('Invalid signature')
        json.loads(body)
        digest = hashlib.sha256(body).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            previous = self.db.execute('SELECT digest FROM events WHERE id=?', (event_id,)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise ValueError('Event ID already used for different content')
                result = False
            else:
                self.db.execute('INSERT INTO events VALUES (?, ?, ?, ?, 0, ?, NULL, NULL)',
                                (event_id, body, digest, 'pending', self.clock()))
                result = True
            self.db.execute('COMMIT')
            return result
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def claim(self):
        """Atomically acquire work; expired leases count toward retry budget."""
        now = self.clock()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE events SET state='dead', lease=NULL, last_error='lease expired' "
                            "WHERE state='processing' AND due<=? AND attempts>=?",
                            (now, self.max_attempts))
            row = self.db.execute("SELECT * FROM events WHERE state IN ('pending','processing') "
                                  'AND due<=? ORDER BY due,id LIMIT 1', (now,)).fetchone()
            result = None
            if row:
                token = uuid.uuid4().hex
                self.db.execute("UPDATE events SET state='processing', attempts=attempts+1, "
                                'due=?,lease=? WHERE id=?',
                                (now + self.lease_seconds, token, row['id']))
                result = {'id': row['id'], 'body': json.loads(row['body']),
                          'lease': token, 'attempt': row['attempts'] + 1}
            self.db.execute('COMMIT')
            return result
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def finish(self, event_id, lease, *, success):
        """Stale workers cannot acknowledge work owned by a replacement worker."""
        now = self.clock()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute("SELECT attempts FROM events WHERE id=? AND lease=? "
                                  "AND state='processing' AND due>?", (event_id, lease, now)).fetchone()
            if not row:
                raise ValueError('Expired or invalid lease')
            state = 'done' if success else ('dead' if row['attempts'] >= self.max_attempts else 'pending')
            self.db.execute('UPDATE events SET state=?,lease=NULL,due=?,last_error=? WHERE id=?',
                            (state, now + min(300, 2 ** row['attempts']),
                             None if success else 'delivery failed', event_id))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def status(self):
        return [dict(r) for r in self.db.execute('SELECT id,state,attempts,due,last_error FROM events ORDER BY id')]

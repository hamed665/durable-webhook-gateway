# Durable Webhook Gateway

![Verify](https://github.com/hamed665/durable-webhook-gateway/actions/workflows/verify.yml/badge.svg)

A signed webhook ingress and durable work scheduler that handles duplicate events, worker crashes and retry exhaustion.

**Status:** tested reference implementation with a reproducible synthetic CLI demo.
Built as an independent engineering portfolio project by [Hamed Minaei](https://github.com/hamed665).

## Features

- HMAC-SHA256 binds the event ID, timestamp and exact payload bytes.
- Five-minute ingress window, 64 KiB body cap and event-content collision rejection.
- SQLite WAL persistence with atomic work claims and fenced lease tokens.
- Exponential retry scheduling, bounded attempts and dead-letter state.
- Concurrent-worker and restart tests using real SQLite connections.

## Quick start

Python 3.11 or newer; only the standard library is used. No package install is required.

```bash
git clone https://github.com/hamed665/durable-webhook-gateway.git
cd durable-webhook-gateway
python demo.py
python -m unittest discover -s tests -v
```

The demo performs real computation against synthetic data. Its captured output is in
[docs/demo-output.json](docs/demo-output.json). It uses no customer records or production systems.

## Architecture

The integration supplies raw bytes, event ID, integer timestamp and signature to `Gateway.ingest`. A unique event ID protects insertion inside `BEGIN IMMEDIATE`. Workers call `claim`, perform their own delivery, then `finish`. A lease token fences stale acknowledgements; expired leases become reclaimable until their attempt budget is exhausted. Completed event rows remain available for idempotency checks.

Core implementation: [gateway.py](gateway.py).
The core is separate from the demonstration runner and can be imported directly:

```python
from gateway import Gateway
import time

g = Gateway("queue.db", b"replace-with-32-random-bytes-minimum", clock=time.time)
body = b'{"order":"SYNTHETIC-1"}'
timestamp = int(time.time())
signature = g.signature("event-1", timestamp, body)  # sender-side example
g.ingest("event-1", timestamp, body, signature)
job = g.claim()
if job:
    # A separate adapter performs delivery here, with its own idempotency key.
    g.finish(job["id"], job["lease"], success=True)
g.close()
```

## Installation and configuration

Construct `Gateway(path, secret, clock=..., max_attempts=3, lease_seconds=30)`. The secret must contain at least 32 bytes. Inject an epoch-seconds clock; persist the database outside the container for a real integration. The demo uses an isolated temporary database and a public synthetic key. `.env.example` documents adapter settings; the library deliberately does not load environment files.

An optional Dockerfile runs the same demo under a non-root UID:

```bash
docker build -t durable-webhook-gateway .
docker run --rm --read-only --network none --tmpfs /tmp:rw,noexec,nosuid,size=32m durable-webhook-gateway
```

The Docker definition is provided for reproducibility. Container execution is not claimed as verified unless recorded in VALIDATION.md.

## Testing

`python -m unittest discover -s tests -v` runs 11 behavioral tests.
GitHub Actions runs those tests and the demo on Python 3.11, 3.12 and 3.13.
Action dependencies are pinned to commit SHAs and the workflow token is read-only.
See [VALIDATION.md](VALIDATION.md) for local evidence and verification scope.

## Security

Verify exact raw bytes before parsing and keep the signing secret outside version control. Use TLS and sender authentication in the HTTP adapter. Only trusted worker code should call claim/finish. The database contains payloads and requires filesystem access controls, retention and backups. A signed event can still represent malicious input; validate its business schema downstream.

## Limitations

This is a tested scheduling library, not a hosted gateway. There is no HTTP listener, outbound transport, destination allowlist or SSRF defense because it never performs network delivery. Supply a reviewed adapter before exposing it. Delivery is at least once: a worker crash after sending but before acknowledgment can duplicate the external effect. Consumers must deduplicate. Dead-letter replay, retention and distributed database operation are not implemented.

## Attribution and ownership

This repository is an original implementation created for Hamed Minaei's portfolio with
AI-assisted development and local verification. No upstream application's source code or
Git history was imported. Python's standard library supplies the underlying runtime,
SQLite bindings, cryptography primitives, JSON, dates and decimal arithmetic as used here.
The implementation, tests, synthetic fixtures and documentation are maintained in this repository.

License: [MIT](LICENSE). No third-party runtime packages are required.

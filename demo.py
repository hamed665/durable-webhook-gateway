import json
import tempfile
from pathlib import Path
from gateway import Gateway


def run():
    now = [1760000000]
    with tempfile.TemporaryDirectory() as directory:
        g = Gateway(str(Path(directory) / 'demo.db'), b'synthetic-demo-secret-32-bytes-only', clock=lambda: now[0])
        try:
            body = b'{"order_id":"DEMO-100","amount":"12.345","currency":"OMR"}'
            sig = g.signature('demo-order', now[0], body)
            accepted = g.ingest('demo-order', now[0], body, sig)
            duplicate = g.ingest('demo-order', now[0], body, sig)
            first = g.claim()
            g.finish(first['id'], first['lease'], success=False)
            wait_state = g.status()
            now[0] += 2
            retry = g.claim()
            g.finish(retry['id'], retry['lease'], success=True)
            return {'synthetic': True, 'accepted': accepted, 'duplicate_accepted': duplicate,
                    'after_failure': wait_state, 'final': g.status()}
        finally:
            g.close()


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))

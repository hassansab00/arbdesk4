"""Authenticated bounded HTTP entrypoint for n8n. Deploy behind HTTPS."""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from paper_worker import cycle


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_json(200 if self.path=='/health' else 404, {'service':'arbdesk-paper-worker','paper_only':True})

    def do_POST(self):
        secret = os.environ['PAPER_WORKER_TOKEN']
        if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+secret):
            return self.send_json(401,{'error':'Unauthorized'})
        if self.path not in ('/paper-cycle','/paper-proposals','/paper-exits','/paper-settlements'):
            return self.send_json(404,{'error':'Unknown operation'})
        try:
            if self.path=='/paper-proposals':
                from paper_plans import cycle as proposals
                self.send_json(200,proposals())
            elif self.path=='/paper-exits':
                from paper_exits import cycle as exits
                self.send_json(200,exits())
            elif self.path=='/paper-settlements':
                from paper_settlement import cycle as settlements
                self.send_json(200,settlements())
            else:
                result=cycle()
                from common import log_run
                log_run('P2.2_paper_trades','ok',result['orders_completed'],{'summary':'Paper queue processed','trigger':'worker HTTP'})
                self.send_json(200,result)
        except Exception:
            # Detailed errors belong in worker logs, not an external response.
            import traceback
            traceback.print_exc()
            self.send_json(503,{'error':'Paper cycle failed; recoverable leases retained'})


if __name__ == '__main__':
    if len(os.environ.get('PAPER_WORKER_TOKEN','')) < 32:
        raise ValueError('PAPER_WORKER_TOKEN must contain at least 32 characters')
    if not os.environ.get('ARBDESK_ENGINE_VERSION'):
        raise ValueError('ARBDESK_ENGINE_VERSION must identify the deployed commit')
    HTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()

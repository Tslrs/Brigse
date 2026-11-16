"""The dashboard must only obey the person at this computer's browser, never a web page, another host or the agent.

Attacks tried: a foreign Host (DNS rebinding), a foreign Origin (another site's page), a missing or wrong
token, a decision that is already made, a huge request, ids that try to leave their folder, framing the
page (clickjacking). Needs no vendor tools. usage: uv run python tests/test_dashboard_security.py
"""
import http.client
import json
import os
import re
import tempfile

HOME = tempfile.mkdtemp()
os.environ["BRIGSE_HOME"] = HOME
from brigse import dashboard, store  # noqa: E402

PORT = 8798
dashboard.start_in_background(PORT)
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def call(method, path, host="brigse.localhost", headers=None, body=None, raw_length=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Host": host, **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if raw_length is not None:
        h["Content-Length"] = str(raw_length)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    if data is not None and raw_length is None:
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data if raw_length is None else None)
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


page = call("GET", "/")
token = re.search(r'TOKEN\s*=\s*"([^"]+)"', page[2].decode()).group(1)
check("the page itself is served on this machine's names", page[0] == 200 and call("GET", "/", host="127.0.0.1")[0] == 200
      and call("GET", "/", host="localhost:8798")[0] == 200)
check("the token is not in the state data", token.encode() not in call("GET", "/api/state")[2])

# DNS rebinding: a foreign name pointing at this machine
for host in ("evil.com", "127.0.0.1.evil.com", "localhost.evil.com", "evil.com:8798", "brigse.localhost.evil.com", ""):
    check(f"GET with Host '{host}' is refused", call("GET", "/api/state", host=host)[0] == 403)
check("POST with the right token but a foreign Host is refused",
      call("POST", "/api/board", host="evil.com", headers={"X-Token": token}, body={"id": "tangnano9k"})[0] == 403)

# another site's page in the user's browser
for origin in ("http://evil.com", "https://brigse.localhost.evil.com", "null", "http://127.0.0.1.evil.com"):
    check(f"GET with Origin '{origin}' is refused", call("GET", "/api/state", headers={"Origin": origin})[0] == 403)
    check(f"POST with the right token but Origin '{origin}' is refused",
          call("POST", "/api/board", headers={"X-Token": token, "Origin": origin}, body={"id": "tangnano9k"})[0] == 403)
check("our own Origin is accepted", call("GET", "/api/state", headers={"Origin": "http://brigse.localhost:8798"})[0] == 200)

# the token
req = store.new_request("install_backend", "x", "Install the example add-on",
                        {"what": "w", "source": "s", "disk": "1 MB", "licence": "MIT", "automatic": False}, "test")
url = f"/api/requests/{req['id']}/approve"
check("no token: refused", call("POST", url, body={})[0] == 403)
check("wrong token: refused", call("POST", url, headers={"X-Token": token[:-1] + "x"}, body={})[0] == 403)
check("a token of another length: refused", call("POST", url, headers={"X-Token": "a"}, body={})[0] == 403)
check("the request is still pending after all that", store.get_request(req["id"])["state"] == "pending")
check("the token in a query string is not enough", call("POST", url + f"?token={token}", body={})[0] == 403)
check("the right token decides", call("POST", url, headers={"X-Token": token}, body={})[0] == 200
      and store.get_request(req["id"])["state"] == "approved")
call("POST", f"/api/requests/{req['id']}/reject", headers={"X-Token": token}, body={})
check("a decision, once made, is final", store.get_request(req["id"])["state"] == "approved")

# size and shape of requests
check("a huge request is refused without being read", call("POST", "/api/board", headers={"X-Token": token}, raw_length=500_000_000)[0] == 413)
check("a broken body is a 400, not a crash", call("POST", "/api/board", headers={"X-Token": token}, raw_length=0)[0] in (200, 400))
check("a non-number length is refused", call("POST", "/api/board", headers={"X-Token": token, "Content-Type": "x"}, raw_length="abc")[0] in (400, 413))

# ids that try to leave their folder
for path in ("/api/sims/..%2F..%2Fstate/waves.json", "/api/sims/../waves.json", "/api/jobs/..%2Fx/floorplan.svg",
             "/api/jobs/..%2F..%2Fx/log", "/api/sims/%2e%2e/waves.json", "/api/jobs//log"):
    status = call("GET", path)[0]
    check(f"{path} gives a plain 404", status == 404)

# framing, sniffing, cross-site reading
status, headers, _ = call("GET", "/")
low = {k.lower(): v for k, v in headers.items()}
check("the page cannot be framed (clickjacking on Approve)", "frame-ancestors 'none'" in low.get("content-security-policy", "")
      and low.get("x-frame-options") == "DENY")
check("no sniffing, no referrer", low.get("x-content-type-options") == "nosniff" and low.get("referrer-policy") == "no-referrer")
check("no CORS permission is ever given", all("access-control" not in k.lower() for _, h, _ in
      (call("GET", "/"), call("GET", "/api/state"), call("OPTIONS", "/api/board", headers={"Origin": "http://evil.com"}),
       call("POST", "/api/board", headers={"Origin": "http://evil.com"}, body={})) for k in h))

assert not failures, failures
print("\nDASHBOARD SECURITY OK")

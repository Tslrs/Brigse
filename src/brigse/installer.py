"""Run an approved install or uninstall in a separate process so it survives long downloads.

Started only for requests the user approved in the dashboard:
    python -m brigse.installer <request_id>
"""
from __future__ import annotations

import subprocess
import sys

from . import registry, store
from .backends.base import BackendError


def start(request_id: str) -> bool:
    """Launch the installer once for an approved, automatic request."""
    req = store.get_request(request_id)
    if not req or req["state"] != "approved" or not req["plan"].get("automatic"):
        return False
    removing = req["kind"] == "uninstall_backend"
    store.update_request(request_id, state="removing" if removing else "installing", progress="Starting")
    log = open(store.HOME / f"install-{request_id}.log", "ab")
    subprocess.Popen([sys.executable, "-m", "brigse.installer", request_id],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def main() -> None:
    request_id = sys.argv[1]
    req = store.get_request(request_id)
    if not req or req["state"] not in ("installing", "removing"):
        sys.exit("request is not approved for install")
    try:
        backend = registry.by_name(req["subject"])
        run = backend.uninstall if req["kind"] == "uninstall_backend" else backend.install
        result = run(
            plan=req["plan"],
            progress=lambda text: store.update_request(request_id, progress=text))
        store.finish_request(request_id, True, result)
    except BackendError as e:
        store.finish_request(request_id, False, str(e))
    except Exception as e:                      # never leave a request stuck in 'installing'
        store.finish_request(request_id, False, f"Unexpected error: {e!r}")
        raise


if __name__ == "__main__":
    main()

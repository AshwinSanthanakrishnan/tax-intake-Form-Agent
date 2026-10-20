"""A stand-in for the tax firm's intake system.

Serves intake_form/ and accepts submissions at POST /api/intake, issuing an intake ID for each.
It binds to 127.0.0.1 on an OS-assigned port, so it is unreachable from other machines, and
keeps submissions in memory only. In production the form filler would point at the firm's real
intake system instead.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import date, datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .models import DEDUCTIONS, FILING_STATUSES, INCOME_SOURCES, US_STATES

log = logging.getLogger(__name__)

URGENCY_LEVELS = {"low", "medium", "high"}
REQUIRED = (
    "full_name", "email", "phone", "date_of_birth", "street_address", "city", "state", "zip_code",
    "drivers_license_number", "drivers_license_state", "drivers_license_expires_on", "tax_year", "urgency",
)
CHOICES = {
    "state": set(US_STATES),
    "drivers_license_state": set(US_STATES),
    "marital_status": {"single", "married", "divorced", "widowed", "separated"},
    "preferred_contact_method": {"email", "phone"},
    "filing_status": set(FILING_STATUSES),
    "irs_notice_received": {"yes", "no"},
    "consultation_requested": {"yes", "no"},
}
MULTI_CHOICES = {"income_sources": set(INCOME_SOURCES), "deductions": set(DEDUCTIONS)}
DATE_FIELDS = ("date_of_birth", "drivers_license_issued_on", "drivers_license_expires_on", "deadline_date")
MAX_BODY_BYTES = 64 * 1024
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def validate_submission(data: dict[str, Any]) -> list[str]:
    """Server-side checks, independent of the browser's own form validation."""
    problems = [f"{name} is required" for name in REQUIRED if not str(data.get(name) or "").strip()]
    if data.get("urgency") and data["urgency"] not in URGENCY_LEVELS:
        problems.append("urgency must be low, medium, or high")
    for name in DATE_FIELDS:
        if data.get(name) and not _ISO_DATE_RE.match(str(data[name])):
            problems.append(f"{name} must be YYYY-MM-DD")
    if data.get("zip_code") and not _ZIP_RE.match(str(data["zip_code"])):
        problems.append("zip_code must be 5 digits or ZIP+4")
    if data.get("tax_year") and not (
        str(data["tax_year"]).isdigit() and 1990 <= int(data["tax_year"]) <= date.today().year
    ):
        problems.append("tax_year must be a four-digit year no later than the current year")
    if data.get("number_of_dependents") and not (
        str(data["number_of_dependents"]).isdigit() and int(data["number_of_dependents"]) <= 20
    ):
        problems.append("number_of_dependents must be a whole number from 0 to 20")
    for name, allowed in CHOICES.items():
        if data.get(name) and data[name] not in allowed:
            problems.append(f"{name} is not one of the allowed values")
    for name, allowed in MULTI_CHOICES.items():
        value = data.get(name, [])
        if not isinstance(value, list) or not set(value) <= allowed:
            problems.append(f"{name} must be a list of allowed values")
    return problems


def new_intake_id(now: datetime | None = None) -> str:
    return f"TAX-{(now or datetime.now()):%Y%m%d}-{secrets.token_hex(2).upper()}"


class _IntakeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: Any) -> None:
        super().__init__(address, handler)
        self.submissions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()


class _IntakeHandler(SimpleHTTPRequestHandler):
    server: _IntakeHTTPServer

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path != "/api/intake":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request size"})
            return
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be a JSON object"})
            return

        if problems := validate_submission(data):
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "Submission rejected: " + "; ".join(problems), "problems": problems},
            )
            return

        with self.server.lock:
            intake_id = new_intake_id()
            while intake_id in self.server.submissions:
                intake_id = new_intake_id()
            self.server.submissions[intake_id] = {
                **data,
                "intake_id": intake_id,
                "received_at": datetime.now().isoformat(timespec="seconds"),
            }
        log.info("Intake system recorded intake %s", intake_id)
        self._send_json(HTTPStatus.CREATED, {"intake_id": intake_id})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        log.debug("intake-server: " + format, *args)


class IntakeServer:
    def __init__(self, form_dir: Path) -> None:
        if not (form_dir / "index.html").is_file():
            raise FileNotFoundError(f"Intake form not found: {form_dir / 'index.html'}")
        self._form_dir = form_dir
        self._httpd: _IntakeHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("Intake server is not running")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> IntakeServer:
        handler = partial(_IntakeHandler, directory=str(self._form_dir))
        self._httpd = _IntakeHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="intake-server", daemon=True)
        self._thread.start()
        log.info("Local intake system running at %s", self.url)
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def get_submission(self, intake_id: str) -> dict[str, Any] | None:
        if self._httpd is None:
            return None
        with self._httpd.lock:
            return self._httpd.submissions.get(intake_id)

    def __enter__(self) -> IntakeServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

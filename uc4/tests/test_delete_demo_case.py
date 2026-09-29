"""
Deleting one demo case: WAL-DEMO- only, demo builds only, console only. The
case disappears everywhere, its portal link stops working, its id is never
issued again, and the audit trail's append-only guard is back in place after.

Run: python -m pytest tests/test_delete_demo_case.py -q
"""

import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist                      # noqa: E402
from orchestrator import db, demo_cases, settings                     # noqa: E402
from orchestrator.steps import document_quality                      # noqa: E402
from portal import apply, server as portal_server                     # noqa: E402
from tools import make_sample_documents as samples                    # noqa: E402
from web import render, server as console_server                      # noqa: E402

PDF = b"%PDF-1.4\n% synthetic demo file\n"


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    conn = data.connect(path)
    answers = {key: value for _, _, key, value in samples.FORM_VALUES}
    case_id = data.submit_application(conn, apply.build_application(answers, "PORTAL-TEST"))
    item = next(i for i in customer_checklist(conn, case_id)["items"] if i["can_upload"])
    data.upload_document(conn, case_id, item["checklist_item_id"], "certificate.pdf", PDF)
    token = data.issue_portal_access(conn, case_id, "test.link")
    yield {"conn": conn, "path": path, "tmp": tmp_path, "case": case_id, "token": token}
    conn.close()


def rows_for(conn, case_id):
    applicant = conn.execute("SELECT applicant_id FROM onboarding_case WHERE case_id = ?",
                             (case_id,)).fetchone()
    found = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "case_id" in cols:
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE case_id = ?",
                             (case_id,)).fetchone()[0]
            if n:
                found[table] = n
    return found, applicant


def test_a_demo_case_is_deleted_everywhere(demo):
    conn, case_id = demo["conn"], demo["case"]
    before, (applicant,) = rows_for(conn, case_id)
    assert {"onboarding_case", "document", "case_hold", "audit_event", "portal_token"} <= \
        set(before)
    assert (demo["tmp"] / "uploads" / case_id).is_dir()
    assert (demo["tmp"] / "applications" / f"{case_id}.json").exists()

    result = data.delete_demo_case(conn, case_id, "analyst.test")
    assert result["case_id"] == case_id and result["rows"] > 0
    after, _ = rows_for(conn, case_id)
    assert after == {}, after
    for table in ("applicant", "individual"):
        assert not conn.execute(f"SELECT 1 FROM {table} WHERE applicant_id = ?",
                                (applicant,)).fetchone()
    assert not conn.execute("SELECT 1 FROM checklist_item WHERE pack_id NOT IN"
                            " (SELECT pack_id FROM requirement_pack)").fetchone()
    assert not (demo["tmp"] / "uploads" / case_id).exists()
    assert not (demo["tmp"] / "applications" / f"{case_id}.json").exists()
    assert case_id not in [r["case_id"] for r in data.dashboard(conn)]
    for role in ("analyst", "compliance"):
        assert case_id not in [r["case_id"] for r in data.queue(conn, role)]
    log = (demo["tmp"] / "demo_deletions.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "analyst.test" in log[0] and f"deleted {case_id}" in log[0]
    # the audit trail is append-only again
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM audit_event WHERE event_id = (SELECT MIN(event_id)"
                     " FROM audit_event)")


def test_a_scripted_case_can_never_be_deleted(demo):
    conn = demo["conn"]
    before = rows_for(conn, "WAL-ONB-0002")[0]
    for case_id in ("WAL-ONB-0002", "APP-0001", "anything"):
        with pytest.raises(demo_cases.NotADemoCase):
            data.delete_demo_case(conn, case_id, "analyst.test")
    assert rows_for(conn, "WAL-ONB-0002")[0] == before
    assert not (demo["tmp"] / "demo_deletions.log").exists()


def test_not_in_a_demo_build_nothing_can_be_deleted_and_no_button_shows(demo, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_BUILD", False)
    with pytest.raises(PermissionError):
        data.delete_demo_case(demo["conn"], demo["case"], "analyst.test")
    page = render.case_detail(demo["conn"], demo["case"])
    assert "Delete demo case" not in page


def test_the_button_shows_only_on_a_demo_case(demo):
    assert "Delete demo case" in render.case_detail(demo["conn"], demo["case"])
    assert "Delete demo case" not in render.case_detail(demo["conn"], "WAL-ONB-0002")
    for p in (ROOT / "portal").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "delete-demo-case" not in text and "Delete demo case" not in text, p.name


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


def test_through_the_console_with_confirmation_and_the_portal_link_stops(demo):
    conn, case_id, token = demo["conn"], demo["case"], demo["token"]
    console_server.drop_connection()
    console_server._conn = data.connect(demo["path"])
    console = console_server.serve(0)
    portal = portal_server.serve(0, db_path=demo["path"], demo=True,
                                 uploads_dir=demo["tmp"] / "uploads")
    for httpd in (console, portal):
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cbase = f"http://127.0.0.1:{console.server_address[1]}"
    pbase = f"http://127.0.0.1:{portal.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                         NoRedirect())

    def go(url, fields=None):
        req = urllib.request.Request(url, urlencode(fields).encode() if fields else None)
        try:
            with opener.open(req) as r:
                return r.status, r.headers.get("Location")
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location")
    try:
        assert go(pbase + "/access/" + token)[0] == 303, "the link works before"
        status, where = go(cbase + "/action/delete-demo-case",
                           {"case_id": case_id, "back": "/"})
        assert status == 303 and "err=" in where, "no confirmation, no deletion"
        assert data.connect(demo["path"]).execute(
            "SELECT 1 FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
        status, where = go(cbase + "/action/delete-demo-case",
                           {"case_id": case_id, "confirm": case_id, "back": "/"})
        assert status == 303 and "ok=Deleted" in where
        assert go(cbase + "/case/" + case_id)[0] == 404
        assert go(pbase + "/access/" + token)[0] == 401, "the portal link stops working"
        status, where = go(cbase + "/action/delete-demo-case",
                           {"case_id": "WAL-ONB-0002", "confirm": "WAL-ONB-0002", "back": "/"})
        assert "err=NotADemoCase" in where
    finally:
        for httpd in (console, portal):
            httpd.shutdown()
            httpd.server_close()
        console_server.drop_connection()

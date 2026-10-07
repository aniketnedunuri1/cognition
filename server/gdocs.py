"""Google Doc render of a saved brief, via a service account.

The target folder must be in a Shared Drive the service account is a member of:
service accounts have no Drive storage quota of their own. Re-running for the same
brief updates the same Doc, so its link and comment threads survive.
"""

from __future__ import annotations

import io
import json
import os

import markdown

from .briefs import load_record, render_slack
from .cache import ROOT

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _drive():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    path = path if os.path.isabs(path) else str(ROOT / path)
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def configured() -> bool:
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    path = path if os.path.isabs(path) else str(ROOT / path)
    return bool(path and os.path.isfile(path) and os.environ.get("GOOGLE_DRIVE_FOLDER_ID"))


def create_brief_doc(domain: str, day: str | None = None) -> dict:
    """Create (or update) the Google Doc for the latest saved brief. Returns its link
    and the Slack summary with the link filled in."""
    from googleapiclient.http import MediaIoBaseUpload

    found = load_record(domain, day)
    if not found:
        return {"status": "no_brief", "reason": "save_brief first"}
    path, rec = found
    md_path = path.with_suffix(".md")
    if not configured():
        return {"status": "not_configured", "markdown": str(md_path),
                "slack_summary": render_slack(rec),
                "reason": "set GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_DRIVE_FOLDER_ID (a Shared Drive folder)"}

    html = markdown.markdown(md_path.read_text(), extensions=["tables"])
    media = MediaIoBaseUpload(io.BytesIO(html.encode()), mimetype="text/html", resumable=False)
    drive = _drive()
    if rec.get("doc_id"):
        f = drive.files().update(fileId=rec["doc_id"], media_body=media, supportsAllDrives=True,
                                 fields="id,webViewLink").execute()
    else:
        meta = {"name": f"{rec['brief']['company_name']} — account brief ({rec['date']})",
                "mimeType": "application/vnd.google-apps.document",
                "parents": [os.environ["GOOGLE_DRIVE_FOLDER_ID"]]}
        f = drive.files().create(body=meta, media_body=media, supportsAllDrives=True,
                                 fields="id,webViewLink").execute()
    rec["doc_id"], rec["doc_url"] = f["id"], f["webViewLink"]
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    return {"status": "ok", "url": rec["doc_url"], "slack_summary": render_slack(rec)}

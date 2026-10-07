"""Google Doc render of a saved brief, in the rep's own Drive (one-time user OAuth).

    python -m server.gdocs auth    # once: browser consent, saves a refresh token in secrets/

Scope is drive.file: the app can only see files it created. Briefs go in a folder
the app creates on first use. Re-running for the same brief updates the same Doc,
so its link and comment threads survive.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import markdown

from .briefs import load_record, render_slack
from .cache import ROOT

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "Account research briefs"


def _path(env: str, default: str) -> Path:
    p = Path(os.environ.get(env, default))
    return p if p.is_absolute() else ROOT / p


def client_file() -> Path:
    return _path("GOOGLE_OAUTH_CLIENT_FILE", "secrets/google-oauth-client.json")


def token_file() -> Path:
    return _path("GOOGLE_TOKEN_FILE", "secrets/google-token.json")


def configured() -> bool:
    return token_file().is_file()


def _creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_file()), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds, folder_id=_token_extra().get("folder_id"))
    return creds


def _token_extra() -> dict:
    return json.loads(token_file().read_text()).get("_account_research", {})


def _save_token(creds, folder_id: str | None = None) -> None:
    data = json.loads(creds.to_json())
    if folder_id:
        data["_account_research"] = {"folder_id": folder_id}
    token_file().parent.mkdir(parents=True, exist_ok=True)
    token_file().write_text(json.dumps(data))
    os.chmod(token_file(), 0o600)


def _drive():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)


def _folder(drive) -> str:
    """The app's own briefs folder (drive.file can't write into folders it didn't create)."""
    folder_id = _token_extra().get("folder_id")
    if folder_id:
        return folder_id
    f = drive.files().create(body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
                             fields="id").execute()
    _save_token(_creds(), folder_id=f["id"])
    return f["id"]


def authorize() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_file().is_file():
        raise SystemExit(f"Put your OAuth client JSON (type: Desktop app) at {client_file()}")
    creds = InstalledAppFlow.from_client_secrets_file(str(client_file()), SCOPES).run_local_server(port=0)
    _save_token(creds)
    print(f"Authorized. Token saved to {token_file()}. Briefs will go to Drive folder '{FOLDER_NAME}'.")


def create_brief_doc(domain: str, day: str | None = None) -> dict:
    """Create (or update) the Google Doc for the latest saved brief. Returns its link and
    the Slack summary with the link filled in."""
    found = load_record(domain, day)
    if not found:
        return {"status": "no_brief", "reason": "save_brief first"}
    path, rec = found
    md_path = path.with_suffix(".md")
    if not configured():
        return {"status": "not_configured", "markdown": str(md_path), "slack_summary": render_slack(rec),
                "reason": "Google Docs not authorized: run `python -m server.gdocs auth` once"}

    from googleapiclient.http import MediaIoBaseUpload

    html = markdown.markdown(md_path.read_text(), extensions=["tables"])
    media = MediaIoBaseUpload(io.BytesIO(html.encode()), mimetype="text/html", resumable=False)
    drive = _drive()
    if rec.get("doc_id"):
        f = drive.files().update(fileId=rec["doc_id"], media_body=media, fields="id,webViewLink").execute()
    else:
        meta = {"name": f"{rec['brief']['company_name']} — account brief ({rec['date']})",
                "mimeType": "application/vnd.google-apps.document", "parents": [_folder(drive)]}
        f = drive.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
    rec["doc_id"], rec["doc_url"] = f["id"], f["webViewLink"]
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    return {"status": "ok", "url": rec["doc_url"], "slack_summary": render_slack(rec)}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["auth"]:
        raise SystemExit("usage: python -m server.gdocs auth")
    authorize()

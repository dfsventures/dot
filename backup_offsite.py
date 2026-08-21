#!/home/joey/dot/venv/bin/python
"""
WS-18A — upload the newest local backup pair to Dropbox /Dot Backups/.

Separate from ingest.py's /Dot Dump watch — this folder is never scanned by
the ingestion cron job, so backup files can never be mistaken for a document
to ingest. Uses the same already-authenticated Dropbox app as agent.py /
ingest.py: no new dependency, no new account, no new cost line.

Usage: venv/bin/python backup_offsite.py <local db path> <local sessions tar path>
"""
import os, sys
import dropbox as dbx_lib
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.dot.env'))

DEST_FOLDER = "/Dot Backups"


def _ensure_folder(dbx, path):
    try:
        dbx.files_create_folder_v2(path)
    except dbx_lib.exceptions.ApiError as e:
        err = e.error
        if err.is_path() and err.get_path().is_conflict():
            return  # already exists — fine
        raise


def upload(dbx, local_path):
    if not os.path.exists(local_path):
        print(f"  Skipping missing file: {local_path}")
        return
    name = os.path.basename(local_path)
    dest = f"{DEST_FOLDER}/{name}"
    with open(local_path, "rb") as f:
        dbx.files_upload(f.read(), dest, mode=dbx_lib.files.WriteMode.overwrite)
    print(f"  Uploaded {local_path} -> {dest}")


def main():
    if len(sys.argv) != 3:
        print("Usage: backup_offsite.py <db_path> <sessions_tar_path>")
        sys.exit(1)

    dbx = dbx_lib.Dropbox(
        oauth2_access_token=os.getenv("DROPBOX_TOKEN"),
        oauth2_refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN"),
        app_key=os.getenv("DROPBOX_APP_KEY"),
        app_secret=os.getenv("DROPBOX_APP_SECRET"),
    )
    _ensure_folder(dbx, DEST_FOLDER)
    upload(dbx, sys.argv[1])
    upload(dbx, sys.argv[2])


if __name__ == "__main__":
    main()

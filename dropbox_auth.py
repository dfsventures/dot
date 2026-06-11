import os
import dropbox
from dropbox.oauth import DropboxOAuth2FlowNoRedirect
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.dot.env'))

# Get these from your App Console → Settings tab and put them in .dot.env
APP_KEY = os.getenv("DROPBOX_APP_KEY")
APP_SECRET = os.getenv("DROPBOX_APP_SECRET")

auth_flow = DropboxOAuth2FlowNoRedirect(
    APP_KEY,
    APP_SECRET,
    token_access_type='offline'
)

authorize_url = auth_flow.start()
print("1. Go to:", authorize_url)
print("2. Click Allow")
print("3. Copy the authorization code")

auth_code = input("Paste code here: ").strip()

oauth_result = auth_flow.finish(auth_code)

print(f"\nACCESS_TOKEN={oauth_result.access_token}")
print(f"REFRESH_TOKEN={oauth_result.refresh_token}")


from google_auth_oauthlib.flow import InstalledAppFlow
import pickle
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/calendar'
]
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=8081)
with open('token_work.pickle', 'wb') as f:
    pickle.dump(creds, f)
print("Work Gmail token saved.")


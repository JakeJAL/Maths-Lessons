"""Per-user Google OAuth — web server flow for Cloud Run."""

import os
import json
from flask import session, request
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def get_user_creds():
    """Get the current user's Google credentials from the session.

    Returns Credentials or None if not logged in.
    """
    token_data = session.get("google_token")
    if not token_data:
        return None

    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Update session with refreshed token
            session["google_token"] = json.loads(creds.to_json())
        except Exception:
            # Token refresh failed — user needs to re-login
            session.pop("google_token", None)
            return None

    return creds if creds and creds.valid else None


def is_logged_in():
    """Check if the current user has valid Google credentials."""
    return get_user_creds() is not None


def get_login_url():
    """Generate the Google OAuth login URL."""
    import urllib.parse
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5000/auth/callback")
    client_id = os.getenv("GOOGLE_CLIENT_ID")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)


def handle_callback():
    """Handle the OAuth callback and store credentials in session."""
    import requests as req

    code = request.args.get("code")
    if not code:
        raise ValueError("No authorization code received")

    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5000/auth/callback")

    # Exchange code for tokens directly — no PKCE
    token_resp = req.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })

    if token_resp.status_code != 200:
        raise ValueError(f"Token exchange failed: {token_resp.text}")

    token_data = token_resp.json()

    # Build credentials dict for session storage
    creds_info = {
        "token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "scopes": SCOPES,
    }
    session["google_token"] = creds_info

    # Fetch user info
    user_resp = req.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={
        "Authorization": f"Bearer {token_data['access_token']}"
    })
    if user_resp.status_code == 200:
        user_info = user_resp.json()
        session["user_email"] = user_info.get("email", "")
        session["user_name"] = user_info.get("name", "")
    session["user_email"] = user_info.get("email", "")
    session["user_name"] = user_info.get("name", "")


def logout():
    """Clear the user's session."""
    session.pop("google_token", None)
    session.pop("user_email", None)
    session.pop("user_name", None)
    session.pop("oauth_state", None)

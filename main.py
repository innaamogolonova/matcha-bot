import base64
import os
import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional
from email.message import EmailMessage
from datetime import datetime
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scope allows sending mail. Restrict to send-only.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

PHRASE_LIST = [
    "sold out",          # common
    "sold-out",          # with hyphen/nbsp
    "out of stock",
    "notify me when available",
    "email when available",
]

def _any_in_stock_from_jsonld(soup: BeautifulSoup) -> Optional[bool]:
    """
    Returns True if any offer shows InStock, False if all are OutOfStock,
    None if JSON-LD not found or unparsable.
    """
    try:
        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            # Some sites put multiple JSON objects in one tag or wrap in arrays
            raw = tag.string or tag.text or ""
            if not raw.strip():
                continue
            data = json.loads(raw)
            # Normalize to list
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") == "Product" or "offers" in item:
                    offers = item.get("offers", [])
                    offers = offers if isinstance(offers, list) else [offers]
                    statuses = []
                    for off in offers:
                        if not isinstance(off, dict):
                            continue
                        avail = str(off.get("availability", "")).lower()
                        if "instock" in avail:
                            return True
                        if "outofstock" in avail or "out_of_stock" in avail:
                            statuses.append(False)
                    if statuses and all(s is False for s in statuses):
                        return False
        return None
    except Exception:
        return None

def check_availability(url: str) -> bool:
    """
    Returns True only if the product appears available.
    Prefers JSON-LD 'offers.availability'; falls back to heuristics.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 1) JSON-LD (most reliable)
        jsonld_result = _any_in_stock_from_jsonld(soup)
        if jsonld_result is not None:
            return jsonld_result  # True = in stock, False = out of stock

        # 2) Heuristics on visible text (case/nbsp safe)
        text = soup.get_text(" ", strip=True).casefold()
        if any(p in text for p in PHRASE_LIST):
            return False

        # If nothing screams “sold out”, assume not available only if we saw no cart cues.
        # Optional: be conservative and default to False.
        return False
    except requests.RequestException as e:
        print(f"[{datetime.now()}] Network/HTTP error: {e}")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] Parse error: {e}")
        return False


def get_gmail_service() -> any:
    """
    Returns an authenticated Gmail API service client.
    On first run, opens a browser for consent and stores token.json for reuse.
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # client_secret.json comes from Google Cloud Console (Desktop app)
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for next runs
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

def create_message(from_addr: str, to_addr: str, subject: str, body: str) -> dict:
    """
    Creates a Gmail API message payload from an EmailMessage.
    """
    msg = EmailMessage()
    msg["To"] = to_addr
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg.set_content(body)

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": encoded}

def send_email(config_path: str = "config.json") -> None:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    service = get_gmail_service()
    message = create_message(
        from_addr=cfg["fromAddress"],
        to_addr=cfg["toAddress"],
        subject=cfg["subject"],
        body=cfg["body"],
    )

    # "me" means the authenticated user
    sent = service.users().messages().send(userId="me", body=message).execute()
    print(f"Email sent. Gmail message id: {sent.get('id')}")


def main() -> None:
    url = "https://ippodotea.com/collections/matcha/products/ikuyo-100"
    available = check_availability(url)

    if available:
        send_email("config.json")
        print("Notification sent.")
    else:
        print("Not available (or fetch failed).")

if __name__ == "__main__":
    main()

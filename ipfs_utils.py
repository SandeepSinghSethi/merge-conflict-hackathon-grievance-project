
import os
import requests


# PINATA_JWT          = os.environ.get("PINATA_JWT",          "")
PINATA_JWT          = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySW5mb3JtYXRpb24iOnsiaWQiOiJhMTFmOWRhZi03MjhhLTRjZGMtODY1OS1hMjQ2MWFjYmI3OTkiLCJlbWFpbCI6InBhcnJvdGJhazFAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsInBpbl9wb2xpY3kiOnsicmVnaW9ucyI6W3siZGVzaXJlZFJlcGxpY2F0aW9uQ291bnQiOjEsImlkIjoiRlJBMSJ9LHsiZGVzaXJlZFJlcGxpY2F0aW9uQ291bnQiOjEsImlkIjoiTllDMSJ9XSwidmVyc2lvbiI6MX0sIm1mYV9lbmFibGVkIjpmYWxzZSwic3RhdHVzIjoiQUNUSVZFIn0sImF1dGhlbnRpY2F0aW9uVHlwZSI6InNjb3BlZEtleSIsInNjb3BlZEtleUtleSI6IjNlZGQ1OWZlNjg5MmQ1ODcwOTViIiwic2NvcGVkS2V5U2VjcmV0IjoiNWU4ZDNkZTczYmNmMWFiZDgzY2ViNjg2YjBkYjAzNDk0NTY5ZGU2ZDM1NmNiZGU3NzdjM2YwNmFmZjE0ZmMxNiIsImV4cCI6MTgwMTM2NTE4M30.QVc92OEom70e5_rR2M2o8vBH23x0CPHPw7yp81gUHTQ"
# PINATA_GATEWAY_URL  = os.environ.get("PINATA_GATEWAY_URL",  "")   # e.g. https://your-name.gateway.pinatas.cloud
PINATA_GATEWAY_URL = "https://scarlet-deep-firefly-359.mypinata.cloud"
_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"
_PUBLIC_GW  = "https://ipfs.io"                          # last-resort fallback

_TIMEOUT = 60   

def _require_jwt():
    if not PINATA_JWT:
        raise RuntimeError(
            "PINATA_JWT is not set.\n"
            "Create a JWT at https://app.pinata.cloud/developers/api-keys\n"
            "then export PINATA_JWT='eyJhbG…'"
        )

def _auth_header() -> dict:
    return {"Authorization": f"Bearer {PINATA_JWT}"}


def upload(data: bytes, filename: str = "grievance.enc") -> str:

    _require_jwt()

    try:
        resp = requests.post(
            _UPLOAD_URL,
            headers=_auth_header(),
            files={
                "file": (filename, data, "application/octet-stream"),
            },
            data={
                "network": "public",   # pin on the public IPFS network
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Pinata v3 upload failed -> {exc}\n"
            f"Status: {getattr(getattr(exc, 'response', None), 'status_code', '?')}\n"
            f"Body:   {getattr(getattr(exc, 'response', None), 'text', '(empty)')}"
        ) from exc

    try:
        cid = resp.json()["data"]["cid"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            f"Pinata v3 response has no data.cid field.\n"
            f"Full body: {resp.text}"
        ) from exc

    return cid


def fetch(cid: str) -> bytes:

    if PINATA_GATEWAY_URL:
        url = f"{PINATA_GATEWAY_URL.rstrip('/')}/ipfs/{cid}"
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException:
            pass   # fall through to public gateway

    url = f"{_PUBLIC_GW}/ipfs/{cid}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        gw_tried = PINATA_GATEWAY_URL or "(not set)"
        raise RuntimeError(
            f"IPFS fetch failed for CID {cid}.\n"
            f"  Dedicated gateway tried : {gw_tried}\n"
            f"  Public gateway tried    : {_PUBLIC_GW}\n"
            f"  Last error              : {exc}"
        ) from exc
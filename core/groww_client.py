import hashlib
import logging
import time

import requests

log = logging.getLogger("groww")
REFRESH_MIN_INTERVAL = 600  # the token endpoint is limited to 150 calls / 24h


class GrowwAuthError(Exception):
    pass


class GrowwAPIError(Exception):
    pass


class TokenManager:
    """Fetches a Groww access token from an API key + secret (approval flow) or key + TOTP secret."""

    def __init__(self, api_key: str, secret: str = "", totp_secret: str = "",
                 base_url: str = "https://api.groww.in/v1", http=None, timeout: int = 10):
        self.api_key, self.secret, self.totp_secret = api_key, secret, totp_secret
        self.url = f"{base_url.rstrip('/')}/token/api/access"
        self.http, self.timeout = http or requests.Session(), timeout

    @property
    def flow(self) -> str:
        return "totp" if self.totp_secret else "approval"

    def fetch(self) -> str:
        if self.flow == "totp":
            import pyotp
            body = {"key_type": "totp", "totp": pyotp.TOTP(self.totp_secret).now()}
        else:
            ts = str(int(time.time()))
            checksum = hashlib.sha256((self.secret + ts).encode("utf-8")).hexdigest()
            body = {"key_type": "approval", "checksum": checksum, "timestamp": ts}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        try:
            r = self.http.post(self.url, headers=headers, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise GrowwAuthError(f"token request failed: {e}") from e
        if r.status_code >= 400:
            hint = " (approve the API key on the Groww Cloud API Keys page first)" if self.flow == "approval" else ""
            raise GrowwAuthError(f"token request rejected: HTTP {r.status_code}{hint}")
        try:
            data = r.json()
        except ValueError as e:
            raise GrowwAuthError("token response was not JSON") from e
        data = data.get("payload", data) if isinstance(data, dict) else {}
        token = data.get("token")
        if not token or data.get("isActive") is False:
            raise GrowwAuthError(f"no active token in response (is the key approved today?): {str(data)[:120]}")
        log.info("Fetched new Groww access token via %s flow (expiry %s)", self.flow, data.get("expiry"))
        return token


class GrowwClient:
    def __init__(self, access_token, base_url="https://api.groww.in/v1", version="1.0",
                 max_retries=3, retry_backoff=2.0, timeout=10, session=None, token_manager: TokenManager | None = None):
        self.base_url = base_url.rstrip("/")
        self.max_retries, self.retry_backoff, self.timeout = max_retries, retry_backoff, timeout
        self.http = session or requests.Session()
        self.tm = token_manager
        self._last_refresh = 0.0
        self.headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}", "X-API-VERSION": version}

    @property
    def has_token(self) -> bool:
        return self.headers["Authorization"] != "Bearer "

    def set_token(self, token: str) -> None:
        self.headers["Authorization"] = f"Bearer {token}"

    def refresh_token(self, force: bool = False) -> str:
        """Fetch a new token with the token manager. Throttled unless force=True. Raises GrowwAuthError."""
        if not self.tm:
            raise GrowwAuthError("no API key/secret configured for automatic token refresh")
        if not force and time.time() - self._last_refresh < REFRESH_MIN_INTERVAL:
            raise GrowwAuthError("token refresh throttled; use /refreshtoken to force")
        self._last_refresh = time.time()
        token = self.tm.fetch()
        self.set_token(token)
        return token

    def _request(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        last = None
        refreshed = False
        if not self.has_token and self.tm:
            self.refresh_token()
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.get(url, headers=self.headers, params=params, timeout=self.timeout)
                log.debug("GET %s %s -> %s", path, params, r.status_code)
                if r.status_code in (401, 403):
                    if self.tm and not refreshed:
                        refreshed = True
                        log.warning("HTTP %s: token rejected, refreshing", r.status_code)
                        self.refresh_token()
                        r = self.http.get(url, headers=self.headers, params=params, timeout=self.timeout)
                    if r.status_code in (401, 403):
                        raise GrowwAuthError(f"HTTP {r.status_code}: access token invalid or expired ({str(r.text)[:150]})")
                if r.status_code == 429 or r.status_code >= 500:
                    raise GrowwAPIError(f"HTTP {r.status_code}")
                r.raise_for_status()
                body = r.json()
                if body.get("status") != "SUCCESS":
                    raise GrowwAPIError(f"API failure: {body}")
                return body["payload"]
            except GrowwAuthError:
                raise
            except (requests.RequestException, GrowwAPIError, ValueError) as e:
                last = e
                log.warning("request failed (%d/%d): %s", attempt + 1, self.max_retries + 1, e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * 2 ** attempt)
        raise GrowwAPIError(f"giving up after retries: {last}")

    def get_ltp(self, exchange_symbols: list[str], segment: str = "COMMODITY") -> dict[str, float]:
        if not exchange_symbols:
            return {}
        out: dict[str, float] = {}
        for i in range(0, len(exchange_symbols), 50):
            chunk = exchange_symbols[i:i + 50]
            payload = self._request("/live-data/ltp", {"segment": segment, "exchange_symbols": ",".join(chunk)})
            out.update({k: float(v) for k, v in payload.items()})
        return out

    def get_quote(self, exchange: str, segment: str, trading_symbol: str) -> dict:
        return self._request("/live-data/quote", {"exchange": exchange, "segment": segment, "trading_symbol": trading_symbol})

import logging
import time

import requests

log = logging.getLogger("groww")


class GrowwAuthError(Exception):
    pass


class GrowwAPIError(Exception):
    pass


class GrowwClient:
    def __init__(self, access_token, base_url="https://api.groww.in/v1", version="1.0",
                 max_retries=3, retry_backoff=2.0, timeout=10, session=None):
        self.base_url = base_url.rstrip("/")
        self.max_retries, self.retry_backoff, self.timeout = max_retries, retry_backoff, timeout
        self.http = session or requests.Session()
        self.headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}", "X-API-VERSION": version}

    def _request(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.get(url, headers=self.headers, params=params, timeout=self.timeout)
                log.debug("GET %s %s -> %s", path, params, r.status_code)
                if r.status_code in (401, 403):
                    raise GrowwAuthError(f"HTTP {r.status_code}: access token invalid or expired")
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

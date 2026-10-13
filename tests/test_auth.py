import hashlib
import os

import pytest
import requests

from core.groww_client import GrowwAuthError, GrowwClient, TokenManager
from tests.test_core import FakeHTTP, FakeResp

OK = {"status": "SUCCESS", "payload": {"MCX_A": 1.5}}


class PostHTTP(FakeHTTP):
    def __init__(self, gets=(), posts=()):
        super().__init__(gets)
        self.posts, self.post_calls = list(posts), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.post_calls.append((url, headers, json))
        r = self.posts.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_approval_flow_request_shape(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1719830400.4)
    h = PostHTTP(posts=[FakeResp(200, {"token": "TOK", "isActive": True, "expiry": "x"})])
    tm = TokenManager("KEY", "SECRET", http=h)
    assert tm.fetch() == "TOK"
    url, headers, body = h.post_calls[0]
    assert url == "https://api.groww.in/v1/token/api/access" and headers["Authorization"] == "Bearer KEY"
    assert body == {"key_type": "approval", "timestamp": "1719830400",
                    "checksum": hashlib.sha256(b"SECRET1719830400").hexdigest()}


def test_totp_flow_and_payload_wrapped_response():
    import pyotp
    secret = pyotp.random_base32()
    h = PostHTTP(posts=[FakeResp(200, {"payload": {"token": "T2", "isActive": True}})])
    tm = TokenManager("KEY", totp_secret=secret, http=h)
    assert tm.flow == "totp" and tm.fetch() == "T2"
    body = h.post_calls[0][2]
    assert body["key_type"] == "totp" and len(body["totp"]) == 6 and "checksum" not in body


@pytest.mark.parametrize("resp", [FakeResp(401, {}), FakeResp(200, {"isActive": False, "token": "x"}),
                                  FakeResp(200, {}), requests.ConnectionError("down")])
def test_token_fetch_failures_raise_auth_error(resp):
    with pytest.raises(GrowwAuthError):
        TokenManager("K", "S", http=PostHTTP(posts=[resp])).fetch()


def test_client_fetches_token_on_start_and_refreshes_on_403(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    h = PostHTTP(gets=[FakeResp(200, OK), FakeResp(403), FakeResp(200, OK)],
                 posts=[FakeResp(200, {"token": "T1"}), FakeResp(200, {"token": "T2"})])
    c = GrowwClient("", session=h, token_manager=TokenManager("K", "S", http=h))
    assert c.get_ltp(["MCX_A"]) == {"MCX_A": 1.5}                      # no token yet -> fetched first
    assert h.calls[0][1]["Authorization"] == "Bearer T1"
    c._last_refresh -= 1000                                            # allow another refresh
    assert c.get_ltp(["MCX_A"]) == {"MCX_A": 1.5}                      # 403 -> new token -> retried
    assert h.calls[-1][1]["Authorization"] == "Bearer T2" and len(h.post_calls) == 2


def test_refresh_is_throttled_and_failure_surfaces():
    h = PostHTTP(gets=[FakeResp(403)] * 3, posts=[FakeResp(200, {"token": "T1"})])
    c = GrowwClient("old", session=h, token_manager=TokenManager("K", "S", http=h))
    with pytest.raises(GrowwAuthError):
        c.get_ltp(["X"])                                               # refreshed once, still 403
    assert len(h.post_calls) == 1
    with pytest.raises(GrowwAuthError, match="throttled"):
        c.refresh_token()
    # without credentials a 403 is simply an auth error
    with pytest.raises(GrowwAuthError):
        GrowwClient("t", session=PostHTTP(gets=[FakeResp(403)])).get_ltp(["X"])


def test_config_accepts_key_secret_without_token(cfg):
    from core.config import validate
    cfg["api"].update(access_token="", api_key="K", api_secret="S")
    validate(cfg)
    cfg["api"].update(api_secret="", totp_secret="")
    with pytest.raises(ValueError):
        validate(cfg)
    cfg["api"].update(totp_secret="BASE32")
    validate(cfg)


def test_bot_builds_token_manager_from_config(cfg, base_dir):
    from bot import Bot
    cfg["api"].update(access_token="", api_key="K", api_secret="S")
    b = Bot(cfg, base_dir=base_dir)
    assert isinstance(b.client, GrowwClient) and b.client.tm and b.client.tm.flow == "approval"
    cfg["api"].update(api_key="", api_secret="")
    assert Bot(cfg, base_dir=base_dir).client.tm is None


def test_telegram_setkeys_and_refreshtoken(cfg, base_dir, monkeypatch):
    from bot import Bot
    from core.telegram_control import Controller

    h = PostHTTP(posts=[FakeResp(200, {"token": "NEWTOK"}), FakeResp(401, {}), FakeResp(200, {"token": "T3"})])
    b = Bot(cfg, client=GrowwClient("", session=h), base_dir=base_dir)
    monkeypatch.setattr("core.groww_client.requests.Session", lambda: h)
    ctl = Controller(b, base_dir / "c.yaml", base_dir / ".env", "679902", base_dir / "a.json")
    ctl.handle(1, "login", ["679902"])
    for k in ("GROWW_API_KEY", "GROWW_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    reply, delete = ctl.handle(1, "setkeys", ["MYKEY", "MYSECRET"])
    assert delete and "fetched" in reply and b.client.headers["Authorization"] == "Bearer NEWTOK"
    env = (base_dir / ".env").read_text()
    assert "GROWW_API_KEY=MYKEY" in env and "GROWW_API_SECRET=MYSECRET" in env
    assert "Usage" in ctl.handle(1, "setkeys", ["onlyone"])[0]
    reply, _ = ctl.handle(1, "refreshtoken", [])           # not approved -> 401
    assert "failed" in reply and "approve" in reply
    assert "fetched" in ctl.handle(1, "refreshtoken", [])[0] and b.client.headers["Authorization"] == "Bearer T3"
    for k in ("GROWW_API_KEY", "GROWW_API_SECRET"):
        monkeypatch.delenv(k, raising=False)

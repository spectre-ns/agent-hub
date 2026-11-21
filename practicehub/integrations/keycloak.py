"""Keycloak as the practice's identity provider (optional).

Each member becomes a Keycloak principal:

* a person is a **user** in the realm (username = member id), who signs in with the practice's own password policy, MFA,
  etc., all of it Keycloak's job;
* an agent or service is a **confidential client** ``member-<id>`` with a *service account*, so it has its own
  credentials, its own sessions in Keycloak's audit, and can be switched off in Keycloak as well as in the hub.

Agents get their tokens with the client-credentials grant and, where an agent acts in a user's session (e.g. a portal
request), exchange them (RFC 8693 token exchange) for a token whose audience is the hub. The hub checks every Keycloak
token by **introspection** (RFC 7662) with its own client, so no JWT library or key handling lives in the hub, and a
token revoked in Keycloak stops working at once.

Keycloak says *who* is calling; the hub (and OpenFGA) still decide *what they may do*. A token only maps to a member
that is active in the hub, so the kill switch works whichever side it is pulled on.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

HUB_CLIENT = "practicehub"


def client_id_for(member_id: str) -> str:
    return f"member-{member_id}"


class KeycloakAuth:
    """Maps a Keycloak access token to a hub member, by introspection."""

    def __init__(self, base_url: str, realm: str, client_id: str, client_secret: str, cache_seconds: float = 30):
        self.base = base_url.rstrip("/")
        self.realm, self.client_id, self.client_secret = realm, client_id, client_secret
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, dict | None]] = {}

    @property
    def token_url(self) -> str:
        return f"{self.base}/realms/{self.realm}/protocol/openid-connect/token"

    def introspect(self, token: str) -> dict:
        body = urllib.parse.urlencode({"token": token}).encode()
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(f"{self.token_url}/introspect", data=body, method="POST",
                                     headers={"Authorization": f"Basic {basic}",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def member_id(self, claims: dict) -> str | None:
        """The hub member a token's claims name: a service account's client, or a user's username."""
        if not claims.get("active"):
            return None
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud] if aud else []
        if HUB_CLIENT not in auds and claims.get("azp") != HUB_CLIENT:
            return None  # a token for some other application is not a token for the hub
        username = claims.get("preferred_username") or ""
        if username.startswith("service-account-member-"):
            return username[len("service-account-member-"):]
        client = claims.get("client_id") or claims.get("azp") or ""
        if client.startswith("member-") and not username:
            return client[len("member-"):]
        return username or None

    def authenticate(self, hub, token: str) -> dict | None:
        now = time.monotonic()
        hit = self._cache.get(token)
        if hit and now - hit[0] < self.cache_seconds:
            mid = hit[1]
        else:
            try:
                mid = self.member_id(self.introspect(token))
            except (urllib.error.URLError, ValueError):
                return None
            self._cache[token] = (now, mid)
            if len(self._cache) > 5000:
                self._cache.clear()
        if not mid:
            return None
        try:
            m = hub.member(mid)
        except Exception:
            return None
        return m if m["status"] == "active" else None


def client_credentials(base_url: str, realm: str, client_id: str, client_secret: str, audience: str = HUB_CLIENT) -> str:
    """An agent's access token: client credentials, then a token exchange for the hub's audience."""
    url = f"{base_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"

    def post(form):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    tok = post({"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret})
    try:
        ex = post({"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": client_id,
                   "client_secret": client_secret, "subject_token": tok["access_token"],
                   "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": audience})
        return ex["access_token"]
    except urllib.error.HTTPError:
        return tok["access_token"]  # token exchange not enabled: the audience mapper below still names the hub


def realm_export(hub, realm: str = "practice", hub_redirect: str = "http://localhost:8200/*") -> dict:
    """A Keycloak realm (import JSON) with every hub member: users for people, service-account clients for agents.

    No secrets are written: Keycloak generates client secrets on import, and people set passwords on first sign-in.
    """
    audience_mapper = {"name": "hub-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                       "config": {"included.client.audience": HUB_CLIENT, "access.token.claim": "true",
                                  "id.token.claim": "false"}}
    clients = [{
        "clientId": HUB_CLIENT, "name": hub.practice.get("name", "Practice Hub"), "enabled": True,
        "publicClient": False, "standardFlowEnabled": True, "serviceAccountsEnabled": False,
        "redirectUris": [hub_redirect], "protocolMappers": [audience_mapper],
        "attributes": {"standard.token.exchange.enabled": "true"},
    }]
    users = []
    for m in hub.members():
        attrs = {"practicehub_member": [m["id"]], "practicehub_kind": [m["kind"]]}
        if m["sponsor"]:
            attrs["practicehub_sponsor"] = [m["sponsor"]]
        if m["kind"] == "person":
            first, _, last = m["name"].replace("Dr. ", "").partition(" ")
            users.append({"username": m["id"], "enabled": m["status"] == "active", "firstName": first,
                          "lastName": last, "attributes": attrs,
                          "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]})
        else:
            cid = client_id_for(m["id"])
            clients.append({
                "clientId": cid, "name": m["name"], "description": f"{m['kind']} member, sponsored by {m['sponsor']}",
                "enabled": m["status"] == "active", "publicClient": False, "serviceAccountsEnabled": True,
                "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                "protocolMappers": [audience_mapper],
                "attributes": {"standard.token.exchange.enabled": "true", "access.token.lifespan": "300"},
            })
            users.append({"username": f"service-account-{cid}", "enabled": m["status"] == "active",
                          "serviceAccountClientId": cid, "attributes": attrs})
    return {"realm": realm, "enabled": True, "sslRequired": "external", "accessTokenLifespan": 300,
            "bruteForceProtected": True, "passwordPolicy": "length(12) and notUsername",
            "clients": clients, "users": users}

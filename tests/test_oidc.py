"""Tier-1 OIDC tests: real authlib validation against a locally-minted JWKS —
no live IdP, no mocks of the crypto. Covers the role mapping, every rejection
path, and the break-glass policy."""
from __future__ import annotations

import time

import pytest
from authlib.jose import JsonWebKey, jwt

from markwright.api.auth import OIDCError, OIDCValidator

ISSUER = "https://idp.test/realms/markwright"
KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
OTHER_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
JWKS = {"keys": [KEY.as_dict(private=False)]}


def mint(roles=("operator",), *, issuer=ISSUER, aud="markwright", exp_in=600,
         key=KEY, claim_shape="flat", sub="kim", username="kim@corp"):
    claims = {"iss": issuer, "aud": aud, "sub": sub, "preferred_username": username,
              "exp": int(time.time()) + exp_in, "iat": int(time.time())}
    if claim_shape == "flat":
        claims["roles"] = list(roles)
    else:                                   # keycloak shape
        claims["realm_access"] = {"roles": list(roles)}
    return jwt.encode({"alg": "RS256"}, claims, key).decode()


def _validator(role_claim="roles"):
    return OIDCValidator(ISSUER, audience="markwright", role_claim=role_claim, jwks=JWKS)


def test_valid_token_maps_principal_and_role():
    assert _validator().principal(mint(("operator",))) == ("kim@corp", "operator")


def test_multiple_roles_resolve_to_highest_privilege():
    assert _validator().principal(mint(("verifier", "admin", "ci-team")))[1] == "admin"
    assert _validator().principal(mint(("auditor", "verifier")))[1] == "auditor"


def test_keycloak_realm_access_claim_path():
    v = _validator(role_claim="realm_access.roles")
    assert v.principal(mint(("operator",), claim_shape="keycloak"))[1] == "operator"


@pytest.mark.parametrize("bad,match", [
    (dict(exp_in=-600), "Expired"),
    (dict(aud="someone-else"), "Claim"),
    (dict(issuer="https://evil.test"), "Claim"),
    (dict(key=OTHER_KEY), "BadSignature"),
])
def test_rejections(bad, match):
    with pytest.raises(OIDCError, match=match):
        _validator().principal(mint(**bad))


def test_token_without_markwright_role_is_rejected():
    with pytest.raises(OIDCError, match="no markwright role"):
        _validator().principal(mint(("random-group",)))
    with pytest.raises(OIDCError, match="no markwright role"):
        _validator().principal(mint(()))


def test_garbage_token_is_rejected():
    with pytest.raises(OIDCError):
        _validator().principal("not-a-jwt")


# ---- API integration ------------------------------------------------------------------------
def _enable_oidc(a):
    a.STATE["oidc"] = _validator()


def test_api_accepts_jwt_and_rejects_static_tokens_when_oidc_on(api):
    client, a = api
    _enable_oidc(a)
    try:
        hdr = {"Authorization": f"Bearer {mint(('operator',))}"}
        assert client.get("/v1/models", headers=hdr).status_code == 200
        r = client.post("/v1/models", json={"model_id": "oidc-llm"}, headers=hdr)
        assert r.status_code == 201 and r.json()["created_by"] == "kim@corp"
        # verifier JWT hits RBAC like the static verifier did
        vhdr = {"Authorization": f"Bearer {mint(('verifier',), username='journo')}"}
        assert client.post("/v1/models", json={"model_id": "x2"}, headers=vhdr).status_code == 403
        # static dev tokens are dead now
        assert client.get("/v1/models", headers={"Authorization": "Bearer operator-token"}).status_code == 401
    finally:
        a.STATE["oidc"] = None


def test_break_glass_requires_flag_is_audited_and_never_in_provider(api, monkeypatch):
    client, a = api
    _enable_oidc(a)
    try:
        static = {"Authorization": "Bearer operator-token"}
        assert client.get("/v1/models", headers=static).status_code == 401
        monkeypatch.setenv("MARKWRIGHT_BREAK_GLASS", "1")
        assert client.get("/v1/models", headers=static).status_code == 200
        assert any(e["action"] == "auth.login" and
                   e["details"].get("method") == "break-glass-static-token"
                   for e in a.STATE["audit"].entries)
        monkeypatch.setattr(a, "PROFILE", "provider")
        assert client.get("/v1/models", headers=static).status_code == 401
    finally:
        a.STATE["oidc"] = None
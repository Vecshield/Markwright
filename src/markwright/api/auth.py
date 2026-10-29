"""OIDC bearer-token validation (design §6.3 / tech stack §9: authlib).

The deployment points Markwright at its IdP (Keycloak, Entra ID, Okta) via:

    MARKWRIGHT_OIDC_ISSUER     e.g. https://keycloak.corp/realms/markwright
    MARKWRIGHT_OIDC_AUDIENCE   expected `aud` (default: "markwright")
    MARKWRIGHT_OIDC_ROLE_CLAIM dotted path to the roles list
                               (default "roles"; Keycloak: "realm_access.roles")

Group→role mapping is deliberately boring: the claim must contain one of the
four Markwright role names (admin/operator/verifier/auditor — exactly the
realm roles deploy/compose/keycloak-realm.json defines). When a token carries
several, the HIGHEST privilege wins, matching how IdP group inheritance
usually composes.

When OIDC is configured, the static dev tokens STOP working. Break-glass
(design §6.3: "disabled by default and audited when enabled") re-admits them
only with MARKWRIGHT_BREAK_GLASS=1 and never in the provider profile; every
break-glass authentication is individually audited by the caller.

No custom crypto: signature/exp/aud/iss validation is authlib's JWT
implementation against the issuer's JWKS, fetched once from OIDC discovery and
cached for the process lifetime (key-rotation re-fetch is a v1.1 nicety).
"""
from __future__ import annotations

from typing import Any

import httpx

ROLE_PRIORITY = ("admin", "operator", "auditor", "verifier")   # highest first
CLOCK_LEEWAY_S = 30


class OIDCError(Exception):
    """Any reason the token is unacceptable. The API maps this to 401."""


class OIDCValidator:
    def __init__(self, issuer: str, audience: str = "markwright",
                 role_claim: str = "roles", jwks: dict[str, Any] | None = None):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.role_claim = role_claim
        self._jwks = jwks            # injectable for tests; fetched lazily otherwise

    # -- key material ---------------------------------------------------------------
    def _jwks_doc(self) -> dict[str, Any]:
        if self._jwks is None:
            disc = httpx.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
            disc.raise_for_status()
            jwks = httpx.get(disc.json()["jwks_uri"], timeout=10)
            jwks.raise_for_status()
            self._jwks = jwks.json()
        return self._jwks

    # -- validation -----------------------------------------------------------------
    def principal(self, token: str) -> tuple[str, str]:
        """Validate and map to (principal, role). Raises OIDCError otherwise."""
        from authlib.jose import JsonWebKey, JsonWebToken
        from authlib.jose.errors import JoseError

        jwt = JsonWebToken(["RS256", "ES256"])
        try:
            claims = jwt.decode(
                token,
                JsonWebKey.import_key_set(self._jwks_doc()),
                claims_options={
                    "iss": {"essential": True, "value": self.issuer},
                    "aud": {"essential": True, "value": self.audience},
                    "exp": {"essential": True},
                },
            )
            claims.validate(leeway=CLOCK_LEEWAY_S)
        except JoseError as e:
            raise OIDCError(f"token rejected: {e.__class__.__name__}") from e

        roles = self._extract_roles(claims)
        role = next((r for r in ROLE_PRIORITY if r in roles), None)
        if role is None:
            raise OIDCError("token carries no markwright role "
                            f"(claim '{self.role_claim}' = {sorted(roles) or 'absent'})")
        principal = claims.get("preferred_username") or claims.get("email") or claims["sub"]
        return str(principal), role

    def _extract_roles(self, claims: dict[str, Any]) -> set[str]:
        node: Any = claims
        for part in self.role_claim.split("."):
            if not isinstance(node, dict) or part not in node:
                return set()
            node = node[part]
        if isinstance(node, str):
            node = node.split()
        return {str(r) for r in node} if isinstance(node, (list, set, tuple)) else set()

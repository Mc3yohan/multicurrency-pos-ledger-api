# -*- coding: utf-8 -*-
"""Constant-time API-key RBAC authenticator (replaces spoofable client role headers)."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException

from app.models import UserRole


class ApiKeyAuth:
    """Resolves X-API-Key headers into OWNER or SELLER roles in constant time."""

    def __init__(self, owner_key: str, seller_key: str) -> None:
        if not owner_key or not seller_key:
            raise ValueError("Both owner_key and seller_key must be non-empty.")
        if owner_key == seller_key:
            raise ValueError("owner_key and seller_key must be distinct.")
        self._owner_key = owner_key.encode("utf-8")
        self._seller_key = seller_key.encode("utf-8")

    def resolve_role(self, api_key: Optional[str]) -> UserRole:
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header.")
        candidate = api_key.strip().encode("utf-8")
        if hmac.compare_digest(candidate, self._owner_key):
            return UserRole.OWNER
        if hmac.compare_digest(candidate, self._seller_key):
            return UserRole.SELLER
        raise HTTPException(status_code=401, detail="Invalid X-API-Key.")

    def require_owner(self, api_key: Optional[str]) -> UserRole:
        role = self.resolve_role(api_key)
        if role != UserRole.OWNER:
            raise HTTPException(
                status_code=403,
                detail="Cost margins and catalog mutations are restricted to OWNER role.",
            )
        return role

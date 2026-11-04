from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from docuflow.auth import service
from docuflow.auth.models import User
from docuflow.core.database import get_session
from docuflow.core.exceptions import UnauthorizedError
from docuflow.core.security import decode_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="JWT access token")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Personal API key")


async def user_from_access_token(session: AsyncSession, token: str) -> User:
    try:
        payload = decode_token(token, "access")
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token expired") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid access token") from exc
    user = await session.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user


async def get_current_user(
    session: SessionDep,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> User:
    """Authenticate with `Authorization: Bearer <jwt>` (web app) or `X-API-Key` (integrations)."""
    if bearer is not None:
        return await user_from_access_token(session, bearer.credentials)
    if api_key:
        user = await service.user_from_api_key(session, api_key)
        if user is not None and user.is_active:
            return user
        raise UnauthorizedError("Invalid API key")
    raise UnauthorizedError()


CurrentUser = Annotated[User, Depends(get_current_user)]

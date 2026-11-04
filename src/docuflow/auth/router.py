from uuid import UUID

from fastapi import APIRouter, Depends, status

from docuflow.auth import service
from docuflow.auth.dependencies import CurrentUser, SessionDep
from docuflow.auth.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserRead,
)
from docuflow.core.config import get_settings
from docuflow.core.rate_limit import rate_limit
from docuflow.core.schemas import ErrorResponse

_limit = get_settings().rate_limit_auth_per_minute

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", _limit))],
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
async def register(data: RegisterRequest, session: SessionDep) -> UserRead:
    user = await service.register_user(session, data)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(rate_limit("login", _limit))],
    responses={401: {"model": ErrorResponse}},
)
async def login(data: LoginRequest, session: SessionDep) -> TokenPair:
    user = await service.authenticate(session, data.email, data.password)
    return await service.issue_tokens(session, user.id)


@router.post("/refresh", response_model=TokenPair, responses={401: {"model": ErrorResponse}})
async def refresh(data: RefreshRequest, session: SessionDep) -> TokenPair:
    return await service.rotate_refresh_token(session, data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshRequest, session: SessionDep) -> None:
    await service.revoke_refresh_token(session, data.refresh_token)


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


# ---------------------------------------------------------------- API keys


@router.get("/api-keys", response_model=list[ApiKeyRead], tags=["api-keys"])
async def list_api_keys(user: CurrentUser, session: SessionDep) -> list[ApiKeyRead]:
    keys = await service.list_api_keys(session, user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["api-keys"],
)
async def create_api_key(
    data: ApiKeyCreate, user: CurrentUser, session: SessionDep
) -> ApiKeyCreated:
    api_key, raw = await service.create_api_key(session, user.id, data.name)
    return ApiKeyCreated(**ApiKeyRead.model_validate(api_key).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["api-keys"])
async def revoke_api_key(key_id: UUID, user: CurrentUser, session: SessionDep) -> None:
    await service.revoke_api_key(session, user.id, key_id)

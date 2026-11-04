from uuid import UUID

from fastapi import APIRouter, status

from docuflow.auth.dependencies import CurrentUser, SessionDep
from docuflow.webhooks import service
from docuflow.webhooks.schemas import (
    SUBSCRIBABLE_EVENTS,
    DeliveryRead,
    WebhookCreate,
    WebhookCreated,
    WebhookRead,
    WebhookUpdate,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/events", response_model=list[str])
async def list_event_types() -> list[str]:
    return [e.value for e in SUBSCRIBABLE_EVENTS]


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(user: CurrentUser, session: SessionDep) -> list[WebhookRead]:
    return [WebhookRead.model_validate(e) for e in await service.list_endpoints(session, user.id)]


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    data: WebhookCreate, user: CurrentUser, session: SessionDep
) -> WebhookCreated:
    endpoint = await service.create_endpoint(session, user.id, data)
    return WebhookCreated.model_validate(endpoint)


@router.patch("/{endpoint_id}", response_model=WebhookRead)
async def update_webhook(
    endpoint_id: UUID, data: WebhookUpdate, user: CurrentUser, session: SessionDep
) -> WebhookRead:
    endpoint = await service.get_owned_endpoint(session, user.id, endpoint_id)
    return WebhookRead.model_validate(await service.update_endpoint(session, endpoint, data))


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(endpoint_id: UUID, user: CurrentUser, session: SessionDep) -> None:
    endpoint = await service.get_owned_endpoint(session, user.id, endpoint_id)
    await session.delete(endpoint)
    await session.commit()


@router.post(
    "/{endpoint_id}/ping", response_model=DeliveryRead, status_code=status.HTTP_202_ACCEPTED
)
async def ping_webhook(endpoint_id: UUID, user: CurrentUser, session: SessionDep) -> DeliveryRead:
    endpoint = await service.get_owned_endpoint(session, user.id, endpoint_id)
    return DeliveryRead.model_validate(await service.send_ping(session, endpoint))


@router.get("/{endpoint_id}/deliveries", response_model=list[DeliveryRead])
async def list_deliveries(
    endpoint_id: UUID, user: CurrentUser, session: SessionDep
) -> list[DeliveryRead]:
    await service.get_owned_endpoint(session, user.id, endpoint_id)
    deliveries = await service.list_deliveries(session, endpoint_id)
    return [DeliveryRead.model_validate(d) for d in deliveries]

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.groups import service
from app.groups.schemas import (
    AddMembersIn,
    ConversationOut,
    CreateDirectConversationIn,
    CreateGroupConversationIn,
    MemberOut,
    RemoveMemberIn,
    UpdateGroupIn,
)
from app.models import User

router = APIRouter()


def _serialize(conv) -> ConversationOut:
    return ConversationOut(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        avatar_url=conv.avatar_url,
        created_at=conv.created_at,
        members=[
            MemberOut(
                user_id=m.user.id,
                display_name=m.user.display_name,
                avatar_url=m.user.avatar_url,
                role=m.role,
            )
            for m in conv.members
        ],
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convs = await service.get_user_conversations(current_user, db)
    return [_serialize(c) for c in convs]


@router.post("/conversations/direct", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_direct(
    body: CreateDirectConversationIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.other_user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot create a conversation with yourself.")
    conv, _ = await service.get_or_create_direct(current_user, body.other_user_id, db)
    return _serialize(conv)


@router.post("/conversations/group", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: CreateGroupConversationIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if len(body.member_ids) < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A group needs at least 2 members.")
    conv = await service.create_group(current_user, body.name, body.member_ids, db)
    return _serialize(conv)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await service.get_conversation_for_user(conversation_id, current_user, db)
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return _serialize(conv)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_group(
    conversation_id: uuid.UUID,
    body: UpdateGroupIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        conv = await service.update_group(conversation_id, current_user, body.name, body.avatar_url, db)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return _serialize(conv)


@router.post("/conversations/{conversation_id}/members", response_model=ConversationOut)
async def add_members(
    conversation_id: uuid.UUID,
    body: AddMembersIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        conv = await service.add_members(conversation_id, current_user, body.user_ids, db)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    return _serialize(conv)


@router.delete("/conversations/{conversation_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    conversation_id: uuid.UUID,
    body: RemoveMemberIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.remove_member(conversation_id, current_user, body.user_id, db)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
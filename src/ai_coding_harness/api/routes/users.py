"""Administrator-only user lifecycle endpoints."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request

from ai_coding_harness.api.schemas import (
    ManagedUserInfo,
    UserCreateRequest,
    UserResetPasswordRequest,
    UserUpdateRequest,
)
from ai_coding_harness.auth.user_service import (
    ManagedUser,
    UserAdminError,
    UserAdminService,
)

router = APIRouter(prefix="/users", tags=["users"])


def _managed_user(user: ManagedUser) -> ManagedUserInfo:
    return ManagedUserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        active_sessions=user.active_sessions,
        last_active_at=user.last_active_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _service(request: Request) -> UserAdminService:
    return request.app.state.user_admin_service


def _raise(exc: UserAdminError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


@router.get("", response_model=list[ManagedUserInfo])
async def list_users(request: Request) -> list[ManagedUserInfo]:
    return [_managed_user(user) for user in await _service(request).list_users()]


@router.post("", response_model=ManagedUserInfo, status_code=201)
async def create_user(
    payload: UserCreateRequest, request: Request
) -> ManagedUserInfo:
    try:
        user = await _service(request).create_user(
            username=payload.username,
            display_name=payload.display_name,
            role=payload.role,
            temporary_password=payload.temporary_password,
        )
    except UserAdminError as exc:
        _raise(exc)
    return _managed_user(user)


@router.patch("/{user_id}", response_model=ManagedUserInfo)
async def update_user(
    user_id: int, payload: UserUpdateRequest, request: Request
) -> ManagedUserInfo:
    try:
        user = await _service(request).update_user(
            actor_id=request.state.auth.user.id,
            user_id=user_id,
            display_name=payload.display_name,
            role=payload.role,
            is_active=payload.is_active,
        )
    except UserAdminError as exc:
        _raise(exc)
    return _managed_user(user)


@router.post("/{user_id}/reset-password", response_model=ManagedUserInfo)
async def reset_user_password(
    user_id: int, payload: UserResetPasswordRequest, request: Request
) -> ManagedUserInfo:
    try:
        user = await _service(request).reset_password(
            actor_id=request.state.auth.user.id,
            user_id=user_id,
            new_password=payload.new_password,
        )
    except UserAdminError as exc:
        _raise(exc)
    return _managed_user(user)


@router.post("/{user_id}/revoke-sessions", response_model=ManagedUserInfo)
async def revoke_user_sessions(
    user_id: int, request: Request
) -> ManagedUserInfo:
    try:
        user = await _service(request).revoke_sessions(user_id=user_id)
    except UserAdminError as exc:
        _raise(exc)
    return _managed_user(user)

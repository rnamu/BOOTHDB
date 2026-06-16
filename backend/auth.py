# auth.py - 認証ヘルパー（Supabase Auth）

from fastapi import Header, HTTPException, status
from supabase import create_client
from config import settings
from typing import Optional

# anonキーのクライアント（フロントからのトークン検証用）
_anon_client = None


def get_anon_client():
    global _anon_client
    if _anon_client is None:
        _anon_client = create_client(settings.supabase_url, settings.supabase_key)
    return _anon_client


async def get_current_user(
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """
    AuthorizationヘッダーからJWTを取り出してSupabaseで検証し、
    ユーザー情報を返す。トークンが無効な場合は401を返す。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ログインが必要です",
        )

    token = authorization.removeprefix("Bearer ").strip()

    try:
        client = get_anon_client()
        res = client.auth.get_user(token)
        if not res.user:
            raise ValueError("ユーザーが見つかりません")
        return {"id": res.user.id, "email": res.user.email}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="トークンが無効または期限切れです",
        )


async def get_optional_user(
    authorization: Optional[str] = Header(default=None)
) -> Optional[dict]:
    """
    ログイン任意のエンドポイント用。
    トークンがあれば検証してユーザーを返し、なければNoneを返す。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None

# models.py - リクエスト・レスポンスのデータモデル定義

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ==========================================
# 商品関連
# ==========================================

class ProductRegisterRequest(BaseModel):
    """商品URL登録リクエスト"""
    booth_url: str


class ProductResponse(BaseModel):
    """商品情報レスポンス"""
    id: str
    booth_item_id: str
    title: str
    creator_name: str
    shop_name: Optional[str]
    current_price: Optional[int]
    thumbnail_url: Optional[str]
    booth_url: str
    category: Optional[str]
    description: Optional[str]
    registered_at: datetime
    last_checked_at: Optional[datetime]


class ProductListResponse(BaseModel):
    """商品一覧レスポンス"""
    items: list[ProductResponse]
    total: int
    page: int
    per_page: int


# ==========================================
# 価格履歴関連
# ==========================================

class PriceHistoryItem(BaseModel):
    """価格履歴1件"""
    price: int
    recorded_at: datetime


class PriceHistoryResponse(BaseModel):
    """価格履歴レスポンス"""
    product_id: str
    current_price: Optional[int]
    lowest_price: Optional[int]
    lowest_price_date: Optional[datetime]
    highest_price: Optional[int]
    history: list[PriceHistoryItem]


# ==========================================
# レビュー関連（使用アバターなし）
# ==========================================

class ReviewResponse(BaseModel):
    """レビュー1件レスポンス"""
    id: str
    product_id: str
    rating: int
    comment: Optional[str]
    username: Optional[str]
    created_at: datetime


class ReviewListResponse(BaseModel):
    """レビュー一覧レスポンス"""
    items: list[ReviewResponse]
    total: int
    average_rating: Optional[float]
    rating_distribution: dict


# ==========================================
# ユーザー関連
# ==========================================

class UserRegisterRequest(BaseModel):
    """ユーザー登録リクエスト"""
    email: str
    password: str
    username: str


class UserLoginRequest(BaseModel):
    """ログインリクエスト"""
    email: str
    password: str


class AuthResponse(BaseModel):
    """認証レスポンス"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    username: str


# ==========================================
# 共通
# ==========================================

class MessageResponse(BaseModel):
    """汎用メッセージレスポンス"""
    message: str


class ErrorResponse(BaseModel):
    """エラーレスポンス"""
    detail: str

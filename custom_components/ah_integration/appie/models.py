"""Pydantic models used by the appie client."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    """Public token payload returned by AH auth endpoints."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    member_id: str | None = None


class StoredToken(BaseModel):
    """Persisted token payload with an absolute expiry timestamp."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    member_id: str | None = None
    expires_at: datetime

    model_config = ConfigDict(extra="ignore")

    @classmethod
    def from_token_response(cls, token: TokenResponse, expires_at: datetime) -> StoredToken:
        """Create a stored token from a public token payload."""
        return cls(**token.model_dump(), expires_at=expires_at)

    def to_token_response(self) -> TokenResponse:
        """Convert a stored token back to the public token model."""
        return TokenResponse(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_in=self.expires_in,
            token_type=self.token_type,
            member_id=self.member_id,
        )


class DiscountLabel(BaseModel):
    code: str
    description: str
    percentage: float | None = None
    amount: float | None = None
    price: float | None = None
    count: int | None = None


class Product(BaseModel):
    id: int
    title: str
    brand: str | None = None
    price: float | None = None
    original_price: float | None = None
    discount_price: float | None = None
    is_bonus: bool | None = None
    bonus_label: str | None = None
    bonus_start_date: date | None = None
    bonus_end_date: date | None = None
    discount_type: str | None = None
    segment_type: str | None = None
    promotion_type: str | None = None
    is_stapel_bonus: bool | None = None
    is_infinite_bonus: bool | None = None
    multiple_item_promotion: bool | None = None
    bonus_segment_description: str | None = None
    extra_descriptions: list[str] = Field(default_factory=list)
    discount_labels: list[DiscountLabel] = Field(default_factory=list)
    is_organic: bool | None = None
    property_labels: list[str] = Field(default_factory=list)
    unit_size: str | None = None
    image_url: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    nutriscore: str | None = None
    is_orderable: bool | None = None


class ReceiptProduct(BaseModel):
    """A single line item on a receipt."""

    id: int
    name: str
    quantity: float
    price_per_unit: float
    total_price: float


class Receipt(BaseModel):
    """A normalized receipt summary or detailed receipt."""

    id: str
    datetime: datetime
    store_name: str | None = None
    total: float
    products: list[ReceiptProduct]


class ShoppingListItem(BaseModel):
    """A shopping-list item."""

    id: str
    description: str
    quantity: int = 1
    product_id: int | None = None

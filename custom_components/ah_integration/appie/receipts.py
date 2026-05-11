"""Receipt listing and detail operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import Receipt, ReceiptProduct

POS_RECEIPTS_QUERY = """
query FetchPosReceipts($offset: Int!, $limit: Int!) {
  posReceiptsPage(pagination: {offset: $offset, limit: $limit}) {
    posReceipts {
      id
      dateTime
      totalAmount { amount }
    }
  }
}
"""

POS_RECEIPT_DETAILS_QUERY = """
query FetchReceipt($id: String!) {
  posReceiptDetails(id: $id) {
    id
    memberId
    products {
      id
      quantity
      name
      price { amount }
      amount { amount }
    }
  }
}
"""


class GraphQLClient(Protocol):
    """Protocol for clients that can execute GraphQL requests."""

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """Execute a GraphQL request."""
        ...


class ReceiptsAPI:
    """High-level receipt operations."""

    def __init__(self, client: GraphQLClient) -> None:
        self._client = client

    async def list_pos_receipts(self, limit: int = 50, offset: int = 0) -> list[Receipt]:
        """Return POS receipt summaries without line items."""
        data = await self._client.graphql(
            POS_RECEIPTS_QUERY, {"offset": offset, "limit": limit}
        )
        receipts = (data.get("posReceiptsPage") or {}).get("posReceipts", [])
        return [self._map_receipt_summary(item) for item in receipts]

    async def get_pos_receipt(
        self, receipt_id: str, summary: Receipt | None = None
    ) -> Receipt:
        """Return a single POS receipt including product line items."""
        data = await self._client.graphql(POS_RECEIPT_DETAILS_QUERY, {"id": receipt_id})
        receipt = data["posReceiptDetails"]
        if summary is None:
            summary = await self._get_receipt_summary(receipt_id)
        return self._map_receipt_detail(receipt, summary=summary)

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Receipt]:
        """Return receipt summaries sorted by datetime descending."""
        receipts = await self.list_pos_receipts(limit=limit, offset=offset)
        return sorted(receipts, key=lambda receipt: receipt.datetime, reverse=True)

    @staticmethod
    def _map_receipt_summary(payload: dict) -> Receipt:
        return Receipt(
            id=str(payload["id"]),
            datetime=datetime.fromisoformat(payload["dateTime"]),
            store_name=None,
            total=float(payload["totalAmount"]["amount"]),
            products=[],
        )

    @classmethod
    def _map_receipt_detail(cls, payload: dict, summary: Receipt | None = None) -> Receipt:
        products = [
            ReceiptProduct(
                id=int(line["id"]),
                name=line["name"],
                quantity=float(line["quantity"]),
                price_per_unit=float((line.get("price") or {}).get("amount") or 0.0),
                total_price=float(line["amount"]["amount"]),
            )
            for line in payload.get("products", [])
        ]
        return Receipt(
            id=str(payload["id"]),
            datetime=summary.datetime if summary else datetime.min,
            store_name=summary.store_name if summary else None,
            total=summary.total if summary else sum(product.total_price for product in products),
            products=products,
        )

    async def _get_receipt_summary(self, receipt_id: str) -> Receipt | None:
        receipts = await self.list_pos_receipts(limit=100)
        for receipt in receipts:
            if receipt.id == receipt_id:
                return receipt
        return None

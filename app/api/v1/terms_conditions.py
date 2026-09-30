"""
app/api/v1/terms_conditions.py
================================
  GET  /api/v1/terms-conditions   → Public
  PUT  /api/v1/terms-conditions   → Admin only

NOTE: Content is accepted as multipart/form-data (Form field) so that
raw text with newlines, quotes, and special characters works natively.
"""

from fastapi import APIRouter, Depends, Form
from app.models.legal_model import LegalDocType, LegalDocUpsert, LegalDocResponse
from app.services.legal_service import get_legal_doc, upsert_legal_doc
from app.models.user import UserResponse
from app.core.permissions import require_admin

router = APIRouter()

_TYPE = LegalDocType.TERMS_CONDITIONS


@router.get("", response_model=LegalDocResponse)
async def read_terms_conditions():
    """**PUBLIC** — Returns the current Terms & Conditions content."""
    return await get_legal_doc(_TYPE)


@router.put("", response_model=LegalDocResponse)
async def write_terms_conditions(
    content: str = Form(..., description="Rich text / Markdown content. Newlines and quotes work natively."),
    _: UserResponse = Depends(require_admin()),
):
    """**ADMIN** — Create or update Terms & Conditions (upsert).

    Accepts `multipart/form-data` so raw text with newlines,
    quotes, and special characters work without JSON escaping.
    """
    return await upsert_legal_doc(_TYPE, LegalDocUpsert(content=content))
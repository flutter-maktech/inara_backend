"""
app/api/v1/privacy_policy.py
=============================
  GET  /api/v1/privacy-policy   → Public: fetch current Privacy Policy
  PUT  /api/v1/privacy-policy   → Admin only: create or update

NOTE: Content is accepted as multipart/form-data (Form field) so that
raw text with newlines, quotes, and special characters works natively
without JSON escape issues.
"""

from fastapi import APIRouter, Depends, Form
from app.models.legal_model import LegalDocType, LegalDocUpsert, LegalDocResponse
from app.services.legal_service import get_legal_doc, upsert_legal_doc
from app.models.user import UserResponse
from app.core.permissions import require_admin

router = APIRouter()

_TYPE = LegalDocType.PRIVACY_POLICY


@router.get("", response_model=LegalDocResponse)
async def read_privacy_policy():
    """**PUBLIC** — Returns the current Privacy Policy content."""
    return await get_legal_doc(_TYPE)


@router.put("", response_model=LegalDocResponse)
async def write_privacy_policy(
    content: str = Form(..., description="Rich text / Markdown content. Newlines and quotes work natively."),
    _: UserResponse = Depends(require_admin()),
):
    """**ADMIN** — Create or update the Privacy Policy (upsert).
    
    Accepts `multipart/form-data` so raw text with newlines,
    quotes, and special characters work without JSON escaping.
    """
    return await upsert_legal_doc(_TYPE, LegalDocUpsert(content=content))
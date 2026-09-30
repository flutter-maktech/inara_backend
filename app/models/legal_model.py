"""
Shared Pydantic schemas for Privacy Policy, Terms & Conditions, and FAQ.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class LegalDocType(str, Enum):
    PRIVACY_POLICY    = "PRIVACY_POLICY"
    TERMS_CONDITIONS  = "TERMS_CONDITIONS"
    FAQ               = "FAQ"


# ── Request ───────────────────────────────────────────────────────────────────

class LegalDocUpsert(BaseModel):
    """
    Used for both CREATE and UPDATE.
    Content supports plain text, rich text, or Markdown — stored as-is.
    """
    content: str = Field(
        ...,
        min_length=1,
        description="Rich text / Markdown content of the legal document",
    )


# ── Response ──────────────────────────────────────────────────────────────────

class LegalDocResponse(BaseModel):
    id:        str
    type:      LegalDocType
    content:   str
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True
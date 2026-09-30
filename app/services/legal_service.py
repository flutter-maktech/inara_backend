"""
app/services/legal_service.py
==============================
Upsert-based CRUD for legal documents (Privacy Policy, T&C, FAQ).

Design decision: Each doc type has exactly ONE record in the DB.
  - GET   → fetch by type (public)
  - UPSERT → create if missing, update if exists (admin only)

Using Prisma's upsert() keeps the logic atomic and race-condition safe.
"""

from fastapi import HTTPException, status
from app.db.db_client import prisma
from app.models.legal_model import LegalDocType, LegalDocUpsert, LegalDocResponse


async def get_legal_doc(doc_type: LegalDocType) -> LegalDocResponse:
    """
    Fetch the active legal document for the given type.
    Raises 404 if the admin has not created it yet.
    """
    doc = await prisma.legaldocument.find_unique(
        where={"type": doc_type.value}
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{doc_type.value.replace('_', ' ').title()} has not been published yet."
        )
    return doc


async def upsert_legal_doc(
    doc_type: LegalDocType,
    payload: LegalDocUpsert,
) -> LegalDocResponse:
    """
    Create or update the legal document for the given type.
    Prisma upsert() is atomic — safe under concurrent requests.
    """
    doc = await prisma.legaldocument.upsert(
        where={"type": doc_type.value},
        data={
            "create": {
                "type":    doc_type.value,
                "content": payload.content,
            },
            "update": {
                "content": payload.content,
            },
        },
    )
    return doc
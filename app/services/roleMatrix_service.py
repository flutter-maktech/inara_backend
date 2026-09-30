"""
RoleMatrixService — Business logic for Role Matrix, Invitations, and Permissions.

CRITICAL SECURITY FEATURE:
- Admin can invite team members via email
- Admin can control visibility matrix for Manager/Instructor
- Admin can add/edit/delete permission rules
- Terms & Conditions management
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from io import BytesIO
import secrets
import bcrypt

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.roleMatrix_model import (
    InviteTeamMember,
    InvitationResponse,
    InvitationListResponse,
    AcceptInvitationRequest,
    PermissionRuleCreate,
    PermissionRuleUpdate,
    PermissionRuleResponse,
    VisibilityMatrixResponse,
    TermsConditionCreate,
    TermsConditionUpdate,
    TermsConditionResponse,
    RoleActionPermissions,
    ActionPermissionsResponse,
)
from prisma.enums import UserRole, InvitationStatus, VisibilityStatus
from app.core.email import send_email  # Import email function
from app.core.config import settings

# Dashboard sign-in page — where new team members log in after accepting.
# Sourced from settings.DASHBOARD_LOGIN_URL (.env) — see app/core/config.py.


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _format_role_label(role: str) -> str:
    """Convert raw enum value to clean display label, e.g. 'ADMIN' → 'Admin'"""
    mapping = {
        "ADMIN": "Admin",
        "MANAGER": "Manager",
        "INSTRUCTOR": "Instructor",
        "USER": "User",
    }
    role_str = role.value if hasattr(role, "value") else str(role)
    return mapping.get(role_str.upper(), role_str.capitalize())


def _force_https(url: str) -> str:
    """
    Ensure a URL always uses HTTPS.

    When FastAPI runs behind an HTTPS reverse-proxy (nginx, Caddy, AWS ALB …)
    request.base_url is often reported as http:// because the TLS termination
    happens upstream.  Normalising here guarantees every URL we embed in email
    templates uses HTTPS and never triggers Mixed-Content browser errors.
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _send_invitation_email(
    email_to: str,
    invite_token: str,
    inviter_name: str,
    role: str,
    base_url: str,
) -> bool:
    """
    Send invitation email to new team member.
    base_url is passed from the router (request.base_url) so the link
    resolves correctly in any environment — no config value needed.
    _force_https() ensures the embedded link is always HTTPS regardless
    of what the reverse-proxy reports as the scheme.
    """
    subject = f"You're invited to join {settings.APP_NAME}"

    # Force HTTPS so the link in the email is always secure.
    safe_base_url = _force_https(base_url.rstrip("/"))

    # Points to the GET form endpoint on this same backend
    accept_url = f"{safe_base_url}/api/v1/role-matrix/invitations/accept-form?token={invite_token}"

    role_label = _format_role_label(role)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Invitation to join {settings.APP_NAME}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- HEADER -->
          <tr>
            <td style="background:linear-gradient(135deg,#2e7d32,#43a047);padding:40px 48px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:26px;font-weight:700;letter-spacing:-0.5px;">
                {settings.APP_NAME}
              </h1>
              <p style="margin:8px 0 0;color:#c8e6c9;font-size:14px;">Team Invitation</p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding:40px 48px;">
              <p style="margin:0 0 8px;font-size:22px;font-weight:600;color:#1a1a2e;">Hi there 👋</p>
              <p style="margin:0 0 24px;font-size:15px;color:#555;line-height:1.6;">
                <strong style="color:#2e7d32;">{inviter_name}</strong> has invited you to join
                <strong>{settings.APP_NAME}</strong> as a
                <strong style="color:#2e7d32;">{role_label}</strong>.
              </p>

              <p style="margin:0 0 16px;font-size:15px;color:#555;line-height:1.6;">
                Click the button below to accept your invitation and set up your account:
              </p>

              <!-- CTA BUTTON -->
              <table cellpadding="0" cellspacing="0" style="margin:24px 0;">
                <tr>
                  <td style="border-radius:6px;background-color:#2e7d32;">
                    <a href="{accept_url}"
                       style="display:inline-block;padding:14px 36px;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;border-radius:6px;letter-spacing:0.3px;">
                      Accept Invitation →
                    </a>
                  </td>
                </tr>
              </table>

              <!-- DIVIDER -->
              <hr style="border:none;border-top:1px solid #eeeeee;margin:28px 0;" />

              <p style="margin:0 0 8px;font-size:13px;color:#888;">Or copy and paste this link into your browser:</p>
              <p style="margin:0 0 24px;font-size:12px;color:#2e7d32;word-break:break-all;background:#f1f8f1;padding:12px 16px;border-radius:4px;border-left:3px solid #43a047;">
                {accept_url}
              </p>

              <!-- EXPIRY NOTICE -->
              <table cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td style="background:#fff8e1;border-left:4px solid #fbc02d;padding:12px 16px;border-radius:0 4px 4px 0;">
                    <p style="margin:0;font-size:13px;color:#5d4037;">
                      ⏰ <strong>This invitation will expire in 7 days.</strong>
                      Please accept it before it expires.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background:#f9f9f9;padding:24px 48px;border-top:1px solid #eeeeee;text-align:center;">
              <p style="margin:0 0 6px;font-size:12px;color:#aaa;">
                If you didn't expect this invitation, you can safely ignore this email.
              </p>
              <p style="margin:0;font-size:12px;color:#aaa;">
                &copy; {settings.APP_NAME}. All rights reserved.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_content = (
        f"Hello!\n\n"
        f"{inviter_name} has invited you to join {settings.APP_NAME} as a {role_label}.\n\n"
        f"Accept your invitation here:\n{accept_url}\n\n"
        f"This invitation expires in 7 days.\n\n"
        f"If you didn't expect this, you can safely ignore this email."
    )

    return send_email(email_to, subject, html_content, text_content)


class RoleMatrixService:
    """
    Enterprise-grade Role Matrix service.
    
    SECURITY DESIGN:
    - Only ADMIN can access these endpoints
    - Email invitations with secure tokens
    - Invitation expiry (7 days)
    - One-time use tokens
    """
    
    # ─────────────────────────────────────────
    # HELPER: Admin-only guard
    # ─────────────────────────────────────────
    
    @staticmethod
    async def _require_admin(user_id: str) -> None:
        """Only ADMIN can access Role Matrix features"""
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
    
    # ═════════════════════════════════════════════════════════════════
    # INVITATION SYSTEM (Most Important)
    # ═════════════════════════════════════════════════════════════════
    
    @staticmethod
    async def invite_team_member(
        data: InviteTeamMember,
        invited_by_user_id: str,
        base_url: str,
    ) -> InvitationResponse:
        """
        Invite a new team member by email.
        Generates secure token and sends email invitation.
        
        CRITICAL: This is the PRIMARY way to add Admin/Manager/Instructor users.
        """
        await RoleMatrixService._require_admin(invited_by_user_id)
        
        # Validate role (only ADMIN, MANAGER, INSTRUCTOR can be invited)
        if data.role not in [UserRole.ADMIN, UserRole.MANAGER, UserRole.INSTRUCTOR]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only invite ADMIN, MANAGER, or INSTRUCTOR roles"
            )
        
        # Check if user already exists
        existing_user = await prisma.user.find_unique(where={"email": data.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User with email {data.email} already exists"
            )
        
        # Check for pending invitation
        existing_invitation = await prisma.invitation.find_first(
            where={
                "email": data.email,
                "status": InvitationStatus.PENDING
            }
        )
        if existing_invitation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Pending invitation already exists for {data.email}"
            )
        
        # Generate secure invite token
        invite_token = secrets.token_urlsafe(32)
        
        # Create invitation (expires in 7 days)
        invitation = await prisma.invitation.create(
            data={
                "email": data.email,
                "role": data.role,
                "inviteToken": invite_token,
                "invitedBy": invited_by_user_id,
                "expiresAt": _now_utc() + timedelta(days=7),
                "status": InvitationStatus.PENDING
            }
        )
        
        # Get inviter info
        inviter = await prisma.user.find_unique(where={"id": invited_by_user_id})
        
        # ═══════════════════════════════════════════════════════════════
        # Actually send the invitation email!
        # ═══════════════════════════════════════════════════════════════
        email_sent = _send_invitation_email(
            email_to=data.email,
            invite_token=invite_token,
            inviter_name=inviter.name if inviter else "Admin",
            role=_format_role_label(data.role),
            base_url=base_url,
        )
        
        if not email_sent:
            print(f"⚠️  WARNING: Failed to send invitation email to {data.email}")
            # We don't fail the request, but log the warning
            # The invitation is still created and can be resent manually if needed
        else:
            print(f" Invitation email sent successfully to {data.email}")
        
        return InvitationResponse(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            status=invitation.status,
            inviteToken=invitation.inviteToken,
            invitedBy=invitation.invitedBy,
            invitedByName=inviter.name if inviter else None,
            expiresAt=invitation.expiresAt,
            acceptedAt=invitation.acceptedAt,
            createdAt=invitation.createdAt
        )
    
    @staticmethod
    async def get_all_invitations(
        admin_user_id: str,
        page: int = 1,
        page_size: int = 10,
        status_filter: Optional[str] = None
    ) -> InvitationListResponse:
        """Get all invitations with pagination"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        where_clause: Dict[str, Any] = {}
        if status_filter:
            where_clause["status"] = status_filter
        
        total = await prisma.invitation.count(where=where_clause)
        skip = (page - 1) * page_size
        total_pages = max(1, (total + page_size - 1) // page_size)
        
        invitations = await prisma.invitation.find_many(
            where=where_clause,
            skip=skip,
            take=page_size,
            order={"createdAt": "desc"},
            include={"invitedByUser": True}
        )
        
        invitation_responses = []
        for inv in invitations:
            invitation_responses.append(
                InvitationResponse(
                    id=inv.id,
                    email=inv.email,
                    role=inv.role,
                    status=inv.status,
                    inviteToken=inv.inviteToken,
                    invitedBy=inv.invitedBy,
                    invitedByName=inv.invitedByUser.name if inv.invitedByUser else None,
                    expiresAt=inv.expiresAt,
                    acceptedAt=inv.acceptedAt,
                    createdAt=inv.createdAt
                )
            )
        
        return InvitationListResponse(
            invitations=invitation_responses,
            total=total,
            page=page,
            pageSize=page_size,
            totalPages=total_pages
        )
    
    @staticmethod
    async def resend_invitation(
        invitation_id: str,
        admin_user_id: str,
        base_url: str,
    ) -> InvitationResponse:
        """
        Resend invitation email.
        Useful if email failed to send or user didn't receive it.
        """
        await RoleMatrixService._require_admin(admin_user_id)
        
        invitation = await prisma.invitation.find_unique(
            where={"id": invitation_id},
            include={"invitedByUser": True}
        )
        
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found"
            )
        
        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only resend pending invitations"
            )
        
        if invitation.expiresAt < _now_utc():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation has expired. Please create a new one."
            )
        
        # Resend email
        email_sent = _send_invitation_email(
            email_to=invitation.email,
            invite_token=invitation.inviteToken,
            inviter_name=invitation.invitedByUser.name if invitation.invitedByUser else "Admin",
            role=_format_role_label(invitation.role),
            base_url=base_url,
        )
        
        if not email_sent:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send invitation email. Please check SMTP configuration."
            )
        
        print(f" Invitation email resent successfully to {invitation.email}")
        
        return InvitationResponse(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            status=invitation.status,
            inviteToken=invitation.inviteToken,
            invitedBy=invitation.invitedBy,
            invitedByName=invitation.invitedByUser.name if invitation.invitedByUser else None,
            expiresAt=invitation.expiresAt,
            acceptedAt=invitation.acceptedAt,
            createdAt=invitation.createdAt
        )
    
    @staticmethod
    async def accept_invitation(data: AcceptInvitationRequest) -> dict:
        """
        Accept invitation and create user account.
        PUBLIC endpoint (no auth required) - secured by token.
        """
        # Find invitation by token
        invitation = await prisma.invitation.find_unique(
            where={"inviteToken": data.inviteToken}
        )
        
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid invitation token"
            )
        
        # Check if already accepted
        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invitation already {invitation.status.lower()}"
            )
        
        # Check if expired
        if invitation.expiresAt < _now_utc():
            # Mark as expired
            await prisma.invitation.update(
                where={"id": invitation.id},
                data={"status": InvitationStatus.EXPIRED}
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation has expired"
            )
        
        # Check if user already exists (edge case)
        existing_user = await prisma.user.find_unique(where={"email": invitation.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already exists with this email"
            )
        
        # Create user account
        password_hash = _hash_password(data.password)
        
        user = await prisma.user.create(
            data={
                "email": invitation.email,
                "passwordHash": password_hash,
                "name": data.name,
                "phone": data.phone,
                "role": invitation.role,
                "isActive": True,
                "isVerified": True  # Skip verification for invited users
            }
        )
        
        # Mark invitation as accepted
        await prisma.invitation.update(
            where={"id": invitation.id},
            data={
                "status": InvitationStatus.ACCEPTED,
                "acceptedAt": _now_utc()
            }
        )
        
        return {
            "message": "Invitation accepted successfully. You can now log in.",
            "userId": user.id,
            "email": user.email,
            "role": user.role,
            "name": user.name,
            "loginUrl": settings.DASHBOARD_LOGIN_URL,
        }
    
    @staticmethod
    async def cancel_invitation(
        invitation_id: str,
        admin_user_id: str
    ) -> dict:
        """Cancel a pending invitation"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        invitation = await prisma.invitation.find_unique(where={"id": invitation_id})
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found"
            )
        
        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only cancel pending invitations"
            )
        
        await prisma.invitation.update(
            where={"id": invitation_id},
            data={"status": InvitationStatus.CANCELLED}
        )
        
        return {"message": "Invitation cancelled successfully"}
    
    # ═════════════════════════════════════════════════════════════════
    # PERMISSION RULES (Visibility Matrix)
    # ═════════════════════════════════════════════════════════════════
    
    @staticmethod
    async def create_permission_rule(
        data: PermissionRuleCreate,
        admin_user_id: str
    ) -> PermissionRuleResponse:
        """Create a new permission rule (Add New Rule)"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        # Check if module already has a rule
        existing = await prisma.permissionrule.find_unique(
            where={"moduleName": data.moduleName}
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Rule already exists for module: {data.moduleName}"
            )
        
        rule = await prisma.permissionrule.create(
            data=data.model_dump()
        )
        
        return PermissionRuleResponse(**rule.model_dump())
    
    @staticmethod
    async def get_visibility_matrix(
        admin_user_id: str
    ) -> VisibilityMatrixResponse:
        """Get complete visibility matrix"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        rules = await prisma.permissionrule.find_many(
            order={"displayOrder": "asc"}
        )
        
        rule_responses = [
            PermissionRuleResponse(**rule.model_dump())
            for rule in rules
        ]
        
        return VisibilityMatrixResponse(
            rules=rule_responses,
            total=len(rules)
        )
    
    @staticmethod
    async def update_permission_rule(
        rule_id: str,
        data: PermissionRuleUpdate,
        admin_user_id: str
    ) -> PermissionRuleResponse:
        """Update permission rule (Edit Matrix - toggle visibility)"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        rule = await prisma.permissionrule.find_unique(where={"id": rule_id})
        if not rule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission rule not found"
            )
        
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return PermissionRuleResponse(**rule.model_dump())
        
        updated_rule = await prisma.permissionrule.update(
            where={"id": rule_id},
            data=update_data
        )
        
        return PermissionRuleResponse(**updated_rule.model_dump())
    
    @staticmethod
    async def delete_permission_rule(
        rule_id: str,
        admin_user_id: str
    ) -> dict:
        """Delete permission rule"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        rule = await prisma.permissionrule.find_unique(where={"id": rule_id})
        if not rule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission rule not found"
            )
        
        await prisma.permissionrule.delete(where={"id": rule_id})
        
        return {"message": "Permission rule deleted successfully"}
    
    @staticmethod
    async def export_visibility_matrix(
        admin_user_id: str
    ) -> BytesIO:
        """Export visibility matrix to Excel"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        rules = await prisma.permissionrule.find_many(
            order={"displayOrder": "asc"}
        )
        
        export_data = []
        for rule in rules:
            export_data.append({
                "Module / Page": rule.moduleName,
                "Admin": rule.adminAccess,
                "Manager": rule.managerAccess,
                "Instructor": rule.instructorAccess,
                "Display Order": rule.displayOrder
            })
        
        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Visibility Matrix", index=False)
            worksheet = writer.sheets["Visibility Matrix"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output
    
    # ═════════════════════════════════════════════════════════════════
    # ACTION PERMISSIONS
    # ═════════════════════════════════════════════════════════════════
    
    @staticmethod
    async def get_action_permissions() -> ActionPermissionsResponse:
        """
        Get action permissions per role.
        These are HARDCODED per UI requirements and don't change.
        """
        return ActionPermissionsResponse(
            admin=RoleActionPermissions(
                role="Admin",
                description="Full system control and configuration.",
                permissions=[
                    "View Everything",
                    "Create & Delete Data",
                    "Modify System Settings",
                    "Manage Permissions"
                ]
            ),
            manager=RoleActionPermissions(
                role="Manager",
                description="Operations focused, restricted analytics.",
                permissions=[
                    "Manage Classes/Courses",
                    "Create Members",
                    "Edit Instructor Bio",
                    "View Operational Data"
                ]
            ),
            instructor=RoleActionPermissions(
                role="Instructor",
                description="ReadOnly access for schedules and attendance.",
                permissions=[
                    "View Class Schedule",
                    "See Course Rosters",
                    "View Member Names",
                    "Edit just her profile"
                ]
            )
        )
    
    # ═════════════════════════════════════════════════════════════════
    # TERMS & CONDITIONS
    # ═════════════════════════════════════════════════════════════════
    
    @staticmethod
    async def create_terms_condition(
        data: TermsConditionCreate,
        admin_user_id: str
    ) -> TermsConditionResponse:
        """Create Terms & Conditions"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        # Deactivate all previous versions
        await prisma.termscondition.update_many(
            where={"isActive": True},
            data={"isActive": False}
        )
        
        # Create new version
        terms = await prisma.termscondition.create(
            data={
                **data.model_dump(),
                "isActive": True
            }
        )
        
        return TermsConditionResponse(**terms.model_dump())
    
    @staticmethod
    async def get_terms_condition() -> TermsConditionResponse:
        """Get active Terms & Conditions (PUBLIC - no auth required)"""
        terms = await prisma.termscondition.find_first(
            where={"isActive": True},
            order={"createdAt": "desc"}
        )
        
        if not terms:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Terms & Conditions not found"
            )
        
        return TermsConditionResponse(**terms.model_dump())
    
    @staticmethod
    async def update_terms_condition(
        terms_id: str,
        data: TermsConditionUpdate,
        admin_user_id: str
    ) -> TermsConditionResponse:
        """Update Terms & Conditions"""
        await RoleMatrixService._require_admin(admin_user_id)
        
        terms = await prisma.termscondition.find_unique(where={"id": terms_id})
        if not terms:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Terms & Conditions not found"
            )
        
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return TermsConditionResponse(**terms.model_dump())
        
        updated_terms = await prisma.termscondition.update(
            where={"id": terms_id},
            data=update_data
        )
        
        return TermsConditionResponse(**updated_terms.model_dump())
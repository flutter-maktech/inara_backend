"""
Role Matrix API Router
=======================
CRITICAL SECURITY FEATURE for Admin to manage team and permissions.

Endpoints:
  
  INVITATIONS (Most Important):
  POST   /role-matrix/invitations              → Invite team member via email 
  GET    /role-matrix/invitations              → Get all invitations
  POST   /role-matrix/invitations/accept       → Accept invitation (PUBLIC)
  POST   /role-matrix/invitations/{id}/resend  → Resend invitation email 
  DELETE /role-matrix/invitations/{id}         → Cancel invitation
  
  PERMISSION RULES (Visibility Matrix):
  POST   /role-matrix/rules                    → Add new rule
  GET    /role-matrix/rules                    → Get visibility matrix
  PATCH  /role-matrix/rules/{id}               → Edit rule (toggle visibility)
  DELETE /role-matrix/rules/{id}               → Delete rule
  GET    /role-matrix/rules/export/excel       → Export matrix to Excel
  
  ACTION PERMISSIONS :
  GET    /role-matrix/action-permissions       → Get action permissions
  
  TERMS & CONDITIONS:
  POST   /role-matrix/terms                    → Create Terms & Conditions
  GET    /role-matrix/terms                    → Get active Terms & Conditions (PUBLIC)
  PATCH  /role-matrix/terms/{id}               → Update Terms & Conditions
"""

from fastapi import APIRouter, Depends, Request, Response, status as http_status, Query
from fastapi.responses import HTMLResponse
from typing import Optional

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
    ActionPermissionsResponse,
)
from app.services.roleMatrix_service import RoleMatrixService
from app.api.v1.dependencies import get_current_active_user
from app.core.config import settings

router = APIRouter()

# Dashboard login URL — where newly onboarded team members sign in.
# Sourced from settings.DASHBOARD_LOGIN_URL (.env) — see app/core/config.py.


def _force_https(url: str) -> str:
    """
    Ensure a URL always uses HTTPS.

    When FastAPI runs behind an HTTPS reverse-proxy (nginx, Caddy, AWS ALB …)
    request.base_url is often reported as http:// because the TLS termination
    happens upstream.  Calling this helper on every URL we embed in HTML or
    email templates guarantees we never generate a Mixed-Content URL.
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


# ═════════════════════════════════════════════════════════════════
# INVITATIONS (Most Important Feature) - NOW SENDS EMAILS!
# ═════════════════════════════════════════════════════════════════

@router.post("/invitations", response_model=InvitationResponse, status_code=http_status.HTTP_201_CREATED)
async def invite_team_member(
    request: Request,
    data: InviteTeamMember,
    current_user=Depends(get_current_active_user)
):
    """
    **Invite new team member by email!!**
    
    **Workflow:**
    1. Admin enters email and role (ADMIN, MANAGER, or INSTRUCTOR)
    2. System generates secure invitation token
    3. **Email automatically sent** with invitation link (expires in 7 days)
    4. Recipient clicks link to accept and set password
    5. Account created automatically with assigned role
    
    **Request Body:**
    - email: Email address of person to invite
    - role: ADMIN, MANAGER, or INSTRUCTOR
    
    **Returns:** Invitation details with secure token
    
    **UI Reference:** "Invite your team" modal (Image 1, top)
    **Access:** Admin only
    
    **APPLIED Policy:**
    - Integrated with existing SMTP email system
    - Professional HTML email template with styling
    - Automatic email sending after invitation creation
    - Graceful error handling if email fails
    """
    base_url = _force_https(str(request.base_url))
    return await RoleMatrixService.invite_team_member(data, current_user.id, base_url)


@router.get("/invitations", response_model=InvitationListResponse)
async def get_all_invitations(
    page: int = 1,
    pageSize: int = 10,
    status: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Get all invitations with pagination.
    
    **Query Parameters:**
    - status: Filter by PENDING, ACCEPTED, EXPIRED, CANCELLED
    - page: Page number (default: 1)
    - pageSize: Items per page (default: 10)
    
    **Access:** Admin only
    """
    return await RoleMatrixService.get_all_invitations(
        admin_user_id=current_user.id,
        page=page,
        page_size=pageSize,
        status_filter=status
    )


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationResponse)
async def resend_invitation(
    request: Request,
    invitation_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    **** Resend invitation email.
    
    **Use cases:**
    - User didn't receive the original email
    - Email went to spam
    - User deleted the email
    
    **Behavior:**
    - Only works for PENDING invitations
    - Uses the same invitation token (doesn't create new one)
    - Sends fresh email with same link
    
    **Access:** Admin only
    """
    base_url = _force_https(str(request.base_url))
    return await RoleMatrixService.resend_invitation(invitation_id, current_user.id, base_url)


@router.get("/invitations/accept-form", response_class=HTMLResponse, include_in_schema=False)
async def accept_invitation_form(request: Request, token: str = Query(...)):
    """
    Serves the HTML accept-invitation page when a user clicks the link in their
    invitation email.

    PUBLIC ENDPOINT — no auth required.

    Flow:
      1. User opens the link → this endpoint renders the form.
      2. JavaScript POSTs to /api/v1/role-matrix/invitations/accept (HTTPS).
      3. On success → the form is hidden and a success screen is shown with a
         "Go to Login" button that redirects to the dashboard sign-in page.

    Root-cause fix (Mixed-Content error):
      request.base_url can resolve to http:// when the server sits behind an
      HTTPS reverse proxy.  _force_https() normalises every URL we embed in
      the page so the browser never tries to POST to an http:// endpoint from
      an https:// page, which browsers block as Mixed Content.
    """
    base_url   = _force_https(str(request.base_url).rstrip("/"))
    accept_api = f"{base_url}/api/v1/role-matrix/invitations/accept"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Accept Invitation — {settings.APP_NAME}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, #e8f5e9 0%, #f4f4f7 100%);
      font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
      padding: 24px;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.10);
      width: 100%;
      max-width: 440px;
      overflow: hidden;
    }}
    .card-header {{
      background: linear-gradient(135deg, #2e7d32, #43a047);
      padding: 32px 40px 24px;
      text-align: center;
    }}
    .card-header h1 {{
      color: #fff;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.3px;
    }}
    .card-header p {{
      color: #c8e6c9;
      font-size: 13px;
      margin-top: 6px;
    }}
    .card-body {{ padding: 32px 40px 36px; }}
    .card-body h2 {{
      font-size: 18px;
      color: #1a1a2e;
      margin-bottom: 6px;
    }}
    .card-body .subtitle {{
      font-size: 13px;
      color: #888;
      margin-bottom: 24px;
    }}
    .form-group {{ margin-bottom: 18px; }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #444;
      margin-bottom: 6px;
    }}
    input {{
      width: 100%;
      padding: 11px 14px;
      border: 1.5px solid #ddd;
      border-radius: 6px;
      font-size: 14px;
      color: #222;
      transition: border-color 0.2s;
      outline: none;
    }}
    input:focus {{ border-color: #43a047; }}
    button[type="submit"] {{
      width: 100%;
      padding: 13px;
      background: linear-gradient(135deg, #2e7d32, #43a047);
      color: #fff;
      border: none;
      border-radius: 6px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
      letter-spacing: 0.3px;
      transition: opacity 0.2s;
    }}
    button[type="submit"]:hover {{ opacity: 0.9; }}
    button[type="submit"]:disabled {{ opacity: 0.6; cursor: not-allowed; }}

    /* ── inline error banner ── */
    #errorMsg {{
      display: none;
      margin-top: 16px;
      padding: 12px 16px;
      border-radius: 6px;
      font-size: 13px;
      line-height: 1.5;
      background: #ffebee;
      color: #c62828;
      border-left: 4px solid #ef5350;
    }}
    #errorMsg.visible {{ display: block; }}

    /* ── success screen (hidden until account is created) ── */
    #successScreen {{
      display: none;
      text-align: center;
      padding: 8px 0 4px;
    }}
    #successScreen .check-icon {{
      font-size: 56px;
      line-height: 1;
      margin-bottom: 16px;
    }}
    #successScreen h3 {{
      font-size: 20px;
      font-weight: 700;
      color: #1a1a2e;
      margin-bottom: 8px;
    }}
    #successScreen p {{
      font-size: 14px;
      color: #666;
      line-height: 1.6;
      margin-bottom: 28px;
    }}
    .login-btn {{
      display: inline-block;
      width: 100%;
      padding: 13px;
      background: linear-gradient(135deg, #2e7d32, #43a047);
      color: #fff;
      border: none;
      border-radius: 6px;
      font-size: 15px;
      font-weight: 600;
      text-decoration: none;
      letter-spacing: 0.3px;
      transition: opacity 0.2s;
      cursor: pointer;
    }}
    .login-btn:hover {{ opacity: 0.9; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <h1>{settings.APP_NAME}</h1>
      <p>You've been invited to join the team</p>
    </div>
    <div class="card-body">

      <!-- ── FORM ── -->
      <div id="formSection">
        <h2>Set Up Your Account</h2>
        <p class="subtitle">Complete the details below to activate your account.</p>

        <form id="acceptForm" novalidate>
          <div class="form-group">
            <label for="name">Full Name <span style="color:#e53935">*</span></label>
            <input type="text" id="name" placeholder="e.g. Jane Cooper" required />
          </div>
          <div class="form-group">
            <label for="phone">Phone Number</label>
            <input type="tel" id="phone" placeholder="+974 XX XXX XXXX" />
          </div>
          <div class="form-group">
            <label for="password">Password <span style="color:#e53935">*</span></label>
            <input type="password" id="password" placeholder="Min 8 characters" minlength="8" required />
          </div>
          <div class="form-group">
            <label for="confirmPassword">Confirm Password <span style="color:#e53935">*</span></label>
            <input type="password" id="confirmPassword" placeholder="Re-enter your password" required />
          </div>

          <button type="submit" id="submitBtn">Join Now →</button>
          <div id="errorMsg"></div>
        </form>
      </div>

      <!-- ── SUCCESS SCREEN (shown after account is created) ── -->
      <div id="successScreen">
        <div class="check-icon">🎉</div>
        <h3>You're all set!</h3>
        <p id="successText">
          Your account has been created successfully.<br/>
          Use your email and the password you just set to sign in.
        </p>
        <a href="{settings.DASHBOARD_LOGIN_URL}" class="login-btn">
          Go to Login →
        </a>
      </div>

    </div>
  </div>

  <script>
    const TOKEN   = "{token}";
    const API_URL = "{accept_api}";

    document.getElementById("acceptForm").addEventListener("submit", async function(e) {{
      e.preventDefault();

      const name     = document.getElementById("name").value.trim();
      const phone    = document.getElementById("phone").value.trim();
      const password = document.getElementById("password").value;
      const confirm  = document.getElementById("confirmPassword").value;
      const errorEl  = document.getElementById("errorMsg");
      const btn      = document.getElementById("submitBtn");

      // Reset error
      errorEl.textContent = "";
      errorEl.classList.remove("visible");

      // Client-side validation
      if (!name) {{
        errorEl.textContent = "Full name is required.";
        errorEl.classList.add("visible");
        return;
      }}
      if (password.length < 8) {{
        errorEl.textContent = "Password must be at least 8 characters.";
        errorEl.classList.add("visible");
        return;
      }}
      if (password !== confirm) {{
        errorEl.textContent = "Passwords do not match. Please try again.";
        errorEl.classList.add("visible");
        return;
      }}

      btn.disabled = true;
      btn.textContent = "Setting up your account…";

      try {{
        const res = await fetch(API_URL, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            inviteToken: TOKEN,
            name:        name,
            phone:       phone || null,
            password:    password
          }})
        }});

        const data = await res.json();

        if (res.ok) {{
          // Personalise success message with the user's name
          const displayName = data.name || name;
          document.getElementById("successText").innerHTML =
            "Welcome, <strong>" + displayName + "</strong>! Your account has been created successfully.<br/>" +
            "Use your email and the password you just set to sign in.";

          // Hide form, show success screen
          document.getElementById("formSection").style.display = "none";
          document.getElementById("successScreen").style.display = "block";
        }} else {{
          errorEl.textContent = data.detail || "Something went wrong. Please try again.";
          errorEl.classList.add("visible");
          btn.disabled = false;
          btn.textContent = "Join Now →";
        }}
      }} catch (err) {{
        errorEl.textContent = "Network error. Please check your connection and try again.";
        errorEl.classList.add("visible");
        btn.disabled = false;
        btn.textContent = "Join Now →";
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@router.post("/invitations/accept")
async def accept_invitation(data: AcceptInvitationRequest):
    """
    Accept invitation and create account.
    
    **PUBLIC ENDPOINT** - No authentication required.
    Secured by invitation token.
    
    **Request Body:**
    - inviteToken: Token from invitation email
    - password: New password (min 8 characters)
    - name: Full name
    - phone: Phone number (optional)
    
    **Workflow:**
    1. User clicks link in invitation email
    2. Frontend extracts token from URL
    3. User fills in name, password, phone
    4. Account created with invited role
    5. User can immediately login
    
    **UI Reference:** Invitation acceptance page
    """
    return await RoleMatrixService.accept_invitation(data)


@router.delete("/invitations/{invitation_id}", status_code=http_status.HTTP_200_OK)
async def cancel_invitation(
    invitation_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Cancel a pending invitation.
    
    **Use case:** Admin made a mistake or person declined
    **Access:** Admin only
    """
    return await RoleMatrixService.cancel_invitation(invitation_id, current_user.id)


# ═════════════════════════════════════════════════════════════════
# PERMISSION RULES (Visibility Matrix)
# ═════════════════════════════════════════════════════════════════

@router.get("/rules/export/excel")
async def export_visibility_matrix(
    current_user=Depends(get_current_active_user)
):
    """
    Export visibility matrix to Excel.
    
    **UI Reference:** Download button on Visibility Matrix (Image 1, bottom)
    **Access:** Admin only
    """
    excel_file = await RoleMatrixService.export_visibility_matrix(current_user.id)
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=visibility_matrix.xlsx"}
    )


@router.post("/rules", response_model=PermissionRuleResponse, status_code=http_status.HTTP_201_CREATED)
async def create_permission_rule(
    data: PermissionRuleCreate,
    current_user=Depends(get_current_active_user)
):
    """
    Add a new permission rule (Add New Rule).
    
    **Request Body:**
    - moduleName: Module/Page name (e.g., "Financial Reporting")
    - adminAccess: VISIBLE or HIDDEN
    - managerAccess: VISIBLE or HIDDEN
    - instructorAccess: VISIBLE or HIDDEN
    - displayOrder: Sort order (optional)
    
    **UI Reference:** "Add New Rule" button (Image 1, top right)
    **Access:** Admin only
    """
    return await RoleMatrixService.create_permission_rule(data, current_user.id)


@router.get("/rules", response_model=VisibilityMatrixResponse)
async def get_visibility_matrix(
    current_user=Depends(get_current_active_user)
):
    """
    Get complete visibility matrix.
    
    **Returns:** All permission rules showing which roles can access which modules
    
    **UI Reference:** Visibility Matrix table (Image 1, bottom)
    **Access:** Admin only
    """
    return await RoleMatrixService.get_visibility_matrix(current_user.id)


@router.patch("/rules/{rule_id}", response_model=PermissionRuleResponse)
async def update_permission_rule(
    rule_id: str,
    data: PermissionRuleUpdate,
    current_user=Depends(get_current_active_user)
):
    """
    Edit permission rule (Edit Matrix - toggle visibility).
    
    **Use case:** Admin toggles VISIBLE/HIDDEN for Manager or Instructor
    
    **Request Body (all fields optional):**
    - adminAccess: VISIBLE or HIDDEN
    - managerAccess: VISIBLE or HIDDEN
    - instructorAccess: VISIBLE or HIDDEN
    
    **UI Reference:** Toggle buttons in Edit Matrix (Image 1, middle)
    **Access:** Admin only
    """
    return await RoleMatrixService.update_permission_rule(rule_id, data, current_user.id)


@router.delete("/rules/{rule_id}", status_code=http_status.HTTP_200_OK)
async def delete_permission_rule(
    rule_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Delete a permission rule.
    
    **Use case:** Remove module from visibility matrix
    **UI Reference:** Delete icon in Action column (Image 1)
    **Access:** Admin only
    """
    return await RoleMatrixService.delete_permission_rule(rule_id, current_user.id)


# ═════════════════════════════════════════════════════════════════
# ACTION PERMISSIONS (from UI)
# ═════════════════════════════════════════════════════════════════

@router.get("/action-permissions", response_model=ActionPermissionsResponse)
async def get_action_permissions(
    current_user=Depends(get_current_active_user)
):
    """
    Get action permissions per role.
    
    **Returns:** Hardcoded permissions for Admin, Manager, Instructor
    
    These are displayed for reference only.
    They cannot be edited via API.
    
    **UI Reference:** "Action Permissions" section (Image 2, bottom)
    **Access:** Admin only
    """
    return await RoleMatrixService.get_action_permissions()


# ═════════════════════════════════════════════════════════════════
# TERMS & CONDITIONS
# ═════════════════════════════════════════════════════════════════

@router.post("/terms", response_model=TermsConditionResponse, status_code=http_status.HTTP_201_CREATED)
async def create_terms_condition(
    data: TermsConditionCreate,
    current_user=Depends(get_current_active_user)
):
    """
    Create Terms & Conditions.
    
    **Request Body:**
    - introduction: Introduction text
    - eligibility: Eligibility requirements
    - healthDisclaimer: Health disclaimer
    - userResponsibility: User responsibility
    - accountUsage: Account usage terms
    - subscriptionsPayments: Subscription & payment terms
    - version: Version number (e.g., "1.0", "2.0")
    
    **Behavior:** Deactivates all previous versions, activates new one
    
    **UI Reference:** Terms & Condition section (Image 2)
    **Access:** Admin only
    """
    return await RoleMatrixService.create_terms_condition(data, current_user.id)


@router.get("/terms", response_model=TermsConditionResponse)
async def get_terms_condition():
    """
    Get active Terms & Conditions.
    
    **PUBLIC ENDPOINT** - No authentication required.
    Users need to see Terms & Conditions before signing up.
    
    **Returns:** Currently active Terms & Conditions
    
    **UI Reference:** Terms & Condition display (Image 2)
    """
    return await RoleMatrixService.get_terms_condition()


@router.patch("/terms/{terms_id}", response_model=TermsConditionResponse)
async def update_terms_condition(
    terms_id: str,
    data: TermsConditionUpdate,
    current_user=Depends(get_current_active_user)
):
    """
    Update Terms & Conditions.
    
    **Request Body (all fields optional):**
    - Same fields as create
    - isActive: Set to false to deactivate
    
    **UI Reference:** Edit button in Terms & Condition (Image 2)
    **Access:** Admin only
    """
    return await RoleMatrixService.update_terms_condition(terms_id, data, current_user.id)
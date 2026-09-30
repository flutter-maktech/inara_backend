"""
Members API Router
==================
Members = Users with role == USER who have purchased memberships.

Endpoints:
  GET    /members/stats                              → Stats cards (total, male, female)
  GET    /members                                    → Paginated member list + search
  GET    /members/{member_id}                        → Full member profile
  PATCH  /members/{member_id}                        → Edit member profile (Admin/Manager)
  DELETE /members/{member_id}                        → Delete member account (Admin only)
  POST   /members/notify                             → Send notification (app/whatsapp/both)
  GET    /members/export/excel                       → Export member list to Excel
  GET    /members/{member_id}/classes/export         → Export member's class list to Excel
  GET    /members/{member_id}/courses/export         → Export member's course list to Excel
  GET    /members/{member_id}/purchases/export       → Export member's purchase history to Excel

  MEMBER INVITATION FLOW:
  POST   /members/invitations                        → Invite a USER as a new member
  GET    /members/invitations                        → List all member invitations
  POST   /members/invitations/{invitation_id}/resend → Resend invitation email
  GET    /members/invitations/accept-form            → Serve HTML acceptance form (PUBLIC)
  POST   /members/invitations/accept                 → Accept invitation & create account (PUBLIC)
  DELETE /members/invitations/{invitation_id}        → Cancel invitation (Admin/Manager)

  Revised invitation workflow:
    Invite → Accept → Complete Details → Done
    (Membership selection happens inside the mobile app)
"""

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from typing import Optional

from app.models.member_model import (
    MemberStats,
    MemberListResponse,
    MemberProfile,
    SendNotificationRequest,
    SendNotificationResponse,
    UpdateMemberRequest,
    UpdateMemberResponse,
    InviteMemberRequest,
    MemberInvitationResponse,
    MemberInvitationListResponse,
    AcceptMemberInvitationRequest,
    AcceptMemberInvitationResponse,
)
from app.services.member_service import MemberService
from app.core.cloudinary_service import upload_image
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# HTTPS-AWARE BASE URL HELPER
# ─────────────────────────────────────────────────────────────────

def _get_public_base_url(request: Request) -> str:
    """
    Returns the public-facing base URL, correctly using https:// when the
    application is running behind a reverse proxy (nginx, Caddy, AWS ALB, etc.).

    The problem this solves:
      FastAPI's `request.base_url` reads the raw socket-level scheme, which is
      always `http://` when the app runs inside a container — even if the public
      URL is `https://`. The reverse proxy terminates TLS and forwards the request
      over plain HTTP internally, setting the `X-Forwarded-Proto: https` header to
      signal the original scheme.

      Without this helper, the HTML form's JavaScript `fetch()` target URL would be
      `http://...` while the page itself was served over `https://`, causing the
      browser to block the request as Mixed Content.

    Resolution order (most-specific first):
      1. X-Forwarded-Proto header  — set by nginx / Caddy / ALB (most reliable)
      2. Forwarded header          — RFC 7239 standard header
      3. request.base_url scheme   — raw socket scheme (fallback / localhost)
    """
    base = str(request.base_url).rstrip("/")

    # Check X-Forwarded-Proto (nginx, Caddy, AWS ALB, Cloudflare, etc.)
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    if forwarded_proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
        return base

    # Check RFC 7239 Forwarded header  e.g. "Forwarded: proto=https;host=example.com"
    forwarded = request.headers.get("forwarded", "")
    if forwarded:
        for part in forwarded.split(";"):
            part = part.strip().lower()
            if part.startswith("proto=https") and base.startswith("http://"):
                base = "https://" + base[len("http://"):]
                return base

    return base


# ─────────────────────────────────────────────────────────────────
# STATS — 3 cards at the top of the Members page
# ─────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=MemberStats)
async def get_member_stats(
    current_user=Depends(get_current_active_user)
):
    """
    Returns aggregate member counts:
    - Total Number of Members
    - Total Men
    - Total Women

    **UI Reference:** Three stat cards at top of Members page.
    **Access:** Admin / Manager only
    """
    return await MemberService.get_member_stats(current_user.id)


# ─────────────────────────────────────────────────────────────────
# EXPORT — must be defined BEFORE /{member_id} to avoid routing conflict
# ─────────────────────────────────────────────────────────────────

@router.get("/export/excel")
async def export_members_to_excel(
    search: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export full member list to Excel (.xlsx).

    **UI Reference:** Download button on Members page.
    **Access:** Admin / Manager only
    """
    excel_file = await MemberService.export_members_to_excel(
        caller_id=current_user.id,
        search=search
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=members_export.xlsx"}
    )


# ─────────────────────────────────────────────────────────────────
# MEMBER INVITATIONS
# Defined BEFORE /{member_id} routes to avoid routing conflicts.
# ─────────────────────────────────────────────────────────────────

@router.post(
    "/invitations",
    response_model=MemberInvitationResponse,
    status_code=status.HTTP_201_CREATED
)
async def invite_member(
    request: Request,
    data: InviteMemberRequest,
    current_user=Depends(get_current_active_user)
):
    """
    Invite a new user (USER role) to join the app as a member.

    **Workflow:**
    1. Admin enters the invitee's email (and optionally their name).
    2. System generates a secure invitation token valid for 7 days.
    3. An email is sent with a link to the account setup form.
    4. The invitee clicks the link and fills in their details (name, phone, password).
    5. Their account is created immediately — no membership step during onboarding.
    6. The invitee is directed to the app to explore and purchase a membership.

    **Request Body:**
    ```json
    { "email": "newmember@example.com", "name": "Jane Doe" }
    ```

    **Access:** Admin / Manager only
    """
    base_url = _get_public_base_url(request)
    return await MemberService.invite_member(
        caller_id=current_user.id,
        data=data,
        base_url=base_url
    )


@router.get("/invitations", response_model=MemberInvitationListResponse)
async def get_member_invitations(
    page: int = 1,
    pageSize: int = 10,
    status: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Get all sent member invitations with pagination.

    **Query Parameters:**
    - status: Filter by PENDING, ACCEPTED, EXPIRED, CANCELLED
    - page / pageSize: Pagination

    **Access:** Admin / Manager only
    """
    return await MemberService.get_member_invitations(
        caller_id=current_user.id,
        page=page,
        page_size=pageSize,
        status_filter=status
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=MemberInvitationResponse
)
async def resend_member_invitation(
    request: Request,
    invitation_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Resend a member invitation email.

    - Works on PENDING and EXPIRED invitations.
    - If the invitation was expired, a new token and 7-day expiry are issued.
    - CANCELLED invitations cannot be resent.

    **Access:** Admin / Manager only
    """
    base_url = _get_public_base_url(request)
    return await MemberService.resend_member_invitation(
        caller_id=current_user.id,
        invitation_id=invitation_id,
        base_url=base_url
    )


@router.get(
    "/invitations/accept-form",
    response_class=HTMLResponse,
    include_in_schema=False
)
async def accept_member_invitation_form(request: Request, token: str):
    """
    Serves the HTML page the invitee lands on after clicking the email link.

    PUBLIC ENDPOINT — no authentication required.

    Revised workflow (single step):
      Step 1 — Account Setup: Full Name, Phone, Password, Confirm Password.
      Done   — Success screen with:
               • "Thank you for joining Inara Yoga..." message
               • iOS App Store button + link
               • Google Play button + link
               • QR code for easy mobile scanning

    Membership selection happens inside the app — not in this web flow.
    """
    from app.core.config import settings
    base_url   = _get_public_base_url(request)
    accept_api = f"{base_url}/api/v1/members/invitations/accept"

    ios_url     = settings.IOS_APP_URL
    android_url = settings.ANDROID_APP_URL

    # QR code pointing to the Android store (universal camera fallback)
    qr_api_url = (
        f"https://api.qrserver.com/v1/create-qr-code/"
        f"?size=180x180&data={android_url}&color=1a237e&bgcolor=ffffff&margin=10"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Join {settings.APP_NAME}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, #e8eaf6 0%, #f4f4f7 100%);
      font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
      padding: 24px;
    }}

    .card {{
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 6px 36px rgba(0,0,0,0.10);
      width: 100%;
      max-width: 460px;
      overflow: hidden;
    }}

    .card-header {{
      background: linear-gradient(135deg, #1a237e, #283593);
      padding: 36px 40px 28px;
      text-align: center;
    }}
    .card-header h1 {{ color: #fff; font-size: 24px; font-weight: 700; letter-spacing: -0.3px; }}
    .card-header p  {{ color: #c5cae9; font-size: 13px; margin-top: 6px; }}

    .card-body {{ padding: 36px 40px 32px; }}

    /* Steps */
    .step         {{ display: none; }}
    .step.active  {{ display: block; }}

    h2 {{ font-size: 19px; color: #1a1a2e; font-weight: 700; margin-bottom: 5px; }}
    .subtitle {{ font-size: 13px; color: #888; margin-bottom: 24px; line-height: 1.5; }}

    /* Form fields */
    .form-group {{ margin-bottom: 18px; }}
    label {{ display: block; font-size: 12px; font-weight: 700; color: #555;
             text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; }}
    input {{
      width: 100%; padding: 12px 15px;
      border: 1.5px solid #dde0e8; border-radius: 8px;
      font-size: 14px; color: #1a1a2e;
      transition: border-color 0.2s, box-shadow 0.2s; outline: none;
      background: #fafafa;
    }}
    input:focus {{
      border-color: #283593;
      box-shadow: 0 0 0 3px rgba(40,53,147,0.10);
      background: #fff;
    }}
    input::placeholder {{ color: #bbb; }}

    /* Gender selector */
    .gender-group {{
      display: flex; gap: 10px; flex-wrap: wrap;
    }}
    .gender-btn {{
      flex: 1; min-width: 110px;
      padding: 10px 8px; border: 1.5px solid #dde0e8;
      border-radius: 8px; background: #fafafa;
      font-size: 13px; font-weight: 600; color: #555;
      cursor: pointer; text-align: center;
      transition: border-color 0.2s, background 0.2s, color 0.2s;
      user-select: none;
    }}
    .gender-btn:hover {{ border-color: #283593; background: #f0f2ff; color: #1a237e; }}
    .gender-btn.selected {{
      border-color: #1a237e; background: #e8eaf6; color: #1a237e;
    }}

    /* Buttons */
    .btn {{
      width: 100%; padding: 14px;
      background: linear-gradient(135deg, #1a237e, #283593);
      color: #fff; border: none; border-radius: 8px;
      font-size: 15px; font-weight: 700; cursor: pointer;
      margin-top: 6px; transition: opacity 0.2s, transform 0.1s;
      letter-spacing: 0.2px;
    }}
    .btn:hover   {{ opacity: 0.92; transform: translateY(-1px); }}
    .btn:active  {{ transform: translateY(0); }}
    .btn:disabled {{ opacity: 0.50; cursor: not-allowed; transform: none; }}

    /* Alert messages */
    #msg {{
      margin-top: 16px; padding: 12px 16px;
      border-radius: 8px; font-size: 13px; line-height: 1.5;
      display: none;
    }}
    #msg.success {{
      background: #e8eaf6; color: #1a237e;
      border-left: 4px solid #283593; display: block;
    }}
    #msg.error {{
      background: #ffebee; color: #c62828;
      border-left: 4px solid #ef5350; display: block;
    }}

    /* ── Success / Done screen ── */
    .success-icon {{
      font-size: 56px; text-align: center;
      margin-bottom: 18px; animation: pop 0.4s ease;
    }}
    @keyframes pop {{
      0%   {{ transform: scale(0.5); opacity: 0; }}
      80%  {{ transform: scale(1.15); }}
      100% {{ transform: scale(1); opacity: 1; }}
    }}

    .welcome-msg {{
      font-size: 15px; color: #555; line-height: 1.7;
      text-align: center; margin-bottom: 28px;
    }}
    .welcome-msg strong {{ color: #1a237e; }}

    /* App store buttons */
    .store-btns {{
      display: flex; gap: 12px; justify-content: center;
      flex-wrap: wrap; margin-bottom: 28px;
    }}
    .store-btn {{
      display: inline-flex; align-items: center; gap: 8px;
      padding: 13px 22px; border-radius: 10px;
      font-size: 14px; font-weight: 700; text-decoration: none;
      transition: opacity 0.2s, transform 0.15s;
      white-space: nowrap;
    }}
    .store-btn:hover {{ opacity: 0.88; transform: translateY(-2px); }}
    .store-btn.ios     {{ background: #000; color: #fff; }}
    .store-btn.android {{ background: #1a237e; color: #fff; }}
    .store-btn .icon   {{ font-size: 18px; }}

    /* QR code */
    .qr-section {{ text-align: center; padding-top: 4px; }}
    .qr-label {{
      font-size: 12px; color: #999; margin-bottom: 12px;
      text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;
    }}
    .qr-section img {{
      border: 2px solid #e8eaf6; border-radius: 12px;
      padding: 6px; background: #fff;
    }}
    .qr-note {{
      font-size: 11px; color: #bbb; margin-top: 10px; line-height: 1.4;
    }}

    /* Divider */
    .divider {{
      border: none; border-top: 1px solid #eee;
      margin: 24px 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <h1>{settings.APP_NAME}</h1>
      <p>Complete your account setup</p>
    </div>

    <div class="card-body">

      <!-- ── STEP 1: Account Setup ── -->
      <div class="step active" id="step1">
        <h2>Set Up Your Account</h2>
        <p class="subtitle">Enter your details to create your {settings.APP_NAME} account.</p>

        <div class="form-group">
          <label>Full Name <span style="color:#e53935">*</span></label>
          <input id="name" type="text" placeholder="e.g. Jane Doe" autocomplete="name" required/>
        </div>
        <div class="form-group">
          <label>Phone Number <span style="color:#aaa;font-weight:400;text-transform:none;">(optional)</span></label>
          <input id="phone" type="tel" placeholder="+974 XX XXX XXXX" autocomplete="tel"/>
        </div>
        <div class="form-group">
          <label>Gender <span style="color:#aaa;font-weight:400;text-transform:none;">(optional)</span></label>
          <div class="gender-group" id="genderGroup">
            <div class="gender-btn" data-value="Male"   onclick="selectGender(this)">♂ Male</div>
            <div class="gender-btn" data-value="Female" onclick="selectGender(this)">♀ Female</div>
          </div>
        </div>
        <div class="form-group">
          <label>Password <span style="color:#e53935">*</span></label>
          <input id="password" type="password" placeholder="Min. 8 characters"
                 minlength="8" autocomplete="new-password" required/>
        </div>
        <div class="form-group">
          <label>Confirm Password <span style="color:#e53935">*</span></label>
          <input id="confirmPassword" type="password" placeholder="Re-enter your password"
                 autocomplete="new-password" required/>
        </div>

        <button class="btn" id="submitBtn" onclick="submitForm()">
          Join {settings.APP_NAME} →
        </button>

        <div id="msg"></div>
      </div>

      <!-- ── DONE: Success screen ── -->
      <div class="step" id="stepDone">
        <div class="success-icon">🎉</div>

        <p class="welcome-msg" id="welcomeText">
          Thank you for joining <strong>{settings.APP_NAME}</strong>!
          Download our app to book a class, get your membership, and find out more about us.
        </p>

        <!-- App Store Buttons -->
        <div class="store-btns">
          <a id="iosBtn" href="#" class="store-btn ios" target="_blank" rel="noopener">
            <span class="icon">🍎</span> App Store
          </a>
          <a id="androidBtn" href="#" class="store-btn android" target="_blank" rel="noopener">
            <span class="icon">🤖</span> Google Play
          </a>
        </div>

        <hr class="divider"/>

        <!-- QR Code -->
        <div class="qr-section">
          <p class="qr-label">📱 Scan to Download</p>
          <img id="qrImg" src="{qr_api_url}" width="150" height="150"
               alt="Scan to download {settings.APP_NAME}"/>
          <p class="qr-note">Point your phone camera at the QR code to open the app store.</p>
        </div>
      </div>

    </div>
  </div>

  <script>
    const TOKEN      = "{token}";
    const ACCEPT_API = "{accept_api}";
    const IOS_URL    = "{ios_url}";
    const ANDROID_URL= "{android_url}";

    function showMsg(text, type) {{
      const el = document.getElementById('msg');
      el.textContent = text;
      el.className = type;
    }}

    // ── Gender selection ────────────────────────────────────────────
    let selectedGender = null;

    function selectGender(btn) {{
      document.querySelectorAll('.gender-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      selectedGender = btn.dataset.value;
    }}

    function setDone(iosUrl, androidUrl) {{
      // Wire up the real app store URLs returned from the API
      document.getElementById('iosBtn').href     = iosUrl;
      document.getElementById('androidBtn').href = androidUrl;

      // Hide step 1, show done screen
      document.getElementById('step1').classList.remove('active');
      document.getElementById('stepDone').classList.add('active');
    }}

    async function submitForm() {{
      const name    = document.getElementById('name').value.trim();
      const phone   = document.getElementById('phone').value.trim();
      const pass    = document.getElementById('password').value;
      const confirm = document.getElementById('confirmPassword').value;

      // ── Client-side validation ──────────────────────────────────
      if (!name) {{
        showMsg('Full name is required.', 'error');
        document.getElementById('name').focus();
        return;
      }}
      if (pass.length < 8) {{
        showMsg('Password must be at least 8 characters.', 'error');
        document.getElementById('password').focus();
        return;
      }}
      if (pass !== confirm) {{
        showMsg('Passwords do not match.', 'error');
        document.getElementById('confirmPassword').focus();
        return;
      }}

      // ── Submit ──────────────────────────────────────────────────
      const btn = document.getElementById('submitBtn');
      btn.disabled    = true;
      btn.textContent = 'Creating your account…';

      try {{
        const res = await fetch(ACCEPT_API, {{
          method:  'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body:    JSON.stringify({{
            inviteToken: TOKEN,
            name:        name,
            phone:       phone || null,
            gender:      selectedGender || null,
            password:    pass,
          }})
        }});

        const data = await res.json();

        if (res.ok) {{
          setDone(
            data.iosAppUrl     || IOS_URL,
            data.androidAppUrl || ANDROID_URL
          );
        }} else {{
          showMsg(data.detail || 'Something went wrong. Please try again.', 'error');
          btn.disabled    = false;
          btn.textContent = 'Join {settings.APP_NAME} →';
        }}
      }} catch (e) {{
        showMsg('Network error. Please check your connection and try again.', 'error');
        btn.disabled    = false;
        btn.textContent = 'Join {settings.APP_NAME} →';
      }}
    }}

    // Allow Enter key to submit the form
    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Enter' && document.getElementById('step1').classList.contains('active')) {{
        const btn = document.getElementById('submitBtn');
        if (!btn.disabled) submitForm();
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@router.post(
    "/invitations/accept",
    response_model=AcceptMemberInvitationResponse,
    status_code=status.HTTP_201_CREATED
)
async def accept_member_invitation(data: AcceptMemberInvitationRequest):
    """
    Accept a member invitation and create the user account.

    **PUBLIC ENDPOINT** — no authentication required. Secured by `inviteToken`.

    **Revised Workflow:** Invite → Accept → Complete Details → Done

    **Request Body:**
    ```json
    {
      "inviteToken": "<token from email>",
      "name":        "Jane Doe",
      "phone":       "+974 XX XXX XXXX",
      "password":    "MySecurePass1"
    }
    ```

    **Workflow:**
    1. Validates the invitation token.
    2. Creates a USER account (pre-verified — no OTP needed).
    3. Marks the invitation as ACCEPTED.
    4. Sends a welcome email with app store links and QR code.

    **Returns:** userId, email, name, iosAppUrl, androidAppUrl

    Membership selection is handled inside the mobile app.
    """
    return await MemberService.accept_member_invitation(data)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_200_OK
)
async def cancel_member_invitation(
    invitation_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Cancel a pending member invitation.

    **Behaviour:** Sets invitation status to CANCELLED. Cannot be undone from the
    same record — re-invite the email to issue a fresh invitation.

    **Access:** Admin / Manager only
    """
    return await MemberService.cancel_member_invitation(
        caller_id=current_user.id,
        invitation_id=invitation_id
    )


# ─────────────────────────────────────────────────────────────────
# GET ALL MEMBERS
# ─────────────────────────────────────────────────────────────────

@router.get("", response_model=MemberListResponse)
async def get_all_members(
    search: Optional[str] = None,
    page: int = 1,
    pageSize: int = 10,
    current_user=Depends(get_current_active_user)
):
    """
    Returns paginated member list.

    **Columns:** Name/Email, Membership/Package, Bookings, Joined, Last Booking, Gender
    **Features:** Search by member name or email
    **Access:** Admin / Manager only
    """
    return await MemberService.get_all_members(
        caller_id=current_user.id,
        search=search,
        page=page,
        page_size=pageSize
    )


# ─────────────────────────────────────────────────────────────────
# SEND NOTIFICATION
# ─────────────────────────────────────────────────────────────────

@router.post("/notify", response_model=SendNotificationResponse, status_code=status.HTTP_200_OK)
async def send_notification(
    data: SendNotificationRequest,
    current_user=Depends(get_current_active_user)
):
    """
    Send a notification to all members (or a selected subset).

    **Request body:**
    ```json
    {
      "message": "Your membership expires in 3 days...",
      "channel": "both",        // "app" | "whatsapp" | "both"
      "memberIds": []           // empty = send to ALL active members
    }
    ```

    **UI Reference:** Send Notification modal
    **Access:** Admin / Manager only
    """
    return await MemberService.send_notification(
        caller_id=current_user.id,
        data=data
    )


# ─────────────────────────────────────────────────────────────────
# GET MEMBER PROFILE BY ID
# ─────────────────────────────────────────────────────────────────

@router.get("/{member_id}", response_model=MemberProfile)
async def get_member_profile(
    member_id: str,
    classesPage: int = 1,
    classesPageSize: int = 6,
    coursesPage: int = 1,
    coursesPageSize: int = 6,
    purchasesPage: int = 1,
    purchasesPageSize: int = 6,
    current_user=Depends(get_current_active_user)
):
    """
    Full member profile (eye icon on member row).

    **Returns:**
    - Profile info + avatar + last booking date
    - Active membership card with expiry and total class bookings
    - Favourite class + favourite instructor
    - Paginated list of classes (with booking count per class)
    - Paginated list of courses (via class bookings)
    - Paginated purchase history (store orders)

    **UI Reference:** Member Profile screen
    **Access:** Admin / Manager only
    """
    return await MemberService.get_member_profile(
        caller_id=current_user.id,
        member_id=member_id,
        classes_page=classesPage,
        classes_page_size=classesPageSize,
        courses_page=coursesPage,
        courses_page_size=coursesPageSize,
        purchases_page=purchasesPage,
        purchases_page_size=purchasesPageSize
    )


# ─────────────────────────────────────────────────────────────────
# UPDATE MEMBER PROFILE (Admin / Manager)
# ─────────────────────────────────────────────────────────────────

@router.patch("/{member_id}", response_model=UpdateMemberResponse, status_code=status.HTTP_200_OK)
async def update_member(
    member_id: str,
    name: Optional[str] = Form(default=None, description="Display name"),
    phone: Optional[str] = Form(default=None, description="Phone number"),
    gender: Optional[str] = Form(default=None, description="Gender"),
    bio: Optional[str] = Form(default=None, description="Short bio / about text"),
    dateOfBirth: Optional[str] = Form(
        default=None,
        description="Birthday in DD-MM format (day + month only, e.g. '25-12')"
    ),

    # ── Image ──────────────────────────────────────────────────────
    avatar: UploadFile = File(default=None),

    current_user=Depends(get_current_active_user)
):
    """
    Edit a member's profile data.

    **Content-Type:** `multipart/form-data`

    **Form fields (all optional — send only what needs to change):**
    | Field         | Type   | Description                                      |
    |---------------|--------|--------------------------------------------------|
    | `name`        | string | Display name                                     |
    | `phone`       | string | Phone number                                     |
    | `gender`      | string | Gender                                           |
    | `bio`         | string | Short bio / about text                           |
    | `dateOfBirth` | string | Birthday in DD-MM format (e.g. `"25-12"`)        |
    | `avatar`      | file   | Avatar image — JPEG/PNG/WEBP/GIF, max 5 MB       |

    **Behaviour:** PATCH semantics — only supplied (non-null) fields are written to DB.
    If `avatar` file is provided it is uploaded to Cloudinary and the resulting
    secure URL is stored; otherwise the existing avatar is left unchanged.

    **Response:** Returns the full updated member snapshot alongside a list of
    which fields were changed — no follow-up GET required.

    **Access:** Admin / Manager only
    """
    # Upload avatar to Cloudinary if a file was provided
    avatar_url: Optional[str] = None
    if avatar and avatar.filename:
        avatar_url = await upload_image(avatar, folder="members/avatars")

    # Build the Pydantic model the service already understands
    data = UpdateMemberRequest(
        name=name,
        phone=phone,
        gender=gender,
        avatar=avatar_url,
        bio=bio,
        dateOfBirth=dateOfBirth,
    )

    return await MemberService.update_member(
        caller_id=current_user.id,
        member_id=member_id,
        data=data
    )


# ─────────────────────────────────────────────────────────────────
# DELETE MEMBER (Admin only)
# ─────────────────────────────────────────────────────────────────

@router.delete("/{member_id}", status_code=status.HTTP_200_OK)
async def delete_member(
    member_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Permanently delete a member's account.

    ⚠️ **This action is irreversible.**
    All associated bookings, memberships, orders, reviews, and cart items
    are also deleted via DB cascade.

    **Access:** Admin only
    """
    return await MemberService.delete_member(
        caller_id=current_user.id,
        member_id=member_id
    )


# ─────────────────────────────────────────────────────────────────
# EXPORT SUB-LISTS FROM MEMBER PROFILE
# ─────────────────────────────────────────────────────────────────

@router.get("/{member_id}/classes/export")
async def export_member_classes(
    member_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Export member's class booking list to Excel.

    **UI Reference:** Download button next to 'List of Classes' on Member Profile
    **Access:** Admin / Manager only
    """
    excel_file = await MemberService.export_member_classes_to_excel(
        caller_id=current_user.id,
        member_id=member_id
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=member_classes.xlsx"}
    )


@router.get("/{member_id}/courses/export")
async def export_member_courses(
    member_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Export member's course list to Excel.

    **UI Reference:** Download button next to 'List of Courses' on Member Profile
    **Access:** Admin / Manager only
    """
    excel_file = await MemberService.export_member_courses_to_excel(
        caller_id=current_user.id,
        member_id=member_id
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=member_courses.xlsx"}
    )


@router.get("/{member_id}/purchases/export")
async def export_member_purchases(
    member_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Export member's purchase history to Excel.

    **UI Reference:** Download button next to 'Purchase History' on Member Profile
    **Access:** Admin / Manager only
    """
    excel_file = await MemberService.export_member_purchases_to_excel(
        caller_id=current_user.id,
        member_id=member_id
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=member_purchases.xlsx"}
    )
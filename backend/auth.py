from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from jose import jwt, JWTError
import httpx, os, time
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SECRET_KEY           = os.getenv("SECRET_KEY")
ALLOWED_EMAILS       = [e.strip() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]
ADMIN_EMAILS         = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
BASE_URL             = os.getenv("BASE_URL", "http://localhost:8000")
IS_PROD              = os.getenv("ENVIRONMENT", "development") == "production"

SESSION_HOURS = 8

# ── Helpers ────────────────────────────────────────────────────────────────

def create_session_token(email: str, name: str, picture: str) -> str:
    role = "superadmin" if email in ADMIN_EMAILS else "user"
    payload = {
        "email":   email,
        "name":    name,
        "picture": picture,
        "role":    role,
        "exp":     int(time.time()) + SESSION_HOURS * 3600,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def decode_session_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada")

def get_current_user(request: Request) -> dict:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return decode_session_token(token)

def require_superadmin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return user

# ── OAuth Routes ───────────────────────────────────────────────────────────

@router.get("/auth/login")
async def login():
    redirect_uri = f"{BASE_URL}/auth/callback"
    params = "&".join([
        f"client_id={GOOGLE_CLIENT_ID}",
        f"redirect_uri={redirect_uri}",
        "response_type=code",
        "scope=openid%20email%20profile",
        "access_type=online",
        "prompt=select_account",
    ])
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@router.get("/auth/callback")
async def callback(code: str):
    redirect_uri = f"{BASE_URL}/auth/callback"
    async with httpx.AsyncClient() as client:
        # Troca code por access_token
        token_res = await client.post("https://oauth2.googleapis.com/token", data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        })
        token_data = token_res.json()
        if "error" in token_data:
            raise HTTPException(status_code=400, detail=f"Erro OAuth: {token_data['error']}")

        # Busca dados do usuário
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
        user = user_res.json()

    email = user.get("email", "").lower().strip()
    if email not in [e.lower() for e in ALLOWED_EMAILS]:
        raise HTTPException(status_code=403, detail="Email não autorizado para acessar este sistema.")

    token = create_session_token(email, user.get("name", ""), user.get("picture", ""))
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        "session", token,
        httponly=True,
        secure=IS_PROD,
        samesite="lax",
        max_age=SESSION_HOURS * 3600,
    )
    return resp

@router.get("/auth/logout")
async def logout():
    resp = RedirectResponse(url="/login.html", status_code=302)
    resp.delete_cookie("session")
    return resp

@router.get("/api/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "email":   user["email"],
        "name":    user["name"],
        "picture": user.get("picture", ""),
        "role":    user["role"],
    }

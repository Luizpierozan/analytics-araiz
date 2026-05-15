from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from auth import router as auth_router, get_current_user, require_superadmin
from analise_avancada import ingest_new_file, get_dashboard_geral, get_inadimplencia
from projecoes import get_projecoes, invalidate_cache
from clientes import get_clientes, invalidate_cache as invalidate_cache_clientes
from dotenv import load_dotenv
import os, tempfile

load_dotenv()

app = FastAPI()

IS_PROD    = os.getenv("ENVIRONMENT", "development") == "production"
ALLOWED_ORIGINS = ["*"] if not IS_PROD else [os.getenv("BASE_URL", "")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Auth routes (públicas: /auth/login, /auth/callback, /auth/logout, /api/me)
app.include_router(auth_router)

# ── API protegida ────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(require_superadmin),   # só superadmin
):
    # Validar tipo de arquivo
    if not file.filename.endswith((".xls", ".xlsx", ".csv")):
        return JSONResponse({"sucesso": False, "erro": "Arquivo deve ser .xls, .xlsx ou .csv"}, status_code=400)

    # Limitar tamanho: 20MB
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return JSONResponse({"sucesso": False, "erro": "Arquivo muito grande (máx 20MB)"}, status_code=400)

    try:
        suffix = os.path.splitext(file.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        linhas = ingest_new_file(tmp_path, usuario=user["email"])
        os.unlink(tmp_path)

        if linhas is not False:
            invalidate_cache()  # força recálculo das projeções
            invalidate_cache_clientes()
            return {"sucesso": True, "linhas": linhas}
        else:
            return JSONResponse({"sucesso": False, "erro": "Erro ao processar arquivo"}, status_code=500)
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)


@app.get("/api/dashboard")
async def dashboard(
    start: str = None,
    end:   str = None,
    benchmark: str = "mom",
    user: dict = Depends(get_current_user),     # qualquer usuário autenticado
):
    return get_dashboard_geral(start_date=start, end_date=end, benchmark=benchmark)


@app.get("/api/projecoes")
async def projecoes(user: dict = Depends(get_current_user)):
    return get_projecoes()


@app.get("/api/clientes")
async def clientes(user: dict = Depends(get_current_user)):
    return get_clientes()


@app.get("/api/inadimplencia")
async def inadimplencia(
    start: str = None,
    end:   str = None,
    user: dict = Depends(get_current_user),
):
    return get_inadimplencia(start_date=start, end_date=end)


# ── Frontend estático (deve ser o último mount) ───────────────────────────────
frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

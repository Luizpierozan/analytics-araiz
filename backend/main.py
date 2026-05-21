from fastapi import FastAPI, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from auth import router as auth_router, get_current_user, require_superadmin, decode_session_token
from analise_avancada import ingest_new_file, get_dashboard_geral, get_inadimplencia
from projecoes import get_projecoes, invalidate_cache
from clientes import get_clientes, invalidate_cache as invalidate_cache_clientes
from dotenv import load_dotenv
import os, tempfile

load_dotenv()

IS_PROD = os.getenv("ENVIRONMENT", "development") == "production"

app = FastAPI()

# ── Middleware: protege index.html em produção ───────────────────────────────
class FrontendAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Só aplica proteção em produção e só para a rota raiz / index.html
        if IS_PROD and request.url.path in ("/", "/index.html"):
            token = request.cookies.get("session")
            if not token:
                return RedirectResponse(url="/login.html", status_code=302)
            try:
                decode_session_token(token)
            except Exception:
                return RedirectResponse(url="/login.html", status_code=302)
        return await call_next(request)

app.add_middleware(FrontendAuthMiddleware)

ALLOWED_ORIGINS = ["*"] if not IS_PROD else [os.getenv("BASE_URL", "")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
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


@app.get("/api/uploads")
async def list_uploads(user: dict = Depends(require_superadmin)):
    """Lista histórico de importações com metadados para rollback."""
    from database import get_supabase
    import json
    sb = get_supabase()
    res = sb.table('audit_uploads').select('id,usuario,arquivo,linhas,chaves,criado_em').order('criado_em', desc=True).limit(50).execute()
    items = []
    for r in (res.data or []):
        chaves = r.get('chaves')
        n_chaves = 0
        if chaves:
            try:
                parsed = json.loads(chaves) if isinstance(chaves, str) else chaves
                n_chaves = len(parsed)
            except Exception:
                n_chaves = 0
        items.append({
            'id':        r['id'],
            'usuario':   r['usuario'],
            'arquivo':   r['arquivo'],
            'linhas':    r['linhas'],
            'n_chaves':  n_chaves,
            'revertivel': n_chaves > 0,
            'criado_em': r['criado_em'],
        })
    return {'sucesso': True, 'uploads': items}


@app.delete("/api/uploads/{upload_id}/reverter")
async def reverter_upload(upload_id: int, user: dict = Depends(require_superadmin)):
    """Reverte um upload deletando todas as transações daquele import."""
    from database import get_supabase
    import json
    sb = get_supabase()

    # Buscar registro de auditoria
    res = sb.table('audit_uploads').select('id,arquivo,linhas,chaves').eq('id', upload_id).execute()
    if not res.data:
        return JSONResponse({'sucesso': False, 'erro': 'Upload não encontrado'}, status_code=404)

    registro = res.data[0]
    chaves_raw = registro.get('chaves')
    if not chaves_raw:
        return JSONResponse({'sucesso': False, 'erro': 'Este upload não possui chaves salvas — não é possível reverter automaticamente'}, status_code=400)

    try:
        chaves = json.loads(chaves_raw) if isinstance(chaves_raw, str) else chaves_raw
    except Exception:
        return JSONResponse({'sucesso': False, 'erro': 'Erro ao ler lista de chaves'}, status_code=500)

    if not chaves:
        return JSONResponse({'sucesso': False, 'erro': 'Lista de chaves vazia'}, status_code=400)

    # Deletar em lotes de 100 (limite do PostgREST)
    BATCH = 100
    deletadas = 0
    for i in range(0, len(chaves), BATCH):
        lote = chaves[i:i+BATCH]
        sb.table('transacoes').delete().in_('chave', lote).execute()
        deletadas += len(lote)

    # Marcar upload como revertido na auditoria
    sb.table('audit_uploads').update({
        'arquivo': f'[REVERTIDO] {registro["arquivo"]}',
        'chaves':  None,
    }).eq('id', upload_id).execute()

    # Invalida caches
    invalidate_cache()
    invalidate_cache_clientes()

    return {
        'sucesso':   True,
        'deletadas': deletadas,
        'arquivo':   registro['arquivo'],
    }


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

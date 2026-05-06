from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from analise_avancada import ingest_new_file, get_dashboard_geral, get_inadimplencia
import os
import tempfile

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xls") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
            
        success = ingest_new_file(tmp_path)
        os.unlink(tmp_path)
        
        if success:
            return {"sucesso": True}
        else:
            return {"sucesso": False, "erro": "Erro ao processar arquivo no SQLite"}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

@app.get("/api/dashboard")
async def dashboard(start: str = None, end: str = None, benchmark: str = "mom"):
    return get_dashboard_geral(start_date=start, end_date=end, benchmark=benchmark)

@app.get("/api/inadimplencia")
async def inadimplencia(start: str = None, end: str = None):
    return get_inadimplencia(start_date=start, end_date=end)

frontend_path = os.path.join(os.path.dirname(__file__), '../frontend')
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

from supabase import create_client, Client
import os

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY devem estar definidos")
    return create_client(url, key)

def fetch_all_transacoes() -> list[dict]:
    """Busca todas as transações do Supabase com paginação automática."""
    sb = get_supabase()
    all_rows = []
    page = 0
    PAGE_SIZE = 1000
    while True:
        res = (sb.table("transacoes")
               .select("*")
               .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1)
               .execute())
        if not res.data:
            break
        all_rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        page += 1
    return all_rows

from supabase import create_client, Client
import os

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY devem estar definidos")
    return create_client(url, key)


def _paginate(query_builder) -> list[dict]:
    """Executa uma query com paginação automática."""
    all_rows = []
    page = 0
    PAGE_SIZE = 1000
    while True:
        res = query_builder.range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).execute()
        if not res.data:
            break
        all_rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        page += 1
    return all_rows


def fetch_all_transacoes() -> list[dict]:
    """Busca todas as transações (usado apenas para upload/ingestão)."""
    sb = get_supabase()
    return _paginate(sb.table("transacoes").select("*").order("Data de Venda"))


def fetch_transacoes_period(start_iso: str, end_iso: str) -> list[dict]:
    """Busca transações de um período específico (ambos inclusive).

    start_iso / end_iso: strings ISO 8601, ex: '2026-04-01T00:00:00'
    """
    sb = get_supabase()
    q = (sb.table("transacoes")
           .select("*")
           .gte("Data de Venda", start_iso)
           .lte("Data de Venda", end_iso)
           .order("Data de Venda"))
    return _paginate(q)


def fetch_emails_before(end_iso: str) -> set:
    """Retorna conjunto de e-mails de clientes que compraram ANTES de end_iso.

    Usado para distinguir clientes novos de renovações.
    Busca apenas coluna Email — muito mais leve.
    """
    sb = get_supabase()
    q = (sb.table("transacoes")
           .select("Email")
           .lt("Data de Venda", end_iso)
           .in_("Status", ["Completo", "Aprovado"]))
    rows = _paginate(q)
    return {r["Email"].lower().strip() for r in rows if r.get("Email")}

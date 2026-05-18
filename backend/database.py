from supabase import create_client, Client
import pandas as pd
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
    Nota: coluna com espaço precisa de aspas duplas no PostgREST.
    """
    sb = get_supabase()
    col = '"Data de Venda"'
    q = (sb.table("transacoes")
           .select("*")
           .gte(col, start_iso)
           .lte(col, end_iso)
           .order(col))
    return _paginate(q)


def fetch_assinantes() -> list[dict]:
    """Busca todas as linhas de assinantes (Código do assinante preenchido).

    Exclui produtos Experience (ingressos de evento — não são assinaturas recorrentes).
    Inclui Email para permitir correspondência com matrículas email-based (cohort por turma).
    """
    sb = get_supabase()
    cols = ",".join([
        '"Código do assinante"',
        '"Email"',
        '"Nome do Produto"',
        '"Recorrência"',
        '"Status"',
        '"Data de Venda"',
        '"Preço Total"',
        '"Preço Total Convertido"',
        '"Moeda de recebimento"',
        '"Taxa de Câmbio Real"',
        '"Taxa de Câmbio do valor recebido"',
        '"Faturamento líquido"',
        '"Valor que você recebeu convertido"',
    ])
    q = (sb.table("transacoes")
           .select(cols)
           .not_.is_('"Código do assinante"', 'null')
           .not_.ilike('"Nome do Produto"', '%experience%')
           .order('"Data de Venda"'))
    return _paginate(q)


def fetch_approved_since(year: int = 2023) -> list[dict]:
    """Busca todas as transações aprovadas a partir de um ano, com colunas de receita e data.

    Usado para análise histórica de faturamento mensal e projeções de turma.
    """
    sb = get_supabase()
    cols = ",".join([
        '"Data de Venda"', '"Nome do Produto"', '"Status"', '"Recorrência"',
        '"Faturamento líquido"', '"Preço Total Convertido"', '"Preço Total"',
        '"Moeda de recebimento"', '"Taxa de Câmbio Real"',
        '"Taxa de Câmbio do valor recebido"', '"Valor que você recebeu convertido"',
    ])
    start = f"{year}-01-01T00:00:00"
    end   = pd.Timestamp.now().strftime('%Y-%m-%dT%H:%M:%S')
    q = (sb.table("transacoes")
           .select(cols)
           .in_("Status", ["Completo", "Aprovado"])
           .gte('"Data de Venda"', start)
           .lte('"Data de Venda"', end)
           .order('"Data de Venda"'))   # ORDER BY obrigatório para paginação determinística
    return _paginate(q)


def fetch_raiz_enrollments() -> list[dict]:
    """Busca matrículas do curso base 'A Raiz da Solução' (Recorrência 1 ou NaN).

    Filtro de produto: apenas o curso principal — exclui Experience, PRO, Gravação.
    Inclui 'A Raiz da Solução 2.0' para compatibilidade com possível renome na T9+.

    Recorrência = 1   → primeiro pagamento de um ciclo de assinatura (nova entrada ou renovação)
    Recorrência nula  → compra avulsa / parcelada sem código de assinante

    Usado para: taxa de renovação, cohort por turma, cross-turma matrix.
    """
    sb   = get_supabase()
    cols = '"Email","Nome do Produto","Recorrência","Status","Data de Venda","Preço Total Convertido","Faturamento líquido","Moeda de recebimento","Taxa de Câmbio Real","Taxa de Câmbio do valor recebido","Valor que você recebeu convertido"'
    BASE_PRODUTOS = ['A Raiz da Solução', 'A Raiz da Solução 2.0']

    # Recorrência == 1: primeiro pagamento de cada ciclo (nova entrada ou renovação)
    q1 = (sb.table("transacoes")
            .select(cols)
            .in_('"Nome do Produto"', BASE_PRODUTOS)
            .eq('"Recorrência"', 1)
            .in_("Status", ["Completo", "Aprovado"])
            .order('"Data de Venda"'))

    # Recorrência nula: compras avulsas (parceladas sem assinatura formal)
    q2 = (sb.table("transacoes")
            .select(cols)
            .in_('"Nome do Produto"', BASE_PRODUTOS)
            .is_('"Recorrência"', 'null')
            .in_("Status", ["Completo", "Aprovado"])
            .order('"Data de Venda"'))

    rows = _paginate(q1) + _paginate(q2)
    return rows


def fetch_all_approved_by_email(year: int = 2022) -> list[dict]:
    """Todas as transações aprovadas desde year, com Email. Exclui Mentoria R100."""
    sb = get_supabase()
    cols = ",".join([
        '"Email"', '"Nome"', '"Nome do Produto"', '"Recorrência"', '"Status"',
        '"Data de Venda"', '"Faturamento líquido"', '"Preço Total Convertido"',
        '"Preço Total"', '"Moeda de recebimento"', '"Taxa de Câmbio Real"',
        '"Taxa de Câmbio do valor recebido"', '"Valor que você recebeu convertido"',
    ])
    start = f"{year}-01-01T00:00:00"
    end = pd.Timestamp.now().strftime('%Y-%m-%dT%H:%M:%S')
    q = (sb.table("transacoes")
           .select(cols)
           .in_("Status", ["Completo", "Aprovado"])
           .not_.ilike('"Nome do Produto"', '%mentoria%')
           .gte('"Data de Venda"', start)
           .lte('"Data de Venda"', end)
           .order('"Data de Venda"'))
    return _paginate(q)


def fetch_emails_before(end_iso: str) -> set:
    """Retorna conjunto de e-mails de clientes que compraram ANTES de end_iso.

    Usado para distinguir clientes novos de renovações.
    Busca apenas coluna Email — muito mais leve.
    """
    sb = get_supabase()
    col = '"Data de Venda"'
    q = (sb.table("transacoes")
           .select("Email")
           .lt(col, end_iso)
           .in_("Status", ["Completo", "Aprovado"]))
    rows = _paginate(q)
    return {r["Email"].lower().strip() for r in rows if r.get("Email")}

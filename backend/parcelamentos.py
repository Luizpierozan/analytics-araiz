"""
Módulo de Parcelamentos (Assinantes) — A Raiz da Solução

Analisa a retenção mensal dos assinantes (Recorrência 1 → 12):
- Curva de retenção por turma: % que ainda paga em cada posição do ciclo
- Projeção de receita dos próximos 6 meses (assinantes ativos × meses restantes)
- Tabela de assinantes ativos com próximo vencimento estimado

Base: fetch_assinantes() — linhas com Código do assinante preenchido.
Excluído: Mentoria R100 (dados parciais fora da plataforma).
Turma: determinada pela data da Recorrência=1 do assinante.
"""

import pandas as pd
import time
from dateutil.relativedelta import relativedelta
from database import fetch_assinantes
from analise_avancada import compute_net_revenue

CUTOFF_ATIVO_DIAS = 60   # pagou nos últimos N dias → ativo
MIN_COHORT        = 5    # mínimo de assinantes para exibir linha de retenção
MAX_REC           = 12   # ciclo completo de assinatura

_TURMA_JANELAS = [
    (1, '2021-08-01', '2021-09-30'),
    (2, '2022-05-01', '2022-09-06'),
    (3, '2023-04-01', '2023-06-19'),
    (4, '2023-10-01', '2023-12-09'),
    (5, '2023-12-10', '2024-07-24'),
    (6, '2024-09-01', '2025-04-04'),
    (7, '2025-04-05', '2025-08-21'),
    (8, '2025-09-20', '2026-02-01'),
    (9, '2026-02-02', '2026-12-31'),
]

_cache: dict = {}
_CACHE_TTL = 600


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assign_turma(dt: pd.Timestamp) -> str:
    if pd.isna(dt):
        return 'Outros'
    for tid, s, e in _TURMA_JANELAS:
        if pd.Timestamp(s) <= dt <= pd.Timestamp(e):
            return f'T{tid:02d}'
    return 'Outros'


def _to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Excluir Mentoria
    df = df[~df['Nome do Produto'].astype(str).str.lower().str.contains('mentoria')].copy()
    df['data']    = pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True).dt.tz_localize(None)
    df['rec']     = pd.to_numeric(df['Recorrência'], errors='coerce')
    df['fat_liq'] = df.apply(compute_net_revenue, axis=1)
    df['cod']     = df['Código do assinante'].astype(str)
    return df


def _build_cohorts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Retorna DataFrame enriquecido com turma e data_rec1 por assinante.
    Turma = baseada na data da Recorrência=1 de cada assinante.
    """
    if df.empty:
        return df

    rec1 = (df[df['rec'] == 1][['cod', 'data']]
            .sort_values('data')
            .drop_duplicates('cod', keep='first')
            .rename(columns={'data': 'data_rec1'}))
    rec1['turma'] = rec1['data_rec1'].apply(_assign_turma)

    return df.merge(rec1[['cod', 'turma', 'data_rec1']], on='cod', how='left').fillna(
        {'turma': 'Outros'}
    )


# ── Retenção ──────────────────────────────────────────────────────────────────

def compute_retention(df: pd.DataFrame) -> dict:
    """
    Curva de retenção por turma.

    Para cada turma e posição p (1–12):
      elegíveis  = assinantes cujo data_rec1 + p meses <= hoje
                   (têm tempo suficiente para ter chegado à posição p)
      pagos_em_p = elegíveis com Completo/Aprovado na Recorrência == p
      retenção   = pagos_em_p / elegíveis × 100

    Atrasado que depois regularizou → conta como pago (verificado via
    ANY Completo/Aprovado na posição, independente de Atrasados anteriores).
    """
    if df.empty:
        return {'geral': {}, 'por_turma': {}, 'turmas': []}

    hoje = pd.Timestamp.now()
    OK   = {'Completo', 'Aprovado'}

    # Mapa de quem PAGOU cada posição: {(cod, rec): True}
    df_pago = df[df['Status'].isin(OK)][['cod', 'rec']].drop_duplicates()
    pago_set = set(zip(df_pago['cod'], df_pago['rec'].astype(int)))

    # Cohort map: cod → (turma, data_rec1)
    cohort_cols = df[['cod', 'turma', 'data_rec1']].drop_duplicates('cod').dropna(subset=['data_rec1'])
    cohort_map  = cohort_cols.set_index('cod')[['turma', 'data_rec1']].to_dict('index')

    turmas_disponiveis = sorted(
        t for t in cohort_cols['turma'].unique() if t != 'Outros'
    )

    def _curve(cods_subset):
        curve = {}
        for pos in range(1, MAX_REC + 1):
            cutoff = hoje - relativedelta(months=int(pos))
            elegíveis = [c for c in cods_subset if cohort_map[c]['data_rec1'] <= cutoff]
            if len(elegíveis) < MIN_COHORT:
                curve[pos] = None
                continue
            pagos = sum(1 for c in elegíveis if (c, pos) in pago_set)
            curve[pos] = round(pagos / len(elegíveis) * 100, 1)
        return curve

    # Global
    todos_cods = [c for c in cohort_map if cohort_map[c]['turma'] != 'Outros']
    ret_geral  = {
        'contratos': len(todos_cods),
        'curva':     _curve(todos_cods),
    }

    # Por turma
    ret_turmas = {}
    for turma in turmas_disponiveis:
        cods_t = [c for c in cohort_map if cohort_map[c]['turma'] == turma]
        if len(cods_t) < MIN_COHORT:
            continue
        ret_turmas[turma] = {
            'contratos': len(cods_t),
            'curva':     _curve(cods_t),
        }

    return {
        'geral':     ret_geral,
        'por_turma': ret_turmas,
        'turmas':    [t for t in turmas_disponiveis if t in ret_turmas],
    }


# ── Projeção 6 meses ─────────────────────────────────────────────────────────

def compute_projection(df: pd.DataFrame, months_ahead: int = 6) -> dict:
    """
    Projeta receita mensal dos próximos months_ahead meses.

    Assinante ativo = último pagamento (Completo/Aprovado) nos últimos
    CUTOFF_ATIVO_DIAS dias E ainda não completou o ciclo (rec < MAX_REC).
    Valor projetado = média das parcelas pagas daquele assinante.
    """
    if df.empty:
        return {'labels': [], 'valores': [], 'total': 0}

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    monthly = {m: 0.0 for m in range(1, months_ahead + 1)}

    for cod, grp in df.groupby('cod'):
        pagas = grp[grp['Status'].isin(OK)].sort_values('rec')
        if pagas.empty:
            continue
        if pagas['data'].max() < cutoff:
            continue   # inativo
        current = int(pagas['rec'].max())
        if current >= MAX_REC:
            continue   # ciclo completo
        avg_val = float(pagas['fat_liq'].mean())
        if avg_val <= 0:
            continue
        for m in range(1, months_ahead + 1):
            if current + m <= MAX_REC:
                monthly[m] += avg_val

    labels = [(hoje + relativedelta(months=m)).strftime('%b/%Y') for m in range(1, months_ahead + 1)]
    valores = [round(monthly[m], 2) for m in range(1, months_ahead + 1)]

    return {'labels': labels, 'valores': valores, 'total': round(sum(valores), 2)}


# ── Tabela de ativos ──────────────────────────────────────────────────────────

def compute_active_table(df: pd.DataFrame) -> list[dict]:
    """Lista de assinantes ativos com dados para a tabela."""
    if df.empty:
        return []

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    result = []

    for cod, grp in df.groupby('cod'):
        pagas = grp[grp['Status'].isin(OK)].sort_values('rec')
        if pagas.empty:
            continue
        ultimo = pagas['data'].max()
        if ultimo < cutoff:
            continue
        current = int(pagas['rec'].max())
        if current >= MAX_REC:
            continue

        turma    = str(grp['turma'].iloc[0]) if 'turma' in grp.columns else '—'
        nome_raw = str(grp.get('Nome', pd.Series([''])).iloc[0]) if 'Nome' in grp.columns else ''
        email    = str(grp['Email'].iloc[0]) if 'Email' in grp.columns else ''
        nome     = ' '.join(w.capitalize() for w in nome_raw.split()) if nome_raw else email
        produto  = str(pagas['Nome do Produto'].iloc[-1])
        restantes = MAX_REC - current

        result.append({
            'nome':          nome,
            'email':         email,
            'produto':       produto,
            'turma':         turma,
            'rec_atual':     current,
            'restantes':     restantes,
            'proxima':       (ultimo + pd.Timedelta(days=30)).strftime('%d/%m/%Y'),
        })

    result.sort(key=lambda x: x['restantes'], reverse=True)
    return result[:200]


# ── Cards ─────────────────────────────────────────────────────────────────────

def _compute_cards(df: pd.DataFrame, projection: dict) -> dict:
    if df.empty:
        return {'assinantes_ativos': 0, 'vencimentos_30d': 0, 'receita_6m': projection['total']}

    hoje     = pd.Timestamp.now()
    cutoff   = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    limite30 = hoje + pd.Timedelta(days=30)
    OK       = {'Completo', 'Aprovado'}
    ativos = venc30 = 0

    for cod, grp in df.groupby('cod'):
        pagas = grp[grp['Status'].isin(OK)]
        if pagas.empty:
            continue
        ultimo = pagas['data'].max()
        if ultimo < cutoff:
            continue
        current = int(pagas['rec'].max())
        if current >= MAX_REC:
            continue
        ativos += 1
        if ultimo + pd.Timedelta(days=30) <= limite30:
            venc30 += 1

    return {
        'assinantes_ativos': ativos,
        'vencimentos_30d':   venc30,
        'receita_6m':        projection['total'],
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def invalidate_cache():
    _cache.clear()


def get_parcelamentos() -> dict:
    cached = _cache.get('result')
    if cached and (time.time() - _cache.get('ts', 0)) < _CACHE_TTL:
        return cached

    rows = fetch_assinantes()
    if not rows:
        return {'sucesso': False, 'erro': 'Sem dados de assinantes'}

    df_raw = _to_df(rows)
    df     = _build_cohorts(df_raw)

    if df.empty:
        return {'sucesso': False, 'erro': 'Sem dados após filtros'}

    retention  = compute_retention(df)
    projection = compute_projection(df)
    table      = compute_active_table(df)
    cards      = _compute_cards(df, projection)

    result = {
        'sucesso':    True,
        'cards':      cards,
        'retention':  retention,
        'projection': projection,
        'tabela':     table,
    }
    _cache['result'] = result
    _cache['ts']     = time.time()
    return result

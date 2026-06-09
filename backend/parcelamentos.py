"""
Módulo de Parcelamentos — A Raiz da Solução

Analisa contratos parcelados (compras em Nx — sem assinatura):
- Retenção por tipo de plano (4x, 6x, 10x, 12x) — global e por turma
- Projeção de receita das parcelas nos próximos 6 meses
- Tabela de contratos ativos

Definição de contrato: (email_norm, produto, mes_inicio)
mes_inicio = mês calculado como: data_parcela - (parcela_num - 1) * 30 dias
Isso funciona para qualquer parcela da sequência, sem precisar ver a Parcela 1 explicitamente.

Nota Hotmart: a Parcela 1 é sempre marcada como "Apenas à vista" no campo
"Tipo pagamento oferta". Só as parcelas >= 2 têm "Parcelamento padrão".
Por isso o filtro de contratos válidos é: max(parcela_num) > 1 por grupo.
"""

import pandas as pd
import time
from dateutil.relativedelta import relativedelta
from database import fetch_parcelamentos
from analise_avancada import compute_net_revenue

MIN_CONTRATOS_PLANO = 5   # mínimo de contratos para exibir linha de retenção
CUTOFF_ATIVO_DIAS   = 60  # considera ativo se pagou nos últimos N dias
PLANOS_PADRAO       = [2, 3, 4, 6, 10, 12]  # tamanhos de plano reconhecidos


def _infer_plano_final(max_obs: int) -> int:
    """Infere o tamanho real do plano com base no máximo observado.

    Para contratos em andamento (max_obs < plano real), usa o menor plano padrão
    >= max_obs. Ex: max_obs=9 → 10; max_obs=11 → 12.
    """
    for p in PLANOS_PADRAO:
        if max_obs <= p:
            return p
    return max_obs

# Janelas de turma (mês início, mês fim — formato YYYY-MM, ambos inclusive)
_TURMA_JANELAS_MESES = [
    (1,  '2021-08', '2021-09'),
    (2,  '2022-05', '2022-09'),
    (3,  '2023-04', '2023-06'),
    (4,  '2023-10', '2023-12'),
    (5,  '2023-12', '2024-07'),
    (6,  '2024-09', '2025-04'),
    (7,  '2025-04', '2025-08'),
    (8,  '2025-09', '2026-02'),
    (9,  '2026-02', '2026-12'),
]

_cache: dict = {}
_CACHE_TTL = 600


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assign_turma(mes: str) -> str:
    """Atribui turma pelo mês de início do contrato (YYYY-MM)."""
    for tid, start, end in _TURMA_JANELAS_MESES:
        if start <= mes <= end:
            return f'T{tid:02d}'
    return 'Outros'


def _to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['Data de Venda'] = (pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True)
                           .dt.tz_localize(None))
    df['parcela_num']   = pd.to_numeric(df['Número da Parcela'], errors='coerce')
    df['fat_liq']       = df.apply(compute_net_revenue, axis=1)
    df['email_norm']    = df['Email'].astype(str).str.lower().str.strip()
    return df


def _build_contracts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa parcelas em contratos de forma vetorizada.

    Para cada linha com parcela_num >= 1, calcula o mês de início esperado:
        mes_inicio = strftime('%Y-%m', data - (parcela_num - 1) * 30 dias)

    Isso cria uma chave consistente para todas as parcelas do mesmo contrato.
    Contratos com plano == 1 (só Parcela 1 observada) são excluídos — provavelmente
    compras à vista cujas parcelas subsequentes nunca apareceram.
    """
    if df.empty:
        return pd.DataFrame()

    df = df[df['parcela_num'].notna() & (df['parcela_num'] >= 1)].copy()
    if df.empty:
        return pd.DataFrame()

    df['parcela_int'] = df['parcela_num'].astype(int)

    # Mês de início estimado (vetorizado)
    offset_days = (df['parcela_int'] - 1) * 30
    df['mes_inicio'] = (df['Data de Venda'] - pd.to_timedelta(offset_days, unit='D')).dt.strftime('%Y-%m')
    df['produto']    = df['Nome do Produto'].astype(str)
    df['nome']       = df['Nome'].fillna('').astype(str) if 'Nome' in df.columns else ''
    df['status']     = df['Status'].astype(str)
    df['data']       = df['Data de Venda']
    df['parcela_num'] = df['parcela_int']

    C_KEYS = ['email_norm', 'produto', 'mes_inicio']

    # Manter só contratos com plano > 1 (verdadeiro parcelamento)
    max_p = df.groupby(C_KEYS)['parcela_num'].transform('max')
    df = df[max_p > 1].copy()
    if df.empty:
        return pd.DataFrame()

    # Atribuição de turma
    df['turma'] = df['mes_inicio'].map(_assign_turma)

    cols = ['email_norm', 'produto', 'nome', 'mes_inicio', 'turma',
            'parcela_num', 'status', 'data', 'fat_liq']
    return df[cols].reset_index(drop=True)


# ── Retenção ─────────────────────────────────────────────────────────────────

def _retention_for(df: pd.DataFrame) -> dict:
    """
    Calcula curvas de retenção por tamanho de plano para um subset de contratos.

    Usa merge vetorizado — evita loops Python por contrato.
    """
    if df.empty:
        return {}

    hoje     = pd.Timestamp.now()
    OK       = {'Completo', 'Aprovado'}
    C_KEYS   = ['email_norm', 'produto', 'mes_inicio']

    plano_df = (df.groupby(C_KEYS)['parcela_num'].max()
                .reset_index().rename(columns={'parcela_num': 'plano'}))

    df_full  = df.merge(plano_df, on=C_KEYS)
    df_pago  = df_full[df_full['status'].isin(OK)].copy()

    result = {}
    for plano_size in sorted(plano_df['plano'].unique()):
        if plano_size <= 1:
            continue

        contratos_plano = plano_df[plano_df['plano'] == plano_size][C_KEYS].copy()
        if len(contratos_plano) < MIN_CONTRATOS_PLANO:
            continue

        curve = {}
        for pos in range(1, int(plano_size) + 1):
            min_inicio = (hoje - relativedelta(months=int(pos))).strftime('%Y-%m')
            elegíveis = contratos_plano[contratos_plano['mes_inicio'] <= min_inicio]
            if len(elegíveis) < MIN_CONTRATOS_PLANO:
                curve[pos] = None
                continue

            # Quantos elegíveis pagaram a posição `pos`?
            pagos_na_pos = (df_pago[df_pago['parcela_num'] == pos]
                            .merge(elegíveis, on=C_KEYS, how='inner')[C_KEYS]
                            .drop_duplicates())
            curve[pos] = round(len(pagos_na_pos) / len(elegíveis) * 100, 1)

        result[f'{plano_size}x'] = {
            'plano':     int(plano_size),
            'contratos': int(len(contratos_plano)),
            'curva':     {int(k): v for k, v in curve.items()},
        }

    return result


def compute_retention(contracts_df: pd.DataFrame) -> dict:
    """Retenção global + por turma."""
    if contracts_df.empty:
        return {'geral': {}, 'por_turma': {}, 'turmas': []}

    turmas = sorted(t for t in contracts_df['turma'].unique() if t != 'Outros')

    ret_geral    = _retention_for(contracts_df)
    ret_turmas   = {t: _retention_for(contracts_df[contracts_df['turma'] == t]) for t in turmas}

    return {
        'geral':     ret_geral,
        'por_turma': ret_turmas,
        'turmas':    turmas,
    }


# ── Projeção 6 meses ─────────────────────────────────────────────────────────

def compute_projection(contracts_df: pd.DataFrame, months_ahead: int = 6) -> dict:
    """Projeta receita mensal das parcelas futuras (até months_ahead meses).

    Plano inferido com _infer_plano_final: contratos em andamento (ex: parcela 9 de 12)
    têm seu plano corrigido para o padrão mais próximo.
    """
    if contracts_df.empty:
        return {'labels': [], 'valores': [], 'total': 0}

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    C_KEYS = ['email_norm', 'produto', 'mes_inicio']

    plano_df = (contracts_df.groupby(C_KEYS)['parcela_num'].max()
                .reset_index().rename(columns={'parcela_num': 'max_obs'}))
    plano_df = plano_df[plano_df['max_obs'] > 1]
    if plano_df.empty:
        return {'labels': [], 'valores': [], 'total': 0}

    df = contracts_df.merge(plano_df, on=C_KEYS)
    monthly = {m: 0.0 for m in range(1, months_ahead + 1)}

    for (email, produto, mes_inicio), grp in df.groupby(C_KEYS):
        max_obs = int(grp['max_obs'].iloc[0])
        pagas   = grp[grp['status'].isin(OK)].sort_values('parcela_num')
        if pagas.empty:
            continue
        if pagas['data'].max() < cutoff:
            continue
        current = int(pagas['parcela_num'].max())
        plano   = _infer_plano_final(max(max_obs, current))
        if current >= plano:
            continue
        avg_val = float(pagas['fat_liq'].mean())
        if avg_val <= 0:
            continue
        for m in range(1, months_ahead + 1):
            if current + m <= plano:
                monthly[m] += avg_val

    labels = [(hoje + relativedelta(months=m)).strftime('%b/%Y') for m in range(1, months_ahead + 1)]
    valores = [round(monthly[m], 2) for m in range(1, months_ahead + 1)]

    return {'labels': labels, 'valores': valores, 'total': round(sum(valores), 2)}


# ── Tabela de ativos ──────────────────────────────────────────────────────────

def compute_active_table(contracts_df: pd.DataFrame) -> list[dict]:
    if contracts_df.empty:
        return []

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    C_KEYS = ['email_norm', 'produto', 'mes_inicio']

    plano_df = (contracts_df.groupby(C_KEYS)['parcela_num'].max()
                .reset_index().rename(columns={'parcela_num': 'max_obs'}))
    plano_df = plano_df[plano_df['max_obs'] > 1]
    df = contracts_df.merge(plano_df, on=C_KEYS)

    result = []
    for (email, produto, mes_inicio), grp in df.groupby(C_KEYS):
        max_obs = int(grp['max_obs'].iloc[0])
        pagas   = grp[grp['status'].isin(OK)].sort_values('parcela_num')
        if pagas.empty:
            continue
        ultimo  = pagas['data'].max()
        if ultimo < cutoff:
            continue
        current = int(pagas['parcela_num'].max())
        plano   = _infer_plano_final(max(max_obs, current))
        if current >= plano:
            continue

        turma    = str(grp['turma'].iloc[0]) if 'turma' in grp.columns else '—'
        nome_raw = grp['nome'].iloc[-1]
        nome     = ' '.join(w.capitalize() for w in nome_raw.split()) if nome_raw else email
        restantes = plano - current

        result.append({
            'nome':          nome,
            'email':         email,
            'produto':       produto,
            'turma':         turma,
            'plano':         f'{plano}x',
            'parcela_atual': current,
            'restantes':     restantes,
            'proxima':       (ultimo + pd.Timedelta(days=30)).strftime('%d/%m/%Y'),
        })

    result.sort(key=lambda x: x['restantes'], reverse=True)
    return result[:200]


# ── Cards ─────────────────────────────────────────────────────────────────────

def _compute_cards(contracts_df: pd.DataFrame, projection: dict) -> dict:
    if contracts_df.empty:
        return {'contratos_ativos': 0, 'parcelas_30d': 0, 'receita_6m': projection['total']}

    hoje      = pd.Timestamp.now()
    cutoff    = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    limite30  = hoje + pd.Timedelta(days=30)
    OK        = {'Completo', 'Aprovado'}
    C_KEYS    = ['email_norm', 'produto', 'mes_inicio']

    plano_df = (contracts_df.groupby(C_KEYS)['parcela_num'].max()
                .reset_index().rename(columns={'parcela_num': 'max_obs'}))
    plano_df = plano_df[plano_df['max_obs'] > 1]
    df = contracts_df.merge(plano_df, on=C_KEYS)

    ativos = parcelas_30d = 0
    for _, grp in df.groupby(C_KEYS):
        max_obs = int(grp['max_obs'].iloc[0])
        pagas   = grp[grp['status'].isin(OK)]
        if pagas.empty:
            continue
        ultimo  = pagas['data'].max()
        if ultimo < cutoff:
            continue
        current = int(pagas['parcela_num'].max())
        plano   = _infer_plano_final(max(max_obs, current))
        if current >= plano:
            continue
        ativos += 1
        if ultimo + pd.Timedelta(days=30) <= limite30:
            parcelas_30d += 1

    return {'contratos_ativos': ativos, 'parcelas_30d': parcelas_30d,
            'receita_6m': projection['total']}


# ── Entry point ───────────────────────────────────────────────────────────────

def invalidate_cache():
    _cache.clear()


def get_parcelamentos() -> dict:
    cached = _cache.get('result')
    if cached and (time.time() - _cache.get('ts', 0)) < _CACHE_TTL:
        return cached

    rows = fetch_parcelamentos()
    if not rows:
        return {'sucesso': False, 'erro': 'Sem dados de parcelamentos'}

    df_raw    = _to_df(rows)
    contracts = _build_contracts(df_raw)

    if contracts.empty:
        return {'sucesso': False, 'erro': 'Nenhum contrato parcelado identificado'}

    retention  = compute_retention(contracts)
    projection = compute_projection(contracts)
    table      = compute_active_table(contracts)
    cards      = _compute_cards(contracts, projection)

    result = {
        'sucesso':    True,
        'cards':      cards,
        'retention':  retention,   # {geral, por_turma, turmas}
        'projection': projection,
        'tabela':     table,
    }
    _cache['result'] = result
    _cache['ts']     = time.time()
    return result

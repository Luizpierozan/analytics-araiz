"""
Módulo de Parcelamentos — A Raiz da Solução

Analisa contratos parcelados (compras em Nx — sem assinatura):
- Retenção por tipo de plano (4x, 6x, 10x, 12x)
- Projeção de receita das parcelas nos próximos 6 meses
- Tabela de contratos ativos

Definição de contrato: (email_norm, Nome do Produto, mes_inicio)
mes_inicio = mês/ano da Parcela 1 daquele contrato.
Contratos separados: mesmo email que comprou em T5 e T7 gera 2 contratos distintos.
"""

import pandas as pd
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from database import fetch_parcelamentos
from analise_avancada import compute_net_revenue

MIN_CONTRATOS_PLANO = 5   # mínimo de contratos para exibir linha de retenção
CUTOFF_ATIVO_DIAS   = 60  # considera ativo se pagou nos últimos N dias
MESES_PT = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']

_cache: dict = {}
_CACHE_TTL = 600


def _to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['Data de Venda'] = pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True).dt.tz_localize(None)
    df['parcela_num']   = pd.to_numeric(df['Número da Parcela'], errors='coerce')
    df['fat_liq']       = df.apply(compute_net_revenue, axis=1)
    df['email_norm']    = df['Email'].astype(str).str.lower().str.strip()
    return df


def _build_contracts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa parcelas em contratos: (email_norm, produto, mes_inicio).

    Lógica:
    1. Para cada (email, produto), encontra todas as linhas com parcela_num >= 1.
    2. Identifica "inícios" de contratos: linhas com parcela_num == 1.
    3. Cada Parcela N é atribuída ao início cujo data_p1_esperada (= data_row - (N-1)*30 dias)
       está mais próxima de uma Parcela 1 real daquele (email, produto).
    4. Contratos com apenas Parcela 1 observada e sem parcelas subsequentes são excluídos
       (não confirmados como parcelamento — podem ser compras à vista).
    """
    if df.empty:
        return pd.DataFrame()

    records = []

    for (email, produto), grp in df.groupby(['email_norm', 'Nome do Produto']):
        grp = grp.sort_values('Data de Venda')

        # Inícios de contratos = linhas com parcela_num == 1
        starts = grp[grp['parcela_num'] == 1].copy()
        if starts.empty:
            continue

        start_dates = starts['Data de Venda'].tolist()

        # Para cada linha, encontra o início mais próximo
        for _, row in grp.iterrows():
            n = row['parcela_num']
            if pd.isna(n) or n < 1:
                continue
            n = int(n)
            # Data esperada da parcela 1 para essa linha
            expected_p1 = row['Data de Venda'] - pd.Timedelta(days=int((n - 1) * 30))
            # Início mais próximo
            diffs = [abs((expected_p1 - sd).total_seconds()) for sd in start_dates]
            best_idx = diffs.index(min(diffs))
            # Aceita apenas se a diferença for < 45 dias (tolerância)
            if min(diffs) > 45 * 86400:
                continue
            mes_inicio = start_dates[best_idx].strftime('%Y-%m')
            records.append({
                'email_norm':  email,
                'produto':     produto,
                'nome':        str(row.get('Nome', '')),
                'mes_inicio':  mes_inicio,
                'parcela_num': n,
                'status':      str(row.get('Status', '')),
                'data':        row['Data de Venda'],
                'fat_liq':     float(row['fat_liq']),
            })

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def _infer_plano(group_parcelas: pd.DataFrame) -> int:
    """Infere o tamanho do plano pelo máximo de parcela_num observado."""
    return int(group_parcelas['parcela_num'].max())


def compute_retention(contracts_df: pd.DataFrame) -> dict:
    """
    Curva de retenção por tipo de plano.

    Para cada plano N e posição p:
      eligible = contratos do plano N cujo mes_inicio tem pelo menos p meses atrás
      paid_p   = eligible com Status Completo/Aprovado na posição p
      retention[p] = paid_p / eligible   (se eligible >= MIN_CONTRATOS_PLANO)

    Atrasado resolvido = conta como pago (verificado em _build_contracts:
    se existem parcelas Completo e Atrasado para a mesma posição,
    a Completo prevalece).
    """
    if contracts_df.empty:
        return {}

    hoje = pd.Timestamp.now()
    OK   = {'Completo', 'Aprovado'}

    # Para cada contrato, calcular plano (max parcela observada)
    contrato_keys = ['email_norm', 'produto', 'mes_inicio']
    plano_map = (contracts_df
                 .groupby(contrato_keys)['parcela_num']
                 .max()
                 .reset_index()
                 .rename(columns={'parcela_num': 'plano'}))

    # Excluir contratos com plano == 1 (compra à vista confirmada)
    plano_map = plano_map[plano_map['plano'] > 1]
    if plano_map.empty:
        return {}

    df = contracts_df.merge(plano_map, on=contrato_keys, how='inner')

    # Posição paga por contrato (paga = Completo/Aprovado)
    df_pago = df[df['status'].isin(OK)]

    result = {}
    for plano_size, grp_plano in plano_map.groupby('plano'):
        contrato_ids = set(map(tuple, grp_plano[contrato_keys].values.tolist()))
        if len(contrato_ids) < MIN_CONTRATOS_PLANO:
            continue

        curve = {}
        for pos in range(1, plano_size + 1):
            # Elegíveis: contratos velhos o suficiente para ter chegado na posição pos
            min_inicio = (hoje - relativedelta(months=pos)).strftime('%Y-%m')
            elegíveis = {
                c for c in contrato_ids
                if c[2] <= min_inicio   # mes_inicio <= min_inicio
            }
            if len(elegíveis) < MIN_CONTRATOS_PLANO:
                curve[pos] = None
                continue

            pagos = set()
            for c in elegíveis:
                mask = (
                    (df_pago['email_norm'] == c[0]) &
                    (df_pago['produto']    == c[1]) &
                    (df_pago['mes_inicio'] == c[2]) &
                    (df_pago['parcela_num'] == pos)
                )
                if df_pago[mask].shape[0] > 0:
                    pagos.add(c)

            curve[pos] = round(len(pagos) / len(elegíveis) * 100, 1)

        result[f'{plano_size}x'] = {
            'plano':     plano_size,
            'contratos': len(contrato_ids),
            'curva':     curve,   # {1: 100.0, 2: 87.3, ...}
        }

    return result


def compute_projection(contracts_df: pd.DataFrame, months_ahead: int = 6) -> dict:
    """
    Projeta receita das parcelas futuras, mês a mês (até months_ahead meses).

    Só considera contratos ativos: último pagamento nos últimos CUTOFF_ATIVO_DIAS dias
    e parcelas restantes > 0.
    Valor das parcelas futuras = média das parcelas já pagas daquele contrato.
    """
    if contracts_df.empty:
        return {'labels': [], 'valores': [], 'total': 0}

    hoje    = pd.Timestamp.now()
    cutoff  = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK      = {'Completo', 'Aprovado'}
    contrato_keys = ['email_norm', 'produto', 'mes_inicio']

    plano_map = (contracts_df
                 .groupby(contrato_keys)['parcela_num']
                 .max()
                 .reset_index()
                 .rename(columns={'parcela_num': 'plano'}))
    plano_map = plano_map[plano_map['plano'] > 1]
    if plano_map.empty:
        return {'labels': [], 'valores': [], 'total': 0}

    df = contracts_df.merge(plano_map, on=contrato_keys, how='inner')

    monthly = {m: 0.0 for m in range(1, months_ahead + 1)}

    for (email, produto, mes_inicio), grp in df.groupby(contrato_keys):
        plano = int(grp['plano'].iloc[0])

        pagas = grp[grp['status'].isin(OK)].sort_values('parcela_num')
        if pagas.empty:
            continue

        # Ativo: último pagamento recente
        ultimo_pag = pagas['data'].max()
        if ultimo_pag < cutoff:
            continue

        current_parcela = int(pagas['parcela_num'].max())
        if current_parcela >= plano:
            continue  # contrato completo

        avg_valor = float(pagas['fat_liq'].mean()) if not pagas.empty else 0.0
        if avg_valor <= 0:
            continue

        for m in range(1, months_ahead + 1):
            fut = current_parcela + m
            if fut <= plano:
                monthly[m] += avg_valor

    labels = [
        (hoje + relativedelta(months=m)).strftime('%b/%Y')
        for m in range(1, months_ahead + 1)
    ]
    valores = [round(monthly[m], 2) for m in range(1, months_ahead + 1)]

    return {
        'labels': labels,
        'valores': valores,
        'total':   round(sum(valores), 2),
    }


def compute_active_table(contracts_df: pd.DataFrame) -> list[dict]:
    """Retorna lista de contratos ativos com dados para exibição na tabela."""
    if contracts_df.empty:
        return []

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    contrato_keys = ['email_norm', 'produto', 'mes_inicio']

    plano_map = (contracts_df
                 .groupby(contrato_keys)['parcela_num']
                 .max()
                 .reset_index()
                 .rename(columns={'parcela_num': 'plano'}))
    plano_map = plano_map[plano_map['plano'] > 1]
    df = contracts_df.merge(plano_map, on=contrato_keys, how='inner')

    result = []
    for (email, produto, mes_inicio), grp in df.groupby(contrato_keys):
        plano = int(grp['plano'].iloc[0])
        pagas = grp[grp['status'].isin(OK)].sort_values('parcela_num')
        if pagas.empty:
            continue
        ultimo_pag = pagas['data'].max()
        if ultimo_pag < cutoff:
            continue
        current = int(pagas['parcela_num'].max())
        if current >= plano:
            continue

        nome = str(grp['nome'].iloc[-1]) if 'nome' in grp.columns else ''
        restantes = plano - current
        proxima_data = ultimo_pag + pd.Timedelta(days=30)

        result.append({
            'nome':          ' '.join(w.capitalize() for w in nome.split()) if nome else email,
            'email':         email,
            'produto':       produto,
            'mes_inicio':    mes_inicio,
            'plano':         f'{plano}x',
            'parcela_atual': current,
            'restantes':     restantes,
            'proxima':       proxima_data.strftime('%d/%m/%Y'),
        })

    result.sort(key=lambda x: x['restantes'], reverse=True)
    return result[:200]


def _compute_cards(contracts_df: pd.DataFrame, projection: dict) -> dict:
    """Calcula os 3 cards de resumo."""
    if contracts_df.empty:
        return {'contratos_ativos': 0, 'parcelas_30d': 0, 'receita_6m': projection['total']}

    hoje   = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=CUTOFF_ATIVO_DIAS)
    OK     = {'Completo', 'Aprovado'}
    contrato_keys = ['email_norm', 'produto', 'mes_inicio']

    plano_map = (contracts_df.groupby(contrato_keys)['parcela_num'].max()
                 .reset_index().rename(columns={'parcela_num': 'plano'}))
    plano_map = plano_map[plano_map['plano'] > 1]
    df = contracts_df.merge(plano_map, on=contrato_keys, how='inner')

    ativos = 0
    parcelas_30d = 0
    limite_30d = hoje + pd.Timedelta(days=30)

    for _, grp in df.groupby(contrato_keys):
        plano = int(grp['plano'].iloc[0])
        pagas = grp[grp['status'].isin(OK)]
        if pagas.empty:
            continue
        ultimo_pag = pagas['data'].max()
        if ultimo_pag < cutoff:
            continue
        current = int(pagas['parcela_num'].max())
        if current >= plano:
            continue
        ativos += 1
        prox = ultimo_pag + pd.Timedelta(days=30)
        if prox <= limite_30d:
            parcelas_30d += 1

    return {
        'contratos_ativos': ativos,
        'parcelas_30d':     parcelas_30d,
        'receita_6m':       projection['total'],
    }


def invalidate_cache():
    _cache.clear()


def get_parcelamentos() -> dict:
    cached = _cache.get('result')
    if cached and (time.time() - _cache.get('ts', 0)) < _CACHE_TTL:
        return cached

    rows = fetch_parcelamentos()
    if not rows:
        return {'sucesso': False, 'erro': 'Sem dados de parcelamentos'}

    df_raw     = _to_df(rows)
    contracts  = _build_contracts(df_raw)

    retention   = compute_retention(contracts)
    projection  = compute_projection(contracts)
    table       = compute_active_table(contracts)
    cards       = _compute_cards(contracts, projection)

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

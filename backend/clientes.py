"""
clientes.py — Inteligência de Alunos

Análises de clientes para a aba "Inteligência de Alunos":
  - RFM adaptado ao ciclo anual de A Raiz da Solução
  - Matriz cross-turma (origem → destino)
  - Top 100 renovadores (por nº de turmas participadas)
  - Ranking LTV top 100 (todos os produtos, exceto Mentoria R100)
  - Top 50 participantes de Experience
"""

import time
import pandas as pd
from database import fetch_raiz_enrollments, fetch_assinantes, fetch_all_approved_by_email
from analise_avancada import compute_net_revenue
from projecoes import TURMA_COHORTS, _TURMA_JANELAS

# ── Constantes ────────────────────────────────────────────────────────────────

NOMES_RAIZ_CURSO = {'A Raiz da Solução', 'A Raiz da Solução 2.0'}
LIMIAR_ATIVO_MESES = 16   # gap ≤ 16 meses entre turmas = renovação ativa

_cache: dict = {}
_CACHE_TTL = 600


def invalidate_cache():
    _cache.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['Data de Venda'] = pd.to_datetime(
        df['Data de Venda'], errors='coerce', utc=True
    ).dt.tz_localize(None)
    return df


def _add_fat_liq(df: pd.DataFrame) -> pd.DataFrame:
    df['fat_liq'] = df.apply(compute_net_revenue, axis=1)
    return df


def _add_email_norm(df: pd.DataFrame) -> pd.DataFrame:
    df['email_norm'] = df['Email'].str.lower().str.strip()
    return df


def _assign_turma(purchase_date: pd.Timestamp) -> int | None:
    """Atribui turma pela janela de vendas oficial (comparação por data, sem hora)."""
    if pd.isna(purchase_date):
        return None
    d = purchase_date.date()
    for tid, inicio, fim in _TURMA_JANELAS:
        if pd.Timestamp(inicio).date() <= d <= pd.Timestamp(fim).date():
            return tid
    return None


# ── Turma entries por email ───────────────────────────────────────────────────

def build_turma_entries(df_raiz: pd.DataFrame) -> dict:
    """
    Retorna dict: email_norm → lista de {turma_id, date, fat_liq} ordenada por data.

    Fonte: df_raiz (Rec=1 ou NULL, somente NOMES_RAIZ_CURSO).
    Por email, guarda apenas a entrada mais antiga em cada turma
    (caso haja compras duplicadas na mesma turma).
    """
    if df_raiz.empty:
        return {}

    df = df_raiz[df_raiz['Nome do Produto'].isin(NOMES_RAIZ_CURSO)].copy()
    if df.empty:
        return {}

    result = {}
    for email, grp in df.groupby('email_norm'):
        seen: dict[int, dict] = {}
        for _, row in grp.sort_values('Data de Venda').iterrows():
            t_id = _assign_turma(row['Data de Venda'])
            if t_id is None:
                continue
            if t_id not in seen:
                seen[t_id] = {
                    'turma_id': t_id,
                    'date':     row['Data de Venda'],
                    'fat_liq':  float(row.get('fat_liq', 0)),
                }
        if seen:
            result[email] = sorted(seen.values(), key=lambda x: x['date'])
    return result


# ── Cross-turma matrix ────────────────────────────────────────────────────────

def compute_cross_turma(turma_entries: dict) -> dict:
    """
    Para cada turma de destino, conta de onde vieram os alunos.

    Estrutura retornada:
    {
      "destinations": ["T3","T4","T5","T6","T7","T8","T9"],
      "rows": [
        {"origem": "Novo",  "cells": {"T3": 180, "T4": 95, ...}},
        {"origem": "T3",    "cells": {"T4": {"total":30,"ativa":28,"inativa":2}, ...}},
        ...
      ],
      "totals": {"T3": 220, "T4": 140, ...}
    }

    Renovação ativa  = gap entre a turma anterior e esta ≤ LIMIAR_ATIVO_MESES
    Renovação inativa = gap > LIMIAR_ATIVO_MESES
    """
    turma_ids = [t['id'] for t in TURMA_COHORTS]
    dest_ids  = turma_ids       # todas as turmas são destino (T1 inclusive)

    # Acumula transições: dest_id → origin_label → {total, ativa, inativa}
    data: dict[int, dict] = {d: {} for d in dest_ids}

    for email, entries in turma_entries.items():
        if not entries:
            continue

        # Primeira turma → "Novo"
        first_id = entries[0]['turma_id']
        if first_id in data:
            data[first_id].setdefault('Novo', 0)
            data[first_id]['Novo'] += 1

        # Transições subsequentes
        for i in range(1, len(entries)):
            prev = entries[i - 1]
            curr = entries[i]
            dest = curr['turma_id']
            orig_label = f"T{prev['turma_id']}"
            if dest not in data:
                continue
            gap_months = (curr['date'] - prev['date']).days / 30.44
            is_active  = gap_months <= LIMIAR_ATIVO_MESES

            if orig_label not in data[dest]:
                data[dest][orig_label] = {'total': 0, 'ativa': 0, 'inativa': 0}
            data[dest][orig_label]['total']  += 1
            if is_active:
                data[dest][orig_label]['ativa']  += 1
            else:
                data[dest][orig_label]['inativa'] += 1

    # Origens possíveis (linhas): "Novo" + todos os T{id} como origem
    all_origins = ['Novo'] + [f'T{t}' for t in turma_ids]

    destinations = [f'T{d}' for d in dest_ids if any(data[d].values())]
    dest_id_map  = {f'T{d}': d for d in dest_ids}

    rows = []
    for orig in all_origins:
        cells = {}
        has_data = False
        for dest_label in destinations:
            d_id = dest_id_map[dest_label]
            val = data[d_id].get(orig)
            if val is not None:
                cells[dest_label] = val if isinstance(val, dict) else val
                has_data = True
            else:
                cells[dest_label] = None
        if has_data:
            rows.append({'origem': orig, 'cells': cells})

    totals = {
        dest_label: sum(
            (v['total'] if isinstance(v, dict) else v)
            for v in data[dest_id_map[dest_label]].values()
            if v is not None
        )
        for dest_label in destinations
    }

    return {
        'destinations': destinations,
        'rows': rows,
        'totals': totals,
    }


# ── RFM ───────────────────────────────────────────────────────────────────────

def compute_rfm(turma_entries: dict, df_todos: pd.DataFrame) -> dict:
    """
    RFM adaptado ao ciclo anual (apenas A Raiz da Solução / 2.0):
      R = meses desde a última entrada em turma (Rec=1 ou NULL mais recente)
      F = número de turmas distintas em que entrou
      M = total pago no curso (todas as recorrências, via df_todos filtrado)

    Retorna:
      segments: {segmento: count}
      records:  top 200 por M (para tabela)
      total:    total de alunos únicos
    """
    hoje = pd.Timestamp.now()

    # M por email (soma de todas as parcelas do curso)
    monetary: dict[str, float] = {}
    if not df_todos.empty:
        df_r = df_todos[
            df_todos['Nome do Produto'].isin(NOMES_RAIZ_CURSO) &
            df_todos['Status'].isin(['Completo', 'Aprovado'])
        ].copy()
        df_r = _add_email_norm(df_r)
        df_r = _add_fat_liq(df_r)
        for em, grp in df_r.groupby('email_norm'):
            monetary[em] = float(grp['fat_liq'].sum())

    records = []
    for email, entries in turma_entries.items():
        if not entries:
            continue
        r_months = (hoje - entries[-1]['date']).days / 30.44
        f_count  = len(entries)
        m_value  = monetary.get(email, sum(e['fat_liq'] for e in entries))
        records.append({
            'email':    email,
            'r_months': round(r_months, 1),
            'f_count':  f_count,
            'm_value':  round(m_value, 2),
            'turmas':   [f"T{e['turma_id']}" for e in entries],
            'last_turma': f"T{entries[-1]['turma_id']}",
        })

    if not records:
        return {'records': [], 'segments': {}, 'total': 0}

    df_rfm = pd.DataFrame(records)

    def _score(series: pd.Series, ascending: bool) -> pd.Series:
        """Quintil 1-5 tolerante a poucos valores únicos."""
        try:
            labels = [1,2,3,4,5] if ascending else [5,4,3,2,1]
            return pd.qcut(series, q=5, labels=labels, duplicates='drop').astype(int)
        except Exception:
            return pd.Series(3, index=series.index)

    df_rfm['r_score'] = _score(df_rfm['r_months'], ascending=False)  # menor R = mais recente = score alto
    df_rfm['f_score'] = _score(df_rfm['f_count'],  ascending=True)
    df_rfm['m_score'] = _score(df_rfm['m_value'],  ascending=True)

    def _segment(row) -> str:
        r, f, m = row['r_score'], row['f_score'], row['m_score']
        if r >= 4 and f >= 4 and m >= 4:  return 'Campeão'
        if r >= 3 and f >= 3:              return 'Leal Ativo'
        if r >= 4 and f == 1:              return 'Novo Promissor'
        if r <= 2 and f >= 3:              return 'Em Risco'
        if r <= 2 and f >= 2:              return 'Adormecido'
        if r == 1 and f == 1:              return 'Perdido'
        return 'Regular'

    df_rfm['segmento'] = df_rfm.apply(_segment, axis=1)

    top = df_rfm.nlargest(200, 'm_value').copy()
    top['r_score'] = top['r_score'].astype(int)
    top['f_score'] = top['f_score'].astype(int)
    top['m_score'] = top['m_score'].astype(int)

    return {
        'records':  top.to_dict('records'),
        'segments': df_rfm['segmento'].value_counts().to_dict(),
        'total':    len(df_rfm),
    }


# ── Top Renovadores ───────────────────────────────────────────────────────────

def compute_top_renovadores(turma_entries: dict, top_n: int = 100) -> list:
    """Top N alunos por nº de turmas participadas (somente quem renovou ≥1x)."""
    records = []
    for email, entries in turma_entries.items():
        if len(entries) < 2:
            continue
        records.append({
            'email':      email,
            'n_turmas':   len(entries),
            'turmas':     [f"T{e['turma_id']}" for e in entries],
            'total_pago': round(sum(e['fat_liq'] for e in entries), 2),
            'primeiro':   entries[0]['date'].strftime('%m/%Y'),
            'ultimo':     entries[-1]['date'].strftime('%m/%Y'),
            'meses_ativo': round(
                (entries[-1]['date'] - entries[0]['date']).days / 30.44, 1
            ),
        })
    records.sort(key=lambda x: (-x['n_turmas'], -x['total_pago']))
    return records[:top_n]


# ── LTV Ranking ───────────────────────────────────────────────────────────────

def compute_ltv_ranking(df_todos: pd.DataFrame, top_n: int = 100) -> list:
    """Top N clientes por LTV total (todos os produtos exceto Mentoria)."""
    if df_todos.empty:
        return []
    df = df_todos[df_todos['Status'].isin(['Completo', 'Aprovado'])].copy()
    df = _add_email_norm(df)
    df = _add_fat_liq(df)

    records = []
    for email, grp in df.groupby('email_norm'):
        total = float(grp['fat_liq'].sum())
        if total <= 0:
            continue
        products = grp['Nome do Produto'].dropna().unique().tolist()
        records.append({
            'email':          email,
            'ltv':            round(total, 2),
            'n_produtos':     len(products),
            'produtos':       products[:6],
            'primeira_compra': grp['Data de Venda'].min().strftime('%m/%Y'),
            'ultima_compra':  grp['Data de Venda'].max().strftime('%m/%Y'),
        })
    records.sort(key=lambda x: -x['ltv'])
    return records[:top_n]


# ── LTV Médio por Produto ─────────────────────────────────────────────────────

def compute_ltv_by_product(df_todos: pd.DataFrame) -> dict:
    """
    LTV médio por produto + opção "Todos".
    Para cada produto (exceto Mentoria, já filtrada), retorna:
      ltv_medio   — média do gasto total de cada cliente único nesse produto
      n_clientes  — quantos emails distintos compraram esse produto

    "Todos" usa o gasto total por cliente somando todos os produtos.

    Normaliza "A Raiz da Solução 2.0" → "A Raiz da Solução" para manter
    a série histórica unificada no dropdown.
    """
    if df_todos.empty:
        return {}

    df = df_todos[df_todos['Status'].isin(['Completo', 'Aprovado'])].copy()
    df = _add_email_norm(df)
    df = _add_fat_liq(df)

    # Normaliza nomes: A Raiz 2.0 → A Raiz; qualquer Experience → "Experience";
    # exclui Sinal (evento pontual, não faz sentido em LTV médio por produto)
    def _norm_produto(nome: str) -> str | None:
        if not isinstance(nome, str):
            return None
        if 'sinal' in nome.lower():
            return None          # será descartado
        if 'experience' in nome.lower():
            return 'Experience'  # unifica todas as edições
        if 'A Raiz da Solução 2.0' == nome:
            return 'A Raiz da Solução'
        return nome

    df['produto_norm'] = df['Nome do Produto'].apply(_norm_produto)
    df = df[df['produto_norm'].notna()]   # remove Sinal e nomes nulos

    result = {}

    # "Todos" — gasto total de cada cliente (soma todos os produtos restantes)
    total_por_email = df.groupby('email_norm')['fat_liq'].sum()
    result['Todos'] = {
        'ltv_medio':  round(float(total_por_email.mean()), 2),
        'n_clientes': int(len(total_por_email)),
    }

    # Por produto normalizado
    for prod, grp in df.groupby('produto_norm'):
        by_email = grp.groupby('email_norm')['fat_liq'].sum()
        result[prod] = {
            'ltv_medio':  round(float(by_email.mean()), 2),
            'n_clientes': int(len(by_email)),
        }

    return result


# ── Experience Top ────────────────────────────────────────────────────────────

def compute_experience_top(df_todos: pd.DataFrame, top_n: int = 50) -> list:
    """Top participantes de eventos Experience (comprou em 2+ edições)."""
    if df_todos.empty:
        return []
    df_exp = df_todos[
        df_todos['Nome do Produto'].str.contains('experience', case=False, na=False) &
        df_todos['Status'].isin(['Completo', 'Aprovado'])
    ].copy()
    if df_exp.empty:
        return []
    df_exp = _add_email_norm(df_exp)
    df_exp = _add_fat_liq(df_exp)

    records = []
    for email, grp in df_exp.groupby('email_norm'):
        eventos = grp['Nome do Produto'].dropna().unique().tolist()
        records.append({
            'email':      email,
            'n_eventos':  len(eventos),
            'eventos':    eventos,
            'total_pago': round(float(grp['fat_liq'].sum()), 2),
        })
    records.sort(key=lambda x: (-x['n_eventos'], -x['total_pago']))
    return [r for r in records if r['n_eventos'] >= 2][:top_n]


# ── Endpoint principal ────────────────────────────────────────────────────────

def get_clientes() -> dict:
    cached = _cache.get('result')
    if cached and (time.time() - _cache.get('ts', 0)) < _CACHE_TTL:
        return cached

    from concurrent.futures import ThreadPoolExecutor, as_completed
    tasks = {
        'raiz':  fetch_raiz_enrollments,
        'assin': fetch_assinantes,
        'todos': fetch_all_approved_by_email,
    }
    fetched: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures):
            fetched[futures[fut]] = fut.result()

    # ── DataFrames ──────────────────────────────────────────────────────────
    rows_raiz  = fetched.get('raiz',  [])
    rows_todos = fetched.get('todos', [])

    df_raiz = pd.DataFrame(rows_raiz) if rows_raiz else pd.DataFrame()
    if not df_raiz.empty:
        df_raiz = _parse_dt(df_raiz)
        df_raiz = _add_fat_liq(df_raiz)
        df_raiz = _add_email_norm(df_raiz)

    df_todos = pd.DataFrame(rows_todos) if rows_todos else pd.DataFrame()
    if not df_todos.empty:
        df_todos = _parse_dt(df_todos)

    # ── Análises ─────────────────────────────────────────────────────────────
    turma_entries   = build_turma_entries(df_raiz)
    cross_turma     = compute_cross_turma(turma_entries)
    rfm             = compute_rfm(turma_entries, df_todos)
    top_renovadores = compute_top_renovadores(turma_entries, top_n=50)
    ltv_ranking     = compute_ltv_ranking(df_todos, top_n=50)
    experience_top  = compute_experience_top(df_todos, top_n=50)
    ltv_by_product  = compute_ltv_by_product(df_todos)

    # ── KPIs ─────────────────────────────────────────────────────────────────
    total_unicos    = len(turma_entries)
    multi_turma     = sum(1 for e in turma_entries.values() if len(e) >= 2)
    tres_mais_turmas = sum(1 for e in turma_entries.values() if len(e) >= 3)
    ltv_medio    = (
        round(sum(r['ltv'] for r in ltv_ranking[:50]) / len(ltv_ranking[:50]), 2)
        if ltv_ranking else 0
    )

    result = {
        'sucesso':         True,
        'kpis': {
            'total_alunos_unicos': total_unicos,
            'multi_turma':         multi_turma,
            'taxa_retorno':        round(multi_turma / total_unicos, 4) if total_unicos else 0,
            'ltv_medio_top100':    ltv_medio,
            'tres_mais_turmas':    tres_mais_turmas,
        },
        'cross_turma':     cross_turma,
        'rfm':             rfm,
        'top_renovadores': top_renovadores,
        'ltv_ranking':     ltv_ranking,
        'experience_top':  experience_top,
        'ltv_by_product':  ltv_by_product,
    }
    _cache['result'] = result
    _cache['ts'] = time.time()
    return result

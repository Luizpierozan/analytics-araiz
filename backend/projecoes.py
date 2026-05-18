"""
Módulo de Projeções — A Raiz da Solução

Modelo de negócio:
- Produto principal: parcelamento em 12x (tratado como assinatura pela Hotmart)
- 1 ano de acesso por ciclo; renovação inicia novo ciclo de 12 meses
- Turmas ímpares: abertura em maio | Turmas pares: abertura em setembro
- Experience: ingresso para evento presencial — excluído das projeções de recorrência
"""

import pandas as pd
import numpy as np
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from database import fetch_assinantes, fetch_raiz_enrollments, fetch_approved_since
from analise_avancada import compute_net_revenue, clean_currency

LIMIAR_RENOVACAO = 4200.0  # preço líquido abaixo disso = renovação

# Calendário oficial de turmas (id, YYYY-MM, tipo)
# Turma 2 excluída do cálculo de ratio/sazonalidade — 2022 atípico
TURMA_SCHEDULE = [
    {"id": 3, "abertura": "2023-05", "tipo": "impar"},
    {"id": 4, "abertura": "2023-10", "tipo": "par"},
    {"id": 5, "abertura": "2024-05", "tipo": "impar"},
    {"id": 6, "abertura": "2024-09", "tipo": "par"},
    {"id": 7, "abertura": "2025-04", "tipo": "impar"},
    {"id": 8, "abertura": "2025-09", "tipo": "par"},
    {"id": 9, "abertura": "2026-05", "tipo": "impar"},   # a projetar
]

# Para análise de cohort por turma, T1 é a turma inaugural (Yampi, Ago/2021)
TURMA_COHORTS = [
    {"id": 1, "abertura": "2021-08"},
    {"id": 2, "abertura": "2022-06"},
    {"id": 3, "abertura": "2023-05"},
    {"id": 4, "abertura": "2023-10"},
    {"id": 5, "abertura": "2024-05"},
    {"id": 6, "abertura": "2024-09"},
    {"id": 7, "abertura": "2025-04"},
    {"id": 8, "abertura": "2025-09"},
    {"id": 9, "abertura": "2026-05"},
]

# Produto outlier: Mentoria R100 — parte das vendas ocorre fora da plataforma,
# distorcendo survival, sazonalidade e projeções.
MENTORIA_KEYWORDS = ['mentoria']

MESES_PT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

# Cache simples com TTL de 10 minutos (dados mudam só em upload)
_cache: dict = {}
_CACHE_TTL = 600  # segundos


# ── Helpers ──────────────────────────────────────────────────────────────────

def _filter_mentoria(rows: list[dict]) -> list[dict]:
    """Remove produtos outliers (Mentoria R100) — dados parciais na plataforma."""
    return [
        r for r in rows
        if not any(kw in str(r.get('Nome do Produto', '')).lower() for kw in MENTORIA_KEYWORDS)
    ]


def _to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['Data de Venda'] = pd.to_datetime(
        df['Data de Venda'], errors='coerce', utc=True
    ).dt.tz_localize(None)
    df['Recorrência'] = pd.to_numeric(df['Recorrência'], errors='coerce')
    df['fat_liq'] = df.apply(compute_net_revenue, axis=1)
    return df


def _monthly_value(group: pd.DataFrame) -> float:
    """Valor líquido mensal médio de um assinante (últimas 3 recorrências pagas)."""
    pagas = group[group['Status'].isin(['Completo', 'Aprovado'])].sort_values('Recorrência', ascending=False)
    if pagas.empty:
        return 0.0
    return float(pagas.head(3)['fat_liq'].mean())


# ── Curva de Sobrevivência ────────────────────────────────────────────────────

def compute_survival_curve(df: pd.DataFrame) -> dict:
    """
    Calcula a taxa de sobrevivência por posição no ciclo (1-12).

    Para cada posição N, usa apenas cohorts com idade suficiente para
    ter chegado lá (cohort_age >= N meses). Assim o denominador é honesto.

    Retorna: {1: 1.0, 2: 0.87, 3: 0.75, ..., 12: 0.48}
    """
    hoje = pd.Timestamp.now()

    # Cohort de cada assinante = mês da sua Recorrência 1
    rec1 = df[df['Recorrência'] == 1].copy()
    cohort_map = (
        rec1.groupby('Código do assinante')['Data de Venda']
        .min()
        .rename('cohort_date')
    )
    df = df.join(cohort_map, on='Código do assinante')

    survival = {}
    for pos in range(1, 13):
        # Só cohorts que tiveram tempo suficiente para chegar à posição pos
        min_cohort = hoje - relativedelta(months=pos)
        eligible = df[df['cohort_date'] <= min_cohort]['Código do assinante'].unique()

        if len(eligible) == 0:
            survival[pos] = None
            continue

        paid = df[
            df['Código do assinante'].isin(eligible) &
            (df['Recorrência'] == pos) &
            df['Status'].isin(['Completo', 'Aprovado'])
        ]['Código do assinante'].nunique()

        survival[pos] = round(paid / len(eligible), 4)

    return survival


# ── Assinantes Ativos ─────────────────────────────────────────────────────────

def get_active_subscribers(df: pd.DataFrame, cutoff_days: int = 60) -> pd.DataFrame:
    """
    Retorna 1 linha por assinante ativo.

    "Ativo" = teve pagamento Completo/Aprovado nos últimos `cutoff_days` dias
    e ainda não completou o ciclo de 12 meses (ou completou e está no início de um novo).

    Colunas retornadas: Código do assinante, current_rec, monthly_value, cohort_month
    """
    hoje = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=cutoff_days)

    records = []
    for cod, grp in df.groupby('Código do assinante'):
        pagas = grp[grp['Status'].isin(['Completo', 'Aprovado'])].sort_values('Data de Venda')
        if pagas.empty:
            continue

        last_paid = pagas.iloc[-1]
        if last_paid['Data de Venda'] < cutoff:
            continue  # inativo

        current_rec = int(last_paid['Recorrência']) if pd.notna(last_paid['Recorrência']) else 1
        if current_rec > 12:
            current_rec = 12  # segurança

        val = _monthly_value(grp)
        cohort_row = pagas[pagas['Recorrência'] == 1]
        cohort_month = cohort_row['Data de Venda'].min() if not cohort_row.empty else pagas['Data de Venda'].min()

        records.append({
            'codigo':        cod,
            'current_rec':   current_rec,
            'monthly_value': val,
            'cohort_month':  cohort_month.strftime('%Y-%m'),
            'produto':       str(last_paid.get('Nome do Produto', '')),
        })

    return pd.DataFrame(records)


# ── Projeção de Receita ───────────────────────────────────────────────────────

def project_revenue(active: pd.DataFrame, survival: dict, months_ahead: int = 12) -> dict:
    """
    Projeta receita para os próximos `months_ahead` meses em 3 cenários.

    Cenários:
        realistic:    sobrevivência histórica real
        optimistic:   churn 30% menor que o histórico
        conservative: churn 40% maior que o histórico

    Retorna dict com lista de {mes, realista, otimista, conservador}
    """
    if active.empty:
        return {"meses": [], "realista": [], "otimista": [], "conservador": []}

    hoje = pd.Timestamp.now()

    # Pré-computa survival ajustado por cenário
    def adjusted_survival(base: dict, factor: float) -> dict:
        """
        factor < 1 = menos churn (otimista)
        factor > 1 = mais churn (conservador)
        """
        adj = {1: 1.0}
        for pos in range(2, 13):
            prev = adj.get(pos - 1, 1.0)
            base_rate = base.get(pos) or base.get(pos - 1) or 0.5
            # churn mensal nessa posição (histórico)
            prev_base = base.get(pos - 1) or 1.0
            monthly_churn = max(0, 1 - (base_rate / prev_base)) if prev_base > 0 else 0.05
            adj_churn = min(0.99, monthly_churn * factor)
            adj[pos] = round(prev * (1 - adj_churn), 4)
        return adj

    surv_real  = {p: (v or 0) for p, v in survival.items()}
    surv_opt   = adjusted_survival(surv_real, 0.70)
    surv_cons  = adjusted_survival(surv_real, 1.40)

    meses, real_vals, opt_vals, cons_vals = [], [], [], []

    for m in range(1, months_ahead + 1):
        mes_label = (hoje + relativedelta(months=m)).strftime('%m/%Y')
        rev_real = rev_opt = rev_cons = 0.0

        for _, row in active.iterrows():
            rec = int(row['current_rec'])
            val = float(row['monthly_value'])
            fut_rec = rec + m  # posição que estaria no mês M

            if fut_rec > 12:
                continue  # ciclo encerrado

            # Probabilidade de sobrevivência (condicional: dado que está ativo agora)
            base_surv = surv_real.get(rec, 0.01) or 0.01
            p_real = (surv_real.get(fut_rec) or 0) / base_surv
            p_opt  = (surv_opt.get(fut_rec)  or 0) / (surv_opt.get(rec) or 0.01)
            p_cons = (surv_cons.get(fut_rec) or 0) / (surv_cons.get(rec) or 0.01)

            rev_real += val * max(0, p_real)
            rev_opt  += val * max(0, p_opt)
            rev_cons += val * max(0, p_cons)

        meses.append(mes_label)
        real_vals.append(round(rev_real, 2))
        opt_vals.append(round(rev_opt, 2))
        cons_vals.append(round(rev_cons, 2))

    return {
        "meses":       meses,
        "realista":    real_vals,
        "otimista":    opt_vals,
        "conservador": cons_vals,
    }


# ── Análise de Cohorts ────────────────────────────────────────────────────────

def compute_cohorts(df: pd.DataFrame) -> list[dict]:
    """
    Para cada cohort (mês da Recorrência 1), retorna:
    - mes: YYYY-MM
    - total: quantos começaram
    - ativos: quantos ainda têm pagamento recente (últimos 60 dias)
    - retencao_pct: % que chegou ao menos no mês 6
    - receita_total: receita líquida acumulada do cohort
    """
    hoje = pd.Timestamp.now()
    cutoff = hoje - pd.Timedelta(days=60)

    rec1 = df[df['Recorrência'] == 1].copy()
    rec1['cohort_month'] = rec1['Data de Venda'].dt.to_period('M')

    cohort_summary = []
    for month, grp in rec1.groupby('cohort_month'):
        subs = set(grp['Código do assinante'].unique())
        total = len(subs)
        if total == 0:
            continue

        sub_data = df[df['Código do assinante'].isin(subs)]

        # Ativos agora
        ativos = sub_data[
            sub_data['Status'].isin(['Completo', 'Aprovado']) &
            (sub_data['Data de Venda'] >= cutoff)
        ]['Código do assinante'].nunique()

        # Chegou ao mês 6 (retenção de meio ciclo)
        chegou_6 = sub_data[
            (sub_data['Recorrência'] >= 6) &
            sub_data['Status'].isin(['Completo', 'Aprovado'])
        ]['Código do assinante'].nunique()

        retencao_6 = round(chegou_6 / total * 100, 1) if total > 0 else 0

        # Receita total gerada
        receita = float(
            sub_data[sub_data['Status'].isin(['Completo', 'Aprovado'])]['fat_liq'].sum()
        )

        cohort_summary.append({
            "mes":          str(month),
            "total":        total,
            "ativos":       int(ativos),
            "retencao_6":   retencao_6,
            "receita_total": round(receita, 2),
        })

    # Ordena por mês
    cohort_summary.sort(key=lambda x: x['mes'])
    return cohort_summary


# ── Cohort por Turma ─────────────────────────────────────────────────────────

def _assign_turma_cohort(first_date: pd.Timestamp) -> dict | None:
    """
    Opção A: atribui à turma mais recente que abriu ANTES ou NO mesmo mês da 1ª compra.
    TURMA_COHORTS deve estar em ordem cronológica crescente.
    """
    assigned = None
    for t in TURMA_COHORTS:
        t_date = pd.Timestamp(t['abertura'] + '-01')
        if first_date >= t_date:
            assigned = t
        else:
            break
    return assigned


def compute_cohorts_por_turma(df_assin: pd.DataFrame, df_raiz: pd.DataFrame) -> list[dict]:
    """
    Cohort por turma expandido — todos os alunos de A Raiz da Solução.

    "Entraram"   = emails únicos cuja 1ª compra cai na janela da turma (email-based)
    "Assinantes" = subconjunto com código de assinante cujo 1º pag. também cai na turma
    "Ativos"     = assinantes com pagamento nos últimos 60 dias
    "Retenção 6" = % de assinantes que chegou à posição 6 do ciclo
    "Receita"    = parcelas dos assinantes (ciclo completo, seguindo o aluno)
                   + o que está registrado para não-assinantes em df_raiz

    Parcelas seguem o ALUNO, não o calendário — a parcela 8 de um aluno da T5
    que cai em jan/26 é contabilizada em T5, não em T8.
    T9 é excluída: recém aberta, dados insuficientes.
    """
    hoje         = pd.Timestamp.now()
    cutoff_ativo = hoje - pd.Timedelta(days=60)
    ok_status    = ['Completo', 'Aprovado']

    # ── Prepara df_raiz (matrículas email-based, Rec=1 ou NULL) ─────────────
    dfr = df_raiz[df_raiz['Status'].isin(ok_status)].copy() if not df_raiz.empty else pd.DataFrame()
    if not dfr.empty:
        dfr['email_norm'] = dfr['Email'].astype(str).str.lower().str.strip()
        dfr['Data de Venda'] = pd.to_datetime(
            dfr['Data de Venda'], errors='coerce', utc=True
        ).dt.tz_localize(None)
        dfr['fat_liq'] = dfr.apply(compute_net_revenue, axis=1)

    # ── Prepara df_assin (todas as recorrências, tem Email agora) ────────────
    dfa = df_assin.copy()
    if 'Email' in dfa.columns:
        dfa['email_norm'] = dfa['Email'].astype(str).str.lower().str.strip()
    else:
        dfa['email_norm'] = ''

    # ── Email → Turma (pela 1ª compra em df_raiz) ───────────────────────────
    email_turma: dict[str, int] = {}
    if not dfr.empty:
        first_by_email = dfr.groupby('email_norm')['Data de Venda'].min()
        for email, fd in first_by_email.items():
            t = _assign_turma_cohort(fd)
            if t:
                email_turma[email] = t['id']

    # ── Código de assinante → Turma (pelo 1º pag. do código, Rec=1) ─────────
    rec1_assin = dfa[dfa['Recorrência'] == 1]
    code_first = rec1_assin.groupby('Código do assinante')['Data de Venda'].min()
    code_turma: dict[str, int] = {}
    for cod, fd in code_first.items():
        t = _assign_turma_cohort(fd)
        if t:
            code_turma[cod] = t['id']

    # ── Email → conjunto de códigos de assinante ─────────────────────────────
    email_codes: dict[str, set] = {}
    for email, grp in dfa.groupby('email_norm'):
        email_codes[email] = set(grp['Código do assinante'].dropna().unique())

    resultado = []
    for turma in TURMA_COHORTS:
        if turma['id'] == 9:           # T9 recém aberta — dados insuficientes
            continue

        tid = turma['id']

        # Emails que entraram nesta turma
        emails_turma = {e for e, t_id in email_turma.items() if t_id == tid}
        if not emails_turma:
            continue

        # Códigos de assinante desta turma
        codes_turma = {cod for cod, t_id in code_turma.items() if t_id == tid}

        # Assinantes = emails cujos códigos pertencem a esta turma
        emails_assinantes = {
            email for email in emails_turma
            if any(cod in codes_turma for cod in email_codes.get(email, set()))
        }
        n_assinantes = len(emails_assinantes)

        # Dados de todas as transações dos assinantes desta turma
        assin_data = dfa[
            dfa['Código do assinante'].isin(codes_turma) &
            dfa['Status'].isin(ok_status)
        ]

        # Ativos: assinantes com pagamento nos últimos 60 dias
        ativos = int(
            assin_data[assin_data['Data de Venda'] >= cutoff_ativo]
            ['Código do assinante'].nunique()
        )

        # Retenção mês 6 (calculada sobre os CÓDIGOS da turma, não os emails)
        t_open        = pd.Timestamp(turma['abertura'] + '-01')
        meses_desde   = (hoje - t_open).days / 30.0

        if meses_desde >= 6 and codes_turma:
            chegou_6 = int(
                assin_data[assin_data['Recorrência'] >= 6]
                ['Código do assinante'].nunique()
            )
            retencao_6 = round(chegou_6 / len(codes_turma) * 100, 1)
        else:
            retencao_6 = None

        # Receita: assinantes (ciclo completo, seguindo o aluno)
        receita_assin = float(assin_data['fat_liq'].sum())

        # Receita: não-assinantes (o que aparece em df_raiz para eles)
        emails_sem_codigo = emails_turma - emails_assinantes
        receita_non_assin = float(
            dfr[dfr['email_norm'].isin(emails_sem_codigo)]['fat_liq'].sum()
        ) if not dfr.empty and emails_sem_codigo else 0.0

        resultado.append({
            "turma_id":     tid,
            "nome":         f"T{tid}",
            "abertura":     turma['abertura'],
            "total":        len(emails_turma),
            "assinantes":   n_assinantes,
            "ativos":       ativos,
            "retencao_6":   retencao_6,
            "receita_total": round(receita_assin + receita_non_assin, 2),
        })

    return resultado


# ── Taxa de Renovação ────────────────────────────────────────────────────────

def compute_renewal_rate() -> dict:
    """
    Taxa de renovação real — por email + limiar de preço.

    Regra do negócio:
    - Preço líquido > R$4.200 → novo aluno no curso (nova matrícula)
    - Preço líquido ≤ R$4.200 → renovação (cliente retornando)

    Metodologia:
    1. Busca todas as matrículas de 'A Raiz da Solução' (Recorrência 1 ou NaN)
    2. Para cada email, ordena as compras por data
    3. Clientes elegíveis = tiveram primeira matrícula há ≥ 12 meses
    4. Renovaram = tiveram uma segunda compra com preço ≤ R$4.200
       OU qualquer segunda compra com gap de ≥ 10 meses da primeira
    """
    rows = fetch_raiz_enrollments()
    if not rows:
        return {"total_elegivel": 0, "renovaram": 0, "taxa": 0.0, "metodo": "email+preco"}

    df = pd.DataFrame(rows)
    df['Data de Venda'] = pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True).dt.tz_localize(None)
    df['fat_liq'] = df.apply(compute_net_revenue, axis=1)
    df['Email'] = df['Email'].astype(str).str.lower().str.strip()
    df = df.sort_values('Data de Venda')

    hoje = pd.Timestamp.now()
    elegivel_cutoff = hoje - relativedelta(months=12)

    total_elegivel = 0
    renovaram = 0

    for email, grp in df.groupby('Email'):
        compras = grp.sort_values('Data de Venda')
        primeira = compras.iloc[0]['Data de Venda']

        # Elegível = fez a primeira matrícula há pelo menos 12 meses
        if primeira > elegivel_cutoff:
            continue
        total_elegivel += 1

        # Tem segunda compra?
        if len(compras) < 2:
            continue

        # Para cada compra após a primeira, verifica se é renovação
        for _, compra in compras.iloc[1:].iterrows():
            gap_meses = (compra['Data de Venda'] - primeira).days / 30.0
            preco = compra['fat_liq']

            # Renovação confirmada se:
            # (a) preço ≤ R$4.200 — critério principal do negócio
            # (b) OU gap ≥ 10 meses da matrícula anterior (novo ciclo por qualquer preço)
            if preco <= LIMIAR_RENOVACAO or gap_meses >= 10:
                renovaram += 1
                break  # conta 1 renovação por email

    taxa = round(renovaram / total_elegivel * 100, 1) if total_elegivel > 0 else 0.0
    return {
        "total_elegivel": int(total_elegivel),
        "renovaram":      int(renovaram),
        "taxa":           taxa,
        "metodo":         "email+preco",
    }


# ── Análise e Projeção de Turmas ─────────────────────────────────────────────

def _build_monthly_series(rows: list[dict]) -> pd.Series:
    """Converte linhas aprovadas em série mensal de receita líquida."""
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    df['Data de Venda'] = pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True).dt.tz_localize(None)
    df['fat_liq'] = df.apply(compute_net_revenue, axis=1)
    df['mes'] = df['Data de Venda'].dt.to_period('M')
    return df.groupby('mes')['fat_liq'].sum()


def _get_month_rev(series: pd.Series, year: int, month: int) -> float:
    try:
        return float(series.get(pd.Period(f"{year}-{month:02d}", 'M'), 0))
    except Exception:
        return 0.0


def compute_seasonality(monthly: pd.Series, anos: list = None) -> dict:
    """
    Peso histórico de cada mês calendário como % do faturamento anual.

    Usa 2023–2025 por padrão:
      - 2022 excluído: ano atípico (poucos dados, modelo de negócio diferente)
      - 2026 excluído: ano incompleto (distorceria a média)

    Retorna: {1: 0.04, 2: 0.03, 5: 0.28, ...}  — valores somam ~1.0
    """
    if anos is None:
        anos = [2023, 2024, 2025]
    pesos: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for ano in anos:
        total_ano = sum(_get_month_rev(monthly, ano, m) for m in range(1, 13))
        if total_ano <= 0:
            continue
        for mes in range(1, 13):
            pesos[mes].append(_get_month_rev(monthly, ano, mes) / total_ano)
    return {m: round(sum(v) / len(v), 4) if v else 0.0 for m, v in pesos.items()}


def project_2026_by_seasonality(monthly: pd.Series, seasonality: dict) -> dict:
    """
    Projeta o faturamento completo de 2026 com base na sazonalidade histórica.

    Método:
      1. Soma dos dados reais de Jan até o mês anterior ao atual (ex: Jan–Abr)
      2. Esses meses representam X% do ano historicamente
      3. Total projetado = realizado / X%
      4. Meses restantes distribuídos proporcionalmente à sua % histórica

    ⚠️ Projeção estatística — não inclui turmas, crescimento ou choques externos.
    Use como referência de tendência, não como previsão operacional.
    """
    mes_atual = pd.Timestamp.now().month
    reais = {m: round(_get_month_rev(monthly, 2026, m), 2) for m in range(1, 13)}

    # Meses completos antes do mês atual
    meses_realizados = list(range(1, mes_atual))

    total_realizado = sum(reais[m] for m in meses_realizados)
    peso_realizado  = sum(seasonality.get(m, 0) for m in meses_realizados)

    total_projetado_ano = (
        round(total_realizado / peso_realizado, 2) if peso_realizado > 0 else 0.0
    )

    realizado_vals, projetado_vals, pct_vals = [], [], []
    for m in range(1, 13):
        pct = seasonality.get(m, 0)
        pct_vals.append(round(pct * 100, 2))
        if m in meses_realizados:
            realizado_vals.append(reais[m])
            projetado_vals.append(None)
        else:
            realizado_vals.append(None)
            projetado_vals.append(
                round(total_projetado_ano * pct, 2) if total_projetado_ano else None
            )

    return {
        "labels":              MESES_PT,
        "realizado":           realizado_vals,
        "projetado":           projetado_vals,
        "pct_historica":       pct_vals,         # % de cada mês (×100)
        "total_realizado":     round(total_realizado, 2),
        "total_projetado_ano": total_projetado_ano,
        "peso_realizado_pct":  round(peso_realizado * 100, 1),
        "meses_realizados":    len(meses_realizados),
    }


def compute_historico_turmas(monthly: pd.Series) -> list[dict]:
    """
    Para cada turma histórica (3-8), retorna:
    - receita do mês de abertura
    - receita dos 4 meses anteriores (pré-turma)
    - ratio pré→abertura
    """
    resultado = []
    hoje = pd.Timestamp.now().to_period('M')

    for t in TURMA_SCHEDULE:
        ano, mes = int(t["abertura"][:4]), int(t["abertura"][5:7])
        period   = pd.Period(f"{ano}-{mes:02d}", 'M')

        # Turma futura — sem dados ainda
        if period >= hoje:
            resultado.append({
                "id":           t["id"],
                "abertura":     t["abertura"],
                "tipo":         t["tipo"],
                "receita":      None,
                "pre_4m":       None,
                "ratio":        None,
                "status":       "futuro",
            })
            continue

        receita = _get_month_rev(monthly, ano, mes)
        pre_4m  = sum(_get_month_rev(monthly, ano, mes - d) for d in range(1, 5))

        resultado.append({
            "id":       t["id"],
            "abertura": t["abertura"],
            "tipo":     t["tipo"],
            "receita":  round(receita, 2),
            "pre_4m":   round(pre_4m, 2),
            "ratio":    round(receita / pre_4m, 4) if pre_4m > 0 else None,
            "status":   "realizado",
        })

    return resultado


def projetar_turmas(monthly: pd.Series, historico: list[dict]) -> dict:
    """
    Projeta T9 (mai/2026) com 3 cenários usando séries históricas.

    Método principal: ratio pré-4m → abertura das turmas comparáveis.

    Para turmas ímpares (T3, T5, T7):
        ratio = receita_abertura / soma_4_meses_anteriores
        T9_proj = ratio × soma_jan-abr/2026

    Para turmas pares (futura set/2026):
        Usa proporção histórica par/ímpar para estimar a partir de T9.

    Retorna: garantido (assinaturas), projetado (turmas), breakdown mensal.
    """
    realizados = [t for t in historico if t["status"] == "realizado"]

    # ── Ratios históricos por tipo ───────────────────────────────────────────
    impar_ratios = [t["ratio"] for t in realizados if t["tipo"] == "impar" and t["ratio"]]
    par_ratios   = [t["ratio"] for t in realizados if t["tipo"] == "par"   and t["ratio"]]

    # Pesos: anos mais recentes valem mais (0.5 / 0.3 / 0.2)
    def _weighted_stats(ratios):
        if not ratios:
            return None, None, None
        n = len(ratios)
        weights = [0.5 ** (n - 1 - i) for i in range(n)]
        total_w = sum(weights)
        weighted = sum(r * w for r, w in zip(ratios, weights)) / total_w
        return min(ratios), weighted, max(ratios)

    impar_min, impar_real, impar_max = _weighted_stats(impar_ratios)
    par_min,   par_real,   par_max   = _weighted_stats(par_ratios)

    # ── Pre-4m de T9 (Jan-Abr 2026 — dados reais) ───────────────────────────
    pre_t9 = sum(_get_month_rev(monthly, 2026, m) for m in range(1, 5))

    def _proj(ratio):
        return round(ratio * pre_t9, 2) if ratio else 0.0

    t9 = {
        "abertura":     "2026-05",
        "tipo":         "impar",
        "pre_4m_real":  round(pre_t9, 2),
        "conservador":  _proj(impar_min),
        "realista":     _proj(impar_real),
        "otimista":     _proj(impar_max),
    }

    # ── T10 (set/2026): usa proporção par/ímpar histórica ───────────────────
    par_impar_ratios = []
    for i, t in enumerate(TURMA_SCHEDULE):
        if t["tipo"] == "par" and i > 0:
            turma_impar_ant = next(
                (x for x in reversed(TURMA_SCHEDULE[:i]) if x["tipo"] == "impar"), None
            )
            if turma_impar_ant:
                h_par  = next((x for x in realizados if x["id"] == t["id"]), None)
                h_imp  = next((x for x in realizados if x["id"] == turma_impar_ant["id"]), None)
                if h_par and h_imp and h_imp["receita"] and h_par["receita"] and h_imp["receita"] > 0:
                    par_impar_ratios.append(h_par["receita"] / h_imp["receita"])

    pi_min  = min(par_impar_ratios)  if par_impar_ratios else 0.4
    pi_real = sum(par_impar_ratios) / len(par_impar_ratios) if par_impar_ratios else 0.55
    pi_max  = max(par_impar_ratios)  if par_impar_ratios else 0.7

    t10 = {
        "abertura":     "2026-09",
        "tipo":         "par",
        "nota":         "Estimada como % de T9 (histórico par/ímpar)",
        "conservador":  round(t9["conservador"] * pi_min,  2),
        "realista":     round(t9["realista"]    * pi_real, 2),
        "otimista":     round(t9["otimista"]    * pi_max,  2),
        "ratio_par_impar": {
            "min":  round(pi_min,  3),
            "real": round(pi_real, 3),
            "max":  round(pi_max,  3),
        }
    }

    return {
        "t9":              t9,
        "t10":             t10,
        "impar_ratios":    {
            "historico": [round(r, 3) for r in impar_ratios],
            "min":  round(impar_min,  3) if impar_min  else None,
            "real": round(impar_real, 3) if impar_real else None,
            "max":  round(impar_max,  3) if impar_max  else None,
        },
        "par_ratios": {
            "historico": [round(r, 3) for r in par_ratios],
            "min":  round(par_min,  3) if par_min  else None,
            "real": round(par_real, 3) if par_real else None,
            "max":  round(par_max,  3) if par_max  else None,
        },
    }


def compute_termometro(
    receita_contratada: float,
    receita_turmas: float,
    meta_anual: float = 12_000_000.0,
) -> dict:
    """
    Termômetro de meta anual.

    Componentes:
      1. Receita contratada:  assinantes ativos → próximos 12 meses (certo)
      2. Receita de turmas:   novas turmas projetadas → próximos 12 meses (esperado)
      3. Gap:                 quanto ainda falta para a meta

    Status:
      🔴 < 40%   Crítico
      🟡 40-70%  Atenção
      🟢 > 70%   Saudável
    """
    total_projetado = receita_contratada + receita_turmas
    pct = min(100.0, round(total_projetado / meta_anual * 100, 1)) if meta_anual > 0 else 0

    if pct >= 70:
        status = 'saudavel'
        cor = '#10B981'
    elif pct >= 40:
        status = 'atencao'
        cor = '#F59E0B'
    else:
        status = 'critico'
        cor = '#EF4444'

    return {
        "meta_anual":          meta_anual,
        "receita_contratada":  round(receita_contratada, 2),
        "receita_turmas":      round(receita_turmas, 2),
        "total_projetado":     round(total_projetado, 2),
        "percentual":          pct,
        "gap":                 round(max(0, meta_anual - total_projetado), 2),
        "status":              status,
        "cor":                 cor,
    }


# ── Endpoint principal ────────────────────────────────────────────────────────

def invalidate_cache():
    """Chama após upload de nova planilha."""
    _cache.clear()


def get_projecoes() -> dict:
    # Cache hit?
    cached = _cache.get('result')
    if cached and (time.time() - _cache.get('ts', 0)) < _CACHE_TTL:
        return cached

    # Busca dados em paralelo: assinantes + faturamento histórico + renovações
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tasks = {
        'assinantes': fetch_assinantes,
        'historico':  fetch_approved_since,
        'renovacoes': fetch_raiz_enrollments,
    }
    fetched = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures):
            fetched[futures[fut]] = fut.result()

    # Filtra Mentoria R100 de ambas as fontes antes de qualquer cálculo
    rows_assin = _filter_mentoria(fetched.get('assinantes', []))
    rows_hist  = _filter_mentoria(fetched.get('historico',  []))

    if not rows_assin:
        return {"sucesso": False, "erro": "Sem dados de assinantes"}

    df_assin = _to_df(rows_assin)
    if df_assin.empty:
        return {"sucesso": False, "erro": "Erro ao processar dados"}

    # ── Análises de assinantes ───────────────────────────────────────────────
    # df_raiz: matrículas A Raiz da Solução (Rec=1 ou NULL) — já filtrado no fetch paralelo
    rows_raiz = fetched.get('renovacoes', [])
    df_raiz   = pd.DataFrame(rows_raiz) if rows_raiz else pd.DataFrame()

    survival      = compute_survival_curve(df_assin)
    active        = get_active_subscribers(df_assin)
    projecao_base = project_revenue(active, survival, months_ahead=12)  # usado só pro termômetro
    cohorts_turma = compute_cohorts_por_turma(df_assin, df_raiz)
    renewal       = compute_renewal_rate()

    mrr_atual          = float(active['monthly_value'].sum()) if not active.empty else 0.0
    receita_contratada = sum(projecao_base['realista'])

    # ── Análise de turmas, sazonalidade e projeção 2026 ─────────────────────
    monthly     = _build_monthly_series(rows_hist)
    historico   = compute_historico_turmas(monthly)
    proj_turmas = projetar_turmas(monthly, historico)
    seasonality = compute_seasonality(monthly)
    proj_2026   = project_2026_by_seasonality(monthly, seasonality)

    receita_t9_real  = proj_turmas['t9']['realista']
    receita_t10_real = proj_turmas['t10']['realista']
    receita_turmas_projetada = receita_t9_real + receita_t10_real

    # ── Termômetro: garantido + projetado ────────────────────────────────────
    termometro = compute_termometro(
        receita_contratada     = receita_contratada,
        receita_turmas         = receita_turmas_projetada,
        meta_anual             = 6_500_000.0,
    )

    result = {
        "sucesso":             True,
        "mrr_atual":           round(mrr_atual, 2),
        "receita_contratada":  round(receita_contratada, 2),
        "total_ativos":        int(len(active)),
        "renewal":             renewal,
        "survival":            {str(k): v for k, v in survival.items()},
        "cohorts_turma":       cohorts_turma,
        "seasonality":         seasonality,
        "proj_2026":           proj_2026,
        "historico_turmas":    historico,
        "proj_turmas":         proj_turmas,
        "termometro":          termometro,
    }
    _cache['result'] = result
    _cache['ts'] = time.time()
    return result

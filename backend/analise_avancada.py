import pandas as pd
import os
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from database import get_supabase, fetch_all_transacoes, fetch_transacoes_period, fetch_emails_before

load_dotenv()

def clean_currency(x):
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        x = x.replace('R$', '').strip()
        if ',' in x and '.' in x:
            x = x.replace('.', '')
        x = x.replace(',', '.')
        try:
            return float(x)
        except:
            return 0.0
    return float(x)

def _clean_val(v):
    """Sanitiza um valor para JSON/Supabase (remove NaN, inf)."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, str) and v in ('NaT', 'nan', 'None', ''):
        return None
    return v

def ingest_new_file(filepath, usuario: str = "sistema"):
    try:
        try:
            df_new = pd.read_excel(filepath)
        except Exception:
            df_new = pd.read_html(filepath)[0]

        # Limpar emails
        if 'Email' in df_new.columns:
            df_new['Email'] = df_new['Email'].astype(str).str.lower().str.strip()

        # Datas para ISO
        df_new['Data de Venda'] = pd.to_datetime(
            df_new['Data de Venda'], dayfirst=True, errors='coerce'
        ).dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')

        if 'Data de Confirmação' in df_new.columns:
            df_new['Data de Confirmação'] = pd.to_datetime(
                df_new['Data de Confirmação'], dayfirst=True, errors='coerce'
            ).dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')

        # Remover duplicatas internas da planilha
        subset = ['Transação', 'Número da Parcela'] if 'Número da Parcela' in df_new.columns else ['Transação', 'Recorrência']
        df_new = df_new.drop_duplicates(subset=subset, keep='last')

        sb = get_supabase()
        rows = df_new.to_dict(orient='records')
        BATCH = 200
        total = 0
        for i in range(0, len(rows), BATCH):
            batch = [{k: _clean_val(v) for k, v in r.items() if _clean_val(v) is not None}
                     for r in rows[i:i+BATCH]]
            sb.table('transacoes').upsert(batch, on_conflict='chave').execute()
            total += len(batch)

        # Registrar auditoria
        sb.table('audit_uploads').insert({
            "usuario": usuario,
            "arquivo": os.path.basename(filepath),
            "linhas":  total,
        }).execute()

        return total
    except Exception as e:
        print(f"Erro na ingestão: {e}")
        return False

def format_percentage(current, previous):
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100.0

def compute_net_revenue(row):
    """Compute net revenue in BRL with explicit currency handling."""
    moeda = str(row.get('Moeda de recebimento', row.get('Moeda', ''))).upper().strip()
    valor_convertido = clean_currency(row.get('Valor que você recebeu convertido'))
    faturamento_liquido = clean_currency(row.get('Faturamento líquido'))
    preco_total = clean_currency(row.get('Preço Total'))
    preco_total_convertido = clean_currency(row.get('Preço Total Convertido'))
    taxa_cambio_real = clean_currency(row.get('Taxa de Câmbio Real'))
    taxa_cambio_recebido = clean_currency(row.get('Taxa de Câmbio do valor recebido'))

    if moeda in ['BRL', 'REAL BRASILEIRO', '']:
        return faturamento_liquido if faturamento_liquido > 0 else valor_convertido

    if valor_convertido > 0:
        return valor_convertido
    if faturamento_liquido > 0:
        return faturamento_liquido
    if preco_total > 0 and taxa_cambio_real > 0 and taxa_cambio_recebido > 0:
        return preco_total * taxa_cambio_real * taxa_cambio_recebido
    return preco_total_convertido

def compute_gross_revenue(row):
    """Bruto em BRL conforme FORMULAS.md."""
    moeda = str(row.get('Moeda de recebimento', row.get('Moeda', ''))).upper().strip()
    preco_total = clean_currency(row.get('Preço Total'))
    preco_total_convertido = clean_currency(row.get('Preço Total Convertido'))
    taxa_cambio_real = clean_currency(row.get('Taxa de Câmbio Real'))
    taxa_cambio_recebido = clean_currency(row.get('Taxa de Câmbio do valor recebido'))

    if moeda not in ('BRL', 'REAL BRASILEIRO', ''):
        if preco_total > 0 and taxa_cambio_real > 0 and taxa_cambio_recebido > 0:
            return preco_total * taxa_cambio_real * taxa_cambio_recebido

    if preco_total_convertido > 0:
        return preco_total_convertido
    return preco_total


def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    """Converte lista de dicts do Supabase em DataFrame com colunas calculadas."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop(columns=['id', 'created_at'], errors='ignore')
    df['Data de Venda'] = pd.to_datetime(df['Data de Venda'], errors='coerce', utc=True).dt.tz_localize(None)
    df['Faturamento_Liquido'] = df.apply(compute_net_revenue, axis=1)
    df['Faturamento_Bruto'] = df.apply(compute_gross_revenue, axis=1)
    return df


def _resolve_dates(start_date, end_date):
    """Resolve datas do filtro.

    Se não informadas, detecta automaticamente o mês mais recente com dados
    consultando o banco (só uma linha).
    """
    if start_date and end_date:
        d_start = pd.to_datetime(start_date)
        d_end   = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        return d_start, d_end

    # Busca a data máxima com uma query leve (1 linha)
    # Nota: colunas com espaço precisam de aspas duplas no PostgREST
    sb = get_supabase()
    col = '"Data de Venda"'
    res = (sb.table("transacoes")
             .select(col)
             .in_("Status", ["Completo", "Aprovado"])
             .order(col, desc=True)
             .limit(1)
             .execute())
    if not res.data:
        raise ValueError("Sem dados aprovados no banco")
    max_date = pd.to_datetime(res.data[0]["Data de Venda"], utc=True).tz_localize(None)
    d_end   = max_date
    d_start = d_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return d_start, d_end


def _period_iso(d_start, d_end):
    """Converte timestamps para strings ISO usadas no filtro Supabase."""
    return d_start.strftime('%Y-%m-%dT%H:%M:%S'), d_end.strftime('%Y-%m-%dT%H:%M:%S')


def _compute_period_metrics(df_aprov: pd.DataFrame):
    vendas_df = df_aprov[(df_aprov['Recorrência'] == 1) | (df_aprov['Recorrência'].isna())]
    return {
        "liq":    df_aprov['Faturamento_Liquido'].sum(),
        "bruto":  df_aprov['Faturamento_Bruto'].sum(),
        "vendas": len(vendas_df)
    }


def get_dashboard_geral(start_date=None, end_date=None, benchmark='mom'):
    try:
        d_start, d_end = _resolve_dates(start_date, end_date)
    except ValueError as e:
        return {"sucesso": False, "erro": str(e)}

    # ── 1-3. Todas as queries em paralelo ──────────────────────────────────────
    s_iso, e_iso = _period_iso(d_start, d_end)
    delta_days = (d_end - d_start).days + 1

    mom_start = d_start - pd.Timedelta(days=delta_days)
    mom_end   = d_start - pd.Timedelta(seconds=1)
    yoy_start = d_start - pd.DateOffset(years=1)
    yoy_end   = d_end   - pd.DateOffset(years=1)
    avg1_start = d_start - pd.DateOffset(years=1)
    avg1_end   = d_end   - pd.DateOffset(years=1)
    avg2_start = d_start - pd.DateOffset(years=2)
    avg2_end   = d_end   - pd.DateOffset(years=2)

    def _fetch(label, *args):
        if label == 'emails':
            return label, fetch_emails_before(args[0])
        return label, fetch_transacoes_period(args[0], args[1])

    tasks = {
        'atual':  (s_iso, e_iso),
        'mom':    _period_iso(mom_start, mom_end),
        'yoy':    _period_iso(yoy_start, yoy_end),
        'avg1':   _period_iso(avg1_start, avg1_end),
        'avg2':   _period_iso(avg2_start, avg2_end),
        'emails': (s_iso,),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_fetch, label, *args): label
            for label, args in tasks.items()
        }
        for future in as_completed(futures):
            label, data = future.result()
            results[label] = data

    rows_atual = results['atual']
    if not rows_atual:
        return {"erro": "Sem dados para o período selecionado"}

    df_atual_all = _rows_to_df(rows_atual)
    df_atual     = df_atual_all[df_atual_all['Status'].isin(['Completo', 'Aprovado'])].copy()

    # Normaliza renomeações de produto: trata versões novas como o nome canônico
    # para não quebrar agrupamentos, gráficos e séries históricas.
    _RENOMEAR_PRODUTO = {'A Raiz da Solução 2.0': 'A Raiz da Solução'}
    if 'Nome do Produto' in df_atual.columns:
        df_atual['Nome do Produto']     = df_atual['Nome do Produto'].replace(_RENOMEAR_PRODUTO)
    if 'Nome do Produto' in df_atual_all.columns:
        df_atual_all['Nome do Produto'] = df_atual_all['Nome do Produto'].replace(_RENOMEAR_PRODUTO)
    df_mom       = _rows_to_df([r for r in results['mom']  if r.get('Status') in ('Completo', 'Aprovado')])
    df_yoy       = _rows_to_df([r for r in results['yoy']  if r.get('Status') in ('Completo', 'Aprovado')])
    avg_dfs      = [
        _rows_to_df([r for r in results['avg1'] if r.get('Status') in ('Completo', 'Aprovado')]),
        _rows_to_df([r for r in results['avg2'] if r.get('Status') in ('Completo', 'Aprovado')]),
    ]
    emails_antes = results['emails']

    # ── 4. Métricas do período atual ──
    if df_atual.empty:
        return {"erro": "Sem dados aprovados no período"}

    atual_metrics = _compute_period_metrics(df_atual)
    fat_liq_atual   = atual_metrics["liq"]
    fat_bruto_atual = atual_metrics["bruto"]
    vol_vendas_atual = atual_metrics["vendas"]

    # ── 5. Comparativos ──
    mom_metrics = _compute_period_metrics(df_mom) if not df_mom.empty else {"liq": 0, "bruto": 0, "vendas": 0}
    yoy_metrics = _compute_period_metrics(df_yoy) if not df_yoy.empty else {"liq": 0, "bruto": 0, "vendas": 0}

    if avg_dfs and any(not d.empty for d in avg_dfs):
        non_empty = [_compute_period_metrics(d) for d in avg_dfs if not d.empty]
        avg_metrics = {
            "liq":    sum(m["liq"]    for m in non_empty) / len(non_empty),
            "bruto":  sum(m["bruto"]  for m in non_empty) / len(non_empty),
            "vendas": sum(m["vendas"] for m in non_empty) / len(non_empty),
        }
    else:
        avg_metrics = {"liq": 0, "bruto": 0, "vendas": 0}

    selected = (benchmark or 'mom').lower()
    if selected == 'yoy':
        ref_metrics = yoy_metrics
        comparativo_label = f"Mesmo período {yoy_start.strftime('%m/%Y')}"
    elif selected == 'avg':
        ref_metrics = avg_metrics
        comparativo_label = "Média histórica do mesmo mês"
    else:
        ref_metrics = mom_metrics
        comparativo_label = "Período anterior equivalente"

    mom_liq   = format_percentage(fat_liq_atual,    ref_metrics["liq"])
    mom_bruto = format_percentage(fat_bruto_atual,  ref_metrics["bruto"])
    mom_vol   = format_percentage(vol_vendas_atual, ref_metrics["vendas"])

    # ── 6. Segmentação de receita ──
    seg_keys = ["Novas", "Renovacoes", "Ingressos", "Mentoria"]
    receita_segmentada = {k: {"valor": 0.0, "volume": 0, "qtd_itens": 0} for k in seg_keys}
    emails_vis = set(emails_antes)  # cópia local mutável

    for _, row in df_atual.iterrows():
        prod  = str(row.get('Nome do Produto', '')).lower()
        val   = row['Faturamento_Liquido']
        email = str(row.get('Email', '')).lower().strip()
        itens = int(float(row.get('Quantidade de itens', 0) or 0))

        if 'mentoria' in prod or 'r100' in prod:
            seg = "Mentoria"
        elif 'experience' in prod:
            seg = "Ingressos"
        elif email in emails_vis:
            seg = "Renovacoes"
        else:
            seg = "Novas"
            emails_vis.add(email)

        receita_segmentada[seg]["valor"]     += val
        receita_segmentada[seg]["volume"]    += 1
        receita_segmentada[seg]["qtd_itens"] += itens

    # ── 7. Por produto ──
    por_produto = (
        df_atual.groupby('Nome do Produto')['Faturamento_Liquido']
        .sum().sort_values(ascending=False).round(2).to_dict()
    )

    # ── 8. Evolução temporal ──
    if delta_days <= 35:
        df_atual = df_atual.copy()
        df_atual['_periodo'] = df_atual['Data de Venda'].dt.strftime('%d/%m')
        periodo_tipo = 'dia'
    elif delta_days <= 120:
        df_atual = df_atual.copy()
        df_atual['_periodo'] = df_atual['Data de Venda'].dt.to_period('W').apply(
            lambda p: p.start_time.strftime('%d/%m'))
        periodo_tipo = 'semana'
    else:
        df_atual = df_atual.copy()
        df_atual['_periodo'] = df_atual['Data de Venda'].dt.strftime('%m/%Y')
        periodo_tipo = 'mes'

    df_evolucao = df_atual[(df_atual['Recorrência'].isna()) | (df_atual['Recorrência'] == 1)]
    qtd_col = 'Quantidade de itens' if 'Quantidade de itens' in df_evolucao.columns else None
    grp = df_evolucao.groupby(['_periodo', 'Nome do Produto']).agg(
        faturamento=('Faturamento_Liquido', 'sum'),
        qtd_itens=(qtd_col, 'sum') if qtd_col else ('Faturamento_Liquido', 'count')
    ).reset_index()

    evolucao_raw = {}
    for _, row in grp.iterrows():
        p    = row['_periodo']
        prod = row['Nome do Produto']
        if p not in evolucao_raw:
            evolucao_raw[p] = {}
        evolucao_raw[p][prod] = {"fat": round(float(row['faturamento']), 2), "qtd": int(row['qtd_itens'])}

    periodos_ordenados = sorted(
        evolucao_raw.keys(),
        key=lambda x: pd.to_datetime(x, dayfirst=True, errors='coerce') or pd.Timestamp.min
    )
    evolucao = {"periodos": periodos_ordenados, "tipo": periodo_tipo, "dados": evolucao_raw}

    # ── 9. Time Comercial ──
    vendedores = {
        'ards-luc': 'Lucas',
        'ards-van': 'Vanessa',
        'ards-isa': 'Isaac',
        'ards-ali': 'Aline'
    }

    comercial = []
    df_atual_all_aprov = df_atual_all[df_atual_all['Status'].isin(['Completo', 'Aprovado'])]
    df_atual_all_atras = df_atual_all[df_atual_all['Status'] == 'Atrasado']

    if 'Origem de Checkout' in df_atual_all.columns:
        for cod, nome in vendedores.items():
            mask = df_atual_all_aprov['Origem de Checkout'].astype(str).str.contains(cod, case=False, na=False)
            df_vend_aprov = df_atual_all_aprov[mask]
            vendido = df_vend_aprov['Faturamento_Liquido'].sum()

            mask_atr = df_atual_all_atras['Origem de Checkout'].astype(str).str.contains(cod, case=False, na=False)
            atrasado = df_atual_all_atras[mask_atr]['Faturamento_Liquido'].sum()

            taxa_inad = (atrasado / (vendido + atrasado)) * 100 if (vendido + atrasado) > 0 else 0

            df_vend_vendas = df_vend_aprov[
                (df_vend_aprov['Recorrência'].isna()) | (df_vend_aprov['Recorrência'] == 1)
            ]
            volume_vendas = len(df_vend_vendas)
            qtd_itens = int(
                df_vend_vendas['Quantidade de itens'].fillna(0).astype(float).sum()
            ) if 'Quantidade de itens' in df_vend_vendas.columns else 0

            comercial.append({
                "nome":              nome,
                "vendido":           float(vendido),
                "inadimplencia_perc": float(taxa_inad),
                "volume_vendas":     volume_vendas,
                "qtd_itens":         qtd_itens
            })

    comercial.sort(key=lambda x: x['vendido'], reverse=True)

    # ── 10. Cards adicionais ──
    LIMIAR_RENOVACAO = 4200.0
    df_vendas = df_atual[
        (df_atual['Recorrência'].isna()) | (df_atual['Recorrência'] == 1)
    ]

    qtd_itens_total = int(
        df_vendas['Quantidade de itens'].fillna(0).astype(float).sum()
    ) if 'Quantidade de itens' in df_vendas.columns else 0

    _RAIZ = {'A Raiz da Solução', 'A Raiz da Solução 2.0'}
    _is_raiz         = df_vendas['Nome do Produto'].isin(_RAIZ)
    raiz             = df_vendas[_is_raiz]
    curso_padrao_liq  = float(raiz[raiz['Faturamento_Liquido'] >  LIMIAR_RENOVACAO]['Faturamento_Liquido'].sum())
    curso_renovacao_liq = float(raiz[raiz['Faturamento_Liquido'] <= LIMIAR_RENOVACAO]['Faturamento_Liquido'].sum())
    outros_liq        = float(df_vendas[~_is_raiz]['Faturamento_Liquido'].sum())

    df_assin_col = 'Código do assinante'
    assinaturas_ativas = int(df_atual[
        (df_atual['Recorrência'] >= 2) &
        (df_atual[df_assin_col].notna()) &
        (df_atual[df_assin_col] != '')
    ]['Código do assinante'].nunique()) if df_assin_col in df_atual.columns else 0

    df_cancel = df_atual_all[df_atual_all['Status'] == 'Cancelado']
    cancelados_total = int(len(df_cancel))
    emails_cancel = set(df_cancel['Email'].dropna().str.lower().str.strip().unique())
    emails_convertidos = set(
        df_atual_all[
            df_atual_all['Status'].isin(['Completo', 'Aprovado']) &
            df_atual_all['Email'].str.lower().str.strip().isin(emails_cancel)
        ]['Email'].str.lower().str.strip().unique()
    ) if emails_cancel else set()
    cancelados_sem_conversao = int(len(emails_cancel - emails_convertidos))

    # Inadimplentes do período
    inad_exec = {"total": 0, "valor": 0.0}
    if df_assin_col in df_atual_all.columns:
        df_assin_periodo = df_atual_all[
            df_atual_all[df_assin_col].notna() & (df_atual_all[df_assin_col] != '')
        ]
        cods_inad = set(df_assin_periodo[df_assin_periodo['Status'] == 'Atrasado'][df_assin_col].unique())
        inad_exec["total"] = int(len(cods_inad))
        valor_inad = 0.0
        for cod in cods_inad:
            atrasadas = df_assin_periodo[
                (df_assin_periodo[df_assin_col] == cod) &
                (df_assin_periodo['Status'] == 'Atrasado')
            ]
            if atrasadas.empty:
                continue
            ref   = atrasadas.iloc[0]
            preco = clean_currency(ref.get('Preço Total', 0))
            moeda = str(ref.get('Moeda de recebimento', 'BRL')).upper().strip()
            if moeda not in ('BRL', 'REAL BRASILEIRO', ''):
                tcr = clean_currency(ref.get('Taxa de Câmbio Real', 0))
                tcv = clean_currency(ref.get('Taxa de Câmbio do valor recebido', 0))
                if tcr > 0 and tcv > 0:
                    preco = preco * tcr * tcv
            valor_inad += preco * len(atrasadas)
        inad_exec["valor"] = float(valor_inad)

    return {
        "sucesso": True,
        "periodo_atual": f"{d_start.strftime('%d/%m/%Y')} a {d_end.strftime('%d/%m/%Y')}",
        "resumo": {
            "faturamento_liquido":     float(fat_liq_atual),
            "mom_liquido":             float(mom_liq),
            "faturamento_bruto":       float(fat_bruto_atual),
            "mom_bruto":               float(mom_bruto),
            "vendas":                  int(vol_vendas_atual),
            "mom_vendas":              float(mom_vol),
            "qtd_itens":               qtd_itens_total,
            "curso_padrao":            curso_padrao_liq,
            "curso_padrao_volume":     int(len(raiz[raiz['Faturamento_Liquido'] >  LIMIAR_RENOVACAO])),
            "curso_renovacao":         curso_renovacao_liq,
            "curso_renovacao_volume":  int(len(raiz[raiz['Faturamento_Liquido'] <= LIMIAR_RENOVACAO])),
            "outros_venda":            outros_liq,
            "assinaturas_ativas":      assinaturas_ativas,
            "cancelados_total":        cancelados_total,
            "cancelados_sem_conversao": cancelados_sem_conversao,
            "inadimplentes":           inad_exec["total"],
            "valor_inadimplente":      inad_exec["valor"]
        },
        "benchmark":         selected,
        "comparativo_label": comparativo_label,
        "comparativos": {
            "mom": {
                "liquido": float(format_percentage(fat_liq_atual,    mom_metrics["liq"])),
                "bruto":   float(format_percentage(fat_bruto_atual,  mom_metrics["bruto"])),
                "vendas":  float(format_percentage(vol_vendas_atual, mom_metrics["vendas"]))
            },
            "yoy": {
                "liquido": float(format_percentage(fat_liq_atual,    yoy_metrics["liq"])),
                "bruto":   float(format_percentage(fat_bruto_atual,  yoy_metrics["bruto"])),
                "vendas":  float(format_percentage(vol_vendas_atual, yoy_metrics["vendas"]))
            },
            "avg": {
                "liquido": float(format_percentage(fat_liq_atual,    avg_metrics["liq"])),
                "bruto":   float(format_percentage(fat_bruto_atual,  avg_metrics["bruto"])),
                "vendas":  float(format_percentage(vol_vendas_atual, avg_metrics["vendas"]))
            }
        },
        "segmentacao": {
            k: {"valor": round(v["valor"], 2), "volume": v["volume"], "qtd_itens": v["qtd_itens"]}
            for k, v in receita_segmentada.items()
        },
        "por_produto": por_produto,
        "evolucao":    evolucao,
        "comercial":   comercial
    }


def get_inadimplencia(start_date=None, end_date=None):
    try:
        d_start, d_end = _resolve_dates(start_date, end_date)
    except ValueError as e:
        return {"sucesso": False, "erro": str(e)}

    s_iso, e_iso = _period_iso(d_start, d_end)
    rows = fetch_transacoes_period(s_iso, e_iso)
    if not rows:
        return {"sucesso": False, "erro": "Sem dados"}

    df_all = _rows_to_df(rows)

    # Inadimplência só existe para assinantes (Código do assinante preenchido)
    df_assin = df_all[df_all['Código do assinante'].notna() & (df_all['Código do assinante'] != '')].copy()

    assinantes_ativos = set(
        df_assin[df_assin['Status'].isin(['Completo', 'Aprovado'])]['Código do assinante'].unique()
    )
    inad_periodo = set(
        df_assin[df_assin['Status'] == 'Atrasado']['Código do assinante'].unique()
    )

    hoje = datetime.now()
    aging = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    lista_devedores = []

    for cod in inad_periodo:
        hist      = df_assin[df_assin['Código do assinante'] == cod]
        atrasadas = hist[hist['Status'] == 'Atrasado']
        qtd_atrasadas = len(atrasadas)

        ref_row       = atrasadas.iloc[0]
        preco_unitario = clean_currency(ref_row.get('Preço Total', 0))
        moeda = str(ref_row.get('Moeda de recebimento', 'BRL')).upper().strip()
        if moeda not in ('BRL', 'REAL BRASILEIRO', ''):
            tcr = clean_currency(ref_row.get('Taxa de Câmbio Real', 0))
            tcv = clean_currency(ref_row.get('Taxa de Câmbio do valor recebido', 0))
            if tcr > 0 and tcv > 0:
                preco_unitario = preco_unitario * tcr * tcv

        valor_devido    = preco_unitario * qtd_atrasadas
        data_mais_antiga = atrasadas['Data de Venda'].min()
        dias = (hoje - data_mais_antiga).days if pd.notna(data_mais_antiga) else 0

        if   dias <= 30: aging["0-30"]  += 1
        elif dias <= 60: aging["31-60"] += 1
        elif dias <= 90: aging["61-90"] += 1
        else:            aging["90+"]   += 1

        nome_row = hist.iloc[-1]
        lista_devedores.append({
            "nome":               str(nome_row.get('Nome', 'Desconhecido')),
            "email":              str(nome_row.get('Email', '')),
            "telefone":           str(nome_row.get('Telefone', '')),
            "produto":            str(nome_row.get('Nome do Produto', '')),
            "codigo_assinante":   str(cod),
            "parcelas_atrasadas": int(qtd_atrasadas),
            "valor":              float(valor_devido),
            "dias":               int(dias),
            "data": data_mais_antiga.strftime('%d/%m/%Y') if pd.notna(data_mais_antiga) else ''
        })

    lista_devedores.sort(key=lambda x: x['dias'], reverse=True)

    total_ativos = len(assinantes_ativos | inad_periodo)
    total_inad   = len(inad_periodo)
    taxa = (total_inad / total_ativos * 100) if total_ativos > 0 else 0

    return {
        "sucesso": True,
        "geral": {
            "total_inadimplentes": int(total_inad),
            "taxa_inadimplencia":  float(taxa)
        },
        "aging": aging,
        "lista": lista_devedores[:100]
    }

// ── Tabelas sortáveis ─────────────────────────────────────────────────────
function initSortableTable(tableId) {
    const table  = document.getElementById(tableId);
    if (!table) return;
    const thead  = table.querySelector('thead tr');
    const tbody  = table.querySelector('tbody');
    const state  = { col: -1, asc: true };

    thead.querySelectorAll('th.sortable').forEach((th, _) => {
        // índice real da th dentro da tr
        const colIdx = Array.from(thead.children).indexOf(th);
        th.addEventListener('click', () => {
            const asc = state.col === colIdx ? !state.asc : true;
            state.col = colIdx;
            state.asc = asc;

            // Atualiza classes de seta
            thead.querySelectorAll('th.sortable').forEach(h => {
                h.classList.remove('sort-asc', 'sort-desc');
            });
            th.classList.add(asc ? 'sort-asc' : 'sort-desc');

            // Ordena as linhas
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort((a, b) => {
                const aCell = a.cells[colIdx];
                const bCell = b.cells[colIdx];
                // Preferir data-val numérico se existir
                const aNum = parseFloat(aCell.dataset.val);
                const bNum = parseFloat(bCell.dataset.val);
                if (!isNaN(aNum) && !isNaN(bNum)) {
                    return asc ? aNum - bNum : bNum - aNum;
                }
                const aStr = aCell.textContent.trim();
                const bStr = bCell.textContent.trim();
                return asc
                    ? aStr.localeCompare(bStr, 'pt-BR')
                    : bStr.localeCompare(aStr, 'pt-BR');
            });

            // Reinsere linhas na ordem nova (rank original mantido)
            rows.forEach(r => tbody.appendChild(r));
        });
    });
}

// ── Auth: verifica sessão antes de carregar qualquer dado ─────────────────
async function checkAuth() {
    try {
        const res = await fetch('/api/me');
        if (res.status === 401) { window.location.href = '/login.html'; return null; }
        const user = await res.json();
        // Mostra nome do usuário na sidebar
        document.getElementById('userInfo').innerText = user.name || user.email;
        // Botão de upload: só superadmin vê
        if (user.role !== 'superadmin') {
            document.getElementById('uploadSection').classList.add('hidden');
        }
        return user;
    } catch { window.location.href = '/login.html'; return null; }
}

const navGeral    = document.getElementById('nav-geral');
const navInad     = document.getElementById('nav-inadimplencia');
const navProj     = document.getElementById('nav-projecoes');
const navClientes = document.getElementById('nav-clientes');
const navParcel   = document.getElementById('nav-parcelamentos');
const pageGeral   = document.getElementById('page-geral');
const pageInad    = document.getElementById('page-inadimplencia');
const pageProj    = document.getElementById('page-projecoes');
const pageClientes = document.getElementById('page-clientes');
const pageParcel  = document.getElementById('page-parcelamentos');
const fileInput   = document.getElementById('fileInput');
const btnUpload   = document.getElementById('btnUpload');
const loading     = document.getElementById('loading');
const loadingLabel = document.getElementById('loadingLabel');

function showLoading(msg = 'Carregando') {
    loadingLabel.innerText = msg;
    loading.classList.remove('hidden', 'fading');
}
function hideLoading() {
    loading.classList.add('fading');
    setTimeout(() => loading.classList.add('hidden'), 420);
}

let receitaChart     = null;
let agingChart       = null;
let produtoChart     = null;
let evolucaoChart    = null;
let survivalChart      = null;
let projecaoChart      = null;
let sazonalidadeChart  = null;
let rfmChart           = null;
let retencaoParcelChart  = null;
let projecaoParcelChart  = null;

let projecoesLoaded    = false;  // carrega só quando a aba for aberta pela primeira vez
let clientesLoaded     = false;
let parcelamentosLoaded = false;

function showPage(page) {
    [pageGeral, pageInad, pageProj, pageClientes, pageParcel].forEach(p => p.classList.add('hidden'));
    [navGeral, navInad, navProj, navClientes, navParcel].forEach(n => n.classList.remove('active'));
    page.classList.remove('hidden');
}

// Routing
navGeral.addEventListener('click', (e) => {
    e.preventDefault();
    showPage(pageGeral); navGeral.classList.add('active');
});
navInad.addEventListener('click', (e) => {
    e.preventDefault();
    showPage(pageInad); navInad.classList.add('active');
});
navProj.addEventListener('click', async (e) => {
    e.preventDefault();
    showPage(pageProj); navProj.classList.add('active');
    if (!projecoesLoaded) {
        showLoading('Calculando projeções');
        try {
            const res  = await fetch('/api/projecoes');
            const data = await res.json();
            if (data.sucesso) { renderProjecoes(data); projecoesLoaded = true; }
        } catch(err) { console.error('Erro projeções', err); }
        finally { hideLoading(); }
    }
});

navClientes.addEventListener('click', e => {
    e.preventDefault();
    showPage(pageClientes); navClientes.classList.add('active');
    if (!clientesLoaded) loadClientes();
});

navParcel.addEventListener('click', async (e) => {
    e.preventDefault();
    showPage(pageParcel); navParcel.classList.add('active');
    if (!parcelamentosLoaded) {
        showLoading('Analisando parcelamentos');
        try {
            const res  = await fetch('/api/parcelamentos');
            const data = await res.json();
            if (data.sucesso) { renderParcelamentos(data); parcelamentosLoaded = true; }
        } catch(err) { console.error('Erro parcelamentos', err); }
        finally { hideLoading(); }
    }
});

// Upload
btnUpload.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', async (e) => {
    if (!e.target.files.length) return;
    showLoading('Sincronizando banco de dados');

    const formData = new FormData();
    formData.append('file', e.target.files[0]);

    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if(data.sucesso) {
            showLoading('Atualizando métricas');
            // Resetar flags para forçar recarga das abas lazy na próxima visita
            projecoesLoaded     = false;
            clientesLoaded      = false;
            parcelamentosLoaded = false;
            await loadAllData();
        } else {
            alert('Erro: ' + data.erro);
        }
    } catch(err) {
        alert('Erro de conexão');
    } finally {
        hideLoading();
    }
});

const btnAtual = document.getElementById('btnAtual');
const btn3Meses = document.getElementById('btn3Meses');
const btnAno = document.getElementById('btnAno');
const btnHistorico = document.getElementById('btnHistorico');
const btnFiltrar = document.getElementById('btnFiltrar');
const dateStart = document.getElementById('dateStart');
const dateEnd = document.getElementById('dateEnd');
const selectBenchmark = document.getElementById('selectBenchmark');
const selectEvolucaoMetrica = document.getElementById('selectEvolucaoMetrica');
let currentBenchmark = 'mom';
let lastEvolucaoData = null;

selectBenchmark.addEventListener('change', () => {
    currentBenchmark = selectBenchmark.value;
    loadAllData(dateStart.value, dateEnd.value);
});

btnAtual.addEventListener('click', () => {
    dateStart.value = ''; dateEnd.value = '';
    loadAllData('', '');
});

btnHistorico.addEventListener('click', () => {
    const d = new Date();
    const start = '2022-01-01';
    const end = d.toISOString().split('T')[0];
    dateStart.value = start; dateEnd.value = end;
    loadAllData(start, end);
});

btn3Meses.addEventListener('click', () => {
    const d = new Date();
    const end = d.toISOString().split('T')[0];
    d.setMonth(d.getMonth() - 3);
    const start = d.toISOString().split('T')[0];
    dateStart.value = start; dateEnd.value = end;
    loadAllData(start, end);
});

btnAno.addEventListener('click', () => {
    const d = new Date();
    const start = `${d.getFullYear()}-01-01`;
    const end = d.toISOString().split('T')[0];
    dateStart.value = start; dateEnd.value = end;
    loadAllData(start, end);
});

btnFiltrar.addEventListener('click', () => {
    if(dateStart.value && dateEnd.value) {
        loadAllData(dateStart.value, dateEnd.value);
    }
});

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function chartColors() {
    return {
        grid: cssVar('--border'),
        text: cssVar('--text-muted'),
        surface: cssVar('--surface'),
    };
}

function formatCurrency(val) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val); }
function formatMom(val, el, label = 'Período Anterior') {
    if(val > 0) { el.innerText = `↑ ${val.toFixed(1)}% vs ${label}`; el.className = 'mom positive'; }
    else if(val < 0) { el.innerText = `↓ ${Math.abs(val).toFixed(1)}% vs ${label}`; el.className = 'mom negative'; }
    else { el.innerText = `0% vs ${label}`; el.className = 'mom'; }
}

async function loadAllData(start = '', end = '') {
    showLoading('Carregando');
    try {
        let urlDash = '/api/dashboard';
        let urlInad = '/api/inadimplencia';
        if(start && end) urlDash += `?start=${start}&end=${end}&benchmark=${currentBenchmark}`;
        else urlDash += `?benchmark=${currentBenchmark}`;
        if(start && end) urlInad += `?start=${start}&end=${end}`;

        const [resDash, resInad] = await Promise.all([
            fetch(urlDash),
            fetch(urlInad)
        ]);
        const dataDash = await resDash.json();
        const dataInad = await resInad.json();

        if(dataDash.sucesso) renderDashboard(dataDash);
        if(dataInad.sucesso) renderInadimplencia(dataInad);
    } catch(err) {
        console.error("Failed to load data", err);
    } finally {
        hideLoading();
    }
}

function renderDashboard(data) {
    document.getElementById('refMes').innerText = `Período: ${data.periodo_atual}`;
    const benchmarkLabel = data.comparativo_label || 'Período Anterior';

    
    const r = data.resumo;

    // Linha 1 — Financeiro principal
    document.getElementById('valFatLiq').innerText = formatCurrency(r.faturamento_liquido);
    formatMom(r.mom_liquido, document.getElementById('momFatLiq'), benchmarkLabel);
    document.getElementById('valFatBruto').innerText = formatCurrency(r.faturamento_bruto);
    formatMom(r.mom_bruto, document.getElementById('momFatBruto'), benchmarkLabel);
    document.getElementById('valVendas').innerText = r.vendas;
    formatMom(r.mom_vendas, document.getElementById('momVendas'), benchmarkLabel);
    document.getElementById('valQtdItens').innerText = r.qtd_itens ?? 0;

    // Linha 2 — Breakdown de vendas
    document.getElementById('valCursoPadrao').innerText = formatCurrency(r.curso_padrao ?? 0);
    document.getElementById('lblCursoPadrao').innerText = `A Raiz da Solução = ${r.curso_padrao_volume ?? 0} vendas`;
    document.getElementById('valCursoRenovacao').innerText = formatCurrency(r.curso_renovacao ?? 0);
    document.getElementById('lblCursoRenovacao').innerText = `A Raiz da Solução = ${r.curso_renovacao_volume ?? 0} vendas`;
    document.getElementById('valOutrosVenda').innerText = formatCurrency(r.outros_venda ?? 0);
    document.getElementById('valAssinaturasAtivas').innerText = r.assinaturas_ativas ?? 0;

    // Linha 3 — Risco e cancelamento
    document.getElementById('valInadimplentes').innerText = r.inadimplentes ?? 0;
    document.getElementById('valValorInadimplente').innerText = formatCurrency(r.valor_inadimplente ?? 0);
    document.getElementById('valCancelados').innerText = r.cancelados_total ?? 0;
    const semConv = r.cancelados_sem_conversao ?? 0;
    document.getElementById('valCanceladosSemConv').innerText =
        semConv === 0 ? '✓ Todos converteram' : `${semConv} sem conversão`;

    // ── Gráfico pizza: Origem da Receita ──────────────────────────────────────
    const seg = data.segmentacao;
    const segLabels = Object.keys(seg);
    const segTotal = segLabels.reduce((s, k) => s + seg[k].valor, 0);
    const segValues = segLabels.map(k => seg[k].valor);
    const segColors = ['#2563EB', '#059669', '#F59E0B', '#8B5CF6'];

    const ctxPizza = document.getElementById('receitaChart').getContext('2d');
    if(receitaChart) receitaChart.destroy();
    receitaChart = new Chart(ctxPizza, {
        type: 'doughnut',
        data: {
            labels: segLabels,
            datasets: [{ data: segValues, backgroundColor: segColors, borderWidth: 0 }]
        },
        options: {
            cutout: '70%',
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 11 }, boxWidth: 12 } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const k = segLabels[ctx.dataIndex];
                            const d = seg[k];
                            const pct = segTotal > 0 ? ((d.valor / segTotal) * 100).toFixed(1) : 0;
                            return [
                                ` ${pct}% — ${formatCurrency(d.valor)}`,
                                ` ${d.volume} vendas · ${d.qtd_itens} itens`
                            ];
                        }
                    }
                },
                datalabels: false
            }
        },
        plugins: [{
            id: 'pctLabel',
            afterDraw(chart) {
                const { ctx: c, data: d, chartArea: { top, bottom, left, right } } = chart;
                const meta = chart.getDatasetMeta(0);
                meta.data.forEach((arc, i) => {
                    const pct = segTotal > 0 ? ((segValues[i] / segTotal) * 100).toFixed(0) : 0;
                    if (pct < 4) return;
                    const angle = (arc.startAngle + arc.endAngle) / 2;
                    const r = (arc.outerRadius + arc.innerRadius) / 2;
                    const x = arc.x + Math.cos(angle) * r;
                    const y = arc.y + Math.sin(angle) * r;
                    c.save();
                    c.fillStyle = '#fff';
                    c.font = 'bold 11px Inter, sans-serif';
                    c.textAlign = 'center';
                    c.textBaseline = 'middle';
                    c.fillText(`${pct}%`, x, y);
                    c.restore();
                });
            }
        }]
    });

    // ── Tabela Performance Comercial ─────────────────────────────────────────
    const tbody = document.getElementById('comercialTbody');
    tbody.innerHTML = '';
    data.comercial.forEach(vend => {
        tbody.innerHTML += `<tr>
            <td><strong>${vend.nome}</strong></td>
            <td>${formatCurrency(vend.vendido)}</td>
            <td style="text-align:center">${vend.volume_vendas ?? 0}</td>
            <td style="text-align:center">${vend.qtd_itens ?? 0}</td>
            <td style="color:${vend.inadimplencia_perc > 20 ? '#DC2626' : '#059669'}">${vend.inadimplencia_perc.toFixed(1)}%</td>
        </tr>`;
    });

    // ── Gráfico barras horizontal: Faturamento por Produto ───────────────────
    const pp = data.por_produto || {};
    const ppLabels = Object.keys(pp);
    const ppValues = ppLabels.map(k => pp[k]);
    const ctxProd = document.getElementById('produtoChart').getContext('2d');
    if(produtoChart) produtoChart.destroy();
    produtoChart = new Chart(ctxProd, {
        type: 'bar',
        data: {
            labels: ppLabels,
            datasets: [{
                label: 'Faturamento Líquido',
                data: ppValues,
                backgroundColor: '#2563EB',
                borderRadius: 4,
                barThickness: 24
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: ctx => ' ' + formatCurrency(ctx.raw) } }
            },
            scales: {
                x: { ticks: { color: chartColors().text, callback: v => formatCurrency(v) }, grid: { color: chartColors().grid } },
                y: { ticks: { color: chartColors().text }, grid: { display: false } }
            }
        }
    });

    // ── Gráfico linha: Evolução temporal ─────────────────────────────────────
    lastEvolucaoData = data.evolucao;
    renderEvolucao(data.evolucao, selectEvolucaoMetrica.value);
}

function renderEvolucao(evolucao, metrica) {
    if (!evolucao) return;
    const { periodos, dados } = evolucao;
    const produtos = [...new Set(Object.values(dados).flatMap(p => Object.keys(p)))];
    const palette = ['#2563EB','#059669','#F59E0B','#8B5CF6','#DC2626','#0891B2'];

    const datasets = produtos.map((prod, i) => ({
        label: prod,
        data: periodos.map(p => {
            const v = dados[p]?.[prod];
            return v ? (metrica === 'fat' ? v.fat : v.qtd) : 0;
        }),
        borderColor: palette[i % palette.length],
        backgroundColor: palette[i % palette.length] + '18',
        borderWidth: 2,
        pointRadius: periodos.length <= 31 ? 3 : 2,
        tension: 0.3,
        fill: false
    }));

    const ctxEv = document.getElementById('evolucaoChart').getContext('2d');
    if(evolucaoChart) evolucaoChart.destroy();
    evolucaoChart = new Chart(ctxEv, {
        type: 'line',
        data: { labels: periodos, datasets },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 11 }, boxWidth: 12 } },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const v = ctx.raw;
                            return ` ${ctx.dataset.label}: ${metrica === 'fat' ? formatCurrency(v) : v + ' itens'}`;
                        }
                    }
                }
            },
            scales: {
                x: { grid: { color: chartColors().grid }, ticks: { color: chartColors().text, maxRotation: 45 } },
                y: {
                    grid: { color: chartColors().grid },
                    ticks: { color: chartColors().text, callback: v => metrica === 'fat' ? formatCurrency(v) : v }
                }
            }
        }
    });
}

selectEvolucaoMetrica.addEventListener('change', () => {
    renderEvolucao(lastEvolucaoData, selectEvolucaoMetrica.value);
});

function renderInadimplencia(data) {
    const g = data.geral;
    document.getElementById('valDevedores').innerText        = g.total_inadimplentes ?? 0;
    document.getElementById('valTaxaInad').innerText         = `${(g.taxa_inadimplencia ?? 0).toFixed(1)}%`;
    document.getElementById('valEmAberto').innerText         = formatCurrency(g.valor_em_aberto ?? 0);
    document.getElementById('valRecuperados').innerText      = g.total_recuperados ?? 0;
    document.getElementById('valTaxaRecuperacao').innerText  = `${(g.taxa_recuperacao ?? 0).toFixed(1)}%`;
    document.getElementById('valRecuperado').innerText       = formatCurrency(g.valor_recuperado ?? 0);
    document.getElementById('subTaxaInad').innerText         = `sobre ${g.total_assinantes ?? '—'} assinantes do período`;

    // Aging Chart
    const ctx = document.getElementById('agingChart').getContext('2d');
    if (agingChart) agingChart.destroy();
    agingChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: Object.keys(data.aging),
            datasets: [{
                label: 'Devedores por Faixa de Atraso (Dias)',
                data: Object.values(data.aging),
                backgroundColor: '#DC2626',
                borderRadius: 4
            }]
        },
        options: {
            scales: {
                x: { grid: { color: chartColors().grid }, ticks: { color: chartColors().text } },
                y: { grid: { color: chartColors().grid }, ticks: { color: chartColors().text } }
            }
        }
    });

    // Tabela devedores ativos
    const tbody = document.getElementById('devedoresTbody');
    tbody.innerHTML = (data.lista || []).map(d => `<tr>
        <td><span class="badge-danger">${d.dias} dias</span></td>
        <td><strong>${d.nome}</strong><br><span style="font-size:11px;color:var(--text-muted)">${d.email}</span></td>
        <td>${d.telefone || 'N/A'}</td>
        <td>${d.produto}</td>
        <td>${formatCurrency(d.valor)}</td>
    </tr>`).join('');

    // Tabela recuperados
    const rec = data.lista_recuperados || [];
    const secao = document.getElementById('secaoRecuperados');
    if (rec.length > 0) {
        secao.style.display = '';
        document.getElementById('recuperadosTbody').innerHTML = rec.map(r => `<tr>
            <td><strong>${r.nome}</strong><br><span style="font-size:11px;color:var(--text-muted)">${r.email}</span></td>
            <td>${r.produto}</td>
            <td style="text-align:center">${r.parcelas_recuperadas}</td>
            <td style="color:var(--success);font-weight:600">${formatCurrency(r.valor_recuperado)}</td>
            <td>${r.data_recuperacao}</td>
        </tr>`).join('');
    } else {
        secao.style.display = 'none';
    }
}

// ── Parcelamentos ─────────────────────────────────────────────────────────────
let _parcelData = null;

function _renderRetencaoChart(retData, label) {
    // retData pode ser:
    //   • objeto geral:     { contratos, curva: {1:78.8, 2:55.0, ...} }
    //   • objeto por_turma: { T06: {contratos, curva}, T07: {contratos, curva}, ... }
    const cc  = chartColors();
    const ctx = document.getElementById('retencaoParcelChart').getContext('2d');
    if (retencaoParcelChart) retencaoParcelChart.destroy();

    const paleta = ['#3B82F6','#10B981','#F59E0B','#EF4444','#8B5CF6','#EC4899','#F97316','#06B6D4'];
    const labels = Array.from({length: 12}, (_, i) => `Mês ${i + 1}`);

    let datasets = [];

    if (retData && retData.curva) {
        // Modo: uma única turma ou geral (objeto com {contratos, curva})
        const vals = Array.from({length: 12}, (_, i) => retData.curva[i + 1] ?? null);
        datasets = [{
            label:           label || `Geral (${retData.contratos} assinantes)`,
            data:            vals,
            borderColor:     paleta[0],
            backgroundColor: paleta[0] + '22',
            tension:         0.3,
            pointRadius:     4,
            spanGaps:        false,
        }];
    } else if (retData) {
        // Modo: todas as turmas — retData = { T03: {...}, T04: {...}, ... }
        datasets = Object.entries(retData).map(([turma, info], idx) => ({
            label:           `${turma} (${info.contratos})`,
            data:            Array.from({length: 12}, (_, i) => info.curva[i + 1] ?? null),
            borderColor:     paleta[idx % paleta.length],
            backgroundColor: paleta[idx % paleta.length] + '22',
            tension:         0.3,
            pointRadius:     4,
            spanGaps:        false,
        }));
    }

    retencaoParcelChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            scales: {
                x: { grid: { color: cc.grid }, ticks: { color: cc.text } },
                y: {
                    min: 0, max: 100,
                    grid: { color: cc.grid }, ticks: { color: cc.text },
                    title: { display: true, text: 'Retenção (%)', color: cc.text }
                }
            },
            plugins: { legend: { labels: { color: cc.text, boxWidth: 12 } } }
        }
    });
}

function renderParcelamentos(data) {
    _parcelData = data;

    // Cards
    document.getElementById('valContratosAtivos').innerText = data.cards.assinantes_ativos ?? 0;
    document.getElementById('valParcelas30d').innerText     = data.cards.vencimentos_30d ?? 0;
    document.getElementById('valReceita6m').innerText       = formatCurrency(data.cards.receita_6m ?? 0);

    // Popular select de turma
    const sel = document.getElementById('filtroTurmaParcel');
    sel.innerHTML = '<option value="geral">Todas as turmas</option>';
    (data.retention.turmas || []).forEach(t => {
        const opt = document.createElement('option');
        opt.value = t; opt.textContent = `${t} (${data.retention.por_turma[t]?.contratos ?? 0} assinantes)`;
        sel.appendChild(opt);
    });
    sel.onchange = () => {
        const v = sel.value;
        if (v === 'geral') {
            _renderRetencaoChart(_parcelData.retention.por_turma, null);
        } else {
            const info = _parcelData.retention.por_turma[v];
            _renderRetencaoChart(info, `${v} (${info?.contratos} assinantes)`);
        }
    };

    // Gráfico 1 — inicia mostrando todas as turmas
    _renderRetencaoChart(data.retention.por_turma, null);

    // Gráfico 2 — Projeção mensal (barras)
    const cc      = chartColors();
    const ctxProj = document.getElementById('projecaoParcelChart').getContext('2d');
    if (projecaoParcelChart) projecaoParcelChart.destroy();

    projecaoParcelChart = new Chart(ctxProj, {
        type: 'bar',
        data: {
            labels:   data.projection.labels || [],
            datasets: [{
                label:           'Receita Esperada (R$)',
                data:            data.projection.valores || [],
                backgroundColor: '#3B82F6',
                borderRadius:    6,
            }]
        },
        options: {
            scales: {
                x: { grid: { color: cc.grid }, ticks: { color: cc.text } },
                y: {
                    grid: { color: cc.grid },
                    ticks: { color: cc.text, callback: v => 'R$ ' + v.toLocaleString('pt-BR') }
                }
            },
            plugins: { legend: { display: false } }
        }
    });

    // Tabela
    const tbody = document.getElementById('parcelTbody');
    tbody.innerHTML = (data.tabela || []).map(c => `<tr>
        <td><strong>${c.nome}</strong><br><span style="font-size:11px;color:var(--text-muted)">${c.email}</span></td>
        <td>${c.produto}</td>
        <td style="text-align:center">${c.turma}</td>
        <td style="text-align:center">${c.rec_atual} / 12</td>
        <td style="text-align:center">
            <span class="${c.restantes <= 2 ? 'badge-danger' : ''}">${c.restantes}</span>
        </td>
        <td>${c.proxima}</td>
    </tr>`).join('');
}

// ── Projeções ────────────────────────────────────────────────────────────────
function renderProjecoes(data) {
    const cc = chartColors();

    // ── Termômetro ──
    const t = data.termometro;
    const fill   = document.getElementById('termometroFill');
    const pctEl  = document.getElementById('termometroPct');
    const descEl = document.getElementById('termometroDesc');
    const statusEmoji = t.status === 'saudavel' ? '🟢' : t.status === 'atencao' ? '🟡' : '🔴';

    pctEl.innerText  = t.percentual + '%';
    pctEl.style.color = t.cor;

    const statusLabel = {
        saudavel: 'Empresa segura',
        atencao:  'Atenção — abaixo do necessário',
        critico:  'Risco de sobrevivência',
    }[t.status];
    descEl.innerText = `${statusEmoji} ${statusLabel} — Projeção de ${formatCurrency(t.total_projetado)} dos ${formatCurrency(t.meta_anual)} necessários para os próximos 12 meses`;

    // Anima o fill com delay para o CSS transition funcionar
    setTimeout(() => {
        fill.style.width = t.percentual + '%';
        if (t.status === 'critico')  fill.style.background = 'linear-gradient(90deg,#EF4444,#F97316)';
        if (t.status === 'atencao')  fill.style.background = 'linear-gradient(90deg,#3B82F6,#F59E0B)';
        if (t.status === 'saudavel') fill.style.background = 'linear-gradient(90deg,#3B82F6,#10B981)';
    }, 100);

    document.getElementById('termometroContratada').innerHTML =
        `Contratada: <strong>${formatCurrency(t.receita_contratada)}</strong>`;
    document.getElementById('termometroTurmas').innerHTML =
        `Turmas esperadas: <strong>${formatCurrency(t.receita_turmas)}</strong>`;
    document.getElementById('termometroGap').innerHTML =
        t.gap > 0
        ? `Falta para sobreviver: <strong style="color:var(--danger)">${formatCurrency(t.gap)}</strong>`
        : `<strong style="color:var(--success)">✓ Piso atingido</strong>`;

    // KPIs
    document.getElementById('valMRR').innerText        = formatCurrency(data.mrr_atual);
    document.getElementById('valContratada').innerText = formatCurrency(data.receita_contratada);
    document.getElementById('valAtivos').innerText     = data.total_ativos;
    document.getElementById('valRenovacao').innerText  = data.renewal.taxa + '%';
    document.getElementById('lblRenovacao').innerText  =
        `${data.renewal.renovaram} de ${data.renewal.total_elegivel} elegíveis (critério: email + preço)`;

    // ── Curva de Sobrevivência ──
    const survLabels = Object.keys(data.survival).map(k => `Mês ${k}`);
    const survValues = Object.values(data.survival).map(v => v ? +(v * 100).toFixed(1) : null);

    if (survivalChart) survivalChart.destroy();
    survivalChart = new Chart(document.getElementById('survivalChart'), {
        type: 'line',
        data: {
            labels: survLabels,
            datasets: [{
                label: '% Retidos',
                data: survValues,
                borderColor: '#3B82F6',
                backgroundColor: 'rgba(59,130,246,0.12)',
                fill: true,
                tension: 0.4,
                pointRadius: 5,
                pointBackgroundColor: '#3B82F6',
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { color: cc.grid }, ticks: { color: cc.text } },
                y: {
                    grid: { color: cc.grid }, ticks: { color: cc.text, callback: v => v + '%' },
                    min: 0, max: 100
                }
            }
        }
    });

    // ── Sazonalidade Histórica ──
    // Meses típicos de turma recebem cor âmbar para destacar o padrão
    const TURMA_MONTHS = [4, 5, 9, 10]; // abr, mai, set, out
    const sazonPct = data.proj_2026.pct_historica;  // array[12] em %
    const sazonBg  = sazonPct.map((_, i) =>
        TURMA_MONTHS.includes(i + 1)
            ? 'rgba(245,158,11,0.80)'   // âmbar — mês típico de turma
            : 'rgba(37,99,235,0.70)'    // azul  — mês recorrente
    );
    if (sazonalidadeChart) sazonalidadeChart.destroy();
    sazonalidadeChart = new Chart(document.getElementById('sazonalidadeChart'), {
        type: 'bar',
        data: {
            labels: data.proj_2026.labels,
            datasets: [{
                label: '% do faturamento anual',
                data: sazonPct,
                backgroundColor: sazonBg,
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => ` ${ctx.raw.toFixed(1)}% do ano`
                    }
                }
            },
            scales: {
                x: { grid: { color: cc.grid }, ticks: { color: cc.text } },
                y: {
                    grid: { color: cc.grid },
                    ticks: { color: cc.text, callback: v => v + '%' },
                    beginAtZero: true
                }
            }
        }
    });

    // ── Projeção 2026 por Sazonalidade ──
    const p26 = data.proj_2026;
    const proj2026Subtitle = `Jan–${['','Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'][p26.meses_realizados]} realizados = ${formatCurrency(p26.total_realizado)} `
        + `(${p26.peso_realizado_pct}% do ano historicamente) → projeção total 2026: ${formatCurrency(p26.total_projetado_ano)}`;

    if (projecaoChart) projecaoChart.destroy();
    projecaoChart = new Chart(document.getElementById('projecaoChart'), {
        type: 'bar',
        data: {
            labels: p26.labels,
            datasets: [
                {
                    label: 'Realizado',
                    data: p26.realizado,
                    backgroundColor: 'rgba(37,99,235,0.85)',
                    borderRadius: 6,
                    order: 1,
                },
                {
                    label: 'Projeção por Sazonalidade',
                    data: p26.projetado,
                    backgroundColor: 'rgba(37,99,235,0.28)',
                    borderColor: 'rgba(37,99,235,0.55)',
                    borderWidth: 1,
                    borderRadius: 6,
                    order: 2,
                },
            ]
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: cc.text } },
                tooltip: {
                    callbacks: {
                        title: items => `${items[0].label}/2026`,
                        label: ctx => {
                            if (ctx.raw == null) return null;
                            const tag = ctx.datasetIndex === 1 ? ' (estimado)' : ' (realizado)';
                            return ` ${ctx.dataset.label}: ${formatCurrency(ctx.raw)}${tag}`;
                        },
                        afterBody: ([item]) => {
                            const pct = p26.pct_historica[item.dataIndex];
                            return [`Peso histórico: ${pct}% do faturamento anual`];
                        }
                    }
                },
                subtitle: {
                    display: true,
                    text: proj2026Subtitle,
                    color: cc.text,
                    font: { size: 11 },
                    padding: { bottom: 8 }
                }
            },
            scales: {
                x: { grid: { color: cc.grid }, ticks: { color: cc.text } },
                y: {
                    grid: { color: cc.grid },
                    ticks: { color: cc.text, callback: v => 'R$ ' + (v / 1000).toFixed(0) + 'k' },
                    beginAtZero: true
                }
            }
        }
    });

    // ── Tabela de Cohorts por Turma ──
    const cohortsTbody = document.getElementById('cohortsTbody');
    cohortsTbody.innerHTML = '';
    const cohortData = (data.cohorts_turma || []).slice().reverse(); // mais recente primeiro
    cohortData.forEach(c => {
        const ret = c.retencao_6;
        const retCell = ret != null
            ? `<span style="color:${ret >= 60 ? 'var(--success)' : ret >= 40 ? 'var(--text-main)' : 'var(--danger)'};font-weight:600">${ret}%</span>`
            : `<span style="color:var(--text-muted);font-size:0.8rem">Aguardando</span>`;

        // % de entrantes que viraram assinantes
        const assinPct = c.total > 0 ? ((c.assinantes / c.total) * 100).toFixed(0) : 0;
        // % de assinantes que ainda estão ativos
        const ativosPct = c.assinantes > 0 ? ((c.ativos / c.assinantes) * 100).toFixed(0) : 0;

        // Alunos sem assinatura formal (pagamento à vista ou dados parciais)
        const semAssin = c.total - c.assinantes;
        const entramTooltip = semAssin > 0
            ? `title="${c.assinantes} assinantes + ${semAssin} sem código de assinante"`
            : '';

        cohortsTbody.innerHTML += `<tr>
            <td><strong>${c.nome}</strong></td>
            <td>${c.abertura}</td>
            <td ${entramTooltip}>${c.total}${semAssin > 0 ? ` <span style="font-size:0.72rem;color:var(--text-muted)">(+${semAssin} s/ assin.)</span>` : ''}</td>
            <td>${c.assinantes} <span style="font-size:0.75rem;color:var(--text-muted)">(${assinPct}%)</span></td>
            <td>${c.ativos} <span style="font-size:0.75rem;color:var(--text-muted)">(${ativosPct}%)</span></td>
            <td>${retCell}</td>
            <td>${formatCurrency(c.receita_total)}</td>
        </tr>`;
    });

    // ── Projeções T9 / T10 ────────────────────────────────────────────────────
    const pt = data.proj_turmas || {};
    const t9 = pt.t9 || {};
    const t10 = pt.t10 || {};

    // Atualiza o span com o valor pré-4m real de T9
    const t9PreEl = document.getElementById('t9Pre');
    if (t9PreEl && t9.pre_4m_real != null) {
        t9PreEl.innerText = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(t9.pre_4m_real);
    }

    function renderScenarioCards(containerId, scenarios) {
        const el = document.getElementById(containerId);
        if (!el) return;
        el.innerHTML = '';
        const items = [
            { key: 'conservador', label: 'Conservador', color: 'var(--text-muted)', bg: '' },
            { key: 'realista',    label: 'Realista',    color: 'var(--secondary)',   bg: '' },
            { key: 'otimista',    label: 'Otimista',    color: 'var(--success)',     bg: '' },
        ];
        items.forEach(({ key, label, color }) => {
            const val = scenarios[key];
            if (val == null) return;
            el.innerHTML += `
                <div class="card">
                    <h3>${label}</h3>
                    <p class="val" style="color:${color};font-size:1.4rem">${formatCurrency(val)}</p>
                </div>`;
        });
    }

    renderScenarioCards('t9Cards', t9);
    renderScenarioCards('t10Cards', t10);

    // ── Tabela Histórico de Turmas ────────────────────────────────────────────
    const turmasTbody = document.getElementById('turmasTbody');
    if (turmasTbody) {
        turmasTbody.innerHTML = '';
        const hist = data.historico_turmas || [];
        hist.forEach(tm => {
            const isFuturo = tm.status === 'futuro';
            const ratioFmt = tm.ratio != null ? tm.ratio.toFixed(2) + 'x' : isFuturo ? '<em style="color:var(--secondary)">projetado</em>' : '—';
            const pre4mFmt = tm.pre_4m != null ? formatCurrency(tm.pre_4m) : isFuturo ? formatCurrency(data.proj_turmas?.t9?.pre_4m_real ?? 0) : '—';
            const receitaFmt = tm.receita != null ? formatCurrency(tm.receita) : isFuturo ? '<em style="color:var(--text-muted)">Não realizado</em>' : '—';
            const finalFmt = tm.final
                ? tm.final.replace(/^(\d{4})-(\d{2})$/, (_, y, m) => {
                    const meses = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
                    return `${meses[parseInt(m)-1]}/${y}`;
                  })
                : '—';
            const aberturaFmt = tm.abertura
                ? tm.abertura.replace(/^(\d{4})-(\d{2})$/, (_, y, m) => {
                    const meses = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
                    return `${meses[parseInt(m)-1]}/${y}`;
                  })
                : tm.abertura;
            const rowStyle = isFuturo ? 'opacity:0.65;font-style:italic' : '';
            turmasTbody.innerHTML += `<tr style="${rowStyle}">
                <td><strong>T${tm.id}</strong>${isFuturo ? ' <span style="font-size:0.7rem;color:var(--secondary);font-style:normal;font-weight:600">PROJETADA</span>' : ''}</td>
                <td>${aberturaFmt}</td>
                <td>${finalFmt}</td>
                <td>${receitaFmt}</td>
                <td>${pre4mFmt}</td>
                <td style="font-weight:600;color:${isFuturo ? 'var(--text-muted)' : 'var(--secondary)'}">${ratioFmt}</td>
            </tr>`;
        });
        if (hist.length === 0) {
            turmasTbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">Nenhuma turma com dados suficientes</td></tr>`;
        }
    }
}

// ── Inteligência de Alunos ────────────────────────────────────────────────────
async function loadClientes() {
    showLoading('Carregando Inteligência de Alunos...');
    try {
        const res  = await fetch('/api/clientes');
        const data = await res.json();
        if (!data.sucesso) throw new Error(data.erro || 'Erro');
        renderClientes(data);
        clientesLoaded = true;
    } catch(e) {
        console.error(e);
    } finally {
        hideLoading();
    }
}

function renderClientes(data) {
    const k = data.kpis;
    document.getElementById('valAlunosUnicos').innerText = k.total_alunos_unicos.toLocaleString('pt-BR');
    document.getElementById('valMultiTurma').innerText   = k.multi_turma.toLocaleString('pt-BR');
    document.getElementById('valTaxaRetorno').innerText  = `${(k.taxa_retorno * 100).toFixed(1)}% de taxa de retorno`;
    document.getElementById('valLtvMedio').innerText     = 'R$ ' + k.ltv_medio_top100.toLocaleString('pt-BR', {minimumFractionDigits:0, maximumFractionDigits:0});
    document.getElementById('valTresMaisTurmas').innerText = (k.tres_mais_turmas ?? 0).toLocaleString('pt-BR');

    // ── Cross-turma matrix ──────────────────────────────────────────────────
    const ct = data.cross_turma;
    if (ct && ct.destinations && ct.destinations.length) {
        // Header
        const thead = document.getElementById('crossTurmaHead');
        thead.innerHTML = '<tr><th>Origem \\ Destino</th>' +
            ct.destinations.map(d => `<th>${d}</th>`).join('') + '</tr>';

        // Max value for color scale
        const allVals = [];
        ct.rows.forEach(row => {
            ct.destinations.forEach(d => {
                const c = row.cells[d];
                if (c) allVals.push(typeof c === 'object' ? c.total : c);
            });
        });
        const maxVal = Math.max(...allVals, 1);

        const tbody = document.getElementById('crossTurmaTbody');
        tbody.innerHTML = ct.rows.map(row => {
            const cells = ct.destinations.map(d => {
                const c = row.cells[d];
                if (!c) return '<td style="color:#d1d5db">—</td>';
                if (typeof c === 'number') {
                    // "Novo"
                    const bg = `rgba(37,99,235,${(c/maxVal*0.6).toFixed(2)})`;
                    return `<td style="background:${bg};font-weight:500">${c}</td>`;
                }
                // transition object: {total, ativa, inativa}
                const bg = `rgba(37,99,235,${(c.total/maxVal*0.5).toFixed(2)})`;
                const tooltip = `Ativa: ${c.ativa} | Inativa: ${c.inativa}`;
                return `<td style="background:${bg}" title="${tooltip}">
                    <span style="font-weight:500">${c.total}</span>
                    <span style="font-size:10px;display:block;color:rgba(255,255,255,0.85)">
                        <span style="color:#60a5fa">${c.ativa}a</span> / <span style="color:#f87171">${c.inativa}i</span>
                    </span>
                </td>`;
            }).join('');
            return `<tr><td style="font-weight:600">${row.origem}</td>${cells}</tr>`;
        }).join('');

        // Totals row
        const totalsRow = '<tr style="font-weight:700;border-top:2px solid var(--border)">' +
            '<td>Total</td>' +
            ct.destinations.map(d => `<td>${ct.totals[d] || 0}</td>`).join('') +
            '</tr>';
        tbody.innerHTML += totalsRow;
    }

    // ── Top Renovadores ─────────────────────────────────────────────────────
    const renovTbody = document.getElementById('renovadoresTbody');
    renovTbody.innerHTML = data.top_renovadores.map((r, i) => {
        const badges = r.turmas.map(t =>
            `<span style="display:inline-block;background:#2563eb;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;margin:1px">${t}</span>`
        ).join('');
        const assinBadge = r.assinante
            ? `<span class="badge-assinante">Assinante</span>`
            : `<span class="badge-nao-assinante">Avulso</span>`;
        return `<tr data-rank="${i+1}">
            <td style="color:#6b7280">${i+1}</td>
            <td title="${r.email}">${r.nome}</td>
            <td>${badges}</td>
            <td style="text-align:center;font-weight:600" data-val="${r.n_turmas}">${r.n_turmas}</td>
            <td style="font-size:12px">${r.primeiro} – ${r.ultimo}</td>
            <td data-val="${r.total_pago}">R$ ${r.total_pago.toLocaleString('pt-BR', {minimumFractionDigits:0,maximumFractionDigits:0})}</td>
            <td>${assinBadge}</td>
        </tr>`;
    }).join('');
    initSortableTable('renovadoresTable');

    // ── LTV Ranking ─────────────────────────────────────────────────────────
    const ltvTbody = document.getElementById('ltvTbody');
    ltvTbody.innerHTML = data.ltv_ranking.map((r, i) => {
        const prods = r.produtos.map(p => {
            const short = p.length > 25 ? p.substring(0,22)+'…' : p;
            return `<span style="font-size:10px;background:var(--card-bg);border:1px solid var(--border);border-radius:3px;padding:1px 4px;margin:1px;display:inline-block">${short}</span>`;
        }).join('');
        return `<tr data-rank="${i+1}">
            <td style="color:#6b7280">${i+1}</td>
            <td title="${r.email}">${r.nome}</td>
            <td style="font-weight:600;color:#2563eb" data-val="${r.ltv}">R$ ${r.ltv.toLocaleString('pt-BR', {minimumFractionDigits:0,maximumFractionDigits:0})}</td>
            <td>${prods}</td>
            <td style="font-size:12px">${r.primeira_compra} – ${r.ultima_compra}</td>
        </tr>`;
    }).join('');
    initSortableTable('ltvTable');

    // ── Experience Top ──────────────────────────────────────────────────────
    const expTbody = document.getElementById('experienceTbody');
    if (data.experience_top && data.experience_top.length) {
        expTbody.innerHTML = data.experience_top.map((r, i) => {
            const evs = r.eventos.map(e => {
                const y = e.match(/\d{4}/)?.[0] || '';
                return `<span style="background:#f59e0b;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;margin:1px">${y || e.substring(0,10)}</span>`;
            }).join('');
            return `<tr data-rank="${i+1}">
                <td style="color:#6b7280">${i+1}</td>
                <td title="${r.email}">${r.nome}</td>
                <td data-val="${r.n_eventos}">${evs}</td>
                <td data-val="${r.total_pago}">R$ ${r.total_pago.toLocaleString('pt-BR', {minimumFractionDigits:0,maximumFractionDigits:0})}</td>
            </tr>`;
        }).join('');
        initSortableTable('experienceTable');
    } else {
        expTbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#9ca3af">Nenhum aluno participou de 2+ edições</td></tr>';
    }

    // ── LTV Médio Total com dropdown ─────────────────────────────────────────
    const ltvByProd = data.ltv_by_product || {};
    const ltvSelect = document.getElementById('ltvProdutoFilter');
    const valLtvTotal = document.getElementById('valLtvTotal');
    const lblLtvTotalClientes = document.getElementById('lblLtvTotalClientes');

    // Populate dropdown (keep "Todos" first, then alphabetical)
    ltvSelect.innerHTML = '';
    const prodNames = Object.keys(ltvByProd).sort((a, b) => {
        if (a === 'Todos') return -1;
        if (b === 'Todos') return 1;
        return a.localeCompare(b, 'pt-BR');
    });
    prodNames.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name === 'Todos' ? 'Todos os produtos' : name;
        ltvSelect.appendChild(opt);
    });

    function updateLtvCard(prod) {
        const entry = ltvByProd[prod];
        if (!entry) return;
        valLtvTotal.textContent = 'R$ ' + entry.ltv_medio.toLocaleString('pt-BR', {minimumFractionDigits: 0, maximumFractionDigits: 0});
        lblLtvTotalClientes.textContent = entry.n_clientes.toLocaleString('pt-BR') + ' clientes';
    }

    ltvSelect.value = 'Todos';
    updateLtvCard('Todos');
    ltvSelect.onchange = () => updateLtvCard(ltvSelect.value);
}

// Theme toggle
const btnTheme = document.getElementById('btnTheme');
function applyTheme(dark) {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    btnTheme.textContent = dark ? '☀️ Tema Claro' : '🌙 Tema Escuro';
    localStorage.setItem('theme', dark ? 'dark' : 'light');
}
btnTheme.addEventListener('click', () => {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    applyTheme(!isDark);
    loadAllData(dateStart.value, dateEnd.value);
});
const savedTheme = localStorage.getItem('theme');
if (savedTheme) applyTheme(savedTheme === 'dark');

// ── Histórico de Importações ─────────────────────────────────────────────────

const modalHistorico  = document.getElementById('modalHistorico');
const modalConfirm    = document.getElementById('modalConfirm');
let _pendingRevertId  = null;
let _uploadsCache     = null;   // {data, ts}
const _UPLOADS_TTL    = 30000;  // 30s

document.getElementById('btnHistorico').addEventListener('click', () => {
    modalHistorico.style.display = 'flex';
    loadUploadHistory();
});
document.getElementById('btnFecharHistorico').addEventListener('click', () => {
    modalHistorico.style.display = 'none';
});
modalHistorico.addEventListener('click', e => {
    if (e.target === modalHistorico) modalHistorico.style.display = 'none';
});

document.getElementById('btnConfirmCancelar').addEventListener('click', () => {
    modalConfirm.style.display = 'none';
    _pendingRevertId = null;
});
modalConfirm.addEventListener('click', e => {
    if (e.target === modalConfirm) { modalConfirm.style.display = 'none'; _pendingRevertId = null; }
});

document.getElementById('btnConfirmReverter').addEventListener('click', async () => {
    if (!_pendingRevertId) return;
    const id = _pendingRevertId;
    _pendingRevertId = null;
    modalConfirm.style.display = 'none';

    showLoading('Revertendo importação...');
    try {
        const res  = await fetch(`/api/uploads/${id}/reverter`, { method: 'DELETE' });
        const data = await res.json();
        if (!data.sucesso) throw new Error(data.erro || 'Erro ao reverter');
        alert(`Importação revertida com sucesso!\n${data.deletadas} transações removidas.`);
        // Recarrega dados e histórico
        _uploadsCache = null;   // força recarregar após rollback
        loadAllData(dateStart.value, dateEnd.value);
        loadUploadHistory();
        clientesLoaded = false;
    } catch(e) {
        alert('Erro ao reverter: ' + e.message);
    } finally {
        hideLoading();
    }
});

async function loadUploadHistory() {
    const loading = document.getElementById('historicoLoading');
    const table   = document.getElementById('historicoTable');
    const vazio   = document.getElementById('historicoVazio');

    // Usa cache se ainda válido
    if (_uploadsCache && (Date.now() - _uploadsCache.ts) < _UPLOADS_TTL) {
        renderUploadHistory(_uploadsCache.data);
        return;
    }

    loading.style.display = 'block';
    table.style.display   = 'none';
    vazio.style.display   = 'none';

    try {
        const res  = await fetch('/api/uploads');
        const data = await res.json();
        _uploadsCache = { data, ts: Date.now() };
        if (!data.sucesso) throw new Error(data.erro);
        renderUploadHistory(data);
    } catch(e) {
        loading.textContent = 'Erro ao carregar histórico.';
        console.error(e);
    }
}

function renderUploadHistory(data) {
    const loading = document.getElementById('historicoLoading');
    const table   = document.getElementById('historicoTable');
    const vazio   = document.getElementById('historicoVazio');
    loading.style.display = 'none';

    if (!data.uploads || !data.uploads.length) {
        vazio.style.display = 'block';
        return;
    }

    const tbody = document.getElementById('historicotTbody');
    tbody.innerHTML = data.uploads.map(u => {
        const dt = new Date(u.criado_em).toLocaleString('pt-BR', {
            day:'2-digit', month:'2-digit', year:'numeric',
            hour:'2-digit', minute:'2-digit'
        });
        const isRevertido = u.arquivo.startsWith('[REVERTIDO]');
        const nomeArquivo = u.arquivo.replace('[REVERTIDO] ', '');
        const statusBadge = isRevertido
            ? `<span style="font-size:10px;background:var(--bg-color);color:var(--text-muted);border-radius:4px;padding:1px 6px;margin-left:6px">revertido</span>`
            : '';
        const btnReverter = (!isRevertido && u.revertivel)
            ? `<button onclick="confirmarRevert(${u.id},'${nomeArquivo.replace(/'/g,"\\'")}',${u.linhas})"
                 style="padding:5px 14px;border-radius:6px;border:none;background:#dc2626;color:#fff;cursor:pointer;font-size:12px;font-weight:600">
                 Reverter
               </button>`
            : `<span style="font-size:11px;color:var(--text-muted)">${isRevertido ? '—' : 'sem chaves'}</span>`;
        return `<tr style="${isRevertido ? 'opacity:0.45' : ''}">
            <td style="color:var(--text-muted);font-size:12px">#${u.id}</td>
            <td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${nomeArquivo}">
                ${nomeArquivo}${statusBadge}
            </td>
            <td style="font-size:12px">${u.usuario}</td>
            <td style="text-align:center;font-weight:600">${(u.linhas||0).toLocaleString('pt-BR')}</td>
            <td style="font-size:12px;white-space:nowrap">${dt}</td>
            <td style="text-align:center">${btnReverter}</td>
        </tr>`;
    }).join('');

    table.style.display = 'table';
}

function confirmarRevert(id, arquivo, linhas) {
    _pendingRevertId = id;
    document.getElementById('confirmArquivo').textContent =
        `${arquivo}  —  ${linhas.toLocaleString('pt-BR')} linhas`;
    modalConfirm.style.display = 'flex';
}

// Init
checkAuth().then(user => { if (user) loadAllData(); });

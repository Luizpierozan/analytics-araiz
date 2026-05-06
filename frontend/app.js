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

const navGeral = document.getElementById('nav-geral');
const navInad = document.getElementById('nav-inadimplencia');
const pageGeral = document.getElementById('page-geral');
const pageInad = document.getElementById('page-inadimplencia');
const fileInput = document.getElementById('fileInput');
const btnUpload = document.getElementById('btnUpload');
const loading = document.getElementById('loading');
const loadingLabel = document.getElementById('loadingLabel');

function showLoading(msg = 'Carregando') {
    loadingLabel.innerText = msg;
    loading.classList.remove('hidden', 'fading');
}
function hideLoading() {
    loading.classList.add('fading');
    setTimeout(() => loading.classList.add('hidden'), 420);
}

let receitaChart = null;
let agingChart = null;
let produtoChart = null;
let evolucaoChart = null;

// Routing
navGeral.addEventListener('click', (e) => {
    e.preventDefault();
    navGeral.classList.add('active'); navInad.classList.remove('active');
    pageGeral.classList.remove('hidden'); pageInad.classList.add('hidden');
});
navInad.addEventListener('click', (e) => {
    e.preventDefault();
    navInad.classList.add('active'); navGeral.classList.remove('active');
    pageInad.classList.remove('hidden'); pageGeral.classList.add('hidden');
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
    // 2024 to today
    const d = new Date();
    const start = '2024-01-01';
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
    document.getElementById('valDevedores').innerText = data.geral.total_inadimplentes;
    document.getElementById('valTaxaInad').innerText = `${data.geral.taxa_inadimplencia.toFixed(1)}%`;

    // Aging Chart
    const ctx = document.getElementById('agingChart').getContext('2d');
    if(agingChart) agingChart.destroy();
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

    // Tabela
    const tbody = document.getElementById('devedoresTbody');
    tbody.innerHTML = '';
    data.lista.forEach(d => {
        tbody.innerHTML += `<tr>
            <td><span class="badge-danger">${d.dias} dias</span></td>
            <td><strong>${d.nome}</strong><br><span style="font-size:11px;color:var(--text-muted)">${d.email}</span></td>
            <td>${d.telefone || 'N/A'}</td>
            <td>${d.produto}</td>
            <td>${formatCurrency(d.valor)}</td>
        </tr>`;
    });
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

// Init
checkAuth().then(user => { if (user) loadAllData(); });

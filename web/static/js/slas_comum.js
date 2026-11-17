/* SLAs (27/10/2026) — o que as duas telas dividem: menu lateral, avisos,
   chamadas ao servidor, formatação brasileira e a escolha de CD/empresa. */
const TODOS_MODULOS = [
  {id:'monitoramento', label:'📦 Abastecimento CD', href:'/'},
  {id:'adensamento',   label:'🧊 Adensamento',      href:'/adensamento'},
  {id:'armazem',       label:'🏭 Painel do Armazém', href:'/armazem'},
  {id:'xsense',        label:'🌡️ Xsense (Câmaras)', href:'/xsense'},
  {id:'fifo',          label:'📦 Devolução & Vencimento', href:'/fifo'},
  {id:'mapa_estoque',  label:'🗺️ Mapa de Estoque', href:'/mapa-estoque'},
  {id:'giro',          label:'🔄 Giro de Estoque', href:'/giro'},
  {id:'produtividade', label:'⏱️ Produtividade', href:'/produtividade'},
  {id:'expedito',      label:'🧭 Expedito', href:'/expedito'},
  {id:'slas', label:'📋 SLAs', href:'/slas', filhos:[{label:'Acompanhamento', href:'/slas'},{label:'Termo de Avença', href:'/slas/termos'}]},
  {id:'entregas', label:'🚚 Entregas', href:'/entregas'},
  {id:'validacao', label:'🧪 Ferramenta de validação', href:'/validacao', filhos:[{label:'Picking', href:'/validacao'},{label:'Adensamento', href:'/validacao/adensamento'}]},
];
function abrirSidebar(){document.getElementById('sidebar').classList.add('show');document.getElementById('sidebar-overlay').classList.add('show');}
function fecharSidebar(){document.getElementById('sidebar').classList.remove('show');document.getElementById('sidebar-overlay').classList.remove('show');}
function montarSidebar(modulosPermitidos, ativoId){
  const permitidos = Array.isArray(modulosPermitidos) ? modulosPermitidos : [];
  const nav = document.getElementById('sidebar-nav');
  nav.innerHTML = TODOS_MODULOS
    .filter(m=>!permitidos.length || permitidos.includes(m.id))
    .map(m=>!m.filhos
      ? `<a class="sidebar-link${m.id===ativoId?' active':''}" href="${m.href}">${m.label}</a>`
      : `<button class="sidebar-link sidebar-grupo${m.id===ativoId?' active':''}" type="button"
           style="width:100%;border:none;background:${m.id===ativoId?'':'none'};font:inherit;cursor:pointer;text-align:left"
           onclick="const s=this.nextElementSibling;s.style.display=s.style.display==='none'?'flex':'none'">${m.label}<span style="margin-left:auto;font-size:11px;opacity:.6">▾</span></button>
         <div style="display:${m.id===ativoId?'flex':'none'};flex-direction:column;gap:2px;padding:0 0 4px 22px">
           ${m.filhos.map(f=>`<a class="sidebar-link" style="padding:7px 12px;font-size:12.5px${
             location.pathname===f.href?';font-weight:700;color:var(--accent)':''}" href="${f.href}">${f.label}</a>`).join('')}
         </div>`)
    .join('');
}
function toggleSidebarConfig(){
  document.getElementById('sidebar-config-sub').classList.toggle('hidden');
  document.getElementById('sidebar-config-toggle').classList.toggle('open');
}
function montarConfigSidebar(perfil, isMaster){
  const admin = perfil==='admin' || perfil==='master' || isMaster;
  document.querySelectorAll('.sidebar-sublink-admin').forEach(el=>el.classList.toggle('hidden', !admin));
}
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ fecharSidebar(); document.querySelectorAll('.modal-bg.show').forEach(m=>m.classList.remove('show')); } });

function abrirModal(id){ document.getElementById(id).classList.add('show'); }
function fecharModal(id){ document.getElementById(id).classList.remove('show'); }
function esc(s){ return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function toast(msg, tipo, duracao){
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  t.style.background = tipo==='err' ? '#b91c1c' : '#1a1a2e';
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(()=>{ t.style.display='none'; }, duracao || 3200);
}

let _csrf = '';
/* resposta do servidor: JSON ou o motivo da falha (sessão, rede, erro interno) */
async function api(url, corpo){
  let r;
  try{
    r = await fetch(url, corpo === undefined ? {} : {method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':_csrf}, body: JSON.stringify(corpo)});
  }catch(e){ throw new Error('sem conexão com o servidor'); }
  let d = null;
  try{ d = await r.json(); }catch(e){ d = null; }
  if(!r.ok || !d || d.error){
    throw new Error((d && d.error) || (r.status === 401 ? 'sessão expirada — entre de novo' : `HTTP ${r.status}`));
  }
  return d;
}

/* ── formatação ── */
const MESES_ABREV = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const MESES_NOME = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
function num(v, casas){ return Number(v).toLocaleString('pt-BR', {minimumFractionDigits: casas ?? 0, maximumFractionDigits: casas ?? 0}); }
function pct(v, casas){ return v == null ? '—' : num(v * 100, casas ?? 1) + '%'; }
/* meta: as casas que ela tem (97% / 97,5% / 99,95%) */
function pctMeta(v){ return v == null ? '—' : Number(v * 100).toLocaleString('pt-BR', {maximumFractionDigits: 2}) + '%'; }
function brl(v){ return v == null ? '—' : 'R$ ' + num(v, 2); }
function hhmm(min){
  if(min == null) return '—';
  const m = Math.round(min);
  return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}
function dataBr(iso){ const m = String(iso||'').match(/^(\d{4})-(\d{2})-(\d{2})/); return m ? `${m[3]}/${m[2]}/${m[1]}` : (iso||''); }
function horaDe(ts){ const m = String(ts||'').match(/(\d{2}):(\d{2})/); return m ? m[1]+':'+m[2] : '—'; }
/* "1.234,56" / "1234,56" / "1234.56" / "1.500" (mil e quinhentos) / "21,221-" -> número.
   Mesma regra de termo_avenca.numero_br_para_float: a grade e o servidor dão o mesmo valor. */
function lerNumero(t){
  let s = String(t ?? '').trim().replace(/R\$|\s/g, '');
  if(!s) return null;
  const neg = /^-|-$|^\(.*\)$/.test(s);
  s = s.replace(/[-()]/g, '');
  if(s.includes(',') && s.includes('.')) s = s.lastIndexOf(',') > s.lastIndexOf('.') ? s.replace(/\./g,'').replace(',', '.') : s.replace(/,/g, '');
  else if(s.includes(',')) s = s.replace(/\./g,'').replace(',', '.');
  else if(/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');   // ponto de milhar, padrão brasileiro
  const v = Number(s);
  return Number.isFinite(v) ? (neg ? -v : v) : null;
}
function lerHora(t){
  const m = String(t||'').trim().match(/^(\d{1,3}):(\d{2})$/);
  if(m) return Number(m[1]) * 60 + Number(m[2]);
  const v = lerNumero(t);
  return v == null ? null : v;   // número puro = minutos
}

/* ── CD e empresa ── */
let _cds = [], _cd = '', _emp = '', _podeEditar = false;
async function carregarCDs(aoTrocar){
  const d = await api('/api/slas/cds');
  _cds = d.cds || [];
  const sel = document.getElementById('sel-cd');
  const salvo = (()=>{ try{ return localStorage.getItem('slas_cd') || ''; }catch(e){ return ''; } })();
  sel.innerHTML = _cds.map(c=>`<option value="${esc(c.cd)}">${esc(c.cd)}</option>`).join('');
  _cd = _cds.some(c=>c.cd === salvo) ? salvo : (_cds.some(c=>c.cd === 'CDA') ? 'CDA' : (_cds[0] || {}).cd || '');
  sel.value = _cd;
  _podeEditar = podeEditarCD();
  sel.onchange = ()=>{ _cd = sel.value; _emp = ''; _podeEditar = podeEditarCD();
    try{ localStorage.setItem('slas_cd', _cd); }catch(e){}
    montarEmpresas(); aoTrocar(); };
  montarEmpresas(aoTrocar);
}
/* grava no CD do próprio cadastro; CD de cobertura (liberado para ver) é consulta */
function podeEditarCD(){ return !!((_cds.find(c=>c.cd === _cd) || {}).pode_editar); }
/* a tela do Termo usa 'empresas_termo' (empresas do CD + consolidadoras, ex.: CDC) */
function empresasDoCD(){ const c = _cds.find(c=>c.cd === _cd) || {}; return c[window.CAMPO_EMPRESAS || 'empresas'] || c.empresas || []; }
function montarEmpresas(aoTrocar){
  const box = document.getElementById('seg-emp');
  if(!box) return;
  if(aoTrocar) box._aoTrocar = aoTrocar;
  const emps = empresasDoCD();
  const opcoes = emps.length > 1 ? [['', 'Consolidado']].concat(emps.map(e=>[e, e])) : emps.map(e=>[e, e]);
  if(emps.length <= 1) _emp = emps[0] || '';
  box.innerHTML = opcoes.map(([v, r])=>`<button type="button" data-v="${esc(v)}" class="${v===_emp?'on':''}">${esc(r)}</button>`).join('');
  box.querySelectorAll('button').forEach(b=>b.onclick = ()=>{ _emp = b.dataset.v;
    box.querySelectorAll('button').forEach(x=>x.classList.toggle('on', x === b)); box._aoTrocar && box._aoTrocar(); });
}
function qsCD(extra){ return '?cd=' + encodeURIComponent(_cd) + (_emp ? '&empresa=' + encodeURIComponent(_emp) : '') + (extra || ''); }

async function iniciarComum(ativo){
  const me = await fetch('/api/me').then(r=>r.json()).catch(()=>({}));
  if(!me.user){ location.href = '/login'; return null; }
  _csrf = me.csrf_token || '';
  montarSidebar(me.modulos_permitidos, ativo);
  montarConfigSidebar(me.perfil, me.is_master);
  const sb = document.querySelector('.sidebar-title');
  if(sb) sb.textContent = me.cd_nome_exibicao || 'CDA';
  return me;
}

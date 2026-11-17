'use strict';

// Retaguarda do Separação Redespacho. Sem framework e sem build: o backend
// serve estes três arquivos e atualizar é reiniciar o serviço.
// Ver docs/TELAS-RETAGUARDA.md.

const API = '/api/v1';
const $ = (s) => document.querySelector(s);

let token = sessionStorage.getItem('token') || null;
let usuario = JSON.parse(sessionStorage.getItem('usuario') || 'null');
let instrucaoAtual = null;
let itemEmFoco = null;
let relogioAtualizacao = null;

/** Perfil CONSULTA só enxerga — o servidor recusa qualquer escrita dele. */
const soLeitura = () => usuario && usuario.perfil === 'CONSULTA';

// ------------------------------------------------------------------ infra ---

async function api(caminho, opcoes = {}) {
  const cfg = { ...opcoes, headers: { ...(opcoes.headers || {}) } };
  if (token) cfg.headers['Authorization'] = 'Bearer ' + token;
  if (cfg.corpo !== undefined) {
    cfg.headers['Content-Type'] = 'application/json';
    cfg.body = JSON.stringify(cfg.corpo);
    delete cfg.corpo;
  }

  let r;
  try {
    r = await fetch(API + caminho, cfg);
  } catch {
    throw { codigo: 'SEM_REDE', mensagem: 'Sem resposta do servidor.' };
  }
  if (r.status === 204) return null;

  const corpo = await r.json().catch(() => null);
  if (!r.ok) {
    const e = (corpo && corpo.erro) || {};
    if (e.codigo === 'SESSAO_EXPIRADA') encerrarSessao();
    throw {
      codigo: e.codigo || 'ERRO_' + r.status,
      mensagem: e.mensagem || 'Falha na operação.',
      detalhe: e.detalhe || null,
    };
  }
  return corpo;
}

/**
 * Limite de espera para passar em `signal`. O fetch não tem prazo próprio:
 * conexão travada deixaria a tela esperando para sempre. O abort cai no catch
 * de api() como SEM_REDE. AbortSignal.timeout falta em navegador antigo.
 */
function limiteDeTempo(ms) {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(ms);
  const c = new AbortController();
  setTimeout(() => c.abort(), ms);
  return c.signal;
}

/**
 * Baixa um arquivo da API. Link direto não leva o token no cabeçalho, então
 * vem por fetch, vira blob e sai por um <a download>.
 */
async function baixarArquivo(caminho, nome) {
  try {
    const r = await fetch(API + caminho, { headers: { Authorization: 'Bearer ' + token } });
    if (!r.ok) {
      const e = ((await r.json().catch(() => null)) || {}).erro || {};
      if (e.codigo === 'SESSAO_EXPIRADA') encerrarSessao();
      avisar(e.mensagem || 'Não foi possível baixar o arquivo', true);
      return;
    }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revogar na hora pode cortar o download em alguns navegadores.
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch {
    avisar('Sem resposta do servidor — o arquivo não foi baixado', true);
  }
}

let timerAviso = null;
function avisar(texto, ruim = false) {
  const el = $('#aviso');
  el.textContent = texto;
  el.classList.toggle('ruim', ruim);
  el.hidden = false;
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => (el.hidden = true), 4000);
}

const escapar = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Número de NF para comparar: "000882463" e "952463" são a mesma nota. */
const semZeros = (s) => String(s ?? '').trim().replace(/^0+(?=\d)/, '');

const marcaStatus = (s) => {
  const classe = { ABERTA: 'aberta', PENDENTE: 'aberta', EM_SEPARACAO: 'separacao',
                   CONCLUIDA: 'concluida', EXPEDIDA: 'expedida',
                   RETORNO_PARCIAL: 'separacao',
                   ABERTO: 'aberta', AGUARDANDO_CONFERENCIA: 'aberta',
                   EM_CONFERENCIA: 'separacao', CONFERIDA: 'concluida',
                   FECHADO: 'concluida', FECHADO_DIVERGENCIA: 'divergente',
                   BAIXADA: 'expedida',
                   // Retorno e seus pallets. Falta de caixa é âmbar: pede
                   // atenção, mas já veio justificada do coletor.
                   AGUARDANDO_TRATATIVA: 'separacao', TRATADO: 'concluida', CANCELADO: 'aberta',
                   CONFERIDO: 'concluida', CONFERIDO_FALTA: 'separacao' }[s] || 'aberta';
  return `<span class="marca ${classe}">${escapar(String(s).replace(/_/g, ' '))}</span>`;
};

const dataCurta = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
};

// ------------------------------------------------------------------ sessão ---

function encerrarSessao() {
  token = null; usuario = null;
  sessionStorage.clear();
  clearInterval(relogioAtualizacao);
  $('#app').hidden = true;
  $('#tela-login').hidden = false;
}

function abrirApp() {
  $('#tela-login').hidden = true;
  $('#app').hidden = false;
  $('#quem').textContent = `${usuario.nome} · ${usuario.perfil}`;
  $('#aba-usuarios').hidden = usuario.perfil !== 'ADMIN';
  $('#aba-impressoras').hidden = usuario.perfil === 'SEPARADOR' || soLeitura();
  $('#aba-aparelhos').hidden = usuario.perfil !== 'ADMIN';
  $('#aba-enderecos').hidden = usuario.perfil !== 'SUPERVISOR' && usuario.perfil !== 'ADMIN';
  // Some tudo que grava: o servidor já recusaria, mas botão que só dá erro
  // é pior do que botão que não existe.
  document.body.classList.toggle('so-leitura', soLeitura());
  atualizarSeloPendentes();
  verificarAvisosDaSubida();
  verInstrucoes();
}

let avisosAgendados = false;

async function verificarAvisosDaSubida() {
  // De tempos em tempos também: o backup pode parar de chegar à rede com a
  // retaguarda já aberta.
  if (!avisosAgendados) {
    avisosAgendados = true;
    setInterval(() => { if (usuario) verificarAvisosDaSubida(); }, 5 * 60 * 1000);
  }
  const s = await api('/saude').catch(() => null);
  // Banco restaurado do backup, bifurcação, backup sem chegar à rede: para
  // todos, porque quem abre a retaguarda de manhã precisa saber.
  const avisos = [s && s.restauracao, s && s.backup].filter(Boolean);
  const faixa = $('#alerta-restauracao');
  faixa.textContent = avisos.join(' — ');
  faixa.hidden = !avisos.length;
  if (usuario.perfil !== 'ADMIN') return;
  $('#alerta-senha').hidden = !(s && s.senhaPadraoPendente);
}

/**
 * Uma seção visível por vez. Antes cada tela listava à mão o que esconder e as
 * listas foram ficando desatualizadas — tela nova aparecia empilhada na antiga.
 */
function mostrarSecao(id) {
  document.querySelectorAll('main > section').forEach((sec) => {
    sec.hidden = sec.id !== id;
  });
}

function trocarAba(nome) {
  document.querySelectorAll('.aba').forEach((b) => b.classList.toggle('ativa', b.dataset.ir === nome));
}

$('#form-login').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const erro = $('#erro-login');
  erro.hidden = true;
  try {
    const r = await api('/auth/login', {
      method: 'POST',
      corpo: { login: $('#login').value.trim(), senha: $('#senha').value },
    });
    token = r.token; usuario = r.usuario;
    sessionStorage.setItem('token', token);
    sessionStorage.setItem('usuario', JSON.stringify(usuario));
    abrirApp();
  } catch (e) {
    erro.textContent = e.mensagem;
    erro.hidden = false;
  }
});

$('#sair').addEventListener('click', async () => {
  await api('/auth/logout', { method: 'POST' }).catch(() => {});
  encerrarSessao();
});

// ------------------------------------------------------------- instruções ---

async function verInstrucoes() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-instrucoes');

  // A lista também é tela viva: o progresso muda enquanto o supervisor olha.
  // Armado antes do await: depois, sobrescreveria o relógio de outra aba
  // aberta enquanto a lista carregava, e aquele nunca mais pararia.
  relogioAtualizacao = setInterval(() => {
    if (!$('#tela-instrucoes').hidden && !document.querySelector('dialog[open]')) desenharInstrucoes();
  }, 5000);
  await desenharInstrucoes();
}

async function desenharInstrucoes() {

  const todas = $('#mostrar-concluidas').checked;
  const status = todas ? 'ABERTA,EM_SEPARACAO,CONCLUIDA,CANCELADA' : 'ABERTA,EM_SEPARACAO';
  const lista = await api('/instrucoes?status=' + status).catch((e) => { avisar(e.mensagem, true); return []; });

  $('#vazio-instrucoes').hidden = lista.length > 0;
  $('#corpo-instrucoes').innerHTML = lista.map((i) => {
    const pct = i.qtdCaixasPrevistas ? Math.round((i.qtdCaixasSeparadas / i.qtdCaixasPrevistas) * 100) : 0;
    return `<tr class="clicavel" data-id="${i.id}">
      <td><strong>${escapar(i.numero)}</strong></td>
      <td>${marcaStatus(i.status)}</td>
      <td class="num">${i.qtdNotas}</td>
      <td>
        <div class="barra-progresso"><i style="width:${pct}%"></i></div>
        <small class="num">${i.qtdCaixasSeparadas}/${i.qtdCaixasPrevistas} caixas</small>
      </td>
      <td>${dataCurta(i.criadoEm)}</td>
      <td>${escapar(i.criadoPor)}</td>
    </tr>`;
  }).join('');

  $('#corpo-instrucoes').querySelectorAll('tr').forEach((tr) =>
    tr.addEventListener('click', () => abrirInstrucao(Number(tr.dataset.id))));
}

$('#mostrar-concluidas').addEventListener('change', verInstrucoes);
$('#voltar-instrucoes').addEventListener('click', verInstrucoes);
$('[data-ir="instrucoes"]').addEventListener('click', () => { trocarAba('instrucoes'); verInstrucoes(); });
$('[data-ir="usuarios"]').addEventListener('click', () => { trocarAba('usuarios'); verUsuarios(); });
$('[data-ir="impressoras"]').addEventListener('click', () => { trocarAba('impressoras'); verImpressoras(); });
$('[data-ir="enderecamento"]').addEventListener('click', () => { trocarAba('enderecamento'); verEnderecamento(); });
$('[data-ir="enderecos"]').addEventListener('click', () => { trocarAba('enderecos'); verEnderecos(); });
$('[data-ir="produtos"]').addEventListener('click', () => { trocarAba('produtos'); verProdutos(); });
$('[data-ir="expedicao"]').addEventListener('click', () => { trocarAba('expedicao'); verExpedicao(); });
$('[data-ir="estoque"]').addEventListener('click', () => { trocarAba('estoque'); verEstoque(); });
$('[data-ir="devolucoes"]').addEventListener('click', () => { trocarAba('devolucoes'); verDevolucoes(); });
$('[data-ir="relatorios"]').addEventListener('click', () => { trocarAba('relatorios'); verRelatorios(); });
$('[data-ir="aparelhos"]').addEventListener('click', () => { trocarAba('aparelhos'); verAparelhos(); });

$('#nova-instrucao').addEventListener('click', async () => {
  try {
    const i = await api('/instrucoes', { method: 'POST' });
    avisar(`Instrução ${i.numero} criada`);
    abrirInstrucao(i.id);
  } catch (e) { avisar(e.mensagem, true); }
});

// -------------------------------------------------------- uma instrução ---

async function abrirInstrucao(id) {
  mostrarSecao('tela-instrucao');
  instrucaoAtual = id;

  // Tela viva: a separação está acontecendo enquanto o supervisor olha.
  // Armado antes do await, pelo mesmo motivo da lista de instruções.
  clearInterval(relogioAtualizacao);
  relogioAtualizacao = setInterval(() => {
    if (!$('#tela-instrucao').hidden && !document.querySelector('dialog[open]')) desenharInstrucao();
  }, 5000);
  await desenharInstrucao();
}

async function desenharInstrucao() {
  let instrucao, notas;
  try {
    [instrucao, notas] = await Promise.all([
      api('/instrucoes/' + instrucaoAtual),
      api(`/instrucoes/${instrucaoAtual}/notas`),
    ]);
  } catch (e) { avisar(e.mensagem, true); return; }

  $('#titulo-instrucao').innerHTML = `${escapar(instrucao.numero)} ${marcaStatus(instrucao.status)}`;
  $('#resumo-instrucao').textContent =
    `${instrucao.qtdNotas} nota(s) · ${instrucao.qtdCaixasSeparadas}/${instrucao.qtdCaixasPrevistas} caixas separadas`;

  if (!notas.length) {
    $('#notas').innerHTML = '<p class="vazio">Nenhuma nota. Use “Importar XMLs”.</p>';
    return;
  }

  // Mantém abertas as notas que o supervisor já tinha expandido.
  const abertas = new Set([...document.querySelectorAll('.nota[open]')].map((d) => d.dataset.id));

  const blocos = await Promise.all(notas.map(async (n) => {
    const itens = await api(`/notas/${n.id}/itens`).catch(() => []);
    return `<details class="nota" data-id="${n.id}" ${abertas.has(String(n.id)) ? 'open' : ''}>
      <summary>
        NF ${escapar(n.numero)} <span class="cliente">${escapar(n.cliente)}</span>
        ${marcaStatus(n.status)}
        <span class="contagem">${n.qtdSeparada}/${n.qtdPrevista}</span>
        ${podeDesembarcar(n) ? `<button class="texto" data-desembarcar="${n.id}"
                data-numero="${escapar(n.numero)}">Desembarcar</button>` : ''}
        ${soLeitura() ? '' : `<button class="texto excluir" data-excluir-nota="${n.id}"
                data-numero="${escapar(n.numero)}" title="Excluir NF">✕</button>`}
      </summary>
      <table class="itens">
        <thead><tr><th></th><th>Produto</th><th>Qtd</th><th></th></tr></thead>
        <tbody>${itens.map(linhaItem).join('')}</tbody>
      </table>
    </details>`;
  }));
  $('#notas').innerHTML = blocos.join('');
  ligarAcoesDeItem();
  document.querySelectorAll('[data-excluir-nota]').forEach((b) =>
    b.addEventListener('click', (ev) => {
      ev.preventDefault();   // não deixa o clique abrir/fechar o <details>
      ev.stopPropagation();
      excluirNota(b.dataset.excluirNota, b.dataset.numero);
    }));
  document.querySelectorAll('[data-desembarcar]').forEach((b) =>
    b.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      abrirDesembarque(Number(b.dataset.desembarcar), b.dataset.numero);
    }));
}

function linhaItem(i) {
  const fechado = i.status === 'FECHADO' || i.status === 'FECHADO_DIVERGENCIA';
  const cadeado = i.status === 'FECHADO_DIVERGENCIA' ? '🔒⚠' : fechado ? '🔒' : '🔓';
  // Item completo fecha normal. Antes a única ação era "fechar com divergência",
  // que carimbava FECHADO_DIVERGENCIA mesmo com tudo coletado — e daí a
  // instrução só concluía pelo "concluir mesmo assim", como se houvesse falta.
  const completo = i.qtdSeparada >= i.qtdPrevista;
  const acoes = fechado
    ? (soLeitura() ? '' : `<button class="texto" data-reabrir="${i.id}">Reabrir</button>`)
    : soLeitura() ? ''
    : (completo
        ? `<button class="texto" data-fechar-item="${i.id}">Fechar</button>`
        : `<button class="texto" data-divergencia="${i.id}"
             data-rotulo="${escapar(i.produtoCodigo)} — ${i.qtdSeparada} de ${i.qtdPrevista} caixas">
             Fechar com divergência</button>`);
  // Excluir mora aqui, junto do item, porque é olhando o item que se descobre
  // a caixa a mais. Antes só existia escondido na aba Endereçamento, atrás de
  // digitar o código do pallet — e ninguém achava.
  const excluir = !soLeitura() && i.qtdSeparada > 0
    ? `<button class="texto excluir" data-excluir-item="${i.id}"
         data-rotulo="${escapar(i.produtoCodigo)} ${escapar(i.produtoDescricao)} — ${i.qtdSeparada} caixa(s)">
         Excluir caixas</button>`
    : '';

  return `<tr class="${fechado ? 'fechado' : ''}">
    <td class="cadeado">${cadeado}</td>
    <td>
      <strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}
      ${i.separadores && i.separadores.length
        ? `<div class="separando">● separando agora: ${escapar(i.separadores.join(', '))}</div>` : ''}
      ${i.justificativa
        ? `<div class="justificativa">“${escapar(i.justificativa)}”${
            i.fechadoPor ? ` — ${escapar(i.fechadoPor)}` : ''}</div>` : ''}
    </td>
    <td class="qtd ${i.qtdSeparada >= i.qtdPrevista ? 'ok' : 'pendente'}">${i.qtdSeparada}/${i.qtdPrevista}</td>
    <td class="acoes">${acoes} ${excluir}</td>
  </tr>`;
}

function ligarAcoesDeItem() {
  document.querySelectorAll('[data-reabrir]').forEach((b) =>
    b.addEventListener('click', async () => {
      try {
        await api(`/itens/${b.dataset.reabrir}/reabrir`, { method: 'POST' });
        avisar('Item reaberto');
        desenharInstrucao();
      } catch (e) { avisar(e.mensagem, true); }
    }));

  document.querySelectorAll('[data-fechar-item]').forEach((b) =>
    b.addEventListener('click', async () => {
      try {
        await api(`/itens/${b.dataset.fecharItem}/fechar`, { method: 'POST' });
        avisar('Item fechado');
        desenharInstrucao();
      } catch (e) { avisar(e.mensagem, true); }
    }));

  document.querySelectorAll('[data-excluir-item]').forEach((b) =>
    b.addEventListener('click', async () => {
      let pallets;
      try {
        pallets = await api(`/itens/${b.dataset.excluirItem}/pallets`);
      } catch (e) { avisar(e.mensagem, true); return; }

      // Pallet expedido não volta atrás por exclusão: aparece, mas travado,
      // para o supervisor entender por que a conta não fecha com a tela.
      const vivos = pallets.filter((p) => p.status !== 'EXPEDIDO');
      if (!vivos.length) {
        avisar('As caixas deste item já foram expedidas — não há o que excluir.', true);
        return;
      }
      exclusaoItem = Number(b.dataset.excluirItem);
      $('#detalhe-excluir-item').textContent = b.dataset.rotulo;
      $('#pallet-excluir-item').innerHTML = pallets.map((p) => `
        <option value="${escapar(p.codigo)}" data-max="${p.quantidade}"
                ${p.status === 'EXPEDIDO' ? 'disabled' : ''}>
          ${escapar(p.codigo)} — ${p.quantidade} caixa(s)${p.status === 'EXPEDIDO' ? ' (expedido)' : ''}
        </option>`).join('');
      $('#pallet-excluir-item').value = vivos[0].codigo;
      $('#qtd-excluir-item').value = 1;
      $('#qtd-excluir-item').max = vivos[0].quantidade;
      $('#just-excluir-item').value = '';
      $('#modal-excluir-item').showModal();
    }));

  document.querySelectorAll('[data-divergencia]').forEach((b) =>
    b.addEventListener('click', () => {
      itemEmFoco = b.dataset.divergencia;
      $('#detalhe-divergencia').textContent = b.dataset.rotulo;
      $('#justificativa').value = '';
      $('#modal-divergencia').showModal();
    }));
}

// ------------------------------------------------------------ desembarque ---

/**
 * O pallet montado por quantidade, sem bipe: a carreta chega e é
 * descarregada de uma vez. Só o admin, e só em NF que ainda não saiu.
 */
const podeDesembarcar = (n) =>
  !soLeitura() && !!usuario && usuario.perfil === 'ADMIN' &&
  n.status !== 'EXPEDIDA' && n.status !== 'RETORNO_PARCIAL';

let desembarqueEmFoco = null;    // o desembarque aberto no diálogo
let notaDoDesembarque = null;    // { id, numero } da NF dele

async function abrirDesembarque(notaId, numero) {
  notaDoDesembarque = { id: notaId, numero };
  desembarqueEmFoco = null;
  erroDesembarque('');
  $('#titulo-desembarque').textContent = `Desembarque da NF ${numero}`;
  $('#detalhe-desembarque').textContent = '';
  mostrarPainelDesembarque(null);
  $('#modal-desembarque').showModal();

  const [aberto, usuarios] = await Promise.all([
    api(`/notas/${notaId}/desembarque`).catch(() => null),
    api('/usuarios').catch(() => []),
  ]);
  // Responsável: quem desembarca é separador, mas qualquer ativo serve —
  // supervisor que desce para a doca também desembarca.
  $('#responsavel-desembarque').innerHTML = usuarios.filter((u) => u.ativo)
    .map((u) => `<option value="${u.id}">${escapar(u.nome)} (${escapar(u.login)})</option>`).join('');
  if (aberto && aberto.id) mostrarPainelDesembarque(aberto);
}

/** Sem desembarque, pede o responsável; com ele, mostra as linhas da NF. */
function mostrarPainelDesembarque(d) {
  desembarqueEmFoco = d;
  $('#abrir-desembarque').hidden = !!d;
  $('#painel-desembarque').hidden = !d;
  $('#iniciar-desembarque').hidden = !!d;
  $('#salvar-desembarque').hidden = !d;
  $('#encerrar-desembarque').hidden = !d;
  $('#cancelar-desembarque').hidden = !d;
  if (!d) return;

  $('#detalhe-desembarque').textContent =
    `Pallet ${d.palletCodigo} · ${d.cliente} · desembarcado por ${d.responsavelNome} · ` +
    `${d.qtdCaixas} caixa(s) neste pallet`;
  $('#itens-desembarque').innerHTML = d.itens.map((i) => {
    const falta = i.qtdPrevista - i.qtdSeparada;
    return `<tr>
      <td class="num">${i.nItem}</td>
      <td><strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}
        ${falta === 0 && i.nestePallet === 0 ? '<div class="dica">completo na NF</div>' : ''}</td>
      <td class="num">${i.qtdPrevista}</td>
      <td class="num">${i.qtdSeparada}</td>
      <td class="num"><input type="number" class="caixas-desembarque" data-item="${i.itemId}"
             min="0" max="${i.limite}" value="${i.nestePallet}"
             aria-label="Caixas do item ${i.nItem} neste pallet"></td>
    </tr>`;
  }).join('');
}

function erroDesembarque(texto) {
  $('#erro-desembarque').textContent = texto;
  $('#erro-desembarque').hidden = !texto;
}

/** Enquanto o servidor responde, o botão não aceita um segundo clique. */
async function comBotao(botao, acao) {
  botao.disabled = true;
  try { await acao(); } finally { botao.disabled = false; }
}

$('#iniciar-desembarque').addEventListener('click', () => comBotao($('#iniciar-desembarque'), async () => {
  const responsavelId = Number($('#responsavel-desembarque').value);
  if (!responsavelId) { erroDesembarque('Escolha quem desembarcou.'); return; }
  try {
    const r = await api(`/notas/${notaDoDesembarque.id}/desembarque`, {
      method: 'POST', corpo: { responsavelId },
    });
    erroDesembarque('');
    mostrarPainelDesembarque(r.desembarque);
    avisar(`Pallet ${r.desembarque.palletCodigo} criado — etiqueta ${
      r.impressao === 'ENVIADA' ? 'enviada à impressora' : 'não saiu: use Reimprimir'}`,
    r.impressao !== 'ENVIADA');
    desenharInstrucao();
  } catch (e) { erroDesembarque(e.mensagem); }
}));

$('#salvar-desembarque').addEventListener('click', () => comBotao($('#salvar-desembarque'), salvarDesembarque));

async function salvarDesembarque() {
  if (!desembarqueEmFoco) return false;
  const itens = [...document.querySelectorAll('.caixas-desembarque')].map((c) => ({
    itemId: Number(c.dataset.item), caixas: Number(c.value) || 0,
  }));
  try {
    const d = await api(`/desembarques/${desembarqueEmFoco.id}/itens`, { method: 'POST', corpo: { itens } });
    erroDesembarque('');
    mostrarPainelDesembarque(d);
    desenharInstrucao();
    return true;
  } catch (e) {
    erroDesembarque(e.mensagem);
    // O servidor recusou um item: a tela volta ao que ele tem de verdade.
    const atual = await api(`/notas/${notaDoDesembarque.id}/desembarque`).catch(() => null);
    if (atual && atual.id) mostrarPainelDesembarque(atual);
    return false;
  }
}

$('#encerrar-desembarque').addEventListener('click', () => comBotao($('#encerrar-desembarque'), async () => {
  if (!desembarqueEmFoco) return;
  // Salva o que está na tela antes de fechar: ninguém espera perder o que digitou.
  if (!await salvarDesembarque()) return;
  try {
    const r = await api(`/desembarques/${desembarqueEmFoco.id}/encerrar`, { method: 'POST', corpo: {} });
    avisar(`Pallet ${r.desembarque.palletCodigo} encerrado com ${r.desembarque.qtdCaixas} caixa(s) — cartão ${
      r.impressao === 'ENVIADA' ? 'enviado à impressora' : 'não saiu: reimprima pelo Estoque'}`,
    r.impressao !== 'ENVIADA');
    desenharInstrucao();
    // Outro pallet da mesma NF: o diálogo volta a pedir o responsável.
    mostrarPainelDesembarque(null);
    erroDesembarque('');
  } catch (e) { erroDesembarque(e.mensagem); }
}));

$('#cancelar-desembarque').addEventListener('click', () => comBotao($('#cancelar-desembarque'), async () => {
  if (!desembarqueEmFoco) return;
  const justificativa = (prompt('Por que o desembarque está sendo cancelado?') || '').trim();
  if (!justificativa) return;
  try {
    await api(`/desembarques/${desembarqueEmFoco.id}/cancelar`, { method: 'POST', corpo: { justificativa } });
    avisar('Desembarque cancelado: as caixas lançadas saíram e o pallet saiu de circulação');
    mostrarPainelDesembarque(null);
    erroDesembarque('');
    desenharInstrucao();
  } catch (e) { erroDesembarque(e.mensagem); }
}));

let exclusaoItem = null;

$('#pallet-excluir-item').addEventListener('change', () => {
  const opcao = $('#pallet-excluir-item').selectedOptions[0];
  if (opcao) $('#qtd-excluir-item').max = opcao.dataset.max;
});

$('#confirmar-excluir-item').addEventListener('click', async () => {
  const opcao = $('#pallet-excluir-item').selectedOptions[0];
  const quantidade = Number($('#qtd-excluir-item').value) || 0;
  const justificativa = $('#just-excluir-item').value.trim();
  if (!opcao) return;
  const maximo = Number(opcao.dataset.max);
  if (quantidade < 1 || quantidade > maximo) {
    avisar(`Quantidade entre 1 e ${maximo} — é o que há de ${opcao.value} neste item`, true);
    return;
  }
  if (!justificativa) { avisar('Informe a justificativa', true); return; }
  try {
    await api('/separacao/excluir', {
      method: 'POST',
      corpo: { palletCodigo: opcao.value, itemId: exclusaoItem, quantidade, justificativa },
    });
    $('#modal-excluir-item').close();
    avisar(`${quantidade} caixa(s) excluída(s) de ${opcao.value}`);
    desenharInstrucao();
  } catch (e) { avisar(e.mensagem, true); }
});

$('#confirmar-divergencia').addEventListener('click', async () => {
  const justificativa = $('#justificativa').value.trim();
  if (!justificativa) { avisar('Informe a justificativa', true); return; }
  try {
    await api(`/itens/${itemEmFoco}/fechar-divergencia`, { method: 'POST', corpo: { justificativa } });
    $('#modal-divergencia').close();
    avisar('Item fechado com divergência');
    desenharInstrucao();
  } catch (e) { avisar(e.mensagem, true); }
});

// ------------------------------------------------------------- usuários ---

let usuarioEmEdicao = null;

async function verUsuarios() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-usuarios');

  const lista = await api('/usuarios').catch((e) => { avisar(e.mensagem, true); return []; });

  $('#corpo-usuarios').innerHTML = lista.map((u) => `
    <tr class="${u.ativo ? '' : 'inativo'}">
      <td><strong>${escapar(u.login)}</strong></td>
      <td>${escapar(u.nome)}</td>
      <td><span class="marca ${u.perfil.toLowerCase()}">${escapar(u.perfil)}</span></td>
      <td>${u.multiItem ? 'sim' : 'não'}</td>
      <td>${u.ativo ? 'ativo' : 'inativo'}${u.temMovimento ? ' · com movimento' : ''}</td>
      <td class="acoes">
        <button class="texto" data-editar='${escapar(JSON.stringify(u))}'>Editar</button>
        <button class="texto" data-senha="${u.id}" data-nome="${escapar(u.nome)}">Senha</button>
      </td>
    </tr>`).join('');

  document.querySelectorAll('[data-editar]').forEach((b) =>
    b.addEventListener('click', () => abrirFormUsuario(JSON.parse(b.dataset.editar))));
  document.querySelectorAll('[data-senha]').forEach((b) =>
    b.addEventListener('click', () => {
      usuarioEmEdicao = { id: Number(b.dataset.senha) };
      $('#detalhe-senha').textContent = b.dataset.nome;
      $('#nova-senha').value = '';
      $('#modal-nova-senha').showModal();
    }));
}

function abrirFormUsuario(u) {
  usuarioEmEdicao = u;
  const novo = !u;
  $('#titulo-usuario').textContent = novo ? 'Novo usuário' : 'Editar ' + u.login;
  $('#u-login').value = novo ? '' : u.login;
  $('#u-login').disabled = !novo;                 // login é a identidade: não muda
  $('#u-nome').value = novo ? '' : u.nome;
  $('#u-senha').value = '';
  $('#u-perfil').value = novo ? 'SEPARADOR' : u.perfil;
  $('#u-multi-item').checked = novo ? false : u.multiItem;
  $('#u-ativo').checked = novo ? true : u.ativo;
  $('#campo-senha').hidden = !novo;               // edição usa "Senha" à parte
  $('#campo-ativo').hidden = novo;
  $('#dica-usuario').textContent = novo
    ? 'A senha é numérica porque o separador digita com luva, no teclado do coletor.'
    : (u.temMovimento ? 'Usuário com leituras registradas: pode ser inativado, nunca excluído.' : '');
  $('#modal-usuario').showModal();
}

$('#novo-usuario').addEventListener('click', () => abrirFormUsuario(null));

$('#salvar-usuario').addEventListener('click', async () => {
  const corpo = {
    login: $('#u-login').value.trim().toLowerCase(),
    nome: $('#u-nome').value.trim(),
    senha: $('#u-senha').value,
    perfil: $('#u-perfil').value,
    multiItem: $('#u-multi-item').checked,
    ativo: $('#u-ativo').checked,
  };
  try {
    if (usuarioEmEdicao) {
      await api('/usuarios/' + usuarioEmEdicao.id, { method: 'PUT', corpo });
      avisar('Usuário atualizado');
    } else {
      await api('/usuarios', { method: 'POST', corpo });
      avisar('Usuário criado');
    }
    $('#modal-usuario').close();
    verUsuarios();
    verificarAvisosDaSubida();
  } catch (e) { avisar(e.mensagem, true); }
});

$('#salvar-senha').addEventListener('click', async () => {
  try {
    await api(`/usuarios/${usuarioEmEdicao.id}/senha`, {
      method: 'POST', corpo: { senha: $('#nova-senha').value },
    });
    $('#modal-nova-senha').close();
    avisar('Senha redefinida');
    verificarAvisosDaSubida();
  } catch (e) { avisar(e.mensagem, true); }
});

// -------------------------------------------------------------- coletores ---

async function verAparelhos() {
  clearInterval(relogioAtualizacao);
  ['#tela-instrucoes','#tela-instrucao','#tela-usuarios','#tela-impressoras',
   '#tela-enderecamento','#tela-produtos','#tela-expedicao','#tela-relatorios']
    .forEach((t) => { $(t).hidden = true; });
  mostrarSecao('tela-aparelhos');

  // NÃO usar location.origin: a retaguarda costuma ser aberta na própria
  // máquina do servidor, onde ela é "localhost" — QR apontando para localhost
  // não leva coletor nenhum a lugar algum. O endereço vem de quem sabe: o
  // servidor, que enxerga as próprias placas de rede.
  const s = await api('/saude').catch(() => null);
  const ip = (s && s.enderecoLocal) || location.hostname;
  const porta = (s && s.porta) || location.port || 8080;
  $('#ip-detectado').textContent = ip;
  $('#endereco-servidor').value = `${ip}:${porta}`;
  desenharQrs();

  const v = await api('/app/versao').catch(() => null);
  $('#versao-app').innerHTML = (v && v.versao)
    ? `<p><strong>${escapar(v.versao)}</strong>${v.obrigatoria ? ' · obrigatória' : ''}<br>
       <span class="dica">${(v.tamanho / 1024 / 1024).toFixed(1)} MB · publicada por
       ${escapar(v.enviadoPor || '—')} em ${dataCurta(v.enviadoEm)}</span></p>`
    : '<p class="dica">Nenhum APK publicado ainda.</p>';
}

/** Normaliza o que o operador digitou para uma URL http completa. */
function baseDoEndereco() {
  const bruto = $('#endereco-servidor').value.trim().replace(/\/+$/, '');
  if (!bruto) return null;
  return /^https?:\/\//i.test(bruto) ? bruto : `http://${bruto}`;
}

function desenharQrs() {
  const base = baseDoEndereco();
  if (!base) return;
  const apk = `${base}/api/v1/app/apk`;

  $('#url-servidor').textContent = base;
  $('#url-apk-qr').textContent = apk;
  $('#url-apk').textContent = apk;   // bloco do StageNow, mais abaixo

  // O `_` quebra o cache: sem ele, trocar o IP deixaria o QR antigo na tela.
  $('#qr-servidor').src = `${API}/app/qrcode?url=${encodeURIComponent(base)}&_=${Date.now()}`;
  $('#qr-apk').src = `${API}/app/qrcode?url=${encodeURIComponent(apk)}&_=${Date.now()}`;

  $('#aviso-localhost').hidden = !/localhost|127\.0\.0\.1/.test(base);
}

$('#form-endereco').addEventListener('submit', (ev) => {
  ev.preventDefault();
  if (!baseDoEndereco()) { avisar('Informe o endereço do servidor', true); return; }
  desenharQrs();
});

$('#form-apk').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const arquivo = $('#arquivo-apk').files[0];
  const versao = $('#versao-apk').value.trim();
  if (!arquivo) { avisar('Escolha o arquivo .apk', true); return; }
  if (!versao) { avisar('Informe a versão', true); return; }

  const dados = new FormData();
  dados.append('arquivo', arquivo, arquivo.name);
  dados.append('versao', versao);
  dados.append('obrigatoria', $('#apk-obrigatoria').checked ? 'true' : 'false');

  try {
    await api('/app/apk', { method: 'POST', body: dados });
    avisar(`APK ${versao} publicado — os coletores recebem no próximo login`);
    $('#arquivo-apk').value = '';
    verAparelhos();
  } catch (e) { avisar(e.mensagem, true); }
});

// ------------------------------------------------------------- relatórios ---

let relatorioAtivo = 'separacao';

function filtrosDaTela() {
  const p = new URLSearchParams();
  const add = (chave, valor) => { if (valor) p.set(chave, valor); };
  add('de', $('#f-de').value);
  add('ate', $('#f-ate').value);
  add('usuarioId', $('#f-usuario').value);
  add('nota', $('#f-nota').value.trim());
  add('produto', $('#f-produto').value.trim());
  return p;
}

async function verRelatorios() {
  clearInterval(relogioAtualizacao);
  ['#tela-instrucoes','#tela-instrucao','#tela-usuarios','#tela-impressoras',
   '#tela-enderecamento','#tela-produtos','#tela-expedicao','#tela-aparelhos']
    .forEach((t) => { $(t).hidden = true; });
  mostrarSecao('tela-relatorios');

  // Só ADMIN lista usuários; para os demais o filtro fica sem opções.
  if (usuario.perfil === 'ADMIN' && $('#f-usuario').options.length <= 1) {
    const lista = await api('/usuarios').catch(() => []);
    lista.forEach((u) => {
      const o = document.createElement('option');
      o.value = u.id; o.textContent = u.nome;
      $('#f-usuario').appendChild(o);
    });
  }
  desenharRelatorio();
}

$('#filtros').addEventListener('submit', (ev) => { ev.preventDefault(); desenharRelatorio(); });

document.querySelectorAll('.aba-rel').forEach((b) =>
  b.addEventListener('click', () => {
    relatorioAtivo = b.dataset.rel;
    document.querySelectorAll('.aba-rel').forEach((x) => x.classList.toggle('ativa', x === b));
    $('#baixar-csv').hidden = relatorioAtivo === 'produtividade';
    desenharRelatorio();
  }));

$('#baixar-csv').addEventListener('click', () => {
  const p = filtrosDaTela();
  p.set('formato', 'csv');
  // O download precisa do token; abre numa aba com ele no cabeçalho não dá,
  // então busca e salva via blob.
  api(`/relatorios/${relatorioAtivo}?${p}`).catch(() => null);
  baixarCsv(`/relatorios/${relatorioAtivo}?${p}`);
});

async function baixarCsv(caminho) {
  try {
    const r = await fetch(API + caminho, { headers: { Authorization: 'Bearer ' + token } });
    if (!r.ok) throw new Error();
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${relatorioAtivo}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch { avisar('Não foi possível baixar o CSV', true); }
}

async function desenharRelatorio() {
  const alvo = $('#conteudo-relatorio');
  const p = filtrosDaTela();
  try {
    if (relatorioAtivo === 'separacao') {
      const r = await api('/relatorios/separacao?' + p);
      alvo.innerHTML = r.linhas.length ? `
        <table class="lista">
          <thead><tr>
            <th>Separador</th><th>Instrução</th><th>NF</th><th>Produto</th>
            <th>Início</th><th>Fim</th><th>Separado</th><th>Excluído</th><th>Líquido</th>
          </tr></thead>
          <tbody>${r.linhas.map((l) => `<tr>
            <td>${escapar(l.usuario)}</td>
            <td>${escapar(l.instrucao)}</td>
            <td class="num">${escapar(l.notaNumero)}</td>
            <td><strong>${escapar(l.produtoCodigo)}</strong> ${escapar(l.produtoDescricao)}</td>
            <td>${dataCurta(l.primeiraEm)}</td>
            <td>${dataCurta(l.ultimaEm)}</td>
            <td class="num">${l.separado}</td>
            <td class="num ${l.excluido ? 'excluido' : ''}">${l.excluido}</td>
            <td class="num liquido">${l.liquido}</td>
          </tr>`).join('')}</tbody>
          <tfoot><tr>
            <td colspan="6">TOTAL</td>
            <td class="num">${r.totais.separado}</td>
            <td class="num ${r.totais.excluido ? 'excluido' : ''}">${r.totais.excluido}</td>
            <td class="num liquido">${r.totais.liquido}</td>
          </tr></tfoot>
        </table>
        <p class="dica">Líquido = separado − excluído. É o número que vale.</p>`
        : '<p class="vazio">Nenhuma separação no período.</p>';

    } else if (relatorioAtivo === 'exclusoes') {
      const lista = await api('/relatorios/exclusoes?' + p);
      alvo.innerHTML = lista.length ? `
        <table class="lista">
          <thead><tr>
            <th>Quem excluiu</th><th>Quando</th><th>NF</th><th>Produto</th>
            <th>Qtd</th><th>Pallet</th><th>Motivo</th>
          </tr></thead>
          <tbody>${lista.map((e) => `<tr>
            <td>${escapar(e.usuario)}</td>
            <td>${dataCurta(e.em)}</td>
            <td class="num">${escapar(e.notaNumero)}</td>
            <td><strong>${escapar(e.produtoCodigo)}</strong> ${escapar(e.produtoDescricao)}</td>
            <td class="num excluido">${e.quantidade}</td>
            <td class="num">${escapar(e.palletCodigo)}</td>
            <td>${escapar(e.motivo || '—')}</td>
          </tr>`).join('')}</tbody>
        </table>
        <p class="dica">
          Exclusão fica isolada de propósito: diluída no volume total ela some, e
          frequência de exclusão por separador costuma anteceder falta no cliente.
        </p>`
        : '<p class="vazio">Nenhuma exclusão no período.</p>';

    } else {
      const lista = await api('/relatorios/produtividade?' + p);
      const porUsuario = {};
      lista.forEach((l) => {
        porUsuario[l.usuario] = (porUsuario[l.usuario] || 0) + l.liquido;
      });
      const maior = Math.max(1, ...lista.map((l) => l.liquido));
      alvo.innerHTML = lista.length ? `
        <table class="lista">
          <thead><tr><th>Separador</th><th>Dia</th><th>Hora</th><th>Caixas</th><th></th></tr></thead>
          <tbody>${lista.map((l) => `<tr>
            <td>${escapar(l.usuario)}</td>
            <td>${escapar(l.dia.split('-').reverse().join('/'))}</td>
            <td class="num">${escapar(l.hora)}h</td>
            <td class="num liquido">${l.liquido}</td>
            <td><div class="barra-progresso"><i style="width:${(l.liquido / maior) * 100}%"></i></div></td>
          </tr>`).join('')}</tbody>
        </table>
        <p class="dica">Total por separador: ${
          Object.entries(porUsuario).map(([u, q]) => `${escapar(u)} ${q}`).join(' · ')}</p>`
        : '<p class="vazio">Sem dados no período.</p>';
    }
  } catch (e) { avisar(e.mensagem, true); }
}

// ------------------------------------------------------------- expedição ---

let expedicaoEmFoco = null;

async function verExpedicao() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-expedicao');

  // Tela viva enquanto houver retorno em conferência: o coletor está bipando e
  // o supervisor espera o fim para dar a tratativa. Armado antes do await para
  // não sobrescrever o relógio de outra aba aberta nesse meio-tempo.
  relogioAtualizacao = setInterval(() => {
    if (!$('#tela-expedicao').hidden && !document.querySelector('dialog[open]') && retornoEmConferencia) {
      desenharRetornos();
    }
  }, 5000);
  desenharRetornos();

  const [prontas, historico] = await Promise.all([
    api('/expedicoes/pendentes').catch(() => []),
    api('/expedicoes').catch(() => []),
  ]);

  $('#lista-expedir').innerHTML = prontas.length ? prontas.map((n) => `
    <label class="nota-expedir">
      <input type="checkbox" class="marcar-nota" value="${n.id}">
      <span class="dados">
        <b>NF ${escapar(n.numero)}</b> ${escapar(n.cliente)}
        ${n.bonificacao ? '<span class="marca supervisor">BONIFICAÇÃO</span>' : ''}
        ${marcaStatus(n.status)}
        <div class="previa-pallet">${escapar(n.instrucao)} · ${n.qtdPallets} pallet(s) · ${n.qtdCaixas} caixas</div>
        ${n.semEndereco > 0
          ? `<div class="alerta">⚠ ${n.semEndereco} pallet(s) sem endereçamento</div>` : ''}
      </span>
    </label>`).join('') : '<p class="vazio">Nenhuma nota pronta para expedir.</p>';

  document.querySelectorAll('.marcar-nota').forEach((c) =>
    c.addEventListener('change', () => {
      $('#expedir').disabled = document.querySelectorAll('.marcar-nota:checked').length === 0;
    }));
  $('#expedir').disabled = true;

  $('#historico-expedicao').innerHTML = historico.length ? `
    <table class="lista">
      <thead><tr><th>NF</th><th>Cliente</th><th>Quando</th><th>Por</th><th>Pallets</th><th></th></tr></thead>
      <tbody>${historico.map((h) => `<tr class="${h.estornadoEm ? 'inativo' : ''}">
        <td><strong>${escapar(h.notaNumero)}</strong></td>
        <td>${escapar(h.cliente)}</td>
        <td>${dataCurta(h.expedidoEm)}</td>
        <td>${escapar(h.expedidoPor)}</td>
        <td class="num">${h.qtdPallets}</td>
        <td class="acoes">${h.estornadoEm
          ? `<span class="dica">estornada — ${escapar(h.estornoMotivo || '')}</span>`
          : `<button class="texto excluir" data-estornar="${h.id}"
               data-nota="${escapar(h.notaNumero)}">Estornar</button>`}</td>
      </tr>`).join('')}</tbody>
    </table>` : '<p class="dica">Nenhuma expedição registrada.</p>';

  document.querySelectorAll('[data-estornar]').forEach((b) =>
    b.addEventListener('click', () => {
      expedicaoEmFoco = b.dataset.estornar;
      $('#detalhe-estorno').textContent =
        `NF ${b.dataset.nota} — os pallets voltam ao endereço original, quando ainda estiver livre.`;
      $('#motivo-estorno').value = '';
      $('#modal-estorno').showModal();
    }));
}

/** Prévia antes de aplicar: expedir libera endereço e é irreversível sem estorno. */
$('#expedir').addEventListener('click', async () => {
  const ids = [...document.querySelectorAll('.marcar-nota:checked')].map((c) => Number(c.value));
  if (!ids.length) return;
  try {
    const previa = await api('/expedicoes/previa', { method: 'POST', corpo: { notaIds: ids } });
    $('#titulo-expedir').textContent = `Confirmar expedição de ${previa.length} nota(s)`;
    $('#corpo-expedir').innerHTML = previa.map((n) => `
      <div class="previa ok">
        <h4>NF ${escapar(n.notaNumero)}</h4>
        ${n.pallets.map((p) => `<div class="previa-pallet">${escapar(p.codigo)} ·
          ${p.endereco ? escapar(p.endereco) : '<span class="sem-endereco">sem endereço</span>'} ·
          ${p.qtdCaixas} caixas</div>`).join('')}
        <div class="previa-pallet"><strong>${n.posicoesLiberadas} posição(ões) liberada(s)</strong></div>
      </div>`).join('');
    $('#obs-expedicao').value = '';
    $('#modal-expedir').showModal();
  } catch (e) { avisar(e.mensagem, true); }
});

$('#confirmar-expedicao').addEventListener('click', async () => {
  const ids = [...document.querySelectorAll('.marcar-nota:checked')].map((c) => Number(c.value));
  try {
    const r = await api('/expedicoes', {
      method: 'POST',
      corpo: { notaIds: ids, observacao: $('#obs-expedicao').value.trim() || null },
    });
    $('#modal-expedir').close();
    avisar(`${r.length} nota(s) expedida(s)`);
    verExpedicao();
  } catch (e) { avisar(e.mensagem, true); }
});

$('#confirmar-estorno').addEventListener('click', async () => {
  const motivo = $('#motivo-estorno').value.trim();
  if (!motivo) { avisar('Informe o motivo', true); return; }
  try {
    const r = await api(`/expedicoes/${expedicaoEmFoco}/estornar`, { method: 'POST', corpo: { motivo } });
    $('#modal-estorno').close();
    if (r.semEndereco.length) {
      // Posição reocupada não é tomada de volta: o pallet fica pendente e o
      // supervisor precisa saber, senão só descobre no carregamento.
      avisar(`Estornada. ${r.semEndereco.length} pallet(s) ficaram sem endereço — ` +
             r.semEndereco.map((p) => `${p.palletCodigo} (${p.posicaoOriginal} ocupada por ${p.ocupadaPor})`)
               .join('; '), true);
    } else {
      avisar(`Estornada — ${r.restaurados} pallet(s) de volta ao endereço original`);
    }
    verExpedicao();
  } catch (e) { avisar(e.mensagem, true); }
});

// -------------------------------------------------------------- produtos ---

let pendenteEmFoco = null;

/** O selo na aba mostra a fila sem o supervisor precisar entrar na tela. */
async function atualizarSeloPendentes() {
  const r = await api('/codigos-nao-identificados/total').catch(() => null);
  const selo = $('#selo-pendentes');
  if (r && r.total > 0) {
    selo.textContent = r.total;
    selo.hidden = false;
  } else {
    selo.hidden = true;
  }
}

async function verProdutos() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-produtos');

  desenharPendentesDeCodigo();
  desenharProdutos($('#busca-produto').value);
}

async function desenharPendentesDeCodigo() {
  const lista = await api('/codigos-nao-identificados').catch(() => []);
  atualizarSeloPendentes();

  if (!lista.length) {
    $('#bloco-pendentes').innerHTML =
      '<p class="dica">Nenhum código pendente — todo código bipado está cadastrado.</p>';
    return;
  }

  $('#bloco-pendentes').innerHTML = `
    <h3 class="titulo-secao">Códigos não identificados (${lista.length})</h3>
    ${lista.map((k) => `
      <div class="pendente">
        <span class="codigo">${escapar(k.codigo)}</span>
        <span class="quantas">${k.qtdLeituras} leitura(s)</span>
        <span class="quando">último em ${dataCurta(k.ultimaLeituraEm)}${
          k.ultimoUsuario ? ' por ' + escapar(k.ultimoUsuario) : ''}</span>
        <span class="acoes">
          <button class="primario" data-vincular="${k.id}" data-codigo="${escapar(k.codigo)}">Vincular</button>
          <button class="texto" data-descartar="${k.id}">Descartar</button>
        </span>
      </div>`).join('')}`;

  document.querySelectorAll('[data-vincular]').forEach((b) =>
    b.addEventListener('click', () => abrirVinculo(b.dataset.vincular, b.dataset.codigo)));

  document.querySelectorAll('[data-descartar]').forEach((b) =>
    b.addEventListener('click', () => confirmar(
      'Descartar código',
      'Use quando o código não for de produto — etiqueta de transporte, de pallet de terceiro, etc. Ele sai da fila.',
      'Descartar',
      async () => {
        try {
          await api(`/codigos-nao-identificados/${b.dataset.descartar}/descartar`, { method: 'POST' });
          avisar('Código descartado');
          desenharPendentesDeCodigo();
        } catch (e) { avisar(e.mensagem, true); }
      })));
}

function abrirVinculo(pendenteId, codigo) {
  pendenteEmFoco = pendenteId;
  $('#detalhe-vincular').textContent = `Código ${codigo} — escolha o produto a que ele pertence.`;
  $('#busca-vinculo').value = '';
  $('#resultado-vinculo').innerHTML = '';
  $('#modal-vincular').showModal();
  $('#busca-vinculo').focus();
}

let debounceVinculo = null;
$('#busca-vinculo').addEventListener('input', (ev) => {
  clearTimeout(debounceVinculo);
  const termo = ev.target.value.trim();
  if (termo.length < 2) { $('#resultado-vinculo').innerHTML = ''; return; }
  debounceVinculo = setTimeout(async () => {
    const lista = await api('/produtos?busca=' + encodeURIComponent(termo)).catch(() => []);
    $('#resultado-vinculo').innerHTML = lista.length
      ? lista.map((p) => `<div class="opcao" data-produto="${p.id}">
           <strong>${escapar(p.codigo)}</strong> ${escapar(p.descricao)}</div>`).join('')
      : '<p class="dica">Nenhum produto encontrado.</p>';

    document.querySelectorAll('[data-produto]').forEach((d) =>
      d.addEventListener('click', async () => {
        try {
          await api(`/codigos-nao-identificados/${pendenteEmFoco}/vincular`, {
            method: 'POST', corpo: { produtoId: Number(d.dataset.produto) },
          });
          $('#modal-vincular').close();
          avisar('Código vinculado — o separador já pode bipar');
          verProdutos();
        } catch (e) { avisar(e.mensagem, true); }
      }));
  }, 250);
});

$('#form-produtos').addEventListener('submit', (ev) => {
  ev.preventDefault();
  desenharProdutos($('#busca-produto').value);
});

async function desenharProdutos(busca) {
  const lista = await api('/produtos' + (busca ? '?busca=' + encodeURIComponent(busca) : ''))
    .catch((e) => { avisar(e.mensagem, true); return []; });

  $('#lista-produtos').innerHTML = lista.length ? lista.map((p) => `
    <div class="produto">
      <div class="cab">
        <b>${escapar(p.codigo)}</b>
        <span>${escapar(p.descricao)}</span>
        <span class="un">${p.unidadesPorCaixa
          ? `${p.unidadesPorCaixa} un/cx${p.unCxOrigem === 'MANUAL' ? ' (manual)' : ''}`
          : '<em>un/cx não definida</em>'}</span>
      </div>
      <div class="barras">
        ${p.codigosBarras.map((b) => `
          <span class="chip ${b.confirmado ? 'confirmado' : ''} ${b.ativo ? '' : 'inativo'}"
                title="${b.confirmado ? 'confirmado por leitura' : 'veio do XML, ainda não bipado'}">
            ${escapar(b.codigo)}
            ${b.ativo ? `<button data-remover-codigo="${b.id}" title="Desativar">✕</button>` : ''}
          </span>`).join('')}
        <button class="texto" data-add-codigo="${p.id}">+ código</button>
        ${ehLoginAdmin() ? `<button class="texto" data-etiqueta-produto="${p.id}">Reimprimir etiqueta</button>` : ''}
      </div>
    </div>`).join('') : '<p class="vazio">Nenhum produto.</p>';

  document.querySelectorAll('[data-remover-codigo]').forEach((b) =>
    b.addEventListener('click', async () => {
      try {
        await api('/codigos-barras/' + b.dataset.removerCodigo, { method: 'DELETE' });
        avisar('Código desativado');
        desenharProdutos($('#busca-produto').value);
      } catch (e) { avisar(e.mensagem, true); }
    }));

  document.querySelectorAll('[data-etiqueta-produto]').forEach((b) =>
    b.addEventListener('click', () => abrirEtiquetaProduto(lista.find((p) => p.id === Number(b.dataset.etiquetaProduto)))));

  document.querySelectorAll('[data-add-codigo]').forEach((b) =>
    b.addEventListener('click', async () => {
      const codigo = prompt('Código de barras da caixa (pode bipar):');
      if (!codigo) return;
      try {
        await api(`/produtos/${b.dataset.addCodigo}/codigos-barras`, {
          method: 'POST', corpo: { codigo },
        });
        avisar('Código adicionado');
        desenharProdutos($('#busca-produto').value);
      } catch (e) { avisar(e.mensagem, true); }
    }));
}

// -------------------------------------------------------- endereçamento ---

async function verEnderecamento() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-enderecamento');
  focarJuntar();

  const t = await api('/boxes/total').catch(() => null);
  $('#total-boxes').textContent = t ? `${t.total} boxes cadastrados` : '';
  desenharPendentes();
}

async function desenharPendentes() {
  const lista = await api('/enderecamentos/pendentes').catch(() => []);
  $('#pendentes').innerHTML = lista.length
    ? `<table class="lista"><thead><tr><th>Pallet</th><th>NF</th><th>Cliente</th><th>Caixas</th></tr></thead>
       <tbody>${lista.map((p) => `<tr>
         <td><strong>${escapar(p.codigo)}</strong></td>
         <td class="num">${escapar(p.notaNumero || '—')}</td>
         <td>${escapar(p.cliente || '—')}</td>
         <td class="num">${p.qtdCaixas}</td></tr>`).join('')}</tbody></table>`
    : '<p class="vazio">Nenhum pallet aguardando endereço.</p>';
}

// ---------------------------------------------------- juntar / repaletizar ---

// O mesmo fluxo do coletor, com o leitor USB do PC — ele "digita" o código e
// dá Enter. Marcado "Juntar o pallet inteiro", é a fusão; desmarcado, as
// caixas passam uma a uma. Nos dois, só entre pallets da mesma NF (ou para um
// pallet novo, sem caixa): quem recusa é o servidor, e a mensagem dele já é
// escrita para o operador — a tela só mostra.

/** Par aberto na repaletização: { origem, destino, movidas, login }; null quando não há. */
let par = null;
/** Bipes ainda sem resposta, na ordem do leitor. O primeiro é o que está (ou esteve) no servidor. */
let filaCaixas = [];
/** Histórico curto da tela: com o leitor disparando, a caixa recusada não pode sumir no bipe seguinte. */
let bipes = [];
let filaRodando = false;
/** Bipe que ficou sem resposta: a fila espera o Reenviar, que manda a MESMA chave. */
let bipeParado = null;
/** Desfazer ou concluir no servidor: a fila espera, para não cruzar com eles. */
let acaoDoPar = false;
/** Diálogo do "Trocar pallets" aberto: a fila espera a resposta do operador. */
let filaPausada = false;
/**
 * Bipes que chegaram durante o Concluir (null fora dele). Não vão para a fila:
 * o par fecha no fim e os cartões já saem com a conta de antes.
 */
let bipesNoConcluir = null;
/** Desfazer sem resposta: o botão fica travado até a tela reler os dois pallets. */
let desfazerAConferir = false;
let abrindoPar = false;
let piscaCaixa = null;
const PLACEHOLDER_CAIXA = $('#caixa-repaletizar').placeholder;

const podeEnviarCaixa = () =>
  !!par && filaCaixas.length > 0 && !bipeParado && !acaoDoPar && !filaPausada;

/** Resposta sem corpo (o limite de tempo venceu no meio da leitura): não dá para saber o que houve. */
const RESPOSTA_INCOMPLETA = { codigo: 'SEM_REDE', mensagem: 'Resposta incompleta do servidor.' };

/**
 * Chave de idempotência de um bipe. crypto.randomUUID só existe em contexto
 * seguro (https ou localhost) — a retaguarda abre por http no IP do servidor,
 * então lá o caminho normal é o de baixo: UUID v4 montado à mão.
 */
function novaChave() {
  if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;   // versão 4
  b[8] = (b[8] & 0x3f) | 0x80;   // variante RFC 4122
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/**
 * Falha em que não dá para saber se a caixa passou: a rede caiu (SEM_REDE) ou
 * veio erro sem o corpo de negócio do servidor — ERRO_INTERNO, ou um ERRO_502
 * de algo no caminho. Reenviar com a mesma chave é seguro nos dois casos. Erro
 * com código de negócio é resposta: aquela caixa não passou.
 */
const semResposta = (e) =>
  e.codigo === 'SEM_REDE' || e.codigo === 'ERRO_INTERNO' || /^ERRO_\d+$/.test(e.codigo || '');

function mostrarErroPar(texto) {
  const el = $('#erro-par');
  el.textContent = texto;
  el.hidden = !texto;
}

/**
 * Ao entrar na aba, o foco já espera o leitor — sem rolar a página: a busca
 * continua no topo, e o campo Origem aparece assim que recebe o 1º caractere.
 */
function focarJuntar() {
  if (soLeitura()) return;
  // Par aberto por outro login no mesmo PC: as passadas e o desfazer são de
  // quem abriu, e o servidor não deixaria o novo usuário desfazer.
  if (par && par.login !== usuario.login) {
    fecharPar(false);
    $('#resultado-juntar').innerHTML = '';
  }
  (par ? $('#caixa-repaletizar') : $('#origem-juntar')).focus({ preventScroll: true });
}

$('#origem-juntar').addEventListener('input', (ev) => ev.target.scrollIntoView({ block: 'nearest' }));

function aplicarModoJuntar() {
  const inteiro = $('#juntar-inteiro').checked;
  $('#dica-caixa-a-caixa').hidden = inteiro;
  $('#dica-inteiro').hidden = !inteiro;
  $('#abrir-par').textContent = inteiro ? 'Juntar' : 'Abrir';
}
aplicarModoJuntar();   // o navegador pode devolver a marca do toggle ao recarregar

$('#juntar-inteiro').addEventListener('change', () => {
  aplicarModoJuntar();
  mostrarErroPar('');
  $('#origem-juntar').focus();
});

// Enter do leitor na origem vai para o destino; só com os dois é que abre.
$('#origem-juntar').addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter') return;
  ev.preventDefault();
  const origem = ev.target.value.trim().toUpperCase();
  if (!origem) return;
  ev.target.value = origem;
  if ($('#destino-juntar').value.trim()) $('#form-par').requestSubmit();
  else $('#destino-juntar').focus();
});

$('#form-par').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (par || abrindoPar || soLeitura()) return;
  const campoOrigem = $('#origem-juntar');
  const campoDestino = $('#destino-juntar');
  const origem = campoOrigem.value.trim().toUpperCase();
  const destino = campoDestino.value.trim().toUpperCase();
  campoOrigem.value = origem;
  campoDestino.value = destino;
  if (!origem) { campoOrigem.focus(); return; }
  if (!destino) { campoDestino.focus(); return; }

  mostrarErroPar('');
  $('#resultado-juntar').innerHTML = '';
  if (origem === destino) {
    mostrarErroPar('Origem e destino são o mesmo pallet.');
    campoDestino.focus();
    campoDestino.select();
    return;
  }
  abrindoPar = true;
  $('#abrir-par').disabled = true;
  try {
    if ($('#juntar-inteiro').checked) await juntarInteiro(origem, destino);
    else await abrirPar(origem, destino);
  } finally {
    abrindoPar = false;
    $('#abrir-par').disabled = false;
  }
});

/** Volta o foco para o campo que o servidor recusou — o destino, se a mensagem fala dele. */
function focarCampoRecusado(mensagem, destino) {
  const campo = String(mensagem || '').includes(destino) ? $('#destino-juntar') : $('#origem-juntar');
  campo.focus();
  campo.select();   // o próximo bipe substitui o código errado
}

/**
 * Fusão: todas as caixas da origem vão para o destino. A contagem vem antes,
 * para a confirmação dizer quantas caixas — juntar não tem desfazer.
 */
async function juntarInteiro(origem, destino) {
  let o;
  try {
    o = await api(`/pallets/${encodeURIComponent(origem)}/conteudo-detalhado`);
  } catch (e) {
    mostrarErroPar(e.mensagem);
    focarCampoRecusado('', destino);
    return;
  }
  if (!o.totalCaixas) {
    mostrarErroPar(`${o.codigo} não tem caixas para juntar.`);
    focarCampoRecusado('', destino);
    return;
  }
  const quantas = o.totalCaixas === 1 ? 'a única caixa' : `todas as ${o.totalCaixas} caixas`;
  confirmar(
    'Juntar pallets',
    `Juntar ${quantas} de ${o.codigo} em ${destino}? ${o.codigo} fica vazio.`,
    'Juntar',
    async () => {
      try {
        const r = await api('/pallets/fusao', { method: 'POST', corpo: { origem: o.codigo, destino } });
        const alvo = $('#resultado-juntar');
        // A fusão não devolve como foi a impressão: o botão fica à mão para o
        // caso de o cartão não sair.
        alvo.innerHTML = `
          <div class="previa ok">
            <h4>${escapar(r.qtdCaixas)} caixa(s) de ${escapar(r.palletOrigem)} juntadas em ${escapar(r.palletDestino)}</h4>
            ${escapar(r.palletOrigem)} ficou vazio e saiu de circulação: descarte o cartão dele.<br>
            O cartão de ${escapar(r.palletDestino)} foi mandado para a impressora.
            <button class="texto" data-cartao-de-novo="${escapar(r.palletDestino)}">Não saiu? Imprimir de novo</button>
          </div>`;
        ligarCartaoDeNovo(alvo);
        avisar(`${r.qtdCaixas} caixa(s) juntadas em ${r.palletDestino}`);
        $('#origem-juntar').value = '';
        $('#destino-juntar').value = '';
        $('#origem-juntar').focus();
        desenharPendentes();
      } catch (e) {
        // Sem resposta, a fusão pode ter acontecido; repetir às cegas só daria
        // "já foi juntado", mas o operador precisa saber que é para conferir.
        mostrarErroPar(semResposta(e)
          ? 'Sem resposta do servidor: a junção pode ter sido feita. Confira o pallet de destino antes de tentar de novo.'
          : e.mensagem);
        focarCampoRecusado(e.mensagem, destino);
      }
    });
}

async function abrirPar(origem, destino) {
  let r;
  try {
    r = await api('/pallets/repaletizacao/abrir', { method: 'POST', corpo: { origem, destino } });
  } catch (e) {
    mostrarErroPar(e.mensagem);
    focarCampoRecusado(e.mensagem, destino);
    return;
  }
  par = { origem: r.origem.codigo, destino: r.destino.codigo, movidas: r.movidas, login: usuario.login };
  filaCaixas = [];
  bipes = [];
  bipeParado = null;
  desfazerAConferir = false;
  // Concluir fecha a sessão no servidor: "movidas" ao abrir é sempre de um
  // par que ficou pela metade (no coletor ou aqui) sem concluir.
  if (r.movidas) {
    bipes.push({
      codigo: '', situacao: 'info',
      texto: `Você já tinha passado ${r.movidas} caixa(s) de ${par.origem} para ${par.destino} sem concluir. ` +
             'Conclua no fim para reimprimir os cartões.',
    });
  }
  for (const s of ['#origem-juntar', '#destino-juntar', '#juntar-inteiro']) $(s).disabled = true;
  $('#abrir-par').hidden = true;
  $('#painel-par').hidden = false;
  desenharPar(r);
  desenharBipes();
  $('#caixa-repaletizar').focus();
}

/** Fecha o par sem chamar o servidor: as caixas ficam onde estão. */
function fecharPar(focar = true) {
  par = null;
  filaCaixas = [];
  bipes = [];
  bipeParado = null;
  filaPausada = false;
  desfazerAConferir = false;
  $('#painel-par').hidden = true;
  $('#lados-par').innerHTML = '';
  $('#bipes').innerHTML = '';
  $('#sem-resposta').hidden = true;
  $('#conferir-desfazer').hidden = true;
  $('#fila-caixas').hidden = true;
  for (const s of ['#origem-juntar', '#destino-juntar', '#juntar-inteiro']) $(s).disabled = false;
  $('#abrir-par').hidden = false;
  $('#origem-juntar').value = '';
  $('#destino-juntar').value = '';
  if (focar) $('#origem-juntar').focus();
}

/** Um lado do par, como está agora. A linha do produto que acabou de mexer fica marcada. */
function ladoDoPar(papel, p, produtoMexido) {
  return `<div class="cartao-info lado-par">
    <h3>${papel} <b>${escapar(p.codigo)}</b> ${marcaStatus(p.status)}</h3>
    <p class="dica">NF ${escapar(p.notaNumero || '—')} · ${escapar(p.cliente || '—')} ·
      ${p.endereco ? escapar(p.endereco) : 'sem endereço'} ·
      <strong class="num">${escapar(p.totalCaixas)}</strong> caixa(s)</p>
    ${p.itens.length ? `<div class="rolagem"><table class="lista">
      <thead><tr><th>Item</th><th>Caixas</th></tr></thead>
      <tbody>${p.itens.map((i) => `<tr${produtoMexido && i.produtoCodigo === produtoMexido ? ' class="mexido"' : ''}>
        <td><strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}</td>
        <td class="num">${escapar(i.quantidade)}</td>
      </tr>`).join('')}</tbody>
    </table></div>` : '<p class="dica">Nenhuma caixa.</p>'}
  </div>`;
}

/** Redesenha o par com a resposta do servidor — ele é quem sabe o que há em cada pallet. */
function desenharPar(r, rotuloProduto = 'Última caixa passada') {
  par.movidas = r.movidas;
  $('#titulo-par').innerHTML = `<b>${escapar(r.origem.codigo)}</b> → <b>${escapar(r.destino.codigo)}</b>`;
  $('#dica-par').innerHTML =
    `NF ${escapar(r.notaNumero || '—')} · ${escapar(r.cliente || '—')} · ` +
    `passadas: <strong class="num">${escapar(r.movidas)}</strong>` +
    (r.produtoCodigo
      ? `<br>${rotuloProduto}: <strong>${escapar(r.produtoCodigo)}</strong> ${escapar(r.produtoDescricao)}`
      : '');
  const aviso = $('#aviso-esvaziada');
  aviso.hidden = !r.origemEsvaziada;
  aviso.textContent = `${r.origem.codigo} ficou vazio e saiu de circulação (o endereço foi liberado): ` +
    `descarte o cartão dele. Conclua para imprimir o cartão de ${r.destino.codigo}.`;
  $('#lados-par').innerHTML =
    ladoDoPar('Origem', r.origem, r.produtoCodigo) + ladoDoPar('Destino', r.destino, r.produtoCodigo);
  atualizarBotoesPar();
}

function desenharBipes() {
  $('#bipes').innerHTML = bipes.slice(-8).reverse().map((b) => `
    <li class="${b.situacao}">${b.codigo ? `<b>${escapar(b.codigo)}</b>` : ''}<span>${escapar(b.texto)}</span></li>`)
    .join('');

  // A lista mostra os oito últimos; com o leitor mais rápido que a rede, a
  // contagem diz quantos ainda esperam a vez.
  const n = filaCaixas.length;
  const fila = $('#fila-caixas');
  fila.hidden = n < 2 && !bipeParado;
  fila.textContent = bipeParado
    ? `${n} bipe(s) esperando o reenvio — nenhum se perde.`
    : `${n} bipe(s) na fila — vão um de cada vez, na ordem.`;

  $('#sem-resposta').hidden = !bipeParado;
  if (bipeParado) {
    $('#texto-sem-resposta').textContent = bipeParado.sessao
      ? `Sessão expirada. Entre de novo e clique em Reenviar: a caixa ${bipeParado.codigo} vai com a mesma leitura.`
      : `Sem resposta do servidor. A caixa ${bipeParado.codigo} pode ter passado ou não: ` +
        'Reenviar confere sem passar outra.';
  }
  atualizarBotoesPar();
}

function atualizarBotoesPar() {
  if (!par) return;
  // Desfazer ou concluir com caixa ainda no caminho cruzaria com ela.
  const ocupado = acaoDoPar || filaCaixas.length > 0;
  $('#desfazer-caixa').disabled = ocupado || !par.movidas || desfazerAConferir;
  $('#concluir-par').disabled = ocupado || !par.movidas;
  $('#trocar-par').disabled = acaoDoPar;
  $('#reconferir-par').disabled = acaoDoPar;
}

function piscar(classe) {
  const campo = $('#caixa-repaletizar');
  // O verde da caixa seguinte não apaga o vermelho da recusada.
  if (classe === 'ok-leitura' && campo.classList.contains('erro-leitura')) return;
  campo.classList.remove('erro-leitura', 'ok-leitura');
  campo.classList.add(classe);
  clearTimeout(piscaCaixa);
  piscaCaixa = setTimeout(() => campo.classList.remove(classe), classe === 'erro-leitura' ? 2000 : 600);
}

// Cada Enter é um bipe com chave nova. O campo esvazia na hora, sem esperar o
// servidor: o leitor já pode estar mandando a caixa seguinte.
$('#form-caixa').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const campo = $('#caixa-repaletizar');
  const codigo = campo.value.trim();
  campo.value = '';
  campo.focus();
  if (!codigo || !par || soLeitura()) return;
  if (bipesNoConcluir) {
    const recusado = {
      codigo, situacao: 'recusado',
      texto: 'NÃO passou: chegou durante o Concluir. Bipe de novo depois, se ainda for passar.',
    };
    bipesNoConcluir.push(recusado);
    bipes.push(recusado);
    desenharBipes();
    piscar('erro-leitura');
    return;
  }
  const b = { codigo, chave: novaChave(), lidoEm: new Date().toISOString(), situacao: 'fila', texto: 'na fila' };
  filaCaixas.push(b);
  bipes.push(b);
  if (bipes.length > 40) bipes = bipes.slice(-40);   // a tela só mostra os últimos
  desenharBipes();
  processarFila();
});

/**
 * Manda os bipes um de cada vez, na ordem. Erro de negócio descarta só aquela
 * caixa e segue; sem resposta, a fila PARA no bipe até o Reenviar — seguir
 * adiante deixaria o operador sem saber qual caixa passou.
 */
async function processarFila() {
  if (filaRodando) return;
  filaRodando = true;
  try {
    while (podeEnviarCaixa()) {
      const meu = par;
      const b = filaCaixas[0];
      b.situacao = 'enviando';
      b.texto = 'enviando…';
      desenharBipes();

      let r = null;
      let erro = null;
      try {
        // Sem limite, conexão travada deixava o bipe em "enviando…" para
        // sempre, sem o "Sem resposta" + Reenviar (que manda a MESMA chave).
        r = await api('/pallets/repaletizacao/caixa', {
          method: 'POST',
          corpo: {
            idempotencyKey: b.chave, origem: meu.origem, destino: meu.destino,
            codigoLido: b.codigo, lidoEm: b.lidoEm,
          },
          signal: limiteDeTempo(15000),
        });
        if (!r) erro = RESPOSTA_INCOMPLETA;
      } catch (e) { erro = e; }

      // Trocou de par com a caixa no caminho: a fila velha já foi descartada,
      // mas o que o servidor fez com esta caixa ainda precisa ser dito.
      if (par !== meu) {
        avisarCaixaForaDoPar(b, meu, erro);
        break;
      }

      // Sessão expirada também para: depois de entrar de novo, o Reenviar
      // manda a mesma chave e nenhum bipe se perde.
      if (erro && (semResposta(erro) || erro.codigo === 'SESSAO_EXPIRADA')) {
        b.situacao = 'parado';
        b.texto = 'sem resposta — esperando Reenviar';
        b.sessao = erro.codigo === 'SESSAO_EXPIRADA';
        bipeParado = b;
        piscar('erro-leitura');
        break;
      }

      filaCaixas.shift();
      if (erro) {
        b.situacao = 'recusado';
        b.texto = erro.mensagem;
        piscar('erro-leitura');
      } else {
        b.situacao = 'passou';
        b.texto = `passou para ${meu.destino}: ${r.produtoCodigo || ''} ${r.produtoDescricao || ''}`.trim();
        desenharPar(r);
        piscar('ok-leitura');
      }
    }
  } finally {
    filaRodando = false;
    desenharBipes();
  }
  // Bipe do par novo que chegou enquanto a última caixa do par velho ainda
  // voltava do servidor: sem isto, ficaria parado na fila.
  if (podeEnviarCaixa()) processarFila();
}

/**
 * Resposta que chegou depois de o par ser fechado ("Trocar pallets" com a
 * caixa no caminho). Descartada calada, o operador acharia que a caixa ficou
 * na origem — e os cartões dos dois mentiriam. Recusa não precisa de aviso:
 * a caixa não saiu do lugar.
 */
function avisarCaixaForaDoPar(b, meu, erro) {
  let texto;
  if (!erro) {
    texto = `A caixa ${b.codigo} passou de ${meu.origem} para ${meu.destino} depois que o par foi fechado. ` +
            'Os cartões dos dois ficaram com a quantidade errada: abra o par de novo e conclua para reimprimir.';
  } else if (semResposta(erro)) {
    texto = `A caixa ${b.codigo}, enviada antes de fechar ${meu.origem} → ${meu.destino}, ficou sem resposta ` +
            `do servidor e pode ter passado. Confira ${meu.destino}.`;
  } else {
    return;
  }
  avisar(texto, true);
  $('#resultado-juntar').insertAdjacentHTML('afterbegin', `<div class="previa aviso">${escapar(texto)}</div>`);
}

/**
 * Relê o par depois de uma resposta que não veio (desfazer, concluir). /abrir
 * é só leitura e traz as "passadas" do servidor; se ele recusa (a origem
 * esvaziou e saiu de circulação), o conteúdo dos dois pallets ainda mostra
 * onde as caixas estão. Devolve { r } com o par relido, { origemVazia, erro }
 * só com o conteúdo, { erro } se nada respondeu, ou null se o par mudou.
 */
async function relerPar(meu) {
  let erro;
  try {
    const r = await api('/pallets/repaletizacao/abrir', {
      method: 'POST', corpo: { origem: meu.origem, destino: meu.destino }, signal: limiteDeTempo(15000),
    });
    if (!r) throw RESPOSTA_INCOMPLETA;
    if (par !== meu) return null;
    desenharPar(r);
    return { r };
  } catch (e) { erro = e; }
  if (par !== meu) return null;
  if (semResposta(erro) || erro.codigo === 'SESSAO_EXPIRADA') return { erro: erro.mensagem };

  const conteudo = (codigo) =>
    api(`/pallets/${encodeURIComponent(codigo)}/conteudo-detalhado`, { signal: limiteDeTempo(15000) });
  try {
    const [o, d] = await Promise.all([conteudo(meu.origem), conteudo(meu.destino)]);
    if (!o || !d) throw RESPOSTA_INCOMPLETA;
    if (par !== meu) return null;
    // Sem /abrir não vêm as "passadas": fica a conta que a tela já tinha.
    desenharPar({
      notaNumero: o.notaNumero || d.notaNumero, cliente: o.cliente || d.cliente,
      origem: o, destino: d, movidas: par.movidas, origemEsvaziada: !o.totalCaixas,
    });
    return { origemVazia: !o.totalCaixas, erro: erro.mensagem };
  } catch (e) {
    return par === meu ? { erro: e.mensagem } : null;
  }
}

/**
 * Depois de um desfazer sem resposta: relê o par e só então solta o botão.
 * Com as "passadas" do servidor a tela sabe a conta; com a origem ainda vazia
 * também — o desfazer põe a caixa de volta nela, então ela não voltou. Fora
 * isso, o Desfazer segue travado e fica o "Conferir de novo".
 */
async function conferirDesfazer(meu) {
  const x = await relerPar(meu);
  if (!x || par !== meu) return;
  if (x.r) {
    desfazerAConferir = false;
    bipes.push({
      codigo: '', situacao: 'info',
      texto: `Relido do servidor: ${x.r.movidas} caixa(s) passada(s) agora. Confira nos pallets abaixo ` +
             'se a caixa voltou antes de desfazer outra.',
    });
  } else if (x.origemVazia) {
    desfazerAConferir = false;
    bipes.push({
      codigo: '', situacao: 'info',
      texto: `${meu.origem} continua vazio: a caixa não voltou. Pode desfazer de novo.`,
    });
  } else {
    $('#texto-conferir-desfazer').textContent =
      `O Desfazer ficou sem resposta e a tela não conseguiu reler o par (${x.erro}). ` +
      'Desfazer fica travado até conferir: veja os pallets e clique em Conferir de novo.';
  }
  $('#conferir-desfazer').hidden = !desfazerAConferir;
  desenharBipes();
}

$('#reconferir-par').addEventListener('click', async () => {
  if (!par || acaoDoPar || !desfazerAConferir) return;
  const meu = par;
  acaoDoPar = true;
  atualizarBotoesPar();
  try {
    await conferirDesfazer(meu);
  } finally {
    acaoDoPar = false;
    desenharBipes();
    processarFila();   // bipes que chegaram enquanto relia
  }
});

$('#reenviar-caixa').addEventListener('click', () => {
  if (!bipeParado) return;
  // Mesma chave de antes: se a caixa tinha passado, o servidor só devolve a
  // situação, sem passar outra.
  bipeParado.situacao = 'fila';
  bipeParado.texto = 'na fila';
  bipeParado = null;
  desenharBipes();
  $('#caixa-repaletizar').focus();
  processarFila();
});

$('#desfazer-caixa').addEventListener('click', async () => {
  if (!par || acaoDoPar || filaCaixas.length || desfazerAConferir) return;
  const meu = par;
  acaoDoPar = true;
  atualizarBotoesPar();
  try {
    const r = await api('/pallets/repaletizacao/desfazer', {
      method: 'POST', corpo: { origem: meu.origem, destino: meu.destino }, signal: limiteDeTempo(15000),
    });
    if (!r) throw RESPOSTA_INCOMPLETA;
    if (par === meu) {
      bipes.push({
        codigo: '', situacao: 'desfeito',
        texto: `desfeita: ${r.produtoCodigo || ''} ${r.produtoDescricao || ''} voltou para ${meu.origem}`,
      });
      desenharPar(r, 'Voltou para a origem');
    }
  } catch (e) {
    if (par === meu) {
      piscar('erro-leitura');
      if (semResposta(e)) {
        // Desfazer não tem chave: repetir sem saber se o primeiro valeu
        // devolveria duas caixas. Antes as tabelas e as "passadas" ficavam
        // velhas e o botão voltava; agora a tela relê o par e o botão só
        // volta depois disso.
        desfazerAConferir = true;
        bipes.push({
          codigo: '', situacao: 'parado',
          texto: 'Desfazer ficou sem resposta do servidor: a caixa pode ter voltado. Relendo os dois pallets…',
        });
        desenharBipes();
        await conferirDesfazer(meu);
      } else {
        bipes.push({ codigo: '', situacao: 'recusado', texto: e.mensagem });
      }
    }
  } finally {
    acaoDoPar = false;
    desenharBipes();
    if (par === meu) $('#caixa-repaletizar').focus();
    processarFila();   // bipes que chegaram durante o desfazer
  }
});

$('#concluir-par').addEventListener('click', async () => {
  if (!par || acaoDoPar || filaCaixas.length) return;
  const meu = par;
  const campo = $('#caixa-repaletizar');
  acaoDoPar = true;
  // A impressão leva até ~16 s e o par fecha no fim. Antes o campo ficava
  // desabilitado e o bipe do leitor sumia sem aviso; agora ele entra, fica na
  // lista como NÃO passou e sai no resumo da conclusão.
  bipesNoConcluir = [];
  campo.placeholder = 'Concluindo — caixa bipada agora NÃO passa';
  campo.classList.add('concluindo');
  atualizarBotoesPar();
  let resposta = null;
  let erro = null;
  try {
    resposta = await api('/pallets/repaletizacao/concluir', {
      method: 'POST', corpo: { origem: meu.origem, destino: meu.destino }, signal: limiteDeTempo(60000),
    });
    if (!resposta) erro = RESPOSTA_INCOMPLETA;
  } catch (e) { erro = e; }
  const naoPassaram = bipesNoConcluir;
  bipesNoConcluir = null;
  campo.placeholder = PLACEHOLDER_CAIXA;
  campo.classList.remove('concluindo');

  // Sem resposta, o servidor pode ter concluído (e fechado a sessão). Relido,
  // "passadas" zerada quer dizer que concluiu: os cartões devem ter saído.
  if (erro && semResposta(erro) && par === meu) {
    const x = await relerPar(meu);
    if (x && x.r && !x.r.movidas && par === meu) {
      acaoDoPar = false;
      fecharPar();
      mostrarConclusaoSemResposta(x.r, naoPassaram);
      desenharPendentes();
      return;
    }
  }
  acaoDoPar = false;
  if (par !== meu) return;
  if (erro) {
    avisar(semResposta(erro)
      ? 'Sem resposta do servidor ao concluir: os cartões podem ter saído. Confira a impressora antes de concluir de novo.'
      : erro.mensagem, true);
    desenharBipes();
    campo.focus();
    return;
  }
  fecharPar();
  mostrarConclusao(resposta, naoPassaram);
  desenharPendentes();   // o pallet novo entra na fila de endereço; a origem vazia sai
});

/** Linha do resumo para os bipes que chegaram durante o Concluir. */
function itemNaoPassaram(naoPassaram, origem, destino) {
  if (!naoPassaram || !naoPassaram.length) return '';
  return `<li class="nao-saiu">${naoPassaram.length} caixa(s) bipada(s) durante a conclusão NÃO passaram:
    ${naoPassaram.map((b) => `<b>${escapar(b.codigo)}</b>`).join(', ')}. No sistema continuam em
    ${escapar(origem)}; se já foram para ${escapar(destino)}, abra o par de novo, bipe essas caixas e conclua.</li>`;
}

function mostrarConclusaoSemResposta(r, naoPassaram) {
  const alvo = $('#resultado-juntar');
  const cartoes = [r.destino, r.origem].filter((p) => p.totalCaixas > 0);
  alvo.innerHTML = `
    <div class="previa aviso">
      <h4>O Concluir ficou sem resposta, mas o servidor já tinha fechado ${escapar(r.origem.codigo)}
        → ${escapar(r.destino.codigo)}</h4>
      <ul class="cartoes-impressos">
        <li>Confira na impressora se os cartões saíram.
          ${cartoes.map((p) => `<button class="texto" data-cartao-de-novo="${escapar(p.codigo)}">
            Imprimir cartão de ${escapar(p.codigo)}</button>`).join('')}</li>
        ${itemNaoPassaram(naoPassaram, r.origem.codigo, r.destino.codigo)}
      </ul>
    </div>`;
  ligarCartaoDeNovo(alvo);
  avisar('Concluir sem resposta: o par já estava fechado no servidor — confira os cartões', true);
}

/** O que o servidor devolve de cada cartão, em palavras de quem opera. */
function motivoImpressao(situacao) {
  const s = String(situacao || '');
  if (s === 'SEM_IMPRESSORA') return 'não há impressora ativa cadastrada';
  if (s === 'SEM_CONTEUDO') return 'o pallet ficou sem conteúdo para o cartão';
  if (s.startsWith('FALH')) return 'a impressora não respondeu';
  return s.replace(/_/g, ' ').toLowerCase();
}

function mostrarConclusao({ repaletizacao: r, impressao }, naoPassaram = []) {
  const envios = Object.entries(impressao || {});
  const naoSaiu = envios.filter(([, s]) => s !== 'ENVIADA').length;
  const alvo = $('#resultado-juntar');
  alvo.innerHTML = `
    <div class="previa ${naoSaiu ? 'aviso' : 'ok'}">
      <h4>Repaletização concluída: ${escapar(r.movidas)} caixa(s) de ${escapar(r.origem.codigo)}
        para ${escapar(r.destino.codigo)}</h4>
      <ul class="cartoes-impressos">
        ${envios.map(([codigo, s]) => s === 'ENVIADA'
          ? `<li>Cartão de <b>${escapar(codigo)}</b> enviado para a impressora.</li>`
          : `<li class="nao-saiu">Cartão de <b>${escapar(codigo)}</b> não saiu: ${escapar(motivoImpressao(s))}.
               <button class="texto" data-cartao-de-novo="${escapar(codigo)}">Imprimir de novo</button></li>`)
          .join('')}
        ${r.origemEsvaziada
          ? `<li>${escapar(r.origem.codigo)} ficou vazio e saiu de circulação: descarte o cartão dele.</li>`
          : ''}
        ${itemNaoPassaram(naoPassaram, r.origem.codigo, r.destino.codigo)}
      </ul>
    </div>`;
  ligarCartaoDeNovo(alvo);
  if (naoPassaram.length) {
    avisar(`Concluído, mas ${naoPassaram.length} caixa(s) bipada(s) durante a conclusão NÃO passaram`, true);
  } else {
    avisar(naoSaiu ? 'Concluído, mas nem todo cartão saiu na impressora' : 'Concluído — cartões na impressora',
           naoSaiu > 0);
  }
}

// Mesmo /cartao do "Reimprimir cartão" do retorno: o cartão com os itens, não
// a etiqueta de código de barras.
function ligarCartaoDeNovo(alvo) {
  alvo.querySelectorAll('[data-cartao-de-novo]').forEach((b) =>
    b.addEventListener('click', async () => {
      const codigo = b.dataset.cartaoDeNovo;
      try {
        const r = await api(`/pallets/${encodeURIComponent(codigo)}/cartao`, { method: 'POST' });
        if (r && r.impressao && r.impressao !== 'ENVIADA') {
          avisar(`Cartão de ${codigo} não saiu: ${motivoImpressao(r.impressao)}`, true);
        } else {
          avisar(`Cartão de ${codigo} enviado para a impressora`);
        }
      } catch (e) { avisar(e.mensagem, true); }
    }));
}

$('#trocar-par').addEventListener('click', () => {
  if (!par || acaoDoPar) return;
  if (!par.movidas && !filaCaixas.length) { fecharPar(); return; }
  // A fila para enquanto o diálogo está aberto: antes ela seguia mandando por
  // trás dele, e passavam as caixas que o texto dizia que "ficam de fora". A
  // que já estava a caminho não dá para segurar — o texto diz isso.
  filaPausada = true;
  const emEnvio = filaCaixas.find((b) => b.situacao === 'enviando') || null;
  const naFila = filaCaixas.filter((b) => b !== emEnvio && b !== bipeParado).length;
  const partes = [];
  if (par.movidas) {
    partes.push(`Você passou ${par.movidas} caixa(s) de ${par.origem} para ${par.destino} sem concluir: ` +
                'os cartões dos dois ficam com a quantidade errada. As caixas continuam onde estão.');
  }
  if (emEnvio) {
    partes.push(`A caixa ${emEnvio.codigo} já estava sendo enviada e pode ter passado — se a resposta chegar ` +
                'depois de trocar, a tela avisa.');
  }
  if (bipeParado) partes.push(`A caixa ${bipeParado.codigo} ficou sem resposta e pode ter passado.`);
  if (naFila) partes.push(`${naFila} bipe(s) na fila NÃO serão enviados.`);
  confirmar('Trocar pallets sem imprimir', `${partes.join(' ')} Trocar mesmo assim?`,
            'Trocar sem imprimir',
            () => fecharPar(),
            () => {
              filaPausada = false;
              desenharBipes();
              $('#caixa-repaletizar').focus();
              processarFila();
            });
});

$('#form-repaletizar').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const codigo = $('#pallet-repaletizar').value.trim().toUpperCase();
  if (!codigo) return;
  const alvo = $('#conteudo-repaletizar');
  try {
    const p = await api(`/pallets/${codigo}/conteudo-detalhado`);
    if (!p.itens.length) {
      alvo.innerHTML = `<p class="erro">${escapar(p.codigo)} está vazio (${escapar(p.status)}).</p>`;
      return;
    }
    alvo.innerHTML = `
      <div class="cartao-info">
        <h3>${escapar(p.codigo)}</h3>
        <p class="dica">NF ${escapar(p.notaNumero || '—')} · ${escapar(p.cliente || '—')} ·
          ${p.totalCaixas} caixas · ${p.endereco ? escapar(p.endereco) : 'sem endereço'}</p>
        <table class="lista tabela-transferir">
          <thead><tr><th>Item</th><th>No pallet</th><th>Transferir</th></tr></thead>
          <tbody>${p.itens.map((i) => `<tr>
            <td><strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}</td>
            <td class="num">${i.quantidade}</td>
            <td><input type="number" class="qtd-transferir" min="0" max="${i.quantidade}"
                       value="0" data-item="${i.itemId}"></td>
          </tr>`).join('')}</tbody>
        </table>
        <div class="resumo-transferir">
          <span><strong id="total-transferir">0</strong> caixa(s) selecionada(s)</span>
          <button id="confirmar-transferencia" class="primario" disabled>Transferir para pallet novo</button>
          <button id="abrir-excluir-sep" class="perigo" disabled>Excluir separação</button>
        </div>
        <p class="dica">Os cartões afetados são reimpressos automaticamente.</p>
      </div>`;

    // `max` no HTML não impede digitar acima: sem prender aqui, o operador só
    // descobre que passou do que há no pallet quando o servidor recusa.
    const atualizarTotal = () => {
      const total = [...document.querySelectorAll('.qtd-transferir')].reduce((s, i) => {
        const limite = Number(i.max);
        const v = Math.min(Math.max(Number(i.value) || 0, 0), limite);
        if (String(v) !== i.value) i.value = v;
        return s + v;
      }, 0);
      $('#total-transferir').textContent = total;
      $('#confirmar-transferencia').disabled = total === 0;
      $('#abrir-excluir-sep').disabled = total === 0;
    };

    const selecionados = () => [...document.querySelectorAll('.qtd-transferir')]
      .map((i) => ({
        itemId: Number(i.dataset.item),
        quantidade: Number(i.value) || 0,
        rotulo: i.closest('tr').querySelector('strong').textContent,
      }))
      .filter((i) => i.quantidade > 0);
    document.querySelectorAll('.qtd-transferir').forEach((i) =>
      i.addEventListener('input', atualizarTotal));

    // Excluir usa a mesma seleção de quantidades da transferência, mas o efeito
    // é oposto: transferir move caixa de pallet, excluir tira caixa da contagem.
    $('#abrir-excluir-sep').addEventListener('click', () => {
      exclusaoEmFoco = { pallet: p.codigo, itens: selecionados() };
      $('#detalhe-excluir-sep').textContent =
        `${p.codigo}: ` + exclusaoEmFoco.itens.map((i) => `${i.quantidade}x ${i.rotulo}`).join(', ');
      $('#justificativa-excluir-sep').value = '';
      $('#modal-excluir-separacao').showModal();
    });

    $('#confirmar-transferencia').addEventListener('click', async () => {
      const itens = selecionados().map((i) => ({ itemId: i.itemId, quantidade: i.quantidade }));
      try {
        const r = await api('/pallets/transferencia', {
          method: 'POST', corpo: { palletOrigem: p.codigo, itens },
        });
        avisar(`${r.qtdCaixas} caixa(s) para ${r.palletDestino}` +
               (r.origemEsvaziada ? ` · ${r.palletOrigem} ficou vazio` : ''));
        $('#pallet-repaletizar').value = '';
        alvo.innerHTML = '';
        desenharPendentes();
      } catch (e) { avisar(e.mensagem, true); }
    });
  } catch (e) { alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`; }
});

let exclusaoEmFoco = null;

$('#confirmar-excluir-sep').addEventListener('click', async () => {
  const justificativa = $('#justificativa-excluir-sep').value.trim();
  if (!justificativa) { avisar('Informe a justificativa', true); return; }
  try {
    let total = 0;
    for (const i of exclusaoEmFoco.itens) {
      await api('/separacao/excluir', {
        method: 'POST',
        corpo: {
          palletCodigo: exclusaoEmFoco.pallet,
          itemId: i.itemId,
          quantidade: i.quantidade,
          justificativa,
        },
      });
      total += i.quantidade;
    }
    $('#modal-excluir-separacao').close();
    avisar(`${total} caixa(s) excluída(s) de ${exclusaoEmFoco.pallet}`);
    $('#form-repaletizar').dispatchEvent(new Event('submit'));
    desenharPendentes();
  } catch (e) { avisar(e.mensagem, true); }
});

$('#form-busca').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const termo = $('#busca').value.trim().toUpperCase();
  if (!termo) return;
  const alvo = $('#resultado-busca');
  alvo.innerHTML = '';

  try {
    // O formato do código diz o que buscar, sem o operador escolher o tipo —
    // mesma ideia dos prefixos distintos no coletor.
    if (termo.startsWith('P-RED-') || termo.startsWith('P-DEV-')) {
      const p = await api(`/pallets/${termo}/enderecamento`);
      alvo.innerHTML = `<div class="previa ok">
        <h4>${escapar(p.codigo)}</h4>
        NF ${escapar(p.notaNumero || '—')} · ${escapar(p.cliente || '—')} · ${p.qtdCaixas} caixas<br>
        ${p.enderecoAtual
          ? `Endereço: <strong>${escapar(p.enderecoAtual)}</strong>`
          : '<span class="sem-endereco">Sem endereçamento</span>'}
      </div>`;
    } else if (/^\d+$/.test(termo)) {
      mostrarRoteiro(await api('/enderecamentos/nota/' + termo));
    } else {
      mostrarBox(await api(`/boxes/${termo}/posicoes`));
    }
  } catch (e) { alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`; }
});

function mostrarRoteiro(r) {
  $('#resultado-busca').innerHTML = `
    <div class="barra"><h3>NF ${escapar(r.notaNumero)} · ${escapar(r.cliente)}</h3>
      <span class="dica">${r.qtdPallets} pallet(s)${
        r.semEndereco ? ` · <span class="sem-endereco">${r.semEndereco} sem endereço</span>` : ''}</span>
    </div>
    <table class="lista">
      <thead><tr><th>Pallet</th><th>Endereço</th><th>Câmara / Rua</th><th>Caixas</th><th>Status</th></tr></thead>
      <tbody>${r.linhas.map((l) => `<tr>
        <td><strong>${escapar(l.palletCodigo)}</strong></td>
        <td>${l.box ? `${escapar(l.box)} nível ${l.nivel}` : '<span class="sem-endereco">sem endereçamento</span>'}</td>
        <td>${escapar(l.deposito || '—')}${l.rua ? ' · Rua ' + escapar(l.rua) : ''}</td>
        <td class="num">${l.qtdCaixas}</td>
        <td>${marcaStatus(l.status)}</td></tr>`).join('')}</tbody>
    </table>
    <p class="dica">Ordenado por câmara e rua — é o caminho a percorrer, não a ordem de criação.</p>`;
}

function mostrarBox(b) {
  $('#resultado-busca').innerHTML = `
    <div class="barra"><h3>Box ${escapar(b.codigo)}</h3>
      <span class="dica">${escapar(b.deposito)} · Rua ${escapar(b.rua)} · ${escapar(b.tipo)}</span>
    </div>
    <div class="niveis">${b.posicoes.map((p) => `
      <div class="nivel ${p.livre ? 'livre' : 'ocupado'}">
        <b>Nível ${p.nivel}</b>
        ${p.livre
          ? '<span class="vazio-nivel">livre</span>'
          : `<span class="quem"><strong>${escapar(p.ocupadaPor)}</strong>
             · NF ${escapar(p.notaNumero || '—')} · ${p.qtdCaixas} caixas</span>`}
      </div>`).join('')}</div>`;
}

// ---------------------------------------------------------- impressoras ---

let impressoraEmEdicao = null;

async function verImpressoras() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-impressoras');

  const lista = await api('/impressoras').catch((e) => { avisar(e.mensagem, true); return []; });
  const admin = usuario.perfil === 'ADMIN';

  $('#corpo-impressoras').innerHTML = lista.length ? lista.map((i) => `
    <tr class="${i.ativo ? '' : 'inativo'}">
      <td><strong>${escapar(i.nome)}</strong></td>
      <td class="num">${escapar(i.ip)}:${i.porta}</td>
      <td class="num">${i.dpi ? i.dpi : '—'}</td>
      <td>${i.ultimoContatoEm ? dataCurta(i.ultimoContatoEm) : 'nunca'}</td>
      <td>${i.ativo ? 'ativa' : 'inativa'}</td>
      <td class="acoes">
        <button class="texto" data-testar="${i.id}">Imprimir teste</button>
        ${admin ? `<button class="texto" data-editar-imp='${escapar(JSON.stringify(i))}'>Editar</button>` : ''}
      </td>
    </tr>`).join('') : '<tr><td colspan="6" class="vazio">Nenhuma impressora cadastrada.</td></tr>';

  document.querySelectorAll('[data-editar-imp]').forEach((b) =>
    b.addEventListener('click', () => abrirFormImpressora(JSON.parse(b.dataset.editarImp))));

  document.querySelectorAll('[data-testar]').forEach((b) =>
    b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = 'Testando...';
      try {
        const r = await api(`/impressoras/${b.dataset.testar}/testar`, { method: 'POST' });
        if (r.alcancada) {
          avisar(`Etiqueta enviada${r.dpi ? ` · ${r.dpi} dpi detectado` : ''}`);
        } else {
          // Distingue rede de aplicação: é o primeiro diagnóstico quando o
          // coletor "não imprime".
          avisar(`Impressora não respondeu: ${r.motivo || 'sem detalhe'}`, true);
        }
      } catch (e) { avisar(e.mensagem, true); }
      verImpressoras();
    }));
}

function abrirFormImpressora(i) {
  impressoraEmEdicao = i;
  const nova = !i;
  $('#titulo-impressora').textContent = nova ? 'Nova impressora' : 'Editar ' + i.nome;
  $('#i-nome').value = nova ? '' : i.nome;
  $('#i-ip').value = nova ? '' : i.ip;
  $('#i-porta').value = nova ? '9100' : i.porta;
  $('#i-ativo').checked = nova ? true : i.ativo;
  $('#campo-imp-ativo').hidden = nova;
  $('#modal-impressora').showModal();
}

$('#nova-impressora').addEventListener('click', () => abrirFormImpressora(null));

$('#salvar-impressora').addEventListener('click', async () => {
  const corpo = {
    nome: $('#i-nome').value.trim(),
    ip: $('#i-ip').value.trim(),
    porta: Number($('#i-porta').value) || 9100,
    ativo: $('#i-ativo').checked,
  };
  try {
    if (impressoraEmEdicao) {
      await api('/impressoras/' + impressoraEmEdicao.id, { method: 'PUT', corpo });
      avisar('Impressora atualizada');
    } else {
      await api('/impressoras', { method: 'POST', corpo });
      avisar('Impressora cadastrada');
    }
    $('#modal-impressora').close();
    verImpressoras();
  } catch (e) { avisar(e.mensagem, true); }
});

// ------------------------------------------------- excluir e concluir ---

function confirmar(titulo, texto, rotulo, aoConfirmar, aoCancelar = null) {
  const modal = $('#modal-confirmar');
  $('#titulo-confirmar').textContent = titulo;
  $('#texto-confirmar').textContent = texto;
  const botao = $('#confirmar-acao');
  botao.textContent = rotulo;
  const novo = botao.cloneNode(true);          // limpa handlers anteriores
  botao.replaceWith(novo);
  let confirmado = false;
  novo.addEventListener('click', async () => {
    confirmado = true;
    modal.close();
    await aoConfirmar();
  });
  // Cancelar, Esc ou qualquer outro fechamento: quem abriu pode ter parado
  // algo à espera da resposta (a fila de caixas da repaletização, por exemplo).
  if (aoCancelar) {
    modal.addEventListener('close', () => { if (!confirmado) aoCancelar(); }, { once: true });
  }
  modal.showModal();
}

$('#excluir-instrucao').addEventListener('click', () => {
  confirmar(
    'Excluir instrução',
    'A instrução e suas notas serão removidas. Só é possível se nenhuma caixa tiver sido separada.',
    'Excluir instrução',
    async () => {
      try {
        await api('/instrucoes/' + instrucaoAtual, { method: 'DELETE' });
        avisar('Instrução excluída');
        verInstrucoes();
      } catch (e) { avisar(e.mensagem, true); }
    });
});

$('#concluir-instrucao').addEventListener('click', () => concluir(false));

async function concluir(confirmarDivergencias) {
  try {
    const i = await api(`/instrucoes/${instrucaoAtual}/concluir`, {
      method: 'POST', corpo: { confirmarDivergencias },
    });
    $('#modal-conclusao').close();
    avisar(`Instrução ${i.numero} concluída`);
    desenharInstrucao();
  } catch (e) {
    if (e.codigo === 'INSTRUCAO_COM_DIVERGENCIA') {
      mostrarDivergencias(e.mensagem);
    } else {
      avisar(e.mensagem, true);
    }
  }
}

async function mostrarDivergencias(mensagem) {
  const lista = await api(`/instrucoes/${instrucaoAtual}/divergencias`).catch(() => []);
  $('#detalhe-conclusao').textContent = mensagem;
  // As justificativas já foram dadas item a item; aqui só se confirma.
  $('#corpo-divergencias').innerHTML = lista.map((d) => `
    <div class="divergente">
      <strong>NF ${escapar(d.notaNumero)} · ${escapar(d.produtoCodigo)} ${escapar(d.produtoDescricao)}</strong>
      Previsto ${d.qtdPrevista} · Separado ${d.qtdSeparada} · Falta ${d.qtdPrevista - d.qtdSeparada}<br>
      <em>“${escapar(d.justificativa || '')}”</em>
      ${d.fechadoPor ? ` — ${escapar(d.fechadoPor)} · ${dataCurta(d.fechadoEm)}` : ''}
    </div>`).join('');
  $('#modal-conclusao').showModal();
}

$('#confirmar-conclusao').addEventListener('click', () => concluir(true));

function excluirNota(id, numero) {
  confirmar(
    'Excluir NF ' + numero,
    'A nota sai da instrução. Só é possível se nenhuma caixa dela tiver sido separada.',
    'Excluir NF',
    async () => {
      try {
        await api('/notas/' + id, { method: 'DELETE' });
        avisar(`NF ${numero} excluída`);
        desenharInstrucao();
      } catch (e) { avisar(e.mensagem, true); }
    });
}

// ----------------------------------------------------------- importar XML ---

let arquivosSelecionados = null;

$('#importar-xml').addEventListener('click', () => $('#arquivos-xml').click());

$('#arquivos-xml').addEventListener('change', (ev) => {
  const arquivos = [...ev.target.files];
  ev.target.value = '';
  enviarParaPrevia(arquivos);
});

async function enviarParaPrevia(arquivos) {
  const xmls = arquivos.filter((a) => a.name.toLowerCase().endsWith('.xml'));
  if (!xmls.length) {
    avisar('Nenhum arquivo .xml selecionado', true);
    return;
  }
  if (xmls.length < arquivos.length) {
    avisar(`${arquivos.length - xmls.length} arquivo(s) ignorado(s) por não ser .xml`);
  }
  arquivosSelecionados = xmls;

  const dados = new FormData();
  xmls.forEach((a) => dados.append('arquivo', a, a.name));
  try {
    const previa = await api(`/instrucoes/${instrucaoAtual}/xmls/previa`, { method: 'POST', body: dados });
    mostrarPrevia(previa);
  } catch (e) { avisar(e.mensagem, true); }
}

// Arrastar e soltar. O dragover precisa de preventDefault, senão o navegador
// abre o arquivo numa aba em vez de entregar à página.
const areaSolta = $('#solta');
['dragenter', 'dragover'].forEach((evt) =>
  areaSolta.addEventListener(evt, (ev) => { ev.preventDefault(); areaSolta.classList.add('sobre'); }));
['dragleave', 'drop'].forEach((evt) =>
  areaSolta.addEventListener(evt, () => areaSolta.classList.remove('sobre')));
areaSolta.addEventListener('drop', (ev) => {
  ev.preventDefault();
  enviarParaPrevia([...ev.dataTransfer.files]);
});
areaSolta.addEventListener('click', () => $('#arquivos-xml').click());

// Soltar fora da área não deve abrir o XML no navegador.
['dragover', 'drop'].forEach((evt) =>
  window.addEventListener(evt, (ev) => { if (!areaSolta.contains(ev.target)) ev.preventDefault(); }));

function mostrarPrevia(previa) {
  const validas = previa.filter((p) => p.situacao !== 'REJEITADO').length;

  $('#corpo-previa').innerHTML = previa.map((p) => {
    if (p.situacao === 'REJEITADO') {
      return `<div class="previa rejeitado">
        <h4>${escapar(p.arquivo)}</h4>
        <div>${escapar(p.erro ? p.erro.mensagem : 'Arquivo rejeitado')}</div>
      </div>`;
    }
    const n = p.nota;
    const classe = p.avisos && p.avisos.length ? 'aviso' : 'ok';
    return `<div class="previa ${classe}">
      <h4>NF ${escapar(n.numero)} · ${escapar(n.cliente)}</h4>
      <div class="num">${n.qtdItens} itens · ${n.qtdCaixas} caixas ·
        ${n.qVolConfere ? 'total de volumes confere' : '<strong>total de volumes NÃO confere</strong>'}</div>
      ${p.avisos && p.avisos.length
        ? `<ul>${p.avisos.map((a) => `<li>${escapar(a.mensagem)}</li>`).join('')}</ul>` : ''}
    </div>`;
  }).join('');

  const botao = $('#confirmar-import');
  botao.disabled = validas === 0;
  botao.textContent = validas ? `Importar ${validas} nota(s)` : 'Nada a importar';
  $('#modal-previa').showModal();
}

$('#confirmar-import').addEventListener('click', async () => {
  const dados = new FormData();
  arquivosSelecionados.forEach((a) => dados.append('arquivo', a, a.name));
  try {
    const r = await api(`/instrucoes/${instrucaoAtual}/xmls`, { method: 'POST', body: dados });
    const gravadas = r.filter((p) => p.situacao !== 'REJEITADO').length;
    $('#modal-previa').close();
    avisar(`${gravadas} nota(s) importada(s)`);
    desenharInstrucao();
  } catch (e) { avisar(e.mensagem, true); }
});

document.querySelectorAll('[data-fechar]').forEach((b) =>
  b.addEventListener('click', () => b.closest('dialog').close()));

// ------------------------------------------------------------------ início ---


// ------------------------------------------------------------- devoluções ---

let devolucaoEmFoco = null;
let previaDevolucao = null;
let arquivoDevolucao = null;

const TIPO_ORIGEM = { SISTEMA: 'do sistema', XML: 'pelo XML', DIGITADA: 'digitada' };

/**
 * A NF de origem com o jeito que foi informada: só o número não diz se ela
 * está no sistema ou foi apenas digitada.
 */
function origemDaDevolucao(d, comChave = false) {
  if (!d.notaOrigemNumero) return '—';
  const tipo = d.origemTipo ? (TIPO_ORIGEM[d.origemTipo] || d.origemTipo) : '';
  return `NF ${escapar(d.notaOrigemNumero)}` +
    (tipo ? ` <span class="tipo-origem">${escapar(tipo)}</span>` : '') +
    (comChave && d.origemChave ? `<br>chave <span class="chave">${escapar(d.origemChave)}</span>` : '');
}

/**
 * A lista nasce mostrando só o que ainda está no estoque: a baixada é
 * histórico, e com o tempo ela é a maioria. O filtro vai na rota — o
 * servidor devolve só o que foi pedido.
 */
const STATUS_DO_FILTRO = {
  ESTOQUE: 'AGUARDANDO_CONFERENCIA,EM_CONFERENCIA,CONFERIDA',
  BAIXADA: 'BAIXADA',
  TODAS: '',
};
let filtroDevolucoes = 'ESTOQUE';
const rotaDevolucoes = () => {
  const status = STATUS_DO_FILTRO[filtroDevolucoes] || '';
  return status ? `/devolucoes?status=${status}` : '/devolucoes';
};

/** A conferência de estoque é aberta pelo supervisor; o separador só bipa. */
const podeAbrirSobra = () =>
  !soLeitura() && !!usuario && (usuario.perfil === 'SUPERVISOR' || usuario.perfil === 'ADMIN');

$('#exportar-devolucoes').addEventListener('click', () => baixarArquivo('/devolucoes/planilha', 'devolucoes.xlsx'));

$('#filtro-devolucoes').addEventListener('submit', (ev) => ev.preventDefault());
$('#d-situacao').addEventListener('change', async () => {
  filtroDevolucoes = $('#d-situacao').value;
  await desenharDevolucoes();
});

$('#nova-sobra').addEventListener('click', abrirSobra);

/**
 * Abre uma conferência de estoque: a sobra entra como devolução sem NF, e o
 * separador bipa no coletor qualquer caixa que esteja aqui sem nota.
 */
async function abrirSobra() {
  const botao = $('#nova-sobra');
  botao.disabled = true;
  try {
    const d = await api('/devolucoes/sobra', { method: 'POST', corpo: {} });
    avisar(`${d.numero} aberta — bipe as caixas no coletor, em Conferir`);
    if (filtroDevolucoes === 'BAIXADA') {
      filtroDevolucoes = 'ESTOQUE';
      $('#d-situacao').value = 'ESTOQUE';
    }
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) {
    avisar(e.mensagem, true);
  } finally {
    botao.disabled = false;
  }
}

async function verDevolucoes() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-devolucoes');
  $('#abrir-motivos').hidden = !podeEditarMotivo();
  $('#nova-sobra').hidden = !podeAbrirSobra();
  $('#d-situacao').value = filtroDevolucoes;
  // Tela viva, como a de instruções: o coletor confere enquanto o supervisor
  // olha. Armado antes do await pelo mesmo motivo de lá.
  assinaturaDevolucoes = '';
  relogioAtualizacao = setInterval(atualizarDevolucoesAoVivo, 5000);
  await desenharDevolucoes();
}

let assinaturaDevolucoes = '';   // o que a tela mostra, para só redesenhar se mudou
let atualizandoDevolucoes = false;

/**
 * A cada 5 s, relê a lista e a devolução aberta. Redesenha só se algo mudou,
 * com os dados já na mão — sem piscar "carregando" — e mantém aberta a NF e
 * a lista de caixas que estavam abertas. Não atropela quem está com diálogo
 * aberto, caixa marcada ou pallet digitado.
 */
async function atualizarDevolucoesAoVivo() {
  const ocupado = () => $('#tela-devolucoes').hidden || !!document.querySelector('dialog[open]') ||
    !!document.querySelector('.marca-caixa:checked') || !!($('#pallet-caixas') && $('#pallet-caixas').value.trim());
  if (atualizandoDevolucoes || ocupado()) return;
  atualizandoDevolucoes = true;
  try {
    const aberta = devolucaoEmFoco;
    const dados = await Promise.all([
      api(rotaDevolucoes()),
      aberta ? api(`/devolucoes/${aberta}/itens`) : null,
      aberta ? api(`/devolucoes/${aberta}/leituras`) : null,
    ]).catch(() => null);
    // Sem rede agora: tenta de novo no próximo ciclo, sem aviso a cada 5 s.
    if (!dados || ocupado() || devolucaoEmFoco !== aberta) return;
    const assinatura = JSON.stringify(dados);
    if (assinatura === assinaturaDevolucoes) return;
    assinaturaDevolucoes = assinatura;
    const [lista, itens, leituras] = dados;
    const caixasAbertas = document.querySelector('details.caixas-devolucao')?.open;
    await desenharDevolucoes(lista);
    if (aberta && lista.some((d) => d.id === aberta)) {
      await abrirDevolucao(aberta, { lista, itens, leituras });
      const caixas = document.querySelector('details.caixas-devolucao');
      if (caixas && caixasAbertas !== undefined) caixas.open = caixasAbertas;
    }
  } finally {
    atualizandoDevolucoes = false;
  }
}

async function desenharDevolucoes(pronta = null) {
  if (!pronta) assinaturaDevolucoes = '';   // o próximo ciclo ao vivo confere de novo
  const lista = pronta || await api(rotaDevolucoes()).catch((e) => { avisar(e.mensagem, true); return []; });
  // A lista nasce recolhida: quem precisa abrir uma de novo (depois de lançar
  // ou baixar) chama abrirDevolucao em seguida.
  devolucaoEmFoco = null;

  // Cada devolução tem a sua linha de detalhe logo abaixo, escondida: clicar
  // abre ali mesmo, clicar de novo recolhe — como o pallet no Estoque.
  $('#lista-devolucoes').innerHTML = lista.length ? `
    <table class="lista">
      <thead><tr><th>NF devolução</th><th>Cliente</th><th>Motivo</th><th>NF de origem</th>
        <th>Conferido</th><th>Situação</th><th>Importada</th></tr></thead>
      <tbody>${lista.map((d) => `<tr class="clicavel" data-devolucao="${d.id}" aria-expanded="false">
        <td><strong>${escapar(d.numero)}</strong>
          ${d.sobra ? '<div><span class="marca separacao">sobra de estoque</span></div>' : ''}</td>
        <td>${escapar(d.emitenteNome)}${[d.clienteBairro, d.clienteCidade].filter(Boolean).length
          ? `<div class="dica">${escapar([d.clienteBairro, d.clienteCidade].filter(Boolean).join(' · '))}</div>` : ''}</td>
        <td>${escapar((d.motivos && d.motivos.length ? d.motivos.join(' · ') : d.motivo) || '—')}
          ${podeEditarMotivo() ? `<button class="texto" type="button" data-motivo-nf="${d.id}">definir</button>` : ''}</td>
        <td class="num">${origemDaDevolucao(d)}</td>
        <td class="num">${d.qtdConferidaUn} / ${d.qtdEsperadaUn} un</td>
        <td>${marcaStatus(d.status)}</td>
        <td>${dataCurta(d.importadoEm)}</td>
      </tr>
      <tr class="conteudo-pallet conteudo-devolucao" id="dev-${d.id}" hidden><td colspan="7"></td></tr>`).join('')}</tbody>
    </table>` : '<p class="vazio">Nenhuma devolução registrada.</p>';

  document.querySelectorAll('[data-devolucao]').forEach((tr) =>
    tr.addEventListener('click', () => alternarDevolucao(Number(tr.dataset.devolucao))));
  document.querySelectorAll('[data-motivo-nf]').forEach((b) =>
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();   // o clique na linha abre a NF; aqui é só o motivo
      alterarMotivo(lista.find((x) => x.id === Number(b.dataset.motivoNf)));
    }));

  // Olhando só as baixadas, o selo do menu não muda: ele conta o que está em
  // aberto, e essa lista não tem como saber.
  if (filtroDevolucoes !== 'BAIXADA') {
    const emAberto = lista.filter((d) => d.status !== 'BAIXADA').length;
    const selo = $('#selo-devolucoes');
    selo.textContent = emAberto;
    selo.hidden = emAberto === 0;
  }
}

/** Clique na linha: abre a devolução ou, se ela já está aberta, recolhe. */
function alternarDevolucao(id) {
  if (devolucaoEmFoco === id) fecharDevolucao();
  else abrirDevolucao(id);
}

function fecharDevolucao() {
  document.querySelectorAll('.conteudo-devolucao').forEach((l) => {
    l.hidden = true;
    l.querySelector('td').innerHTML = '';
  });
  document.querySelectorAll('[data-devolucao]').forEach((tr) => tr.setAttribute('aria-expanded', 'false'));
  devolucaoEmFoco = null;
}

/** [pronto]: { lista, itens, leituras } já lidos — a atualização ao vivo desenha sem esperar. */
async function abrirDevolucao(id, pronto = null) {
  fecharDevolucao();
  const linha = document.getElementById(`dev-${id}`);
  if (!linha) return;
  devolucaoEmFoco = id;
  linha.hidden = false;
  linha.previousElementSibling.setAttribute('aria-expanded', 'true');
  const alvo = linha.querySelector('td');
  if (!pronto) {
    alvo.innerHTML = '<span class="dica">carregando…</span>';
    // Lida fora do ciclo ao vivo: o próximo ciclo redesenha com o que vier.
    assinaturaDevolucoes = '';
  }

  const [lista, itens, leituras] = pronto ? [pronto.lista, pronto.itens, pronto.leituras] : await Promise.all([
    api(rotaDevolucoes()).catch(() => null),
    api(`/devolucoes/${id}/itens`).catch(() => null),
    api(`/devolucoes/${id}/leituras`).catch(() => null),
  ]);
  // Outro clique, ou a atualização ao vivo redesenhou enquanto carregava: esta
  // resposta já não vale. Desenhar numa linha fora da tela ligaria os botões
  // da linha nova duas vezes.
  if (devolucaoEmFoco !== id || !alvo.isConnected) return;
  if (!lista || !itens || !leituras) {
    alvo.innerHTML = '<span class="dica">Sem resposta do servidor — a tela tenta de novo sozinha.</span>';
    return;
  }
  const d = lista.find((x) => x.id === id);
  if (!d) { alvo.innerHTML = '<span class="dica">Esta devolução não existe mais.</span>'; return; }

  alvo.innerHTML = `
    <div class="cartao-info">
      <h3>${d.sobra ? 'Conferência de estoque' : 'NF'} ${escapar(d.numero)}${
        d.sobra ? '' : ` · ${escapar(d.emitenteNome)}`} ${marcaStatus(d.status)}
        <button id="exportar-devolucao" class="texto" type="button">Exportar Excel</button>
        ${podeApagarDevolucao(d)
          ? '<button id="abrir-apagar-devolucao" class="texto excluir" type="button">Excluir</button>'
          : ''}</h3>
      <p class="dica">
        ${d.sobra
          ? `Sobra: mercadoria achada no estoque, sem NF. ${d.qtdConferidaUn} unidades bipadas`
          : `Origem: ${origemDaDevolucao(d, true)} ·
        ${d.qtdConferidaUn} de ${d.qtdEsperadaUn} unidades conferidas`}
      </p>
      <p class="motivo-nf"><strong>Motivo:</strong> ${escapar(d.motivo || 'não informado')}
        ${podeEditarMotivo() ? '<button id="alterar-motivo" class="texto" type="button">Alterar</button>' : ''}
        <span class="dica">vale para todos os itens sem motivo próprio</span></p>
      <table class="lista">
        <thead><tr><th>Produto</th><th>Item do cliente</th><th>Conferido</th><th>Situação</th><th>Motivo</th><th></th></tr></thead>
        <tbody>${itens.map((i) => `<tr>
          <td><strong>${escapar(i.produtoCodigo || '—')}</strong> ${escapar(i.produtoDescricao || '')}
            ${i.unidadesPorCaixa ? `<div class="dica">${i.unidadesPorCaixa} un/caixa</div>` : ''}
            ${i.trocaDeItem ? `<div><span class="marca aberta">troca do item ${escapar(i.trocaDeItem)}</span></div>
              <div class="dica">${escapar(i.trocadoPor || '')} ${dataCurta(i.trocadoEm)}${
                i.trocaJustificativa ? ` · “${escapar(i.trocaJustificativa)}”` : ''}</div>` : ''}</td>
          <td>${escapar(i.cprodCliente || '')} <div class="dica">${escapar(i.xprodCliente || '')}</div></td>
          <td class="num">${i.qtdConferidaUn} / ${i.qtdEsperadaUn} un</td>
          <td>${marcaStatus(i.status)}
            ${i.justificativa ? `<div class="justificativa">“${escapar(i.justificativa)}”</div>` : ''}</td>
          <td>${escapar(i.motivo || d.motivo || '—')}${!i.motivo && d.motivo ? ' <span class="dica">(da NF)</span>' : ''}
            ${podeEditarMotivo()
              ? `<button class="texto" type="button" data-motivo-item="${i.id}">alterar</button>` : ''}</td>
          <td class="acoes">${acaoDoItemDevolucao(d, i)}</td>
        </tr>`).join('')}</tbody>
      </table>
      ${rodapeDevolucao(d, itens)}
      ${blocoCaixasDevolucao(d, leituras)}
    </div>`;

  const botao = alvo.querySelector('#abrir-baixa');
  if (botao) {
    botao.addEventListener('click', () => {
      $('#modal-baixa-devolucao').querySelector('h3').textContent =
        d.sobra ? 'Baixar a sobra toda' : 'Baixar devolução';
      $('#detalhe-baixa').textContent = d.sobra
        ? `${d.numero} · ${d.qtdConferidaUn} unidades no estoque — sai tudo.`
        : `NF ${d.numero} · ${d.qtdConferidaUn} unidades conferidas.`;
      $('#tipo-baixa').value = 'DESCARTE';
      $('#campo-nf-revenda').hidden = true;
      $('#nf-revenda').value = '';
      $('#obs-baixa').value = '';
      $('#modal-baixa-devolucao').showModal();
    });
  }
  const apagar = alvo.querySelector('#abrir-apagar-devolucao');
  if (apagar) apagar.addEventListener('click', () => abrirApagarDevolucao(d));
  const fecharConf = alvo.querySelector('#fechar-conferencia');
  if (fecharConf) {
    fecharConf.addEventListener('click', () => comBotao(fecharConf, () => mudarConferencia(d, 'conferir')));
  }
  const reabrirConf = alvo.querySelector('#reabrir-conferencia');
  if (reabrirConf) {
    reabrirConf.addEventListener('click', () => comBotao(reabrirConf, () => mudarConferencia(d, 'reabrir')));
  }
  const estornar = alvo.querySelector('#estornar-baixa');
  if (estornar) estornar.addEventListener('click', () => abrirEstornoBaixa(d));
  const doItem = (b, campo) => itens.find((i) => i.id === Number(b.dataset[campo]));
  const exportar = alvo.querySelector('#exportar-devolucao');
  if (exportar) {
    exportar.addEventListener('click', () => baixarArquivo(`/devolucoes/${d.id}/planilha`, `devolucao-NF${d.numero}.xlsx`));
  }
  const motivoNf = alvo.querySelector('#alterar-motivo');
  if (motivoNf) motivoNf.addEventListener('click', () => alterarMotivo(d));
  alvo.querySelectorAll('[data-motivo-item]').forEach((b) =>
    b.addEventListener('click', () => alterarMotivoDoItem(d, doItem(b, 'motivoItem'))));
  alvo.querySelectorAll('[data-trocar-item]').forEach((b) =>
    b.addEventListener('click', () => abrirTroca(d, doItem(b, 'trocarItem'))));
  alvo.querySelectorAll('[data-desfazer-troca]').forEach((b) =>
    b.addEventListener('click', () => pedirDesfazerTroca(d, doItem(b, 'desfazerTroca'))));
  alvo.querySelectorAll('[data-baixar-sobra]').forEach((b) =>
    b.addEventListener('click', () => abrirBaixaSobra(d, doItem(b, 'baixarSobra'))));
  alvo.querySelectorAll('[data-encerrar-item]').forEach((b) =>
    b.addEventListener('click', () => abrirEncerrar(d, [doItem(b, 'encerrarItem')])));
  alvo.querySelectorAll('[data-etiqueta-item]').forEach((b) =>
    b.addEventListener('click', () => abrirEtiqueta(doItem(b, 'etiquetaItem'))));
  const encerrarAbertos = alvo.querySelector('#encerrar-abertos');
  if (encerrarAbertos) {
    encerrarAbertos.addEventListener('click', () => abrirEncerrar(d, itens.filter((i) => !itemFechado(i))));
  }
  ligarCaixasDevolucao(d, leituras, alvo);
}

/** O motivo é informação para o cliente: supervisor e admin mexem mesmo depois da baixa. */
const podeEditarMotivo = () =>
  !soLeitura() && !!usuario && (usuario.perfil === 'SUPERVISOR' || usuario.perfil === 'ADMIN');

let motivosCadastrados = [];     // o cadastro de motivos, para escolher

async function carregarMotivos() {
  motivosCadastrados = await api('/motivos-devolucao').catch(() => []);
}

/**
 * As opções de motivo: [primeira] (vazio = sem motivo, ou o da NF), o cadastro
 * e, se o motivo gravado não estiver nele, o gravado — nada some da tela.
 */
function opcoesMotivo(atual, primeira) {
  const igual = (a, b) => a.toLowerCase() === b.toLowerCase();
  const nomes = motivosCadastrados.map((m) => m.descricao);
  const fora = atual && !nomes.some((n) => igual(n, atual)) ? [atual] : [];
  return `<option value="">${escapar(primeira)}</option>` + [...nomes, ...fora].map((n) =>
    `<option value="${escapar(n)}"${atual && igual(n, atual) ? ' selected' : ''}>${escapar(n)}${
      fora.includes(n) ? ' (fora do cadastro)' : ''}</option>`).join('');
}

let motivoEmFoco = null;         // { gravar } do diálogo de motivo

/** Diálogo de escolher o motivo, da NF ou de um item. */
async function escolherMotivo({ titulo, texto, atual, primeira, gravar }) {
  await carregarMotivos();
  motivoEmFoco = { gravar };
  $('#titulo-motivo').textContent = titulo;
  $('#texto-motivo').textContent = texto;
  $('#escolha-motivo').innerHTML = opcoesMotivo(atual || '', primeira);
  $('#erro-motivo').hidden = true;
  $('#modal-motivo').showModal();
}

$('#confirmar-motivo').addEventListener('click', async () => {
  if (!motivoEmFoco) return;
  const botao = $('#confirmar-motivo');
  botao.disabled = true;
  try {
    await motivoEmFoco.gravar($('#escolha-motivo').value);
    $('#modal-motivo').close();
  } catch (e) {
    $('#erro-motivo').textContent = e.mensagem;
    $('#erro-motivo').hidden = false;
  } finally {
    botao.disabled = false;
  }
});

/** Motivo da NF — pela lista ou pelo detalhe: vale para todo item sem motivo próprio. */
function alterarMotivo(d) {
  if (!d) return;
  escolherMotivo({
    titulo: 'Motivo da devolução',
    texto: `NF ${d.numero} · ${d.emitenteNome}. Vale para todos os itens que não têm motivo próprio.`,
    atual: d.motivo,
    primeira: '— sem motivo —',
    gravar: async (motivo) => {
      await api(`/devolucoes/${d.id}/motivo`, { method: 'POST', corpo: { motivo } });
      avisar(`Motivo da NF ${d.numero} gravado`);
      const aberta = devolucaoEmFoco;
      await desenharDevolucoes();
      if (aberta) abrirDevolucao(aberta);
    },
  });
}

/** Motivo de um item só; "o mesmo da NF" volta a seguir a NF. */
function alterarMotivoDoItem(d, item) {
  if (!item) return;
  escolherMotivo({
    titulo: `Motivo do item ${item.nItem}`,
    texto: `${item.produtoCodigo || ''} ${item.produtoDescricao || ''}`.trim(),
    atual: item.motivo,
    primeira: `o mesmo da NF${d.motivo ? ` (${d.motivo})` : ''}`,
    gravar: async (motivo) => {
      await api(`/devolucoes/itens/${item.id}/motivo`, { method: 'POST', corpo: { motivo } });
      avisar(`Motivo do item ${item.nItem} gravado`);
      await desenharDevolucoes();
      abrirDevolucao(d.id);
    },
  });
}

// --------------------------------------------------- cadastro de motivos

async function abrirCadastroMotivos() {
  mostrarErroMotivos('');
  $('#novo-motivo').value = '';
  await desenharCadastroMotivos();
  $('#modal-motivos').showModal();
}

async function desenharCadastroMotivos() {
  const todos = await api('/motivos-devolucao?todos=1').catch((e) => { mostrarErroMotivos(e.mensagem); return []; });
  $('#lista-motivos').innerHTML = todos.length ? todos.map((m) => `<tr>
      <td>${escapar(m.descricao)}${m.ativo ? '' : ' <span class="dica">(desativado)</span>'}</td>
      <td class="acoes">
        <button class="texto" type="button" data-renomear-motivo="${m.id}">renomear</button>
        <button class="texto${m.ativo ? ' excluir' : ''}" type="button" data-ativar-motivo="${m.id}">${
          m.ativo ? 'desativar' : 'reativar'}</button>
      </td></tr>`).join('')
    : '<tr><td class="dica">Nenhum motivo cadastrado ainda.</td></tr>';
  const porId = new Map(todos.map((m) => [String(m.id), m]));
  const gravar = async (m, corpo) => {
    try {
      await api(`/motivos-devolucao/${m.id}`, { method: 'PUT', corpo });
      mostrarErroMotivos('');
      await desenharCadastroMotivos();
    } catch (e) { mostrarErroMotivos(e.mensagem); }
  };
  $('#lista-motivos').querySelectorAll('[data-renomear-motivo]').forEach((b) => b.addEventListener('click', () => {
    const m = porId.get(b.dataset.renomearMotivo);
    const novo = prompt('Novo nome do motivo:', m.descricao);
    if (novo !== null && novo.trim()) gravar(m, { descricao: novo, ativo: m.ativo });
  }));
  $('#lista-motivos').querySelectorAll('[data-ativar-motivo]').forEach((b) => b.addEventListener('click', () => {
    const m = porId.get(b.dataset.ativarMotivo);
    gravar(m, { descricao: m.descricao, ativo: !m.ativo });
  }));
}

function mostrarErroMotivos(texto) {
  $('#erro-motivos').textContent = texto;
  $('#erro-motivos').hidden = !texto;
}

$('#adicionar-motivo').addEventListener('click', async () => {
  const descricao = $('#novo-motivo').value.trim();
  if (!descricao) { mostrarErroMotivos('Escreva o motivo.'); return; }
  try {
    await api('/motivos-devolucao', { method: 'POST', corpo: { descricao } });
    $('#novo-motivo').value = '';
    mostrarErroMotivos('');
    await desenharCadastroMotivos();
  } catch (e) { mostrarErroMotivos(e.mensagem); }
});
$('#novo-motivo').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') { ev.preventDefault(); $('#adicionar-motivo').click(); }
});
$('#abrir-motivos').addEventListener('click', abrirCadastroMotivos);

const itemFechado = (i) => i.status === 'FECHADO' || i.status === 'FECHADO_DIVERGENCIA';

/**
 * Onde a devolução está e o que falta para a baixa: a baixa é aqui mesmo, no
 * detalhe da NF, depois que todos os itens fecham (no coletor ou em Encerrar).
 */
function rodapeDevolucao(d, itens) {
  if (d.status === 'BAIXADA') {
    return `<div class="resumo-transferir">
        <span>${d.sobra ? 'Conferência de estoque baixada' : 'Devolução baixada'} — os pallets saíram do
          estoque.</span>
        ${podeEstornarBaixa()
          ? '<button id="estornar-baixa" class="texto excluir" type="button">Estornar baixa</button>' : ''}
      </div>`;
  }
  // A baixa é de supervisor e admin: para os outros perfis, só o recado.
  const pode = podeApagarDevolucao(d);
  if (d.sobra) {
    const conferida = d.status === 'CONFERIDA';
    return `<div class="resumo-transferir">
        <span>${conferida
          ? 'Conferência encerrada: a sobra está no estoque, esperando a baixa. Ela saiu do coletor — para bipar mais caixas, reabra.'
          : 'O separador bipa as caixas no coletor, em <strong>Conferir</strong>, e elas entram no pallet P-DEV como sobra. Quando acabar, encerre a conferência; a baixa pode ser de um produto só, em <strong>Baixar caixas</strong> na linha dele, ou de tudo de uma vez.'}</span>
        ${pode && !conferida && d.qtdConferidaUn > 0
          ? '<button id="fechar-conferencia" class="texto" type="button">Encerrar conferência</button>' : ''}
        ${pode && conferida
          ? '<button id="reabrir-conferencia" class="texto" type="button">Reabrir conferência</button>' : ''}
        ${pode && d.qtdConferidaUn > 0
          ? '<button id="abrir-baixa" class="primario">Baixar tudo</button>' : ''}
      </div>`;
  }
  // Faltou dar entrada numa caixa: reabrir devolve a NF ao coletor, com os
  // itens abertos. A conferida só tem este caminho de volta.
  const reabrir = pode && itens.some(itemFechado)
    ? '<button id="reabrir-conferencia" class="texto" type="button">Reabrir conferência</button>' : '';
  if (d.status === 'CONFERIDA') {
    return pode
      ? '<div class="resumo-transferir"><span>Conferência encerrada: falta só a baixa. Esqueceu uma caixa? ' +
        'Reabra, bipe no coletor e encerre de novo.</span>' + reabrir +
        '<button id="abrir-baixa" class="primario">Baixar devolução</button></div>'
      : '<p class="dica">Conferência encerrada: falta a baixa, feita pelo supervisor.</p>';
  }
  const abertos = itens.filter((i) => !itemFechado(i)).length;
  return `<div class="resumo-transferir">
      <span>A conferência é feita no coletor, em <strong>Conferir</strong>. A baixa fica aqui, liberada
        quando os ${abertos} item(ns) em aberto forem encerrados — no coletor, pelo cadeado, ou aqui em
        <strong>Encerrar</strong>.</span>
      ${reabrir}
      ${pode && abertos
        ? '<button id="encerrar-abertos" class="texto" type="button">Encerrar itens em aberto</button>' : ''}
      ${pode ? '<button class="primario" type="button" disabled>Baixar devolução</button>' : ''}
    </div>`;
}

/**
 * Caixas conferidas, uma linha por leitura. Supervisor e admin marcam e põem
 * num pallet — a conferida sem pallet não aparece no estoque — ou excluem.
 */
function blocoCaixasDevolucao(d, leituras) {
  if (!leituras.length) return '';
  const semPallet = leituras.filter((l) => !l.palletCodigo).length;
  const pode = podeApagarDevolucao(d);
  const pallets = [...new Set(leituras.map((l) => l.palletCodigo).filter(Boolean))];
  return `
    <details class="caixas-devolucao"${semPallet ? ' open' : ''}>
      <summary>Caixas conferidas (${leituras.length})${pallets.length ? ` · ${pallets.map(escapar).join(', ')}` : ''}
        ${semPallet ? `<span class="marca divergente">${semPallet} sem pallet</span>` : ''}</summary>
      ${semPallet
        ? `<p class="alerta">${semPallet} leitura(s) sem pallet não aparecem no estoque: marque e ponha num pallet P-DEV.</p>`
        : ''}
      ${pode ? `<div class="barra-caixas">
        <button class="texto" type="button" id="marcar-todas-caixas">Marcar todas</button>
        ${semPallet ? '<button class="texto" type="button" id="marcar-sem-pallet">Marcar as sem pallet</button>' : ''}
        <span id="caixas-marcadas" class="dica">nenhuma marcada</span>
        <input id="pallet-caixas" list="pallets-da-devolucao" placeholder="P-DEV-000001" autocomplete="off"
               aria-label="Pallet que recebe as caixas marcadas">
        <datalist id="pallets-da-devolucao">${pallets.map((p) => `<option value="${escapar(p)}">`).join('')}</datalist>
        <button class="primario" type="button" id="por-no-pallet" disabled>Pôr no pallet</button>
        <button class="texto excluir" type="button" id="abrir-excluir-caixas" disabled>Excluir caixas</button>
      </div>` : ''}
      <table class="lista">
        <thead><tr>${pode ? '<th></th>' : ''}<th class="num">Item</th><th>Produto</th><th class="num">Quantidade</th>
          <th>Pallet</th><th>Conferida por</th></tr></thead>
        <tbody>${leituras.map((l) => `<tr>
          ${pode ? `<td><input type="checkbox" class="marca-caixa" value="${l.id}"
              data-sem-pallet="${l.palletCodigo ? 0 : 1}" aria-label="Marcar leitura ${l.id}"></td>` : ''}
          <td class="num">${l.nItem}</td>
          <td><strong>${escapar(l.produtoCodigo || '—')}</strong> ${escapar(l.produtoDescricao || '')}</td>
          <td class="num">${l.qtdUn} un <div class="dica">${l.modo === 'BIPE' ? 'caixa' : 'soltas'}</div></td>
          <td>${l.palletCodigo ? escapar(l.palletCodigo) : '<strong class="sem-pallet">sem pallet</strong>'}</td>
          <td>${escapar(l.usuario || '')} <div class="dica">${dataCurta(l.lidoEm)}</div></td>
        </tr>`).join('')}</tbody>
      </table>
    </details>`;
}

let caixasEmFoco = null;         // { devolucao, ids } do diálogo de excluir caixas

/** [alvo]: a linha de detalhe desta devolução — só os controles dela. */
function ligarCaixasDevolucao(d, leituras, alvo) {
  const q = (seletor) => alvo.querySelector(seletor);
  const caixas = [...alvo.querySelectorAll('.marca-caixa')];
  if (!caixas.length) return;
  const marcadas = () => caixas.filter((c) => c.checked).map((c) => Number(c.value));
  const unidades = (ids) => leituras.filter((l) => ids.includes(l.id)).reduce((s, l) => s + l.qtdUn, 0);
  const atualizar = () => {
    const ids = marcadas();
    q('#caixas-marcadas').textContent = ids.length ? `${ids.length} marcada(s) · ${unidades(ids)} un` : 'nenhuma marcada';
    q('#por-no-pallet').disabled = !ids.length;
    q('#abrir-excluir-caixas').disabled = !ids.length;
  };
  caixas.forEach((c) => c.addEventListener('change', atualizar));
  q('#marcar-todas-caixas').addEventListener('click', () => {
    const todas = caixas.every((c) => c.checked);
    caixas.forEach((c) => { c.checked = !todas; });
    atualizar();
  });
  const semPallet = q('#marcar-sem-pallet');
  if (semPallet) {
    semPallet.addEventListener('click', () => {
      caixas.forEach((c) => { c.checked = c.dataset.semPallet === '1'; });
      atualizar();
    });
  }
  q('#por-no-pallet').addEventListener('click', async () => {
    const codigo = q('#pallet-caixas').value.trim().toUpperCase();
    if (!codigo) { avisar('Informe o pallet P-DEV que recebe as caixas.', true); q('#pallet-caixas').focus(); return; }
    const botao = q('#por-no-pallet');
    botao.disabled = true;   // clique duplo mandaria duas vezes
    try {
      const r = await api(`/devolucoes/${d.id}/leituras/pallet`, {
        method: 'POST', corpo: { leituraIds: marcadas(), palletCodigo: codigo },
      });
      avisar(`${r.caixas} leitura(s) no pallet ${codigo}` +
        (r.palletsLiberados.length ? ` · ${r.palletsLiberados.join(', ')} ficou vazio e voltou livre` : ''));
      await desenharDevolucoes();
      abrirDevolucao(d.id);
    } catch (e) {
      avisar(e.mensagem, true);
      // Outra pessoa mexeu nas caixas: relê, sem as marcas velhas — elas
      // pausariam a atualização ao vivo e o erro voltaria a cada tentativa.
      if (e.codigo === 'CAIXAS_MUDARAM') {
        await desenharDevolucoes();
        abrirDevolucao(d.id);
      } else {
        atualizar();
      }
    }
  });
  q('#abrir-excluir-caixas').addEventListener('click', () => {
    const ids = marcadas();
    caixasEmFoco = { devolucao: d, ids };
    $('#texto-excluir-caixas').textContent =
      `${ids.length} leitura(s), ${unidades(ids)} un, saem da NF ${d.numero}. O item volta a esperar essas ` +
      'unidades e, se já estava encerrado, reabre. Pallet que ficar vazio volta livre, sem endereço.';
    $('#justificativa-excluir-caixas').value = '';
    $('#erro-excluir-caixas').hidden = true;
    $('#modal-excluir-caixas').showModal();
  });
  atualizar();
}

$('#confirmar-excluir-caixas').addEventListener('click', async () => {
  if (!caixasEmFoco) return;
  const { devolucao: d, ids } = caixasEmFoco;
  const erro = $('#erro-excluir-caixas');
  const justificativa = $('#justificativa-excluir-caixas').value.trim();
  if (!justificativa) { erro.textContent = 'Informe por que as caixas saem da devolução.'; erro.hidden = false; return; }
  const botao = $('#confirmar-excluir-caixas');
  botao.disabled = true;
  try {
    const r = await api(`/devolucoes/${d.id}/leituras/excluir`, {
      method: 'POST', corpo: { leituraIds: ids, justificativa },
    });
    $('#modal-excluir-caixas').close();
    avisar(`${r.caixas} leitura(s) excluída(s)` +
      (r.palletsLiberados.length ? ` · ${r.palletsLiberados.join(', ')} ficou vazio e voltou livre` : ''));
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) {
    if (e.codigo === 'CAIXAS_MUDARAM') {
      // Outra pessoa mexeu nas caixas: fecha e relê, sem as marcas velhas.
      $('#modal-excluir-caixas').close();
      avisar(e.mensagem, true);
      await desenharDevolucoes();
      abrirDevolucao(d.id);
      return;
    }
    erro.textContent = e.mensagem;
    erro.hidden = false;
  } finally {
    botao.disabled = false;
  }
});

let encerrarEmFoco = null;       // { devolucao, itens } do diálogo de encerrar

/**
 * Encerrar pela retaguarda o que o coletor não fechou — o mesmo cadeado:
 * completo fecha direto; faltando unidade, com a justificativa.
 */
function abrirEncerrar(d, itensAlvo) {
  const alvo = itensAlvo.filter(Boolean);
  if (!alvo.length) return;
  encerrarEmFoco = { devolucao: d, itens: alvo };
  const faltando = alvo.filter((i) => i.qtdConferidaUn < i.qtdEsperadaUn);
  $('#texto-encerrar-item').innerHTML = alvo.map((i) =>
    `Item ${i.nItem} · ${escapar(i.produtoCodigo || '—')} ${escapar(i.produtoDescricao || '')}: ` +
    `conferido ${i.qtdConferidaUn} de ${i.qtdEsperadaUn} un`).join('<br>');
  $('#campo-justificativa-encerrar').hidden = !faltando.length;
  $('#justificativa-encerrar').value = '';
  $('#erro-encerrar').hidden = true;
  $('#modal-encerrar-item').showModal();
}

$('#confirmar-encerrar').addEventListener('click', async () => {
  if (!encerrarEmFoco) return;
  const { devolucao: d, itens } = encerrarEmFoco;
  const erro = $('#erro-encerrar');
  const faltando = (i) => i.qtdConferidaUn < i.qtdEsperadaUn;
  const justificativa = $('#justificativa-encerrar').value.trim();
  if (itens.some(faltando) && !justificativa) {
    erro.textContent = 'Faltam unidades: informe a justificativa (ex.: o cliente devolveu 9 das 12).';
    erro.hidden = false;
    return;
  }
  const botao = $('#confirmar-encerrar');
  botao.disabled = true;
  let feitos = 0;
  const restantes = [...itens];
  try {
    while (restantes.length) {
      const i = restantes[0];
      try {
        await api(`/devolucoes/itens/${i.id}/fechar`, {
          method: 'POST', corpo: { justificativa: faltando(i) ? justificativa : null },
        });
        feitos += 1;
      } catch (e) {
        // Fechado no coletor enquanto o diálogo estava aberto: já está como se quer.
        if (e.codigo !== 'ITEM_FECHADO') throw e;
      }
      restantes.shift();
    }
    $('#modal-encerrar-item').close();
    avisar(`${feitos} item(ns) encerrado(s)`);
  } catch (e) {
    // Um novo clique continua de onde parou, sem repetir os já encerrados.
    encerrarEmFoco = { devolucao: d, itens: restantes };
    erro.textContent = e.mensagem + (feitos ? ` (${feitos} já encerrado(s))` : '');
    erro.hidden = false;
    return;
  } finally {
    botao.disabled = false;
  }
  await desenharDevolucoes();
  abrirDevolucao(d.id);
});

/**
 * Supervisor e admin, antes da baixa: item da NF registra "Produto trocado";
 * o item da troca, enquanto ninguém o conferiu, pode ser desfeito.
 */
function acaoDoItemDevolucao(d, i) {
  if (!podeApagarDevolucao(d)) return '';
  const encerrar = itemFechado(i)
    ? '' : `<button class="texto" type="button" data-encerrar-item="${i.id}">Encerrar</button>`;
  const etiqueta = i.produtoCodigo
    ? `<button class="texto" type="button" data-etiqueta-item="${i.id}">Etiqueta 2ª via</button>` : '';
  // Sobra não tem NF: não há produto trocado nem divergência a encerrar — o
  // que ela tem é a baixa por caixa, do que saiu para descarte ou revenda.
  if (d.sobra) {
    return etiqueta + (i.qtdConferidaUn > 0
      ? `<button class="texto" type="button" data-baixar-sobra="${i.id}">Baixar caixas</button>` : '');
  }
  if (i.trocaDeItem) {
    return encerrar + etiqueta + (i.qtdConferidaUn === 0
      ? `<button class="texto excluir" type="button" data-desfazer-troca="${i.id}">Desfazer troca</button>`
      : '');
  }
  return encerrar + etiqueta + `<button class="texto" type="button" data-trocar-item="${i.id}">Produto trocado</button>`;
}

/** Estornar a baixa é correção de erro, com mercadoria voltando ao estoque: só o admin. */
const podeEstornarBaixa = () => !soLeitura() && !!usuario && usuario.perfil === 'ADMIN';

/** Encerra ou reabre a conferência de estoque — a sobra não fecha sozinha. */
async function mudarConferencia(d, rota) {
  try {
    await api(`/devolucoes/${d.id}/${rota}`, { method: 'POST', corpo: {} });
    avisar(rota === 'conferir'
      ? `${d.numero} encerrada — a sobra fica no estoque até a baixa`
      : `${d.sobra ? d.numero : `NF ${d.numero}`} reaberta — volta à lista do coletor, em Conferir`);
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) { avisar(e.mensagem, true); }
}

let baixaEstornada = null;       // a devolução do diálogo de estorno

function abrirEstornoBaixa(d) {
  baixaEstornada = d;
  $('#detalhe-estornar-baixa').textContent =
    `${d.sobra ? d.numero : `NF ${d.numero}`} · ${d.emitenteNome} · ${d.qtdConferidaUn} unidades`;
  $('#motivo-estornar-baixa').value = '';
  $('#erro-estornar-baixa').hidden = true;
  $('#modal-estornar-baixa').showModal();
  $('#motivo-estornar-baixa').focus();
}

$('#confirmar-estornar-baixa').addEventListener('click', () => comBotao($('#confirmar-estornar-baixa'), async () => {
  if (!baixaEstornada) return;
  const motivo = $('#motivo-estornar-baixa').value.trim();
  if (!motivo) {
    $('#erro-estornar-baixa').textContent = 'Informe o motivo do estorno.';
    $('#erro-estornar-baixa').hidden = false;
    return;
  }
  try {
    const r = await api(`/devolucoes/${baixaEstornada.id}/estornar-baixa`, { method: 'POST', corpo: { justificativa: motivo } });
    $('#modal-estornar-baixa').close();
    const semEndereco = r.palletsSemEndereco.length
      ? `; ${r.palletsSemEndereco.map((p) => p.palletCodigo).join(', ')} voltaram sem endereço (a posição é de ${
        r.palletsSemEndereco.map((p) => p.ocupante).join(', ')}) e entram na fila do coletor` : '';
    const porCaixa = r.caixasBaixadasPorCaixa
      ? `; ${r.caixasBaixadasPorCaixa} caixa(s) não voltam — saíram antes na baixa por caixa` : '';
    avisar(`Baixa estornada: ${r.caixas} caixa(s) de volta ao estoque${semEndereco}${porCaixa}`);
    await desenharDevolucoes();
    abrirDevolucao(baixaEstornada.id);
  } catch (e) {
    $('#erro-estornar-baixa').textContent = e.mensagem;
    $('#erro-estornar-baixa').hidden = false;
  }
}));

let sobraEmFoco = null;          // { devolucao, item } do diálogo de baixar caixas

/**
 * Baixa de parte da sobra: o produto pode sair em partes — algumas caixas
 * hoje para descarte, o resto depois. O que fica continua no pallet.
 */
function abrirBaixaSobra(d, item) {
  if (!item) return;
  sobraEmFoco = { devolucao: d, item };
  const caixas = item.unidadesPorCaixa ? Math.floor(item.qtdConferidaUn / item.unidadesPorCaixa) : 0;
  $('#detalhe-baixa-sobra').textContent =
    `${item.produtoCodigo || ''} ${item.produtoDescricao || ''} · ${caixas} caixa(s) no estoque` +
    (item.unidadesPorCaixa ? ` (${item.unidadesPorCaixa} un/caixa)` : '');
  const campo = $('#caixas-baixa-sobra');
  campo.max = caixas || 1;
  campo.value = caixas || 1;
  $('#tipo-baixa-sobra').value = 'DESCARTE';
  $('#campo-nf-revenda-sobra').hidden = true;
  $('#nf-revenda-sobra').value = '';
  $('#obs-baixa-sobra').value = '';
  mostrarErroBaixaSobra('');
  $('#modal-baixa-sobra').showModal();
  campo.focus();
  campo.select();
}

function mostrarErroBaixaSobra(texto) {
  $('#erro-baixa-sobra').textContent = texto;
  $('#erro-baixa-sobra').hidden = !texto;
}

$('#tipo-baixa-sobra').addEventListener('change', () => {
  $('#campo-nf-revenda-sobra').hidden = $('#tipo-baixa-sobra').value !== 'REVENDA';
});

$('#confirmar-baixa-sobra').addEventListener('click', async () => {
  if (!sobraEmFoco) return;
  const { devolucao: d, item } = sobraEmFoco;
  const caixas = Number($('#caixas-baixa-sobra').value);
  if (!Number.isInteger(caixas) || caixas <= 0) {
    mostrarErroBaixaSobra('Informe quantas caixas saem.');
    return;
  }
  const botao = $('#confirmar-baixa-sobra');
  botao.disabled = true;
  try {
    const r = await api(`/devolucoes/itens/${item.id}/baixa-sobra`, {
      method: 'POST',
      corpo: {
        caixas,
        tipo: $('#tipo-baixa-sobra').value,
        observacao: $('#obs-baixa-sobra').value.trim() || null,
        nfRevenda: $('#nf-revenda-sobra').value.trim() || null,
      },
    });
    $('#modal-baixa-sobra').close();
    avisar(`${r.caixas} caixa(s) baixada(s)` +
      (r.restanteUn === 0 ? `; saiu a última caixa, ${d.numero} baixada` : ''));
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) {
    mostrarErroBaixaSobra(e.mensagem);
  } finally {
    botao.disabled = false;
  }
});

/** Pede as etiquetas 2ª via ao servidor; devolve o texto do aviso e se saiu. */
async function imprimirEtiquetaItem(itemId, copias, impressoraId = null) {
  return imprimirEtiqueta(`/devolucoes/itens/${itemId}/etiqueta`, copias, impressoraId);
}

async function imprimirEtiqueta(rota, copias, impressoraId = null) {
  const r = await api(rota, { method: 'POST', corpo: { copias, impressoraId } });
  const onde = r.impressao === 'ENVIADA' ? 'enviada(s) à impressora'
    : r.impressao === 'SEM_IMPRESSORA' ? 'nenhuma impressora cadastrada'
      : 'a impressora não respondeu — imprima de novo';
  return {
    ok: r.impressao === 'ENVIADA',
    texto: `${r.copias} etiqueta(s) 2ª via com ${r.etiqueta.codigoBarras}` +
      (r.etiqueta.codigoInterno ? ' (código interno, gravado no produto)' : '') + `: ${onde}`,
  };
}

let etiquetaEmFoco = null;       // item do diálogo de etiqueta 2ª via

async function abrirEtiqueta(item) {
  if (!item) return;
  etiquetaEmFoco = { rota: `/devolucoes/itens/${item.id}/etiqueta` };
  const faltam = Math.max(0, item.qtdEsperadaUn - item.qtdConferidaUn);
  $('#copias-etiqueta').value = String(item.unidadesPorCaixa ? Math.max(1, Math.ceil(faltam / item.unidadesPorCaixa)) : 1);
  $('#texto-etiqueta-produto').textContent =
    `Item ${item.nItem} · ${item.produtoCodigo} ${item.produtoDescricao || ''}. Sai o código de barras que o ` +
    'produto já tem; sem um que o coletor leia, o sistema cria um código interno e grava no produto. ' +
    'Cole na caixa: o conferente bipa como qualquer caixa.';
  await prepararDialogoEtiqueta();
}

/** Só o usuário admin — o login, não o perfil — reimprime etiqueta de produto. */
const ehLoginAdmin = () => !!usuario && String(usuario.login || '').toLowerCase() === 'admin';

/** Reimpressão pela aba Produtos: a mesma etiqueta 80 × 50 mm da devolução. */
async function abrirEtiquetaProduto(p) {
  if (!p) return;
  etiquetaEmFoco = { rota: `/produtos/${p.id}/etiqueta` };
  $('#copias-etiqueta').value = '1';
  $('#texto-etiqueta-produto').textContent =
    `${p.codigo} ${p.descricao}. Etiqueta 80 × 50 mm com "ETIQUETA 2ª VIA". Sai o código de barras do ` +
    'produto; sem um que o coletor leia, o sistema cria um código interno e grava no produto.';
  await prepararDialogoEtiqueta();
}

async function prepararDialogoEtiqueta() {
  $('#erro-etiqueta').hidden = true;
  const impressoras = await api('/impressoras').catch(() => []);
  $('#impressora-etiqueta').innerHTML = '<option value="">padrão (a do meu usuário)</option>' +
    impressoras.filter((i) => i.ativo).map((i) => `<option value="${i.id}">${escapar(i.nome)}</option>`).join('');
  $('#modal-etiqueta-produto').showModal();
}

$('#confirmar-etiqueta').addEventListener('click', async () => {
  if (!etiquetaEmFoco) return;
  const erro = $('#erro-etiqueta');
  const copias = Number($('#copias-etiqueta').value);
  if (!Number.isInteger(copias) || copias < 1 || copias > 200) {
    erro.textContent = 'Imprima de 1 a 200 etiquetas.';
    erro.hidden = false;
    return;
  }
  const botao = $('#confirmar-etiqueta');
  botao.disabled = true;
  try {
    const r = await imprimirEtiqueta(etiquetaEmFoco.rota, copias, Number($('#impressora-etiqueta').value) || null);
    $('#modal-etiqueta-produto').close();
    avisar(r.texto, !r.ok);
  } catch (e) {
    erro.textContent = e.mensagem;
    erro.hidden = false;
  } finally {
    botao.disabled = false;
  }
});

let trocaEmFoco = null;          // { devolucao, item } do diálogo de produto trocado
let produtosTroca = new Map();

/**
 * Produto trocado: veio outro produto no lugar do declarado na NF. O que veio
 * vira um item novo, conferido no coletor como qualquer outro.
 */
async function abrirTroca(d, item) {
  trocaEmFoco = { devolucao: d, item };
  const produtos = await api('/produtos?limite=2000').catch((e) => { avisar(e.mensagem, true); return []; });
  produtosTroca = new Map(produtos.map((p) => [String(p.id), p]));
  $('#produto-troca').innerHTML = '<option value="">— selecionar o produto que veio —</option>' +
    produtos.filter((p) => p.codigo !== item.produtoCodigo)
      .map((p) => `<option value="${p.id}">${escapar(p.codigo)} — ${escapar(p.descricao)}</option>`).join('');
  $('#texto-troca-devolucao').textContent =
    `NF ${d.numero}, item ${item.nItem}: a NF declara ${item.produtoCodigo || '—'} ` +
    `${item.produtoDescricao || ''} (${item.qtdEsperadaUn} un).`;
  $('#qtd-troca').value = '1';
  $('#unidade-troca').value = 'CX';
  // Com leitura do produto da NF, parte dele veio: a troca é só do resto.
  const jaConferido = item.qtdConferidaUn > 0;
  $('#item-todo-troca').checked = !jaConferido;
  $('#item-todo-troca').disabled = jaConferido;
  $('#justificativa-troca').value = '';
  $('#etiqueta-troca').checked = true;
  mostrarErroTroca(jaConferido
    ? `Já foram conferidas ${item.qtdConferidaUn} un do produto da NF: a troca fica só para a parte que veio trocada.`
    : '');
  $('#erro-troca').classList.toggle('erro', !jaConferido);
  atualizarEquivalenteTroca();
  $('#modal-troca-devolucao').showModal();
}

function mostrarErroTroca(texto) {
  $('#erro-troca').textContent = texto;
  $('#erro-troca').hidden = !texto;
}

/** O que a conferência vai esperar do produto que veio, em unidades. */
function atualizarEquivalenteTroca() {
  const qtd = Number($('#qtd-troca').value) || 0;
  const alvo = $('#equivalente-troca');
  alvo.classList.remove('alerta');
  if ($('#unidade-troca').value === 'UN') { alvo.textContent = qtd ? `= ${qtd} un` : ''; return; }
  const p = produtosTroca.get($('#produto-troca').value);
  if (!p) { alvo.textContent = ''; return; }
  if (!p.unidadesPorCaixa) {
    alvo.textContent = 'produto sem unidades por caixa: cadastre em Produtos ou informe em unidades';
    alvo.classList.add('alerta');
    return;
  }
  alvo.textContent = qtd ? `= ${qtd * p.unidadesPorCaixa} un (${p.unidadesPorCaixa} un/cx)` : '';
}

['#produto-troca', '#unidade-troca'].forEach((s) => $(s).addEventListener('change', atualizarEquivalenteTroca));
$('#qtd-troca').addEventListener('input', atualizarEquivalenteTroca);

$('#confirmar-troca').addEventListener('click', async () => {
  if (!trocaEmFoco) return;
  const { devolucao: d, item } = trocaEmFoco;
  $('#erro-troca').classList.add('erro');
  const produtoId = Number($('#produto-troca').value);
  const quantidade = Number($('#qtd-troca').value);
  const justificativa = $('#justificativa-troca').value.trim();
  if (!produtoId) { mostrarErroTroca('Selecione o produto que veio.'); return; }
  if (!Number.isInteger(quantidade) || quantidade < 1) { mostrarErroTroca('Informe uma quantidade inteira, maior que zero.'); return; }
  if (!justificativa) { mostrarErroTroca('Informe a justificativa da troca.'); return; }
  const botao = $('#confirmar-troca');
  botao.disabled = true;   // clique duplo registraria duas trocas
  try {
    const unidade = $('#unidade-troca').value;
    const itensDepois = await api(`/devolucoes/itens/${item.id}/troca`, {
      method: 'POST',
      corpo: { produtoId, quantidade, unidade, itemTodo: $('#item-todo-troca').checked, justificativa },
    });
    $('#modal-troca-devolucao').close();
    let aviso = `Troca registrada no item ${item.nItem}: o produto que veio entra na conferência`;
    let ruim = false;
    // A caixa do produto que veio não tem o código do item da NF: já sai a
    // etiqueta 2ª via, uma por caixa, para o conferente bipar.
    if ($('#etiqueta-troca').checked) {
      const novo = itensDepois.filter((x) => x.trocaDeItem === item.nItem).sort((a, b) => b.nItem - a.nItem)[0];
      const porCaixa = (produtosTroca.get(String(produtoId)) || {}).unidadesPorCaixa;
      const copias = unidade === 'CX' ? quantidade : Math.max(1, Math.ceil(quantidade / (porCaixa || quantidade)));
      try {
        const r = await imprimirEtiquetaItem(novo.id, copias);
        aviso += ` · ${r.texto}`;
        ruim = !r.ok;
      } catch (e) {
        aviso += ` · etiqueta não saiu: ${e.mensagem}`;
        ruim = true;
      }
    }
    avisar(aviso, ruim);
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) {
    mostrarErroTroca(e.mensagem);
  } finally {
    botao.disabled = false;
  }
});

function pedirDesfazerTroca(d, item) {
  confirmar(
    'Desfazer troca',
    `O item ${item.nItem} (${item.produtoCodigo || ''} ${item.produtoDescricao || ''}) sai da devolução, e o ` +
      `item ${item.trocaDeItem} da NF volta a esperar o produto declarado.`,
    'Desfazer troca',
    async () => {
      try {
        await api(`/devolucoes/itens/${item.id}/desfazer-troca`, { method: 'POST', corpo: {} });
        avisar(`Troca do item ${item.trocaDeItem} desfeita`);
        await desenharDevolucoes();
        abrirDevolucao(d.id);
      } catch (e) { avisar(e.mensagem, true); }
    },
  );
}

/** Supervisor e admin; baixada não se apaga — a mercadoria já saiu do estoque (o servidor também trava). */
const podeApagarDevolucao = (d) =>
  !soLeitura() && !!usuario && (usuario.perfil === 'SUPERVISOR' || usuario.perfil === 'ADMIN') &&
  d.status !== 'BAIXADA';

/**
 * Devolução lançada por engano. Sem conferência, apaga direto. Com a
 * conferência começada, só apagando a conferência junto: a caixa a marcar
 * existe para isso não passar sem ser lido.
 */
function abrirApagarDevolucao(d) {
  const comConferencia = d.status !== 'AGUARDANDO_CONFERENCIA' || d.qtdConferidaUn > 0;
  $('#texto-apagar-devolucao').textContent =
    `NF ${d.numero} · ${d.emitenteNome}. Apagada, ela sai da lista e a mesma NF pode ser lançada de novo.`;
  mostrarConferenciaNoApagar(comConferencia
    ? `A conferência já começou (${d.qtdConferidaUn} de ${d.qtdEsperadaUn} un). Para apagar a devolução, a ` +
      'conferência precisa ser apagada junto: as leituras somem e os pallets de devolução voltam livres, sem ' +
      'endereço, para conferir de novo na devolução certa.'
    : null);
  $('#justificativa-apagar-devolucao').value = '';
  mostrarErroApagar('');
  $('#modal-apagar-devolucao').showModal();
  $('#justificativa-apagar-devolucao').focus();
}

function mostrarConferenciaNoApagar(texto) {
  $('#bloco-conferencia-apagar').hidden = !texto;
  $('#texto-conferencia-apagar').textContent = texto || '';
  $('#apagar-conferencia-junto').checked = false;
}

/** Erro dentro do diálogo: o aviso flutuante fica atrás do fundo escurecido do modal. */
function mostrarErroApagar(texto) {
  $('#erro-apagar-devolucao').textContent = texto;
  $('#erro-apagar-devolucao').hidden = !texto;
}

$('#confirmar-apagar-devolucao').addEventListener('click', async () => {
  const justificativa = $('#justificativa-apagar-devolucao').value.trim();
  if (!justificativa) { mostrarErroApagar('Informe por que a devolução está sendo apagada.'); return; }
  const comConferencia = !$('#bloco-conferencia-apagar').hidden;
  if (comConferencia && !$('#apagar-conferencia-junto').checked) {
    mostrarErroApagar('Marque "Excluir também a conferência" para excluir a devolução.');
    return;
  }
  const botao = $('#confirmar-apagar-devolucao');
  botao.disabled = true;   // clique duplo mandaria dois pedidos
  try {
    const r = await api(`/devolucoes/${devolucaoEmFoco}/apagar`, {
      method: 'POST', corpo: { justificativa, comConferencia },
    });
    $('#modal-apagar-devolucao').close();
    avisar(`Devolução NF ${r.numero} excluída` + (r.leiturasApagadas
      ? ` com a conferência (${r.leiturasApagadas} leitura(s))` +
        (r.palletsLiberados.length ? ` — ${r.palletsLiberados.join(', ')} livre(s) para conferir de novo` : '')
      : ''));
    await desenharDevolucoes();
  } catch (e) {
    if (e.codigo === 'DEVOLUCAO_COM_CONFERENCIA') {
      // A conferência começou depois de a tela abrir: o coletor bipou agora.
      mostrarConferenciaNoApagar(`${e.mensagem} Para apagar a devolução, a conferência precisa ser apagada junto.`);
      mostrarErroApagar('');
    } else {
      mostrarErroApagar(e.mensagem);
    }
  } finally {
    botao.disabled = false;
  }
});

$('#tipo-baixa').addEventListener('change', () => {
  $('#campo-nf-revenda').hidden = $('#tipo-baixa').value !== 'REVENDA';
});

$('#confirmar-baixa').addEventListener('click', async () => {
  try {
    const r = await api(`/devolucoes/${devolucaoEmFoco}/baixar`, {
      method: 'POST',
      corpo: {
        tipo: $('#tipo-baixa').value,
        observacao: $('#obs-baixa').value.trim() || null,
        nfRevenda: $('#nf-revenda').value.trim() || null,
      },
    });
    $('#modal-baixa-devolucao').close();
    avisar(`${r.sobra ? '' : 'NF '}${r.numero} baixada`);
    await desenharDevolucoes();
    abrirDevolucao(r.id);
  } catch (e) { avisar(e.mensagem, true); }
});

// --- importação: prévia com o de-para sugerido pelo GTIN ---------------------

/**
 * A NF de origem como está no diálogo: o modo e o que foi informado em cada
 * um. Trocar de modo não apaga o que já foi informado nos outros.
 */
let origemDevolucao = null;
/** Avisos da prévia sem origem (item sem GTIN): o que sobrar na prévia com origem é dela. */
let avisosSemOrigem = [];
let notasOrigemAchadas = new Map();
// Só a resposta mais recente vale: o operador troca a origem mais rápido que a rede.
let seqPreviaOrigem = 0;
let seqBuscaOrigem = 0;
let esperaBuscaOrigem = null;
let esperaNumeroOrigem = null;

$('#nova-devolucao').addEventListener('click', () => $('#arquivo-devolucao').click());

$('#arquivo-devolucao').addEventListener('change', async (ev) => {
  const arquivo = ev.target.files[0];
  ev.target.value = '';
  if (!arquivo) return;
  arquivoDevolucao = arquivo;

  // A primeira prévia vai sem origem: é ela que diz qual NF de venda o próprio
  // XML da devolução aponta (refNFe), e a origem já abre escolhida por ela.
  // Antes a origem era um <select> das NFs do sistema, que parava nas 200 de
  // número maior — a NF antiga não aparecia.
  const corpo = new FormData();
  corpo.append('arquivo', arquivo);
  let previa;
  try {
    previa = await api('/devolucoes/previa', { method: 'POST', body: corpo });
  } catch (e) { avisar(e.mensagem, true); return; }

  previaDevolucao = previa;
  avisosSemOrigem = previa.avisos || [];
  const produtos = await api('/produtos?limite=2000').catch(() => []);
  await carregarMotivos();
  produtosDepara = new Map(produtos.map((p) => [String(p.id), p]));

  const ref = previa.referencia || null;
  origemDevolucao = {
    modo: ref && !ref.notaId ? 'DIGITADA' : 'SISTEMA',
    nota: ref && ref.notaId ? { id: ref.notaId, numero: ref.numero, cliente: ref.cliente, status: null } : null,
    arquivo: null,
    numero: ref && !ref.notaId ? ref.numero : '',
  };
  notasOrigemAchadas = new Map();
  clearTimeout(esperaBuscaOrigem);
  clearTimeout(esperaNumeroOrigem);
  seqBuscaOrigem++;
  mostrarErroDepara('');

  $('#titulo-depara').textContent =
    `NF devolução ${previa.numero} · ${previa.emitenteNome}`;
  $('#corpo-depara').innerHTML = `
    ${blocoOrigemDevolucao(ref)}
    <label class="motivo-nf">Motivo da devolução <span class="dica">— vale para todos os itens; mude só o que tiver outro</span>
      <select id="motivo-devolucao">${opcoesMotivo('', '— sem motivo —')}</select>
    </label>
    <table class="lista tabela-transferir">
      <thead><tr><th>Item do cliente</th><th>Quantidade</th><th>Produto no sistema</th><th>Motivo</th></tr></thead>
      <tbody>${previa.itens.map((i) => `<tr>
        <td><strong>${escapar(i.cprodCliente)}</strong> ${escapar(i.xprodCliente)}
          <div class="dica">cEAN ${escapar(i.ceanCliente || 'ausente')}</div></td>
        <td>
          <input type="number" class="qtd-item" data-item="${i.nItem}" min="1" step="1"
                 value="${escapar(i.qtdCaixas)}" aria-label="Quantidade do item ${i.nItem}">
          <select class="unidade-item" data-item="${i.nItem}" aria-label="Unidade do item ${i.nItem}">
            <option value="CX"${i.unidadeSugerida === 'UN' ? '' : ' selected'}>caixas</option>
            <option value="UN"${i.unidadeSugerida === 'UN' ? ' selected' : ''}>unidades</option>
          </select>
          <div class="dica equivalente-item" data-item="${i.nItem}"></div>
          <div class="dica">no XML: ${escapar(i.qtdCaixas)} ${escapar(i.unidadeXml || '')}</div>
        </td>
        <td>
          <select class="vinculo" data-item="${i.nItem}">
            <option value="">— selecionar produto —</option>
            ${produtos.map((p) => `<option value="${p.id}"${
              i.sugestao && i.sugestao.produtoId === p.id ? ' selected' : ''
            }>${escapar(p.codigo)} — ${escapar(p.descricao)}</option>`).join('')}
          </select>
          <div class="dica">${i.sugestao ? 'vinculado pelo GTIN' : '⚠ sem GTIN — vincular à mão'}</div>
        </td>
        <td><select class="motivo-item" data-item="${i.nItem}" aria-label="Motivo do item ${i.nItem}">
          ${opcoesMotivo('', 'o mesmo da NF')}</select></td>
      </tr>`).join('')}</tbody>
    </table>
    ${avisosSemOrigem.length
      ? `<p class="alerta">${avisosSemOrigem.map(escapar).join('<br>')}</p>` : ''}`;

  ligarOrigemDevolucao();
  ligarQuantidadesDepara();
  $('#modal-depara').showModal();
  atualizarPreviaOrigem();   // avisos da origem que já veio escolhida pela referência
});

/** Produtos do de-para por id: a unidade por caixa dá o total em unidades de cada item. */
let produtosDepara = new Map();

/**
 * A quantidade de cada item é do operador: vem a do XML, mas a NF do cliente
 * nem sempre lança na unidade certa. Ao lado, o que a conferência vai
 * esperar, em unidades — em caixas, pela unidade por caixa do produto.
 */
function ligarQuantidadesDepara() {
  const campo = (classe, n) => document.querySelector(`.${classe}[data-item="${n}"]`);
  const atualizar = (n) => {
    const qtd = Number(campo('qtd-item', n).value) || 0;
    const alvo = campo('equivalente-item', n);
    alvo.classList.remove('alerta');
    if (campo('unidade-item', n).value === 'UN') {
      alvo.textContent = qtd ? `= ${qtd} un` : '';
      return;
    }
    const produto = produtosDepara.get(campo('vinculo', n).value);
    if (!produto) { alvo.textContent = ''; return; }
    if (!produto.unidadesPorCaixa) {
      alvo.textContent = 'produto sem unidades por caixa: cadastre em Produtos ou lance em unidades';
      alvo.classList.add('alerta');
      return;
    }
    alvo.textContent = qtd ? `= ${qtd * produto.unidadesPorCaixa} un (${produto.unidadesPorCaixa} un/cx)` : '';
  };
  document.querySelectorAll('.qtd-item, .unidade-item, .vinculo').forEach((el) =>
    el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', () => atualizar(el.dataset.item)));
  document.querySelectorAll('.vinculo').forEach((sel) => atualizar(sel.dataset.item));
}

function blocoOrigemDevolucao(ref) {
  const o = origemDevolucao;
  const modo = (valor, rotulo) =>
    `<label><input type="radio" name="modo-origem" value="${valor}"${o.modo === valor ? ' checked' : ''}> ${rotulo}</label>`;
  const painel = (valor) => `data-modo-origem="${valor}"${o.modo === valor ? '' : ' hidden'}`;
  return `
    <div class="bloco-origem">
      <h4>NF de venda de origem</h4>
      ${ref
        ? `<p class="dica">O XML desta devolução aponta a NF <strong>${escapar(ref.numero)}</strong>${
            ref.cliente ? ` — ${escapar(ref.cliente)}` : ''}${ref.notaId
              ? ', que está no sistema: ela já vem escolhida.'
              : ', que não está no sistema: o número já vem digitado.'}
            <br>chave <span class="chave">${escapar(ref.chave)}</span></p>`
        : '<p class="dica">O XML desta devolução não aponta a NF de venda. Informe de um dos três jeitos.</p>'}
      <div class="modos-origem">
        ${modo('SISTEMA', 'NF do sistema')}
        ${modo('XML', 'XML da NF de origem')}
        ${modo('DIGITADA', 'Digitar o número')}
      </div>
      <div ${painel('SISTEMA')}>
        <div class="busca">
          <input id="busca-origem" inputmode="numeric" autocomplete="off" aria-label="Número da NF de venda"
                 placeholder="Número da NF de venda" value="${escapar(o.nota ? o.nota.numero : '')}">
          <button type="button" id="buscar-origem">Buscar</button>
        </div>
        <div id="escolhida-origem"></div>
        <div id="resultado-origem" class="resultado-vinculo"></div>
      </div>
      <div ${painel('XML')}>
        <label>Arquivo .xml da NF de venda
          <input type="file" id="arquivo-origem" accept=".xml">
        </label>
      </div>
      <div ${painel('DIGITADA')}>
        <label>Número da NF de venda
          <input id="numero-origem" inputmode="numeric" autocomplete="off" value="${escapar(o.numero)}">
        </label>
      </div>
      <div id="situacao-origem" aria-live="polite"></div>
    </div>`;
}

function ligarOrigemDevolucao() {
  document.querySelectorAll('input[name="modo-origem"]').forEach((r) =>
    r.addEventListener('change', () => trocarModoOrigem(r.value)));

  const busca = $('#busca-origem');
  busca.addEventListener('input', () => {
    clearTimeout(esperaBuscaOrigem);
    esperaBuscaOrigem = setTimeout(buscarNotaOrigem, 400);
  });
  busca.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    clearTimeout(esperaBuscaOrigem);
    buscarNotaOrigem();
  });
  $('#buscar-origem').addEventListener('click', () => {
    clearTimeout(esperaBuscaOrigem);
    buscarNotaOrigem();
  });

  const resultado = $('#resultado-origem');
  const escolherDaLista = (ev) => {
    const opcao = ev.target.closest('[data-nota-origem]');
    const n = opcao && notasOrigemAchadas.get(opcao.dataset.notaOrigem);
    if (n) escolherNotaOrigem(n);
  };
  resultado.addEventListener('click', escolherDaLista);
  resultado.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    escolherDaLista(ev);
  });

  $('#arquivo-origem').addEventListener('change', (ev) => {
    origemDevolucao.arquivo = ev.target.files[0] || null;
    atualizarPreviaOrigem();
  });

  $('#numero-origem').addEventListener('input', (ev) => {
    origemDevolucao.numero = ev.target.value;
    clearTimeout(esperaNumeroOrigem);
    esperaNumeroOrigem = setTimeout(atualizarPreviaOrigem, 600);
  });

  desenharEscolhidaOrigem();
}

function trocarModoOrigem(modo) {
  origemDevolucao.modo = modo;
  document.querySelectorAll('[data-modo-origem]').forEach((p) => { p.hidden = p.dataset.modoOrigem !== modo; });
  $({ SISTEMA: '#busca-origem', XML: '#arquivo-origem', DIGITADA: '#numero-origem' }[modo]).focus();
  mostrarErroDepara('');
  atualizarPreviaOrigem();
}

async function buscarNotaOrigem() {
  const termo = semZeros($('#busca-origem').value);
  const alvo = $('#resultado-origem');
  const minha = ++seqBuscaOrigem;
  if (!termo) { alvo.innerHTML = ''; return; }
  alvo.innerHTML = '<p class="dica">Buscando…</p>';
  let notas;
  try {
    notas = await api('/notas?status=EXPEDIDA,RETORNO_PARCIAL,CONCLUIDA&numero=' + encodeURIComponent(termo));
  } catch (e) {
    if (minha === seqBuscaOrigem) alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`;
    return;
  }
  if (minha !== seqBuscaOrigem) return;

  // O servidor procura por trecho do número. A NF exata vem primeiro e, sendo
  // só uma, já fica escolhida — ela aparece marcada e dá para trocar.
  const exatas = notas.filter((n) => semZeros(n.numero) === termo);
  const lista = [...exatas, ...notas.filter((n) => !exatas.includes(n))];
  notasOrigemAchadas = new Map(lista.map((n) => [String(n.id), n]));
  if (!lista.length) {
    alvo.innerHTML = '<p class="dica">Nenhuma NF expedida ou concluída com esse número. Se ela não passou ' +
                     'pelo sistema, envie o XML dela ou digite o número.</p>';
    return;
  }
  if (exatas.length === 1) escolherNotaOrigem(exatas[0]);
  else desenharResultadoOrigem();
}

function desenharResultadoOrigem() {
  const escolhida = origemDevolucao.nota ? String(origemDevolucao.nota.id) : '';
  $('#resultado-origem').innerHTML = [...notasOrigemAchadas.values()].map((n) => `
    <div class="opcao${String(n.id) === escolhida ? ' escolhida' : ''}" data-nota-origem="${escapar(n.id)}"
         role="button" tabindex="0">
      NF <strong>${escapar(n.numero)}</strong> — ${escapar(n.cliente)} ${marcaStatus(n.status)}
    </div>`).join('');
}

function escolherNotaOrigem(n) {
  origemDevolucao.nota = { id: n.id, numero: n.numero, cliente: n.cliente, status: n.status };
  desenharResultadoOrigem();
  desenharEscolhidaOrigem();
  atualizarPreviaOrigem();
}

function desenharEscolhidaOrigem() {
  const n = origemDevolucao.nota;
  $('#escolhida-origem').innerHTML = n
    ? `<p class="escolhida-origem">Escolhida: NF <strong>${escapar(n.numero)}</strong>${
        n.cliente ? ` — ${escapar(n.cliente)}` : ''} ${n.status ? marcaStatus(n.status) : ''}</p>`
    : '<p class="dica">Busque pelo número e escolha a NF na lista.</p>';
}

/** O campo da origem para o formulário, conforme o modo; null se ainda falta informar. */
function campoDaOrigem() {
  const o = origemDevolucao;
  if (!o) return null;
  if (o.modo === 'SISTEMA') return o.nota ? ['notaOrigemId', String(o.nota.id)] : null;
  if (o.modo === 'XML') return o.arquivo ? ['arquivoOrigem', o.arquivo] : null;
  const numero = o.numero.trim();
  return numero ? ['origemNumero', numero] : null;
}

/**
 * Prévia de novo, agora com a origem, só para mostrar o que o servidor diz
 * dela: outro CNPJ, NF que já tem devolução, XML que não é NF-e. A mesma NF
 * de venda pode ter mais de uma devolução — é aviso, não trava. Os itens e os
 * vínculos já marcados não são redesenhados.
 */
async function atualizarPreviaOrigem() {
  const alvo = $('#situacao-origem');
  const campo = campoDaOrigem();
  const minha = ++seqPreviaOrigem;
  if (!campo) { alvo.innerHTML = ''; return; }
  alvo.innerHTML = '<p class="dica">Conferindo a NF de origem…</p>';
  const corpo = new FormData();
  corpo.append('arquivo', arquivoDevolucao);
  corpo.append(...campo);
  let p;
  try {
    p = await api('/devolucoes/previa', { method: 'POST', body: corpo });
  } catch (e) {
    if (minha === seqPreviaOrigem) alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`;
    return;
  }
  if (minha !== seqPreviaOrigem || !p) return;

  const daOrigem = (p.avisos || []).filter((a) => !avisosSemOrigem.includes(a));
  const tipo = TIPO_ORIGEM[p.origemTipo] || p.origemTipo || '';
  alvo.innerHTML = `
    <p class="dica">Vai como origem: NF <strong>${escapar(p.notaOrigemNumero || '—')}</strong>${
      p.origemCliente ? ` — ${escapar(p.origemCliente)}` : ''}${
      tipo ? ` <span class="tipo-origem">${escapar(tipo)}</span>` : ''}${
      p.origemTipo === 'XML' && p.notaOrigemId ? ' · esta NF também está no sistema' : ''}${
      p.origemChave ? `<br>chave <span class="chave">${escapar(p.origemChave)}</span>` : ''}</p>
    ${daOrigem.length
      ? `<div class="faixa-alerta">${daOrigem.map(escapar).join('<br>')}
           <br><small>É aviso: dá para lançar assim mesmo.</small></div>`
      : ''}`;
}

/** Erro dentro do diálogo: o aviso flutuante fica atrás do fundo escurecido do modal. */
function mostrarErroDepara(texto) {
  const el = $('#erro-depara');
  el.textContent = texto;
  el.hidden = !texto;
}

$('#confirmar-depara').addEventListener('click', async () => {
  const vinculos = [...document.querySelectorAll('.vinculo')]
    .map((sel) => {
      const n = sel.dataset.item;
      return {
        nItem: Number(n),
        produtoId: Number(sel.value),
        quantidade: Number(document.querySelector(`.qtd-item[data-item="${n}"]`).value),
        unidade: document.querySelector(`.unidade-item[data-item="${n}"]`).value,
        motivo: document.querySelector(`.motivo-item[data-item="${n}"]`).value.trim() || null,
      };
    })
    .filter((v) => v.produtoId > 0);

  if (vinculos.length !== previaDevolucao.itens.length) {
    mostrarErroDepara('Vincule todos os itens antes de lançar.');
    return;
  }
  if (vinculos.some((v) => !Number.isInteger(v.quantidade) || v.quantidade < 1)) {
    mostrarErroDepara('Informe em todos os itens uma quantidade inteira, maior que zero.');
    return;
  }
  const origem = campoDaOrigem();
  if (!origem) {
    mostrarErroDepara('Informe a NF de origem: escolha a NF do sistema, envie o XML dela ou digite o número.');
    $('.bloco-origem').scrollIntoView({ block: 'nearest' });
    return;
  }
  mostrarErroDepara('');

  const corpo = new FormData();
  corpo.append('arquivo', arquivoDevolucao);
  corpo.append(...origem);
  corpo.append('vinculos', JSON.stringify(vinculos));
  corpo.append('motivo', $('#motivo-devolucao').value.trim());

  const botao = $('#confirmar-depara');
  botao.disabled = true;   // clique duplo mandaria a devolução duas vezes
  try {
    const d = await api('/devolucoes', { method: 'POST', body: corpo });
    $('#modal-depara').close();
    avisar(`Devolução ${d.numero} lançada para conferência`);
    await desenharDevolucoes();
    abrirDevolucao(d.id);
  } catch (e) {
    mostrarErroDepara(e.mensagem);
  } finally {
    botao.disabled = false;
  }
});

// --------------------------------------------------------------- retornos ---
//
// Retorno em três passos: registrar (aqui), conferir caixa a caixa (coletor)
// e dar a tratativa (aqui). Substitui a reentrega imediata, que devolvia o
// pallet ao estoque no olho — caixa extraviada no caminho só aparecia no
// carregamento seguinte. O POST /reentregas segue no servidor por
// compatibilidade, mas a tela não usa mais.

/** Registrar, tratar, cancelar e reabrir são de supervisor; o servidor também trava. */
const podeTratarRetorno = () =>
  !soLeitura() && !!usuario && (usuario.perfil === 'SUPERVISOR' || usuario.perfil === 'ADMIN');

const TRATATIVA = { REENTREGA: 'Reentrega', REFATURAMENTO: 'Refaturamento', DESCARTE: 'Descarte' };

let retornoParaRegistrar = null;   // corpo do POST, montado quando o diálogo abre
let retornosNaTela = [];
let retornoEmConferencia = false;  // há retorno sendo bipado: a lista se atualiza sozinha
let retornoEmFoco = null;          // RetornoResumo do diálogo de tratativa
let acaoRetorno = null;            // configuração do diálogo de ação com texto

// --- registrar ---------------------------------------------------------------

$('#form-retorno').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const numero = $('#nf-retorno').value.trim();
  if (!numero) return;
  const alvo = $('#conteudo-retorno');

  // O número vai para o servidor: a lista sem filtro para nas 200 NFs de
  // número maior, e NF expedida se acumula. Zeros à esquerda não contam.
  let notas;
  try {
    notas = await api('/notas?status=EXPEDIDA,RETORNO_PARCIAL&numero=' + encodeURIComponent(semZeros(numero)));
  } catch (e) { alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`; return; }
  const nota = notas.find((n) => semZeros(n.numero) === semZeros(numero));
  if (!nota) {
    alvo.innerHTML = `<p class="erro">NF ${escapar(numero)} não está expedida.</p>`;
    return;
  }

  let r, abertos;
  try {
    [r, abertos] = await Promise.all([
      api(`/notas/${nota.id}/expedidos`),
      api('/retornos').catch(() => []),
    ]);
  } catch (e) { alvo.innerHTML = `<p class="erro">${escapar(e.mensagem)}</p>`; return; }

  // O servidor recusa um segundo retorno com o primeiro aberto; avisar antes
  // poupa o supervisor de marcar pallet por pallet para ouvir um "não".
  const emAberto = abertos.find((x) => x.notaId === r.notaId);
  const pendentes = r.pallets.filter((p) => p.retornadoEm === null);
  const retornados = r.pallets.filter((p) => p.retornadoEm);
  const marcavel = podeTratarRetorno() && !emAberto && pendentes.length > 0;

  alvo.innerHTML = `
    <div class="cartao-info">
      <h3>NF ${escapar(r.notaNumero)} · ${escapar(r.cliente)} ${marcaStatus(r.status)}</h3>
      ${emAberto ? `
        <p class="faixa-alerta">Esta NF já tem um retorno
          ${escapar(emAberto.status.replace(/_/g, ' ').toLowerCase())}, registrado por
          ${escapar(emAberto.registradoPor)} em ${dataCurta(emAberto.registradoEm)}.
          Trate ou cancele esse retorno na lista abaixo antes de registrar outro.</p>` : ''}
      ${!pendentes.length
        ? '<p class="dica">Todos os pallets desta NF já retornaram.</p>'
        : marcavel ? `
        <div class="marcar-todos">
          <p class="dica">Marque os pallets que voltaram — sempre pallet inteiro.</p>
          <button type="button" class="texto" id="recusa-total">Recusa total — marcar todos</button>
        </div>
        ${pendentes.map((p) => `
          <label class="nota-expedir">
            <input type="checkbox" class="marcar-retorno" value="${escapar(p.palletCodigo)}"
                   data-caixas="${p.qtdCaixas}">
            <span class="dados"><b>${escapar(p.palletCodigo)}</b> ${p.qtdCaixas} caixas
              <div class="previa-pallet">expedido em ${dataCurta(p.expedidoEm)}</div></span>
          </label>`).join('')}
        <label>Motivo
          <input id="motivo-retorno" autocomplete="off" placeholder="cliente recusou por atraso na entrega">
        </label>
        <div class="resumo-transferir">
          <span>Voltam: <strong id="total-retorno">0</strong> de ${pendentes.length} pallet(s),
            <strong id="caixas-retorno">0</strong> caixa(s)</span>
          <button id="abrir-retorno" class="primario" disabled>Registrar retorno</button>
        </div>`
        : `
        ${podeTratarRetorno() ? '' : '<p class="dica">Registrar retorno é com supervisor ou administrador.</p>'}
        ${pendentes.map((p) => `
          <div class="previa-pallet">${escapar(p.palletCodigo)} · ${p.qtdCaixas} caixas ·
            expedido em ${dataCurta(p.expedidoEm)}</div>`).join('')}`}
      ${retornados.length ? `
        <h4 class="titulo-secao">Já retornados</h4>
        ${retornados.map((p) => `
          <div class="previa-pallet">${escapar(p.palletCodigo)} · ${p.qtdCaixas} caixas ·
            ${dataCurta(p.retornadoEm)}
            <button class="texto" data-reimprimir="${escapar(p.palletCodigo)}">Reimprimir cartão</button>
          </div>`).join('')}` : ''}
    </div>`;

  const caixas = [...alvo.querySelectorAll('.marcar-retorno')];
  const marcados = () => caixas.filter((c) => c.checked);
  const somaCaixas = (lista) => lista.reduce((t, c) => t + Number(c.dataset.caixas || 0), 0);

  const recalcular = () => {
    const m = marcados();
    $('#total-retorno').textContent = m.length;
    $('#caixas-retorno').textContent = somaCaixas(m);
    $('#abrir-retorno').disabled = m.length === 0;
    $('#recusa-total').textContent =
      m.length === caixas.length ? 'Desmarcar todos' : 'Recusa total — marcar todos';
  };
  caixas.forEach((c) => c.addEventListener('change', recalcular));

  // Recusa total é o caso mais comum (entrega atrasada): um clique em vez de
  // marcar vinte pallets, e o cursor já vai para o motivo.
  const todos = $('#recusa-total');
  if (todos) {
    todos.addEventListener('click', () => {
      const marcar = caixas.some((c) => !c.checked);
      caixas.forEach((c) => { c.checked = marcar; });
      recalcular();
      if (marcar) $('#motivo-retorno').focus();
    });
  }

  // O cartão costuma se perder no transporte; sem ele o pallet fica anônimo.
  alvo.querySelectorAll('[data-reimprimir]').forEach((b) =>
    b.addEventListener('click', async () => {
      try {
        // /cartao, e não /reimprimir: este reimprime a etiqueta de código de
        // barras, e o botão promete o cartão com os itens.
        const r = await api(`/pallets/${b.dataset.reimprimir}/cartao`, { method: 'POST' });
        if (r && r.impressao && r.impressao !== 'ENVIADA') {
          avisar(`Cartão de ${b.dataset.reimprimir} não saiu (${r.impressao})`, true);
        } else {
          avisar(`Cartão de ${b.dataset.reimprimir} reenviado à impressora`);
        }
      } catch (e) { avisar(e.mensagem, true); }
    }));

  const abrir = $('#abrir-retorno');
  if (abrir) {
    abrir.addEventListener('click', () => {
      const motivo = $('#motivo-retorno').value.trim();
      if (!motivo) { avisar('Informe o motivo do retorno', true); $('#motivo-retorno').focus(); return; }
      const m = marcados();
      const total = m.length === caixas.length;
      const ficaram = caixas.length - m.length;
      // O corpo é montado aqui, com o que o supervisor está vendo no diálogo,
      // e não relido das caixas de marcação na hora de confirmar.
      retornoParaRegistrar = { notaId: r.notaId, palletCodigos: m.map((c) => c.value), motivo };
      $('#titulo-retorno').textContent = `Registrar retorno da NF ${r.notaNumero}`;
      $('#corpo-retorno').innerHTML = `
        <div class="previa ${total ? 'aviso' : 'ok'}">
          <h4>${total ? 'Recusa total' : 'Retorno parcial'} · ${escapar(r.cliente)}</h4>
          ${m.map((c) => `<div class="previa-pallet">${escapar(c.value)} · ${c.dataset.caixas} caixas</div>`).join('')}
          <div class="previa-pallet"><strong>${m.length} pallet(s) · ${somaCaixas(m)} caixa(s)</strong>${
            total ? '' : ` — os outros ${ficaram} ficaram com o cliente`}</div>
        </div>
        <p class="dica">Motivo: “${escapar(motivo)}”. A NF sai das prontas para expedir até a tratativa.</p>`;
      $('#modal-retorno').showModal();
    });
  }
});

$('#confirmar-retorno').addEventListener('click', async () => {
  const botao = $('#confirmar-retorno');
  botao.disabled = true;   // clique duplo registraria dois retornos
  try {
    const r = await api('/retornos', { method: 'POST', corpo: retornoParaRegistrar });
    $('#modal-retorno').close();
    avisar(`Retorno da NF ${r.notaNumero} registrado — ${r.qtdPallets} pallet(s), ` +
           `${r.caixasEsperadas} caixa(s) para conferir no coletor`);
    $('#nf-retorno').value = '';
    $('#conteudo-retorno').innerHTML = '';
    verExpedicao();
  } catch (e) {
    avisar(e.mensagem, true);
  } finally {
    botao.disabled = false;
  }
});

// --- lista -------------------------------------------------------------------

$('#retornos-encerrados').addEventListener('change', desenharRetornos);

async function desenharRetornos() {
  const encerrados = $('#retornos-encerrados').checked;
  const status = encerrados
    ? 'EM_CONFERENCIA,AGUARDANDO_TRATATIVA,TRATADO,CANCELADO'
    : 'EM_CONFERENCIA,AGUARDANDO_TRATATIVA';
  const lista = await api('/retornos?status=' + status).catch((e) => { avisar(e.mensagem, true); return null; });
  if (!lista) return;

  retornosNaTela = lista;
  retornoEmConferencia = lista.some((r) => r.status === 'EM_CONFERENCIA');

  const alvo = $('#lista-retornos');
  alvo.innerHTML = lista.length ? `
    <table class="lista tabela-retornos">
      <thead><tr>
        <th>NF</th><th>Cliente</th><th>Motivo</th><th>Registrado</th><th>Status</th>
        <th>Conferência</th><th>Tratativa</th><th></th>
      </tr></thead>
      <tbody>${lista.map(linhaRetorno).join('')}</tbody>
    </table>`
    : `<p class="vazio">${encerrados ? 'Nenhum retorno registrado.' : 'Nenhum retorno em aberto.'}</p>`;

  const achar = (id) => retornosNaTela.find((x) => x.id === Number(id));
  alvo.querySelectorAll('[data-ver-retorno]').forEach((b) =>
    b.addEventListener('click', () => abrirConferenciaRetorno(Number(b.dataset.verRetorno))));
  alvo.querySelectorAll('[data-retorno-completo]').forEach((b) =>
    b.addEventListener('click', () => pedirRetornoCompleto(achar(b.dataset.retornoCompleto))));
  alvo.querySelectorAll('[data-cancelar-retorno]').forEach((b) =>
    b.addEventListener('click', () => pedirCancelarRetorno(achar(b.dataset.cancelarRetorno))));
  alvo.querySelectorAll('[data-tratativa-retorno]').forEach((b) =>
    b.addEventListener('click', () => abrirTratativa(achar(b.dataset.tratativaRetorno))));
  alvo.querySelectorAll('[data-desfazer-tratativa]').forEach((b) =>
    b.addEventListener('click', () => pedirDesfazerTratativa(achar(b.dataset.desfazerTratativa))));
}

function linhaRetorno(r) {
  // Duas contas diferentes: o que ainda falta bipar (só em conferência, dos
  // pallets abertos) e o que faltou de fato (pallet já fechado com falta
  // justificada). caixasFaltando é só a segunda.
  const aBipar = r.status === 'EM_CONFERENCIA'
    ? Math.max(0, r.caixasEsperadas - r.caixasConferidas - r.caixasFaltando)
    : 0;
  const falta = (aBipar > 0 ? `<div class="dica">${aBipar} caixa(s) a bipar</div>` : '')
    + (r.caixasFaltando > 0 ? `<div class="qtd-falta">${r.caixasFaltando} caixa(s) faltaram</div>` : '');
  const quemConferiu = r.conferenciaManual
    ? `<div class="dica">completo à mão · ${escapar(r.conferidoPor || '—')} ${dataCurta(r.conferidoEm)}</div>`
    : '';

  return `<tr class="${r.status === 'CANCELADO' ? 'inativo' : ''}">
    <td><strong>${escapar(r.notaNumero)}</strong></td>
    <td>${escapar(r.cliente)}</td>
    <td>${escapar(r.motivo)}</td>
    <td>${escapar(r.registradoPor)}<div class="dica">${dataCurta(r.registradoEm)}</div></td>
    <td>${marcaStatus(r.status)}</td>
    <td class="num">${r.palletsFechados}/${r.qtdPallets} pallets ·
      ${r.caixasConferidas}/${r.caixasEsperadas} caixas${falta}${quemConferiu}</td>
    <td>${resumoTratativa(r)}</td>
    <td class="acoes">${acoesRetorno(r)}</td>
  </tr>`;
}

function resumoTratativa(r) {
  const partes = [];
  if (r.tratativa) partes.push(`<strong>${escapar(TRATATIVA[r.tratativa] || r.tratativa)}</strong>`);
  if (r.nfsRefaturamento) partes.push(`<div class="previa-pallet">NF ${escapar(r.nfsRefaturamento)}</div>`);
  if (r.tratativa === 'REENTREGA' && r.caixasFaltando > 0) {
    partes.push(`<div class="qtd-falta">saiu com ${r.caixasConferidas} · falta de ${r.caixasFaltando} cx ` +
      'por NF de devolução do cliente</div>');
  }
  if (r.tratativaObs) partes.push(`<div class="justificativa">“${escapar(r.tratativaObs)}”</div>`);
  if (r.tratadoPor) partes.push(`<div class="dica">${escapar(r.tratadoPor)} · ${dataCurta(r.tratadoEm)}</div>`);
  return partes.length ? partes.join('') : '<span class="dica">—</span>';
}

/** Ver conferência é leitura e fica para todos; o resto, só para quem trata. */
function acoesRetorno(r) {
  const ver = `<button class="texto" data-ver-retorno="${r.id}">Ver conferência</button>`;
  if (!podeTratarRetorno()) return ver;
  const botao = (atributo, rotulo, perigo = false) =>
    `<button class="texto${perigo ? ' excluir' : ''}" ${atributo}="${r.id}">${rotulo}</button>`;

  switch (r.status) {
    case 'EM_CONFERENCIA':
      return ver + botao('data-retorno-completo', 'Retorno completo')
                 + botao('data-cancelar-retorno', 'Cancelar retorno', true);
    case 'AGUARDANDO_TRATATIVA':
      return ver + botao('data-tratativa-retorno', 'Tratativa')
                 + botao('data-cancelar-retorno', 'Cancelar retorno', true);
    case 'TRATADO':
      return ver + botao('data-desfazer-tratativa', 'Desfazer tratativa', true);
    default:
      return ver;
  }
}

// --- ações com texto: retorno completo, cancelar, desfazer tratativa ---------

/**
 * Um diálogo só para as três. `campo` é o nome que o servidor espera no corpo
 * (observacao ou justificativa); `obrigatorio` barra o texto vazio aqui, antes
 * da ida ao servidor.
 */
function abrirAcaoRetorno(cfg) {
  acaoRetorno = cfg;
  $('#titulo-acao-retorno').textContent = cfg.titulo;
  $('#texto-acao-retorno').textContent = cfg.texto;
  $('#aviso-acao-retorno').textContent = cfg.aviso || '';
  $('#aviso-acao-retorno').hidden = !cfg.aviso;
  $('#rotulo-acao-retorno').textContent = cfg.rotulo;
  $('#campo-acao-retorno').value = '';
  $('#campo-acao-retorno').placeholder = cfg.placeholder || '';
  const botao = $('#confirmar-acao-retorno');
  botao.textContent = cfg.botao;
  botao.className = cfg.perigo ? 'perigo' : 'primario';
  $('#modal-acao-retorno').showModal();
}

$('#confirmar-acao-retorno').addEventListener('click', async () => {
  const cfg = acaoRetorno;
  if (!cfg) return;
  const texto = $('#campo-acao-retorno').value.trim();
  if (cfg.obrigatorio && !texto) { avisar('Informe a justificativa', true); return; }
  const botao = $('#confirmar-acao-retorno');
  botao.disabled = true;
  try {
    const r = await api(cfg.caminho, { method: 'POST', corpo: { [cfg.campo]: texto || null } });
    $('#modal-acao-retorno').close();
    avisar(cfg.sucesso(r));
    verExpedicao();
  } catch (e) {
    avisar(e.mensagem, true);
  } finally {
    botao.disabled = false;
  }
});

function pedirRetornoCompleto(r) {
  if (!r) return;
  const abertos = r.qtdPallets - r.palletsFechados;
  abrirAcaoRetorno({
    titulo: `Retorno completo — NF ${r.notaNumero}`,
    texto: `Fecha ${abertos} pallet(s) ainda aberto(s) como conferido(s) completo(s), sem bipar caixa. ` +
           'Fica registrado como conferência manual, com o seu usuário e a hora. ' +
           'Use só quando a mercadoria voltou inteira e foi contada de outro jeito.' +
           (r.palletsFechados > 0 ? ' Os pallets já fechados continuam como estão.' : ''),
    rotulo: 'Observação (opcional)',
    placeholder: 'Ex.: contado na doca com o motorista',
    obrigatorio: false,
    botao: 'Marcar retorno completo',
    caminho: `/retornos/${r.id}/completo`,
    campo: 'observacao',
    sucesso: (x) => `Retorno da NF ${x.notaNumero} conferido à mão — agora ` +
                    x.status.replace(/_/g, ' ').toLowerCase(),
  });
}

function pedirCancelarRetorno(r) {
  if (!r) return;
  abrirAcaoRetorno({
    titulo: `Cancelar retorno — NF ${r.notaNumero}`,
    texto: 'Para retorno registrado por engano, quando a mercadoria não voltou. Os pallets voltam ' +
           'a constar como expedidos e saem da fila de endereçamento; a conferência feita até ' +
           'aqui deixa de valer.',
    rotulo: 'Justificativa',
    placeholder: 'Ex.: registrei na NF errada',
    obrigatorio: true,
    botao: 'Cancelar retorno',
    perigo: true,
    caminho: `/retornos/${r.id}/cancelar`,
    campo: 'justificativa',
    sucesso: (x) => `Retorno da NF ${x.notaNumero} cancelado`,
  });
}

function pedirDesfazerTratativa(r) {
  if (!r) return;
  // O risco muda com a tratativa: no refaturamento a mercadoria pode já ter
  // sido separada de novo, e aí desfazer põe no estoque o que já saiu em
  // outro pallet.
  const aviso = {
    REFATURAMENTO: 'Se a mercadoria já foi separada para as NFs novas, desfazer duplica o estoque: ' +
                   'os pallets do retorno voltam e a separação nova continua valendo.',
    DESCARTE: 'Só desfaça se a mercadoria ainda não foi descartada de fato — os pallets voltam ao estoque.',
  }[r.tratativa] || null;
  abrirAcaoRetorno({
    titulo: `Desfazer tratativa — NF ${r.notaNumero}`,
    texto: `Tratativa atual: ${TRATATIVA[r.tratativa] || r.tratativa || '—'}` +
           (r.nfsRefaturamento ? ` (NF ${r.nfsRefaturamento})` : '') + '. ' +
           'O retorno volta para aguardando tratativa; pallets que tinham saído do estoque voltam, ' +
           'sem endereço. Se a NF já foi expedida de novo, o desfazer é recusado.',
    aviso,
    rotulo: 'Justificativa',
    placeholder: 'Ex.: faturamento cancelou a NF nova',
    obrigatorio: true,
    botao: 'Desfazer tratativa',
    perigo: true,
    caminho: `/retornos/${r.id}/desfazer-tratativa`,
    campo: 'justificativa',
    sucesso: (x) => `Tratativa desfeita — retorno da NF ${x.notaNumero} aguardando tratativa`,
  });
}

// --- tratativa ---------------------------------------------------------------

const tipoTratativa = () => document.querySelector('input[name="tipo-tratativa"]:checked').value;

function abrirTratativa(r) {
  if (!r) return;
  retornoEmFoco = r;
  $('#titulo-tratativa').textContent = `Tratativa — NF ${r.notaNumero}`;
  $('#detalhe-tratativa').textContent =
    `${r.cliente} · ${r.qtdPallets} pallet(s) · ${r.caixasConferidas} de ${r.caixasEsperadas} ` +
    `caixa(s) conferida(s) · motivo: ${r.motivo}`;
  // Com falta, a reentrega sai com o que voltou e o cliente emite NF de
  // devolução da falta: o servidor tira do pallet as caixas que faltaram.
  // Nada voltou: não há o que reentregar (NADA_A_REENTREGAR).
  const comFalta = r.caixasFaltando > 0;
  const nadaVoltou = r.caixasConferidas === 0;
  $('#aviso-tratativa').textContent = nadaVoltou
    ? 'Nenhuma caixa voltou: não há o que reentregar. Descarte (a falta toda vai por NF de devolução do ' +
      'cliente) ou cancele o retorno, se foi lançado por engano.'
    : comFalta
      ? `${r.caixasFaltando} caixa(s) faltaram na conferência. Reentregando, a NF sai com as ` +
        `${r.caixasConferidas} que voltaram e o cliente emite NF de devolução da falta:`
      : '';
  $('#aviso-tratativa').hidden = !comFalta && !nadaVoltou;
  document.querySelector('input[name="tipo-tratativa"][value="REENTREGA"]').disabled = nadaVoltou;
  document.querySelector(
    `input[name="tipo-tratativa"][value="${nadaVoltou ? 'DESCARTE' : 'REENTREGA'}"]`,
  ).checked = true;
  if (comFalta && !nadaVoltou) listarFaltaNaTratativa(r);
  $('#nfs-refaturamento').value = '';
  $('#obs-tratativa').value = '';
  ajustarTratativa();
  $('#modal-tratativa').showModal();
}

/**
 * O que faltou, produto a produto: é o que a NF de devolução do cliente
 * precisa cobrir. Vem do detalhe do retorno, depois de o diálogo abrir.
 */
async function listarFaltaNaTratativa(r) {
  let d;
  try { d = await api(`/retornos/${r.id}`); } catch (e) { return; }
  if (retornoEmFoco !== r) return;
  const falta = new Map();
  d.pallets.filter((p) => p.status === 'CONFERIDO_FALTA' && !p.manual).forEach((p) =>
    p.produtos.forEach((pr) => {
      const qtd = pr.esperada - pr.conferida;
      if (qtd <= 0) return;
      const atual = falta.get(pr.produtoCodigo) || { descricao: pr.produtoDescricao, qtd: 0 };
      atual.qtd += qtd;
      falta.set(pr.produtoCodigo, atual);
    }));
  if (!falta.size) return;
  const lista = document.createElement('ul');
  falta.forEach((v, codigo) => {
    const li = document.createElement('li');
    li.textContent = `${v.qtd} cx · ${codigo} ${v.descricao}`;
    lista.appendChild(li);
  });
  $('#aviso-tratativa').appendChild(lista);
}

/** NF nova só no refaturamento; justificativa obrigatória só no descarte. */
function ajustarTratativa() {
  const tipo = tipoTratativa();
  $('#campo-nfs-refaturamento').hidden = tipo !== 'REFATURAMENTO';
  $('#rotulo-obs-tratativa').textContent =
    tipo === 'DESCARTE' ? 'Justificativa (obrigatória)' : 'Observação (opcional)';
  $('#obs-tratativa').placeholder = tipo === 'DESCARTE' ? 'Ex.: caixas molhadas no transporte'
    : tipo === 'REENTREGA' && retornoEmFoco?.caixasFaltando > 0 ? 'Ex.: NF de devolução 12345 do cliente, da falta'
    : '';
  const botao = $('#confirmar-tratativa');
  botao.className = tipo === 'DESCARTE' ? 'perigo' : 'primario';
  botao.textContent = { REENTREGA: 'Reentregar', REFATURAMENTO: 'Refaturar', DESCARTE: 'Descartar' }[tipo];
}

document.querySelectorAll('input[name="tipo-tratativa"]').forEach((i) =>
  i.addEventListener('change', ajustarTratativa));

$('#confirmar-tratativa').addEventListener('click', async () => {
  const tipo = tipoTratativa();
  const observacao = $('#obs-tratativa').value.trim();
  // Vírgula, ponto e vírgula ou espaço: vale o que vier colado do faturamento.
  const nfs = [...new Set($('#nfs-refaturamento').value.split(/[\s,;]+/).filter(Boolean))];
  if (tipo === 'REFATURAMENTO' && !nfs.length) {
    avisar('Informe o número das NFs novas', true);
    $('#nfs-refaturamento').focus();
    return;
  }
  if (tipo === 'DESCARTE' && !observacao) {
    avisar('Informe a justificativa do descarte', true);
    $('#obs-tratativa').focus();
    return;
  }

  const botao = $('#confirmar-tratativa');
  botao.disabled = true;
  try {
    const r = await api(`/retornos/${retornoEmFoco.id}/tratativa`, {
      method: 'POST',
      corpo: { tipo, nfs: tipo === 'REFATURAMENTO' ? nfs : null, observacao: observacao || null },
    });
    $('#modal-tratativa').close();
    avisar({
      REENTREGA: `NF ${r.notaNumero} de volta às prontas para expedir` + (r.caixasFaltando > 0
        ? ` com ${r.caixasConferidas} caixa(s); a falta de ${r.caixasFaltando} vai por NF de devolução do cliente`
        : ''),
      REFATURAMENTO: `Retorno da NF ${r.notaNumero} refaturado — NF ${r.nfsRefaturamento || nfs.join(', ')}`,
      DESCARTE: `Retorno da NF ${r.notaNumero} descartado — os pallets saíram do estoque`,
    }[tipo]);
    verExpedicao();
  } catch (e) {
    avisar(e.mensagem, true);
  } finally {
    botao.disabled = false;
  }
});

// --- ver conferência ---------------------------------------------------------

async function abrirConferenciaRetorno(id) {
  let d;
  try {
    d = await api('/retornos/' + id);
  } catch (e) { avisar(e.mensagem, true); return; }

  const r = d.retorno;
  // Reabrir só enquanto a tratativa não saiu: depois dela o pallet já pode
  // ter ido para a rua de novo ou para o descarte.
  const reabre = podeTratarRetorno() &&
    (r.status === 'EM_CONFERENCIA' || r.status === 'AGUARDANDO_TRATATIVA');

  $('#titulo-conferencia-retorno').innerHTML =
    `NF ${escapar(r.notaNumero)} · ${escapar(r.cliente)} ${marcaStatus(r.status)}`;
  const corpo = $('#corpo-conferencia-retorno');
  corpo.innerHTML = `
    <p class="dica">
      Registrado por ${escapar(r.registradoPor)} em ${dataCurta(r.registradoEm)} ·
      motivo: “${escapar(r.motivo)}”<br>
      ${r.palletsFechados}/${r.qtdPallets} pallet(s) fechado(s) ·
      ${r.caixasConferidas}/${r.caixasEsperadas} caixa(s) conferida(s)${
        r.caixasFaltando > 0 ? ` · <span class="qtd-falta">${r.caixasFaltando} faltando</span>` : ''}
      ${r.conferenciaManual ? `<br>Retorno completo marcado à mão por ${escapar(r.conferidoPor || '—')}
        em ${dataCurta(r.conferidoEm)} — os pallets abertos foram fechados sem bipar.` : ''}
      ${r.tratativa ? `<br>Tratativa: <strong>${escapar(TRATATIVA[r.tratativa] || r.tratativa)}</strong>${
        r.nfsRefaturamento ? ` · NF ${escapar(r.nfsRefaturamento)}` : ''}${
        r.tratadoPor ? ` · ${escapar(r.tratadoPor)} em ${dataCurta(r.tratadoEm)}` : ''}${
        r.tratativaObs ? ` · “${escapar(r.tratativaObs)}”` : ''}` : ''}
    </p>
    ${d.pallets.length ? d.pallets.map((p) => blocoPalletRetorno(p, reabre)).join('')
      : '<p class="vazio">Nenhum pallet neste retorno.</p>'}`;

  corpo.querySelectorAll('[data-reabrir-pallet]').forEach((b) =>
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const p = await api(`/retornos/pallets/${b.dataset.reabrirPallet}/reabrir`, { method: 'POST' });
        avisar(`${p.palletCodigo} reaberto — volta para a conferência no coletor`);
        abrirConferenciaRetorno(id);
        desenharRetornos();
      } catch (e) {
        avisar(e.mensagem, true);
        b.disabled = false;
      }
    }));

  const modal = $('#modal-conferencia-retorno');
  if (!modal.open) modal.showModal();
}

function blocoPalletRetorno(p, reabre) {
  const fechado = p.status !== 'ABERTO';
  // Pallet aberto está no meio da bipagem: a diferença é progresso, não falta.
  const curto = (conferida, esperada) => fechado && conferida < esperada ? 'qtd-falta' : '';
  const classe = p.status === 'CONFERIDO' ? 'ok' : p.status === 'CONFERIDO_FALTA' ? 'aviso' : '';
  const naoVoltou = p.status === 'CONFERIDO_FALTA' && p.qtdConferida === 0;

  return `<div class="previa pallet-retorno ${classe}">
    <h4>
      <b>${escapar(p.palletCodigo)}</b> ${marcaStatus(p.status)}
      ${p.manual ? '<span class="marca expedida" title="Fechado pelo “retorno completo”, sem bipar">MANUAL</span>' : ''}
      <span class="num ${curto(p.qtdConferida, p.qtdEsperada)}">${p.qtdConferida}/${p.qtdEsperada} caixas${
        naoVoltou ? ' — não voltou' : ''}</span>
      ${reabre && fechado ? `<button class="texto" data-reabrir-pallet="${p.id}">Reabrir pallet</button>` : ''}
    </h4>
    ${p.justificativa ? `<div class="justificativa">“${escapar(p.justificativa)}”</div>` : ''}
    <div class="itens-pallet">${(p.produtos || []).map((i) => `
      <div><strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}
        <span class="qtd ${curto(i.conferida, i.esperada)}">${i.conferida}/${i.esperada} cx</span></div>`).join('')}
    </div>
  </div>`;
}



// ---------------------------------------------------------------- estoque ---

let linhasEstoque = [];
let palletAberto = null;

const SITUACAO = {
  EM_ESTOQUE: ['Em estoque', 'concluida'],
  EXPEDIDO: ['Já saiu', 'expedida'],
  FORA_DE_CIRCULACAO: ['Fora de circulação', 'aberta'],
};

async function verEstoque() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-estoque');
  await desenharEstoque();
}

function filtrosDoEstoque() {
  const p = new URLSearchParams();
  p.set('situacao', $('#e-situacao').value);
  [['tipo', '#e-tipo'], ['nota', '#e-nota'], ['cliente', '#e-cliente'], ['box', '#e-box']]
    .forEach(([nome, sel]) => {
      const v = $(sel).value.trim();
      if (v) p.set(nome, v);
    });
  return p;
}

async function desenharEstoque() {
  const r = await api('/estoque?' + filtrosDoEstoque())
    .catch((e) => { avisar(e.mensagem, true); return null; });
  if (!r) return;

  linhasEstoque = r.linhas;
  palletAberto = null;

  $('#totais-estoque').textContent =
    `${r.totais.pallets} pallet(s) · ${r.totais.caixas} caixas` +
    (r.totais.semEndereco > 0 ? ` · ${r.totais.semEndereco} sem endereço` : '');

  const mostrarSaida = $('#e-situacao').value !== 'EM_ESTOQUE';

  $('#lista-estoque').innerHTML = r.linhas.length ? `
    <table class="lista">
      <thead><tr>
        <th>Pallet</th><th>NF</th><th>Cliente</th><th class="num">Caixas</th>
        <th>Endereço</th><th>Montado por</th>
        ${mostrarSaida ? '<th>Situação</th><th>Saída</th>' : ''}
      </tr></thead>
      <tbody>${r.linhas.map((l) => `
        <tr class="linha-pallet ${l.situacao !== 'EM_ESTOQUE' ? 'inativo' : ''}"
            data-pallet="${escapar(l.palletCodigo)}">
          <td><strong>${escapar(l.palletCodigo)}</strong>
            ${l.tipo === 'DEVOLUCAO' ? '<span class="marca aberta">DEV</span>' : ''}
            ${l.retorno ? `<div class="dica qtd-falta">${escapar(l.retorno)}</div>` : ''}</td>
          <td class="num">${escapar(l.notaNumero || '—')}
            ${l.bonificacao ? '<span class="marca supervisor">BONIF</span>' : ''}</td>
          <td>${escapar(l.cliente || '—')}</td>
          <td class="num">${l.qtdCaixas}</td>
          <td>${l.endereco ? escapar(l.endereco)
            : l.situacao === 'EM_ESTOQUE'
              // Vermelho só para quem está aqui: pallet que já saiu não tem
              // endereço por definição, e alarme falso ensina a ignorar alarme.
              ? '<span class="sem-endereco">sem endereço</span>'
              : '<span class="dica">—</span>'}</td>
          <td>${escapar(l.montadoPor || '—')}
            <div class="dica">${dataCurta(l.montadoEm)}</div></td>
          ${mostrarSaida ? `
            <td><span class="marca ${(SITUACAO[l.situacao] || ['', 'aberta'])[1]}">${
              escapar((SITUACAO[l.situacao] || [l.situacao])[0])}</span></td>
            <td>${l.saidaEm ? `${escapar(l.saidaPor || '—')}
                  <div class="dica">${dataCurta(l.saidaEm)}${
                    ({ BAIXA_DEVOLUCAO: ' · baixa de devolução',
                       REFATURAMENTO: ' · refaturado (retorno)',
                       DESCARTE: ' · descartado (retorno)' })[l.saidaTipo] || ''}</div>`
              : '<span class="dica">—</span>'}</td>` : ''}
        </tr>
        <tr class="conteudo-pallet" id="itens-${escapar(l.palletCodigo)}" hidden>
          <td colspan="${mostrarSaida ? 8 : 6}"></td>
        </tr>`).join('')}</tbody>
    </table>` : '<p class="vazio">Nada encontrado com estes filtros.</p>';

  // Clicar na linha abre o conteúdo: saber que um pallet tem 45 caixas não diz
  // quais produtos são, e é isso que o supervisor precisa antes de ir na rua.
  document.querySelectorAll('.linha-pallet').forEach((tr) =>
    tr.addEventListener('click', () => abrirConteudoPallet(tr.dataset.pallet)));
}

async function abrirConteudoPallet(codigo) {
  const linha = document.getElementById(`itens-${codigo}`);
  if (!linha) return;

  if (palletAberto === codigo) {
    linha.hidden = true;
    palletAberto = null;
    return;
  }
  document.querySelectorAll('.conteudo-pallet').forEach((l) => { l.hidden = true; });
  palletAberto = codigo;
  linha.hidden = false;
  linha.querySelector('td').innerHTML = '<span class="dica">carregando…</span>';

  try {
    const itens = await api(`/estoque/${codigo}/itens`);
    // O pallet de devolução guarda unidades — pode ser lata avulsa.
    const unidade = codigo.startsWith('P-DEV-') ? 'un' : 'cx';
    linha.querySelector('td').innerHTML = itens.length
      ? `<div class="itens-pallet">${itens.map((i) => `
          <div><strong>${escapar(i.produtoCodigo)}</strong> ${escapar(i.produtoDescricao)}
            <span class="qtd">${i.quantidade} ${unidade}</span></div>`).join('')}</div>`
      : '<span class="dica">Sem caixas neste pallet.</span>';
  } catch (e) {
    linha.querySelector('td').innerHTML = `<span class="erro">${escapar(e.mensagem)}</span>`;
  }
}

$('#filtro-estoque').addEventListener('submit', (ev) => {
  ev.preventDefault();
  desenharEstoque();
});

$('#e-situacao').addEventListener('change', desenharEstoque);
$('#e-tipo').addEventListener('change', desenharEstoque);

// Monta o CSV do que está na tela: os dados já vieram, não vale outra ida ao
// servidor só para reformatar.
$('#csv-estoque').addEventListener('click', () => {
  if (!linhasEstoque.length) { avisar('Nada para baixar', true); return; }
  const campo = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const cabecalho = ['Pallet', 'Tipo', 'Situacao', 'NF', 'Cliente', 'Caixas',
                     'Endereco', 'Montado por', 'Montado em', 'Saida por', 'Saida em', 'Saida', 'Retorno'];
  const corpo = linhasEstoque.map((l) => [
    l.palletCodigo, l.tipo, (SITUACAO[l.situacao] || [l.situacao])[0], l.notaNumero,
    l.cliente, l.qtdCaixas, l.endereco, l.montadoPor, l.montadoEm,
    l.saidaPor, l.saidaEm, l.saidaTipo, l.retorno,
  ].map(campo).join(';'));

  // BOM: sem ele o Excel em pt-BR abre os acentos errados.
  const blob = new Blob(['\ufeff' + [cabecalho.join(';'), ...corpo].join('\r\n')],
                        { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `estoque-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

// -------------------------------------------------------------- endereços ---
//
// Cadastro de boxes em lote: baixa a planilha, preenche no Excel e sobe. A
// lista (~740 boxes) vem inteira e é filtrada aqui, sem nova ida ao servidor.
// Ver e baixar: supervisor e administrador. Subir e gravar: só administrador
// (o servidor também trava).

let boxesCadastrados = [];
/** A planilha escolhida, já lida em memória: o Gravar manda os mesmos bytes que a prévia viu. */
let planilhaEnderecos = null;
let previaEnderecos = null;

const podeGravarEnderecos = () => !!usuario && usuario.perfil === 'ADMIN';
const ordemNatural = (a, b) => String(a).localeCompare(String(b), 'pt-BR', { numeric: true });
const ACAO_PLANILHA = { NOVO: ['novo', 'concluida'], ALTERADO: ['alterado', 'separacao'], ERRO: ['erro', 'divergente'] };

async function verEnderecos() {
  clearInterval(relogioAtualizacao);
  mostrarSecao('tela-enderecos');
  const admin = podeGravarEnderecos();
  $('#subir-planilha-enderecos').hidden = !admin;
  $('#so-admin-enderecos').hidden = admin;
  if (!admin) {
    // Outro login no mesmo PC: a prévia do administrador não fica para trás.
    planilhaEnderecos = null;
    previaEnderecos = null;
    $('#previa-enderecos').innerHTML = '';
  }
  await carregarEnderecos();
}

async function carregarEnderecos() {
  const lista = await api('/boxes').catch((e) => { avisar(e.mensagem, true); return null; });
  if (!lista) return;
  boxesCadastrados = lista;
  preencherDepositos();
  desenharEnderecos();
}

function preencherDepositos() {
  const sel = $('#en-deposito');
  const atual = sel.value;
  const depositos = new Map();
  boxesCadastrados.forEach((b) => {
    if (!depositos.has(b.depositoCodigo)) depositos.set(b.depositoCodigo, b.depositoNome);
  });
  sel.innerHTML = '<option value="">Todos</option>' + [...depositos]
    .sort((a, b) => ordemNatural(a[0], b[0]))
    .map(([codigo, nome]) =>
      `<option value="${escapar(codigo)}">${escapar(codigo)}${nome ? ` — ${escapar(nome)}` : ''}</option>`)
    .join('');
  sel.value = depositos.has(atual) ? atual : '';
  preencherRuas();
}

/** Só as ruas do depósito escolhido: rua de outro depósito na lista só daria tabela vazia. */
function preencherRuas() {
  const sel = $('#en-rua');
  const atual = sel.value;
  const dep = $('#en-deposito').value;
  const ruas = [...new Set(boxesCadastrados.filter((b) => !dep || b.depositoCodigo === dep).map((b) => b.rua))]
    .sort(ordemNatural);
  sel.innerHTML = '<option value="">Todas</option>' +
    ruas.map((r) => `<option value="${escapar(r)}">${escapar(r)}</option>`).join('');
  sel.value = ruas.includes(atual) ? atual : '';
}

function desenharEnderecos() {
  const dep = $('#en-deposito').value;
  const rua = $('#en-rua').value;
  const texto = $('#en-texto').value.trim().toUpperCase();
  const soAtivos = $('#en-ativos').checked;
  const lista = boxesCadastrados.filter((b) =>
    (!dep || b.depositoCodigo === dep) &&
    (!rua || b.rua === rua) &&
    (!soAtivos || b.ativo) &&
    (!texto || String(b.codigo).toUpperCase().includes(texto) ||
               String(b.codigoBarras || '').toUpperCase().includes(texto)));

  const soma = (campo) => lista.reduce((t, b) => t + (Number(b[campo]) || 0), 0);
  const ativos = lista.filter((b) => b.ativo).length;
  $('#totais-enderecos').textContent =
    `${lista.length} de ${boxesCadastrados.length} boxes · ${ativos} ativo(s) · ` +
    `${soma('niveis')} posições · ${soma('ocupadas')} ocupada(s)`;

  $('#lista-enderecos').innerHTML = lista.length ? `
    <div class="rolagem">
    <table class="lista tabela-enderecos">
      <thead><tr>
        <th>Box</th><th>Código de barras</th><th>Depósito</th><th>Rua</th><th>Tipo</th>
        <th>Níveis</th><th>Ocupados</th><th>Situação</th>
      </tr></thead>
      <tbody>${lista.map((b) => `<tr class="${b.ativo ? '' : 'inativo'}">
        <td><strong>${escapar(b.codigo)}</strong></td>
        <td class="cel-codigo">${escapar(b.codigoBarras)}</td>
        <td>${escapar(b.depositoCodigo)}${
          b.depositoNome ? ` <span class="dica-celula">${escapar(b.depositoNome)}</span>` : ''}</td>
        <td>${escapar(b.rua)}</td>
        <td>${escapar(b.tipo)}</td>
        <td class="num">${escapar(b.niveis)}</td>
        <td class="num">${escapar(b.ocupadas)}</td>
        <td>${b.ativo ? 'ativo' : 'inativo'}</td>
      </tr>`).join('')}</tbody>
    </table>
    </div>`
    : `<p class="vazio">${boxesCadastrados.length
        ? 'Nenhum box com estes filtros.'
        : 'Nenhum box cadastrado. Baixe o modelo, preencha e suba a planilha.'}</p>`;
}

$('#filtro-enderecos').addEventListener('submit', (ev) => ev.preventDefault());
$('#en-deposito').addEventListener('change', () => { preencherRuas(); desenharEnderecos(); });
$('#en-rua').addEventListener('change', desenharEnderecos);
$('#en-ativos').addEventListener('change', desenharEnderecos);
$('#en-texto').addEventListener('input', desenharEnderecos);

function ligarDownload(seletor, caminho, nome) {
  const botao = $(seletor);
  botao.addEventListener('click', async () => {
    botao.disabled = true;
    try { await baixarArquivo(caminho, nome); } finally { botao.disabled = false; }
  });
}
ligarDownload('#baixar-modelo-enderecos', '/boxes/planilha', 'modelo-enderecos.xlsx');
ligarDownload('#baixar-atuais-enderecos', '/boxes/planilha?atuais=1', 'enderecos-cadastrados.xlsx');

$('#subir-planilha-enderecos').addEventListener('click', () => $('#arquivo-enderecos').click());

$('#arquivo-enderecos').addEventListener('change', async (ev) => {
  const arquivo = ev.target.files[0];
  ev.target.value = '';
  if (!arquivo || !podeGravarEnderecos()) return;
  // Lida agora: se a planilha for salva de novo no Excel entre a prévia e o
  // Gravar, grava-se o que a prévia mostrou, e não a versão nova sem prévia.
  let bytes;
  try {
    bytes = await arquivo.arrayBuffer();
  } catch {
    avisar('Não foi possível ler o arquivo. Feche-o no Excel e tente de novo.', true);
    return;
  }
  planilhaEnderecos = new File([bytes], arquivo.name, { type: arquivo.type });
  await preverPlanilhaEnderecos();
});

async function preverPlanilhaEnderecos(recado = null) {
  const alvo = $('#previa-enderecos');
  alvo.innerHTML = `<p class="dica">Conferindo ${escapar(planilhaEnderecos.name)}…</p>`;
  const corpo = new FormData();
  corpo.append('arquivo', planilhaEnderecos);
  try {
    previaEnderecos = await api('/boxes/planilha/previa', { method: 'POST', body: corpo });
    if (!previaEnderecos) throw { mensagem: 'Resposta vazia do servidor.' };
  } catch (e) {
    previaEnderecos = null;
    alvo.innerHTML = `
      <div class="previa rejeitado">
        <h4>${escapar(planilhaEnderecos.name)}</h4>
        ${recado ? `<p>${escapar(recado)}</p>` : ''}
        ${escapar(e.mensagem)}
      </div>`;
    return;
  }
  desenharPreviaEnderecos(recado);
}

function desenharPreviaEnderecos(recado = null) {
  const p = previaEnderecos;
  const alvo = $('#previa-enderecos');
  const mudancas = p.novos + p.alterados;
  const podeGravar = p.erros === 0 && mudancas > 0;
  // Erros primeiro: são eles que impedem gravar, e no meio de centenas de
  // linhas novas passariam despercebidos.
  const linhas = [...(p.linhas || [])].sort((a, b) =>
    (a.acao === 'ERRO' ? 0 : 1) - (b.acao === 'ERRO' ? 0 : 1) || a.linha - b.linha);

  alvo.innerHTML = `
    <div class="cartao-info previa-planilha">
      <h3>Prévia de ${escapar(planilhaEnderecos.name)}</h3>
      ${recado ? `<p class="faixa-alerta">${escapar(recado)}</p>` : ''}
      <div class="contagens-planilha">
        <span class="marca concluida">${escapar(p.novos)} novo(s)</span>
        <span class="marca separacao">${escapar(p.alterados)} alterado(s)</span>
        <span class="marca aberta">${escapar(p.iguais)} igual(is)</span>
        <span class="marca ${p.erros ? 'divergente' : 'aberta'}">${escapar(p.erros)} com erro</span>
      </div>
      <p class="dica">${p.erros
        ? 'Corrija as linhas com erro na planilha e suba de novo. Com erro, nada é gravado — nem as linhas certas.'
        : mudancas ? 'Nada foi gravado ainda: confira e clique em Gravar.'
        : 'A planilha é igual ao cadastro: não há o que gravar.'}
        A linha é a do Excel (a 1 é o cabeçalho).</p>
      ${linhas.length ? `
        <div class="rolagem">
        <table class="lista tabela-planilha">
          <thead><tr><th>Linha</th><th>Box</th><th>Ação</th><th>Detalhe</th></tr></thead>
          <tbody>${linhas.map((l) => {
            const [rotulo, classe] = ACAO_PLANILHA[l.acao] || [String(l.acao || '').toLowerCase(), 'aberta'];
            return `<tr class="${l.acao === 'ERRO' ? 'linha-erro' : ''}">
              <td class="num">${escapar(l.linha)}</td>
              <td class="cel-codigo">${escapar(l.box || '—')}</td>
              <td><span class="marca ${classe}">${escapar(rotulo)}</span></td>
              <td>${escapar(l.detalhe)}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
        </div>` : ''}
      <div class="acoes-planilha fim">
        <button type="button" class="texto" id="descartar-planilha">Descartar</button>
        <button type="button" class="primario" id="gravar-planilha"${podeGravar ? '' : ' disabled'}>
          ${mudancas ? `Gravar ${escapar(mudancas)} box(es)` : 'Nada a gravar'}</button>
      </div>
    </div>`;

  $('#descartar-planilha').addEventListener('click', () => {
    planilhaEnderecos = null;
    previaEnderecos = null;
    alvo.innerHTML = '';
  });
  $('#gravar-planilha').addEventListener('click', gravarPlanilhaEnderecos);
}

async function gravarPlanilhaEnderecos() {
  if (!planilhaEnderecos || !previaEnderecos) return;
  const botao = $('#gravar-planilha');
  botao.disabled = true;
  botao.textContent = 'Gravando…';
  const corpo = new FormData();
  corpo.append('arquivo', planilhaEnderecos);
  try {
    const r = await api('/boxes/planilha', { method: 'POST', body: corpo });
    const nome = planilhaEnderecos.name;
    planilhaEnderecos = null;
    previaEnderecos = null;
    $('#previa-enderecos').innerHTML = `
      <div class="previa ok">
        <h4>${escapar(nome)} gravada</h4>
        ${escapar(r && r.novos)} box(es) novo(s) · ${escapar(r && r.alterados)} alterado(s) ·
        ${escapar(r && r.iguais)} sem mudança
      </div>`;
    avisar(`Planilha gravada: ${r ? r.novos : 0} novo(s), ${r ? r.alterados : 0} alterado(s)`);
    carregarEnderecos();
  } catch (e) {
    if (e.codigo === 'PLANILHA_COM_ERROS') {
      // O cadastro mudou entre a prévia e o Gravar (outro administrador, um
      // pallet endereçado no box): a prévia refeita mostra as linhas que agora
      // dão erro. Nada foi gravado.
      await preverPlanilhaEnderecos(e.mensagem);
    } else if (semResposta(e)) {
      // Gravar a mesma planilha de novo é seguro — o que já entrou vira "igual" —,
      // mas o operador precisa ver o cadastro de agora antes.
      carregarEnderecos();
      await preverPlanilhaEnderecos('Sem resposta do servidor ao gravar. A prévia foi refeita com o ' +
                                    'cadastro de agora: o que já tiver sido gravado aparece como igual.');
    } else {
      desenharPreviaEnderecos(e.mensagem);
    }
  }
}


if (token && usuario) abrirApp(); else $('#tela-login').hidden = false;

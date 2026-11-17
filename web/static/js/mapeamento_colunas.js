/* Mapeamento de colunas dos relatórios (20/10/2026).

   Compartilhado entre o painel de carretas e o Expedito. A página fornece o
   modal e o estilo (classes .mc-*); aqui ficam a busca, a montagem dos campos
   e a gravação. O que o usuário escolhe vale para os próximos arquivos
   daquele relatório; "Automático" devolve o campo à leitura pelo nome. */
(function(){
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const COMO = {
    salvo: 'escolhida no mapeamento',
    exato: 'achada pelo nome',
    detalhe: 'achada pelo nome',
    automatico: 'achada pelo cabeçalho',
    posicao: 'lida pela posição — confira',
  };

  async function _json(resp){
    let d = null;
    try { d = await resp.json(); } catch(e) { d = null; }
    if(!resp.ok || !d || d.error) throw new Error((d && d.error) || ('HTTP ' + resp.status));
    return d;
  }

  /* {tipos: [...], pode_editar} dos relatórios de um módulo */
  async function listar(modulo){
    return _json(await fetch('/api/mapeamento?modulo=' + encodeURIComponent(modulo)));
  }

  /* HTML de um relatório. opts: {faltando: [{campo}], colunas: [...], pode_editar} */
  function montar(desc, opts){
    opts = opts || {};
    const ua = desc.ultimo_arquivo || {};
    const colunas = (opts.colunas && opts.colunas.length) ? opts.colunas : (ua.colunas || []);
    const falta = new Set((opts.faltando || []).map(f => f.campo));
    const travado = opts.pode_editar === false ? ' disabled' : '';
    let h = `<div class="mc-tipo" data-tipo="${esc(desc.tipo)}">`;
    h += ua.arquivo
      ? `<div class="mc-sub">Colunas do último arquivo lido: <b>${esc(ua.arquivo)}</b>${ua.carregado_em ? ' · ' + esc(ua.carregado_em) : ''}</div>`
      : `<div class="mc-sub">Nenhum arquivo deste relatório foi lido ainda. Envie um para listar as colunas.</div>`;
    h += '<div class="mc-grade">';
    h += '<div class="mc-cab">Campo do sistema</div><div class="mc-cab">Coluna do arquivo</div><div class="mc-cab">Situação</div>';
    desc.campos.forEach(c => {
      const achada = c.coluna && c.como !== 'salvo' ? c.coluna : '';
      const opc = [`<option value="">${achada ? 'Automático: ' + esc(achada) : 'Automático'}</option>`]
        .concat(colunas.map(col => `<option value="${esc(col)}"${col === c.salvo ? ' selected' : ''}>${esc(col)}</option>`));
      if(c.salvo && !colunas.includes(c.salvo))
        opc.push(`<option value="${esc(c.salvo)}" selected>${esc(c.salvo)} (ausente no último arquivo)</option>`);
      let sit;
      if(falta.has(c.campo) || (!c.coluna && c.obrigatorio)) sit = '<span class="mc-falta">não encontrada</span>';
      else if(c.coluna) sit = `<span class="${c.como === 'posicao' ? 'mc-aviso' : 'mc-ok'}">${esc(COMO[c.como] || 'achada')}</span>`;
      else sit = '<span class="mc-vazio">sem coluna</span>';
      h += `<div class="mc-rot${falta.has(c.campo) ? ' mc-destaque' : ''}">${esc(c.rotulo)}${c.obrigatorio ? ' <b title="obrigatório">*</b>' : ''}</div>
            <select class="mc-sel" data-campo="${esc(c.campo)}" data-inicial="${esc(c.salvo || '')}"${travado}>${opc.join('')}</select>
            <div class="mc-sit">${sit}</div>`;
    });
    h += '</div></div>';
    return h;
  }

  /* Grava o que mudou no(s) relatório(s) dentro de `el`. Devolve quantos
     campos foram alterados. */
  async function salvar(el, csrf){
    let n = 0;
    for(const t of el.querySelectorAll('.mc-tipo')){
      const campos = {};
      t.querySelectorAll('.mc-sel').forEach(s => {
        if(s.value !== (s.dataset.inicial || '')) campos[s.dataset.campo] = s.value;
      });
      if(!Object.keys(campos).length) continue;
      await _json(await fetch('/api/mapeamento/' + encodeURIComponent(t.dataset.tipo), {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf || ''},
        body: JSON.stringify({campos}),
      }));
      n += Object.keys(campos).length;
    }
    return n;
  }

  window.MapeamentoColunas = {listar, montar, salvar, esc};
})();

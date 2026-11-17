/**
 * Mapa 3D do Mapa de Estoque (Fase 5, 31/08/2026) — "entrar na câmara
 * virtualmente", pedido do usuário.
 *
 * Roda 100% no navegador (Three.js): a VM só serve os arquivos estáticos,
 * nada de renderização no servidor — importante porque a VM é uma e2-micro
 * (ver BACKLOG, investigação de lentidão 31/08/2026).
 *
 * Reaproveita EXATAMENTE os mesmos dados do mapa 2D:
 *   - layout  (GET /api/mapa-estoque/layout)   → estrutura física
 *   - ocupação(GET /api/mapa-estoque/ocupacao) → quem ocupa o quê no dia
 * Nenhuma rota nova no backend.
 *
 * Convenção de eixos (mesma leitura do 2D, só que em volume):
 *   X = ao longo da rua (box 1, box 2, box 3...)
 *   Y = altura (nível 1 no chão, subindo)
 *   Z = profundidade dentro do rack + separação entre ruas (corredores)
 */
import * as THREE from 'three';
import { OrbitControls } from './OrbitControls.js';

// ── escala (metros aproximados de um rack porta-palete real) ──────────
const BOX_W      = 1.30;  // largura de um box ao longo da rua
const LEVEL_H    = 1.60;  // altura de um nível
const DEPTH_UNIT = 1.30;  // profundidade de um nível
// corredor de empilhadeira — largo de propósito: o usuário quer que andar
// por ele lembre um street view, "não muito junto, pra dar a dimensão de
// olhar pessoalmente" (31/08/2026)
const AISLE      = 4.70;
// ruas SEM corredor entre elas são um par costa-a-costa: praticamente
// se tocam (ex.: rua B e C na Câmara 3)
const RUA_GAP    = 0.18;
const OLHO_H     = 1.70;  // altura dos olhos no modo primeira pessoa
const ALT_LONGARINA = 0.22;  // a etiqueta tem que caber DENTRO dela
const FOV_PADRAO = 60, FOV_MIN = 22, FOV_MAX = 85;   // zoom de lente (roda do mouse)
// área livre entre a porta e o começo dos racks — na câmara real existe
// esse vão de manobra, e ao entrar ele dá a visão macro que o usuário
// pediu (31/08/2026), em vez de nascer colado na primeira posição
const ENTRADA = 8.00;

const COR_VAZIO     = 0x9aa5b1;
const COR_BLOQUEADO = 0x4b5563;
const COR_MISTO     = 0x9CA3AF;
const PALETA_EMPRESAS = [0x3B82F6, 0xF97316, 0x10B981, 0xA855F7,
                          0xEF4444, 0xEAB308, 0x06B6D4, 0xEC4899];

export function corEmpresa3D(nome){
  let hash = 0;
  for(let i = 0; i < nome.length; i++) hash = (hash * 31 + nome.charCodeAt(i)) >>> 0;
  return PALETA_EMPRESAS[hash % PALETA_EMPRESAS.length];
}

/**
 * Etiqueta do box como no rack real (pedido do usuário 31/08/2026):
 * fundo branco, nome em preto e um código de barras embaixo. As barras são
 * uma SIMULAÇÃO visual derivada do próprio nome — não é Code128 de verdade
 * (não faria sentido: ninguém vai bipar a tela), mas fica determinístico,
 * então o mesmo box desenha sempre a mesma etiqueta.
 */
const ETIQ_W = 384, ETIQ_H = 96;   // 4:1 — cabe na altura da longarina

function criarTexturaEtiqueta(nome){
  const cv = document.createElement('canvas');
  cv.width = ETIQ_W; cv.height = ETIQ_H;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);   // fora do arredondado = transparente

  // plaquinha branca de cantos arredondados
  const r = 14;
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(cv.width, 0, cv.width, cv.height, r);
  ctx.arcTo(cv.width, cv.height, 0, cv.height, r);
  ctx.arcTo(0, cv.height, 0, 0, r);
  ctx.arcTo(0, 0, cv.width, 0, r);
  ctx.closePath();
  ctx.fillStyle = '#ffffff';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#c8c8c8';
  ctx.stroke();

  ctx.fillStyle = '#000000';
  ctx.font = 'bold 40px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(nome, cv.width / 2, 8);

  // barras: largura pseudo-aleatória a partir do hash do nome
  let h = 0;
  for(let i = 0; i < nome.length; i++) h = (h * 131 + nome.charCodeAt(i)) >>> 0;
  let x = 22;
  while(x < cv.width - 22){
    h = (h * 1103515245 + 12345) >>> 0;
    const larg = 2 + (h % 4);
    if((h >> 3) % 3 !== 0) ctx.fillRect(x, 56, larg, 30);
    x += larg + 1 + ((h >> 5) % 3);
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.anisotropy = 4;
  return tex;
}

/**
 * Letra da rua PINTADA NO CHÃO, na cabeceira dela (pedido do usuário
 * 31/08/2026: "coloca só a letra das ruas no chão, pra facilitar
 * identificar"). É o mesmo recurso do galpão real — a sigla marcada no
 * piso na entrada de cada rua. Fundo transparente: só a letra aparece.
 */
const LETRA_PX = 256;

/**
 * Textura RAJADA (listras diagonais) pras posições de fornecedor
 * terceiro — o slot do tipo "outros" (pedido do usuário 01/09/2026:
 * "na visualização 3D deve ter um formato diferente, rajado por
 * exemplo"). Mantém a cor da empresa, só quebra o preenchimento sólido,
 * então continua dando pra ler de quem é a carga e ao mesmo tempo salta
 * aos olhos que aquela posição não é do estoque próprio.
 */
const RAJA_PX = 64;

function criarTexturaRajada(hex){
  const cv = document.createElement('canvas');
  cv.width = RAJA_PX; cv.height = RAJA_PX;
  const ctx = cv.getContext('2d');
  const cor = '#' + hex.toString(16).padStart(6, '0');
  ctx.fillStyle = cor;
  ctx.fillRect(0, 0, RAJA_PX, RAJA_PX);
  // faixas claras a 45°, desenhadas além da borda pra fechar as quinas
  ctx.strokeStyle = 'rgba(255,255,255,.72)';
  ctx.lineWidth = 7;
  for(let i = -RAJA_PX; i < RAJA_PX * 2; i += 16){
    ctx.beginPath();
    ctx.moveTo(i, -4);
    ctx.lineTo(i + RAJA_PX + 8, RAJA_PX + 4);
    ctx.stroke();
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 4;
  return tex;
}


function criarTexturaLetraChao(sigla){
  const cv = document.createElement('canvas');
  cv.width = LETRA_PX; cv.height = LETRA_PX;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  const txt = String(sigla || '?').toUpperCase();
  // tamanho da fonte encolhe se a sigla tiver mais de uma letra
  ctx.font = `bold ${txt.length > 1 ? 130 : 190}px system-ui, Arial, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 14;
  ctx.strokeStyle = 'rgba(255,255,255,.85)';   // contorno claro pra destacar do piso
  ctx.strokeText(txt, cv.width / 2, cv.height / 2 + 6);
  ctx.fillStyle = '#1D4E9C';                    // mesmo azul dos montantes
  ctx.fillText(txt, cv.width / 2, cv.height / 2 + 6);
  const tex = new THREE.CanvasTexture(cv);
  tex.anisotropy = 4;
  return tex;
}

export function criarMapa3D(container, callbacks = {}){
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = false; // sombra custa caro e agrega pouco aqui
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xeef2f7);
  scene.fog = new THREE.Fog(0xeef2f7, 40, 120);

  const camera = new THREE.PerspectiveCamera(FOV_PADRAO, 1, 0.1, 500);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 2.1));
  const luz = new THREE.DirectionalLight(0xffffff, 1.1);
  luz.position.set(18, 30, 14);
  scene.add(luz);

  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true;
  orbit.dampingFactor = 0.08;
  orbit.maxPolarAngle = Math.PI / 2 - 0.02;  // não deixa passar pro subsolo

  // Modo a pé no esquema do Street View (pedido do usuário 31/08/2026):
  // clicar-e-arrastar pra olhar em volta, clicar no chão pra andar. Sem
  // pointer lock de propósito — trava o cursor, depende de gesto do
  // usuário pra engatar e o Esc dava saída confusa; arrastar é o que a
  // pessoa já espera de um street view.
  let yaw = 0, pitch = 0;
  let arrastando = false, arrastouPx = 0, ultimoX = 0, ultimoY = 0;
  const SENS = 0.0026;
  const PITCH_MAX = Math.PI / 2 - 0.06;

  function aplicarRotacaoFP(){
    camera.rotation.order = 'YXZ';
    camera.rotation.set(pitch, yaw, 0);
  }
  function andarFrente(d){
    camera.position.x -= Math.sin(yaw) * d;
    camera.position.z -= Math.cos(yaw) * d;
  }
  function andarLado(d){
    camera.position.x += Math.cos(yaw) * d;
    camera.position.z -= Math.sin(yaw) * d;
  }

  const raycaster = new THREE.Raycaster();
  const ponteiro = new THREE.Vector2();

  // cache de material por cor — sem isso cada cubo criaria seu próprio
  // material (centenas de materiais iguais, desperdício de memória/draw)
  const matCache = new Map();
  function matRajado(hex){
    if(!rajadoCache.has(hex)){
      const tex = criarTexturaRajada(hex);
      texturasEtiqueta.push(tex);   // mesma lista de descarte das etiquetas
      rajadoCache.set(hex, new THREE.MeshLambertMaterial({ map: tex }));
    }
    return rajadoCache.get(hex);
  }

  function matCor(hex, opts = {}){
    const chave = hex + '|' + JSON.stringify(opts);
    if(!matCache.has(chave)){
      matCache.set(chave, new THREE.MeshLambertMaterial({ color: hex, ...opts }));
    }
    return matCache.get(chave);
  }

  let grupoCena = new THREE.Group();
  scene.add(grupoCena);
  let pisoMesh = null;            // alvo do clique-pra-andar
  let destinoAndar = null;        // {de, para, t} enquanto desliza
  // CanvasTexture não é liberada por geometry.dispose() — sem essa lista,
  // cada troca de câmara/dia vazaria ~100 texturas de etiqueta na GPU
  let texturasEtiqueta = [];
  // materiais rajados são por CENA (têm textura própria e limparGrupo os
  // destrói junto com o grupo) — não podem entrar no matCache compartilhado
  let rajadoCache = new Map();
  let selecionaveis = [];   // cubos clicáveis (posições ocupadas)
  let spawn = new THREE.Vector3(0, OLHO_H, 0);
let alvoOlhar = new THREE.Vector3(0, OLHO_H, 10);  // pra onde encarar ao entrar
  let limites = { minX: 0, maxX: 10, minZ: 0, maxZ: 10 };
  let modo = 'orbit';
  const teclas = { w: false, a: false, s: false, d: false, shift: false };
  let animando = true;

  // ── construção da cena a partir dos dados ──────────────────────────
  function render(camara, itens, opcoes = {}){
    limparGrupo();
    selecionaveis = [];
    if(!camara) return;

    const ocupPorBox = {};
    for(const it of (itens || [])){
      (ocupPorBox[it.box_id] = ocupPorBox[it.box_id] || []).push(it);
    }

    const geoCubo   = new THREE.BoxGeometry(BOX_W * 0.86, LEVEL_H * 0.72, DEPTH_UNIT * 0.86);
    const geoVazio  = new THREE.BoxGeometry(BOX_W * 0.86, LEVEL_H * 0.72, DEPTH_UNIT * 0.86);
    const matVazio  = new THREE.MeshBasicMaterial({
      color: COR_VAZIO, wireframe: true, transparent: true, opacity: 0.10 });

    // ── pré-cálculo: onde cada rua começa e de que lado fica o corredor ──
    // O layout real é assim (descrição do usuário 31/08/2026):
    //   Rua A colada na parede · CORREDOR · Rua B | Rua C (costa a costa)
    //   · CORREDOR · Rua D colada na parede
    // Ou seja, racks vêm em PARES costa-a-costa e o corredor separa os
    // pares. A etiqueta de cada rua fica SEMPRE virada pro corredor que
    // atende aquela rua — num par costa-a-costa, as duas ruas olham pra
    // lados opostos. É isso que `corredor_apos` (do cadastro) determina.
    // `inverter_ordem` espelha a câmara (a ordem das ruas na planilha de
    // origem pode sair ao contrário da realidade). Ao inverter, o "corredor
    // depois" de uma rua passa a ser o corredor_apos da SEGUINTE na lista
    // invertida — é o mesmo espaço físico, só muda de que lado se olha.
    // Mesma lógica da prévia em mapa_estoque_layout.html (ruasOrdenadas).
    const listaRuas = camara.inverter_ordem ? [...camara.ruas].reverse() : camara.ruas;
    const corredorDe = (idx) => camara.inverter_ordem
      ? (idx < listaRuas.length - 1 ? !!listaRuas[idx + 1].corredor_apos : false)
      : !!listaRuas[idx].corredor_apos;

    const ruasPos = [];
    let z = 0, maxX = 0, maxY = 0;
    listaRuas.forEach((rua, idx) => {
      const maxProf = Math.max(1, ...rua.boxes.map(b => b.profundidade));
      const maxAlt  = Math.max(1, ...rua.boxes.map(b => b.altura_efetiva || b.altura));
      const fundoRua = maxProf * DEPTH_UNIT;
      const corredorDepois = corredorDe(idx);
      const corredorAntes  = idx > 0 && corredorDe(idx - 1);
      // etiqueta olha pro corredor: se o corredor está depois, a face útil
      // é a de trás (+Z); senão é a da frente (−Z)
      const etiquetaAtras = corredorDepois && !corredorAntes;
      ruasPos.push({ rua, z0: z, fundoRua, maxAlt, maxProf, corredorDepois, corredorAntes, etiquetaAtras });
      maxX = Math.max(maxX, rua.boxes.length * BOX_W);
      maxY = Math.max(maxY, maxAlt * LEVEL_H);
      z += fundoRua + (corredorDepois ? AISLE : RUA_GAP);
    });
    const fimUltimaRua = ruasPos.length
      ? ruasPos[ruasPos.length - 1].z0 + ruasPos[ruasPos.length - 1].fundoRua : 6;
    // centro de cada corredor — serve pra nascer no meio dele no modo a pé
    const corredores = ruasPos
      .filter(r => r.corredorDepois)
      .map(r => r.z0 + r.fundoRua + AISLE / 2);

    const marcadoresPorta = [];

    for(const info of ruasPos){
      const { rua, z0: zCursor, fundoRua, maxAlt, etiquetaAtras } = info;
      const nBoxes  = rua.boxes.length;
      const larguraRua = nBoxes * BOX_W;

      // Estrutura de porta-palete de verdade (referência que o usuário
      // mandou 31/08/2026): montante AZUL a cada 2 boxes — o vão comporta
      // um par (A01+A02), depois vem o montante, depois A03+A04 — e
      // longarina LARANJA horizontal ligando os montantes em cada nível.
      const alturaTotal = maxAlt * LEVEL_H;

      const divisas = [];
      for(let i = 0; i <= nBoxes; i += 2) divisas.push(i);
      if(divisas[divisas.length - 1] !== nBoxes) divisas.push(nBoxes);

      const montantes = new THREE.InstancedMesh(
        new THREE.BoxGeometry(0.11, alturaTotal, 0.11),
        matCor(0x1D4E9C), divisas.length * 2);
      const tmp = new THREE.Object3D();
      let idx = 0;
      for(const i of divisas){
        for(const zLado of [zCursor, zCursor + fundoRua]){
          tmp.position.set(i * BOX_W, alturaTotal / 2, zLado);
          tmp.updateMatrix();
          montantes.setMatrixAt(idx++, tmp.matrix);
        }
      }
      grupoCena.add(montantes);

      // longarinas: uma na frente e uma atrás, em cada nível a partir do 2º
      // (o 1º nível é o piso — o palete apoia no chão, como na referência)
      for(let a = 2; a <= maxAlt; a++){
        for(const zLado of [zCursor, zCursor + fundoRua]){
          const longarina = new THREE.Mesh(
            new THREE.BoxGeometry(larguraRua, ALT_LONGARINA, 0.09),
            matCor(0xE8621D)
          );
          longarina.position.set(larguraRua / 2, (a - 1) * LEVEL_H, zLado);
          grupoCena.add(longarina);
        }
      }

      // etiqueta de cada box: fundo branco, nome em preto e código de
      // barras, PREGADA NA LONGARINA laranja do 1º nível e centralizada no
      // box (como o usuário descreveu o rack real, 31/08/2026). 1 etiqueta
      // por box, que é o que o cadastro nomeia — C3-A01 é a coluna inteira.
      // altura da etiqueta um tico menor que a longarina, pra ficar contida
      // nela (a 1ª versão transbordava — achado ao vivo pelo usuário)
      const alturaEtiq  = ALT_LONGARINA * 0.78;
      const larguraEtiq = alturaEtiq * (ETIQ_W / ETIQ_H);
      rua.boxes.forEach((box, i) => {
        const tex = criarTexturaEtiqueta(box.nome);
        texturasEtiqueta.push(tex);
        const etiqueta = new THREE.Mesh(
          new THREE.PlaneGeometry(larguraEtiq, alturaEtiq),
          new THREE.MeshBasicMaterial({ map: tex, transparent: true })
        );
        // y = altura da 1ª longarina; z rente à face voltada pro corredor
        // (a longarina tem 0.09 de espessura centrada na borda da rua)
        if(etiquetaAtras){
          etiqueta.position.set(i * BOX_W + BOX_W / 2, LEVEL_H, zCursor + fundoRua + 0.055);
          etiqueta.rotation.y = 0;          // olhando pra +Z
        } else {
          etiqueta.position.set(i * BOX_W + BOX_W / 2, LEVEL_H, zCursor - 0.055);
          etiqueta.rotation.y = Math.PI;    // olhando pra −Z
        }
        grupoCena.add(etiqueta);
      });

      // sigla da rua pintada no piso, na cabeceira (dentro do vão de
      // entrada) — orientada pra ser lida por quem entra pela porta
      const ladoLetra = Math.min(2.40, fundoRua * 0.95);
      const texLetra = criarTexturaLetraChao(rua.sigla);
      texturasEtiqueta.push(texLetra);
      const letraChao = new THREE.Mesh(
        new THREE.PlaneGeometry(ladoLetra, ladoLetra),
        new THREE.MeshBasicMaterial({ map: texLetra, transparent: true, depthWrite: false })
      );
      letraChao.position.set(-ladoLetra / 2 - 0.35, 0.02, zCursor + fundoRua / 2);
      letraChao.rotation.x = -Math.PI / 2;   // deitada no chão
      letraChao.rotation.z = -Math.PI / 2;   // "de pé" pra quem olha de −X pra +X
      letraChao.renderOrder = 1;             // não briga com o piso no z-buffer
      grupoCena.add(letraChao);

      // posições (cada combinação box × altura × profundidade)
      rua.boxes.forEach((box, i) => {
        const ocup = ocupPorBox[box.id] || [];
        const porNivel = {};
        for(const o of ocup) porNivel[o.altura + '_' + o.profundidade] = o;
        const bloqNivel = new Set(
          (box.niveis_bloqueados || []).map(n => n.altura + '_' + n.profundidade));

        // altura útil = cadastrada + 1 por meia-posição (ver _box_to_dict)
        const altUtil = box.altura_efetiva || box.altura;
        for(let a = 1; a <= altUtil; a++){
          for(let p = 1; p <= box.profundidade; p++){
            const chave = a + '_' + p;
            const x = i * BOX_W + BOX_W / 2;
            const y = (a - 1) * LEVEL_H + LEVEL_H * 0.42;
            const z = zCursor + (p - 1) * DEPTH_UNIT + DEPTH_UNIT / 2;
            const item = porNivel[chave];

            if(box.bloqueado || bloqNivel.has(chave)){
              const m = new THREE.Mesh(geoCubo, matCor(COR_BLOQUEADO, { transparent: true, opacity: 0.75 }));
              m.position.set(x, y, z);
              m.userData = { tipo: 'bloqueado', box, altura: a, profundidade: p };
              grupoCena.add(m);
              selecionaveis.push(m);
            } else if(item){
              // box de picking é misto por natureza — não marca como exceção
              const misto = box.tipo !== 'picking' && item.produtos && item.produtos.length > 1;
              const cor = misto ? COR_MISTO : corEmpresa3D(item.empresa);
              // fornecedor terceiro sai rajado em vez de sólido
              const m = new THREE.Mesh(geoCubo,
                item.slot_tipo === 'outros' ? matRajado(cor) : matCor(cor));
              m.position.set(x, y, z);
              m.userData = { tipo: 'ocupado', box, item, altura: a, profundidade: p };
              grupoCena.add(m);
              selecionaveis.push(m);
            } else {
              const m = new THREE.Mesh(geoVazio, matVazio);
              m.position.set(x, y, z);
              m.userData = { tipo: 'vazio', box, altura: a, profundidade: p };
              grupoCena.add(m);
              selecionaveis.push(m);
            }
          }
        }
      });

      // porta marcada no cadastro: fica na CABECEIRA do corredor (x antes do
      // início dos racks), de frente pra ele — "porta em frente ao corredor
      // entre a rua C e a D", como o usuário descreveu
      for(const porta of (camara.portas || [])){
        if(porta.rua_id !== rua.id) continue;
        const zPorta = porta.posicao === 'antes'
          ? zCursor - (info.corredorAntes ? AISLE / 2 : RUA_GAP / 2)
          : zCursor + fundoRua + (info.corredorDepois ? AISLE / 2 : RUA_GAP / 2);
        const marca = new THREE.Mesh(
          new THREE.BoxGeometry(0.16, 2.30, 2.20),
          matCor(0x22C55E, { transparent: true, opacity: 0.85 })
        );
        marca.position.set(-ENTRADA + 0.25, 1.15, zPorta);
        grupoCena.add(marca);
        marcadoresPorta.push(new THREE.Vector3(-ENTRADA + 1.5, OLHO_H, zPorta));
      }
    }

    const fundoTotal = Math.max(fimUltimaRua, 6);
    limites = { minX: -ENTRADA + 0.6, maxX: maxX + 2.5, minZ: -2.5, maxZ: fundoTotal + 2.5 };

    // piso + paredes (dão a sensação de "sala", como na referência que o
    // usuário mandou) — paredes translúcidas pra não atrapalhar de fora
    // paredes rentes aos racks das pontas — na câmara real a 1ª e a última
    // rua ficam encostadas na parede (usuário, 31/08/2026)
    const FOLGA_PAREDE = 0.5;
    const zPar0 = -FOLGA_PAREDE, zPar1 = fundoTotal + FOLGA_PAREDE;
    // parede da entrada recuada pelo vão de manobra; a do fundo fica rente
    const xPar0 = -ENTRADA, xPar1 = maxX + 2.6;
    const largSala = xPar1 - xPar0, fundoSala = zPar1 - zPar0;

    const piso = new THREE.Mesh(
      new THREE.PlaneGeometry(largSala, fundoSala),
      matCor(0xdfe6ee)
    );
    piso.rotation.x = -Math.PI / 2;
    piso.position.set((xPar0 + xPar1) / 2, 0, (zPar0 + zPar1) / 2);
    grupoCena.add(piso);
    pisoMesh = piso;   // usado pelo clique-pra-andar do modo a pé

    const alturaParede = Math.max(maxY + 1.5, 5);
    const matParede = new THREE.MeshLambertMaterial({
      color: 0xffffff, transparent: true, opacity: 0.20, side: THREE.DoubleSide });
    const paredes = [
      { w: largSala,  h: alturaParede, x: (xPar0 + xPar1) / 2, z: zPar0, rotY: 0 },
      { w: largSala,  h: alturaParede, x: (xPar0 + xPar1) / 2, z: zPar1, rotY: 0 },
      { w: fundoSala, h: alturaParede, x: xPar0, z: (zPar0 + zPar1) / 2, rotY: Math.PI / 2 },
      { w: fundoSala, h: alturaParede, x: xPar1, z: (zPar0 + zPar1) / 2, rotY: Math.PI / 2 },
    ];
    for(const p of paredes){
      const parede = new THREE.Mesh(new THREE.PlaneGeometry(p.w, p.h), matParede);
      parede.position.set(p.x, p.h / 2, p.z);
      parede.rotation.y = p.rotY;
      grupoCena.add(parede);
    }

    // Entrada no modo a pé: nasce na boca de um corredor e olha ao longo
    // dele (eixo X) — é o que dá a sensação de street view. Prioridade:
    // porta cadastrada > 1º corredor > meio da câmara.
    const zEntrada = marcadoresPorta.length ? marcadoresPorta[0].z
                   : corredores.length      ? corredores[0]
                   : fundoTotal / 2;
    // recuado no vão de manobra: entra vendo a câmara inteira à frente
    spawn = new THREE.Vector3(-ENTRADA + 1.5, OLHO_H, zEntrada);
    // olhar reto pra dentro do corredor (mesma altura, mesmo Z) — sem isso
    // a câmera nascia encarando parede ou entrando torta
    alvoOlhar = new THREE.Vector3(maxX / 2, OLHO_H, zEntrada);

    if(!opcoes.manterCamera) enquadrar(maxX, maxY, fundoTotal);
  }

  function enquadrar(maxX, maxY, fundoTotal){
    const centro = new THREE.Vector3(maxX / 2, maxY / 2, fundoTotal / 2);
    const dist = Math.max(maxX, fundoTotal) * 0.95 + 10;
    camera.position.set(centro.x - dist * 0.45, dist * 0.62, centro.z + dist * 0.75);
    orbit.target.copy(centro);
    orbit.update();
  }

  function limparGrupo(){
    grupoCena.traverse(obj => {
      if(obj.geometry) obj.geometry.dispose();
      // material das etiquetas é único por box (tem textura própria) —
      // os demais vêm do cache compartilhado e NÃO devem ser destruídos
      if(obj.material && obj.material.map) obj.material.dispose();
    });
    for(const t of texturasEtiqueta) t.dispose();
    texturasEtiqueta = [];
    rajadoCache = new Map();
    scene.remove(grupoCena);
    grupoCena = new THREE.Group();
    scene.add(grupoCena);
  }

  // ── modos de câmera ────────────────────────────────────────────────
  function setModo(novo){
    modo = novo;
    destinoAndar = null;
    // volta a lente ao normal ao trocar de modo — senão a visão geral
    // herdava o zoom que ficou de dentro da câmara
    camera.fov = FOV_PADRAO;
    camera.updateProjectionMatrix();
    if(novo === 'fp'){
      orbit.enabled = false;
      camera.position.copy(spawn);
      // deriva yaw/pitch do ponto que queremos encarar, pra o arrastar
      // continuar de onde a câmera nasceu
      const dir = alvoOlhar.clone().sub(spawn);
      yaw = Math.atan2(-dir.x, -dir.z);
      pitch = 0;
      aplicarRotacaoFP();
      renderer.domElement.style.cursor = 'grab';
    } else {
      orbit.enabled = true;
      renderer.domElement.style.cursor = '';
    }
    if(callbacks.onModo) callbacks.onModo(modo);
  }

  // ── interação ──────────────────────────────────────────────────────
  function _ndc(ev, alvo){
    const r = renderer.domElement.getBoundingClientRect();
    alvo.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
    alvo.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
  }

  function onPointerDown(ev){
    if(modo !== 'fp') return;
    arrastando = true; arrastouPx = 0;
    ultimoX = ev.clientX; ultimoY = ev.clientY;
    renderer.domElement.style.cursor = 'grabbing';
    // captura do ponteiro é só conveniência (arrastar pra fora do canvas
    // continua girando); se o navegador recusar o id, seguir sem ela —
    // deixar estourar aqui abortava o tratamento do clique (achado ao vivo)
    try { renderer.domElement.setPointerCapture?.(ev.pointerId); } catch(e){}
  }
  function onPointerMove(ev){
    if(modo !== 'fp' || !arrastando) return;
    const dx = ev.clientX - ultimoX, dy = ev.clientY - ultimoY;
    ultimoX = ev.clientX; ultimoY = ev.clientY;
    arrastouPx += Math.abs(dx) + Math.abs(dy);
    // arrastar pra direita gira a vista pra esquerda (você "puxa" a cena,
    // igual street view); arrastar pra baixo faz olhar pra cima
    yaw   += dx * SENS;
    pitch += dy * SENS;
    pitch = Math.min(Math.max(pitch, -PITCH_MAX), PITCH_MAX);
    aplicarRotacaoFP();
    destinoAndar = null;   // se estava deslizando, o arrasto interrompe
  }
  function onPointerUp(ev){
    if(modo !== 'fp'){ return; }
    arrastando = false;
    renderer.domElement.style.cursor = 'grab';
    try { renderer.domElement.releasePointerCapture?.(ev.pointerId); } catch(e){}
    if(arrastouPx > 6) return;   // foi arrasto pra olhar, não clique

    // Clique seco: vale o que estiver MAIS PERTO na direção do clique —
    // palete/bloqueio abre a info, chão leva você até lá. Posições VAZIAS
    // são ignoradas de propósito: são só wireframe (você enxerga através
    // delas), e como estão espalhadas por todo rack elas capturavam o
    // clique e impediam de andar pelo corredor (achado ao vivo).
    _ndc(ev, ponteiro);
    raycaster.setFromCamera(ponteiro, camera);
    const solidos = selecionaveis.filter(m => m.userData.tipo !== 'vazio');
    const alvo = raycaster.intersectObjects(solidos, false);
    const chao = pisoMesh ? raycaster.intersectObject(pisoMesh, false) : [];

    const distAlvo = alvo.length ? alvo[0].distance : Infinity;
    const distChao = chao.length ? chao[0].distance : Infinity;
    if(distAlvo < distChao){
      if(callbacks.onSelecionar) callbacks.onSelecionar(alvo[0].object.userData);
      return;
    }
    if(!chao.length) return;
    const p = chao[0].point;
    destinoAndar = {
      de: camera.position.clone(),
      para: new THREE.Vector3(
        Math.min(Math.max(p.x, limites.minX), limites.maxX),
        OLHO_H,
        Math.min(Math.max(p.z, limites.minZ), limites.maxZ)),
      t: 0,
    };
  }
  function onClickOrbit(ev){
    if(modo === 'fp') return;   // no modo a pé quem trata é o pointerup
    _ndc(ev, ponteiro);
    raycaster.setFromCamera(ponteiro, camera);
    const hits = raycaster.intersectObjects(selecionaveis, false);
    if(hits.length && callbacks.onSelecionar) callbacks.onSelecionar(hits[0].object.userData);
  }
  // Rolar pra dar zoom no modo a pé — como no street view, o zoom é de
  // LENTE (abre/fecha o campo de visão), não deslocamento: você continua
  // parado no mesmo ponto do corredor. No modo órbita quem cuida da roda é
  // o OrbitControls (aproxima/afasta a câmera), então aqui nem toca.
  function onWheel(ev){
    if(modo !== 'fp') return;
    ev.preventDefault();
    camera.fov = Math.min(Math.max(camera.fov + Math.sign(ev.deltaY) * 4, FOV_MIN), FOV_MAX);
    camera.updateProjectionMatrix();
  }
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('pointermove', onPointerMove);
  renderer.domElement.addEventListener('pointerup', onPointerUp);
  renderer.domElement.addEventListener('click', onClickOrbit);
  renderer.domElement.addEventListener('wheel', onWheel, { passive: false });

  function onKey(ev, valor){
    const k = ev.key.toLowerCase();
    if(k === 'w' || k === 'arrowup')    teclas.w = valor;
    if(k === 's' || k === 'arrowdown')  teclas.s = valor;
    if(k === 'a' || k === 'arrowleft')  teclas.a = valor;
    if(k === 'd' || k === 'arrowright') teclas.d = valor;
    if(ev.key === 'Shift') teclas.shift = valor;
    // sem pointer lock, o Esc precisa sair do modo a pé explicitamente
    if(valor && ev.key === 'Escape' && modo === 'fp') setModo('orbit');
  }
  const keyDown = e => onKey(e, true);
  const keyUp   = e => onKey(e, false);
  document.addEventListener('keydown', keyDown);
  document.addEventListener('keyup', keyUp);

  function redimensionar(){
    const w = container.clientWidth || 800;
    const h = container.clientHeight || 500;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(redimensionar);
  ro.observe(container);
  redimensionar();

  // ── loop ───────────────────────────────────────────────────────────
  const relogio = new THREE.Clock();
  function loop(){
    if(!animando) return;
    requestAnimationFrame(loop);
    const dt = Math.min(relogio.getDelta(), 0.1);

    // IMPORTANTE: no modo a pé o orbit NÃO pode rodar — orbit.update()
    // reescreve a posição da câmera a partir do estado interno dele e
    // jogava o observador de volta pra fora da câmara (achado ao vivo).
    if(modo === 'fp'){
      const andando = teclas.w || teclas.a || teclas.s || teclas.d;
      if(andando) destinoAndar = null;   // teclado assume o controle na hora
      if(destinoAndar){
        // desliza até o ponto clicado (street view) — ~3,5 m/s com
        // suavização nas pontas
        const dist = destinoAndar.de.distanceTo(destinoAndar.para);
        destinoAndar.t += dt * (dist > 0.01 ? 3.5 / dist : 99);
        const k = Math.min(destinoAndar.t, 1);
        const suave = k * k * (3 - 2 * k);   // smoothstep
        camera.position.lerpVectors(destinoAndar.de, destinoAndar.para, suave);
        if(k >= 1) destinoAndar = null;
      } else {
        const vel = (teclas.shift ? 9 : 4.5) * dt;
        if(teclas.w) andarFrente(vel);
        if(teclas.s) andarFrente(-vel);
        if(teclas.a) andarLado(-vel);
        if(teclas.d) andarLado(vel);
      }
      // não deixa sair da sala nem atravessar o chão
      camera.position.x = Math.min(Math.max(camera.position.x, limites.minX), limites.maxX);
      camera.position.z = Math.min(Math.max(camera.position.z, limites.minZ), limites.maxZ);
      camera.position.y = OLHO_H;
    } else {
      orbit.update();
    }
    renderer.render(scene, camera);
  }
  loop();

  return {
    render,
    setModo,
    getModo: () => modo,
    irParaPorta(){ if(spawn) camera.position.copy(spawn); },
    // usado em teste/diagnóstico pra conferir onde o observador está
    getPosicao(){ return camera.position.clone(); },
    dispose(){
      animando = false;
      ro.disconnect();
      document.removeEventListener('keydown', keyDown);
      document.removeEventListener('keyup', keyUp);
      renderer.domElement.removeEventListener('click', onClick);
      limparGrupo();
      renderer.dispose();
      if(renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
    },
  };
}

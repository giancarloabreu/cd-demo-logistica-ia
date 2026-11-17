"""
Renderiza uma string HTML em PNG usando Playwright (Chromium headless).
Produz resultado idêntico ao navegador — fontes, cores, arredondamentos.
"""
from __future__ import annotations
from pathlib import Path


def html_to_png(html: str, output_path: str | Path, width: int = 1600,
                device_scale_factor: float = 1.5) -> Path:
    """
    device_scale_factor=1.5: renderiza com 50% mais pixels por unidade CSS —
    texto e bordas ficam visivelmente mais nítidos ao inserir a imagem em
    e-mail (que costuma reescalar), sem inflar tanto o arquivo quanto 2x.
    O layout não muda: as dimensões CSS são as mesmas, só a densidade sobe.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            return _render(browser, html, output_path, width, device_scale_factor)
        finally:
            browser.close()


def htmls_to_pngs(itens: list[tuple[str, str | Path]], width: int = 1600,
                  device_scale_factor: float = 1.5) -> list[Path]:
    """Mesma renderização de html_to_png, mas pra várias imagens de uma vez
    reaproveitando UM browser Chromium (só abre/fecha uma vez) em vez de um
    por imagem — usado pelo Painel do Armazém, que agora exporta em vários
    PNGs separados (pedido do usuário 24/08/2026) e ficava lento demais
    (~9s por imagem) abrindo um Chromium novo pra cada uma."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            return [_render(browser, html, path, width, device_scale_factor) for html, path in itens]
        finally:
            browser.close()


def htmls_to_pngs_cada(itens: list[tuple[str, str | Path, int]],
                       device_scale_factor: float = 1.5) -> list[Path | Exception]:
    """Várias imagens num Chromium só, cada uma com a sua largura; a falha de
    uma volta como exceção na posição dela e não derruba as outras. Usado
    pela exportação do painel (03/11/2026: um Chromium por imagem levava
    ~3 min na VM e travava o worker único)."""
    if not itens:
        return []
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            out: list[Path | Exception] = []
            for html, path, width in itens:
                try:
                    out.append(_render(browser, html, path, width, device_scale_factor))
                except Exception as exc:
                    out.append(exc)
            return out
        finally:
            browser.close()


def _render(browser, html: str, output_path: str | Path, width: int, device_scale_factor: float) -> Path:
    output_path = Path(output_path)
    page = browser.new_page(viewport={"width": width, "height": 800},
                            device_scale_factor=device_scale_factor)
    try:
        page.set_content(html, wait_until="networkidle")
        # Aguarda fontes e renderização
        page.wait_for_timeout(400)
        # Ajusta altura ao conteúdo real para evitar espaço em branco
        real_height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": width, "height": max(real_height, 100)})
        page.screenshot(path=str(output_path), full_page=False)
    finally:
        page.close()
    return output_path


def html_to_pdf(html: str, output_path: str | Path, margem_mm=(12, 14, 12, 14),
                uma_pagina: bool = False) -> Path:
    """PDF A4 a partir de HTML, no mesmo Chromium das imagens (Termo de
    Avença, 27/10/2026). `margem_mm` = (cima, direita, baixo, esquerda).
    `uma_pagina`: quando o conteúdo passa de uma folha, reduz a escala (até
    70%) para caber — o termo com muitos itens saía em duas páginas."""
    from playwright.sync_api import sync_playwright

    output_path = Path(output_path)
    cima, direita, baixo, esquerda = margem_mm
    largura_px = (210 - direita - esquerda) * 96 / 25.4
    altura_px = (297 - cima - baixo) * 96 / 25.4
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": int(largura_px), "height": int(altura_px)})
            try:
                page.emulate_media(media="print")
                page.set_content(html, wait_until="load")
                escala = 1.0
                if uma_pagina:
                    alto = page.evaluate("document.documentElement.scrollHeight")
                    if alto > altura_px:
                        escala = max(0.7, round(altura_px / alto * 0.98, 3))
                page.pdf(path=str(output_path), format="A4", print_background=True, scale=escala,
                         margin={"top": f"{cima}mm", "right": f"{direita}mm",
                                 "bottom": f"{baixo}mm", "left": f"{esquerda}mm"})
            finally:
                page.close()
        finally:
            browser.close()
    return output_path

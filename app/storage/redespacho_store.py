import json
from datetime import datetime

from app.utils.paths import get_config_path


class RedespachoStore:
    def __init__(self, filename="redespachos_selecionados.json"):
        self.path = get_config_path(filename)
        self.data = self._load()

    def _load(self):
        if not self.path.exists():
            return {"selecionados": {}}

        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)

            if "selecionados" not in data:
                data["selecionados"] = {}

            return data
        except Exception:
            return {"selecionados": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["ultima_atualizacao"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    @staticmethod
    def make_key(formulario="", chave="", pedido="", placa=""):
        formulario = str(formulario or "").strip()
        chave = str(chave or "").strip()
        pedido = str(pedido or "").strip()
        placa = str(placa or "").strip()

        if chave:
            return f"CHAVE::{chave}"

        if formulario:
            return f"FORM::{formulario}"

        if pedido and placa:
            return f"PEDIDO_PLACA::{pedido}_{placa}"

        return f"LINHA::{formulario}_{pedido}_{placa}"

    def is_selected(self, formulario="", chave="", pedido="", placa=""):
        key = self.make_key(formulario, chave, pedido, placa)
        return bool(self.data.get("selecionados", {}).get(key, False))

    def set_selected(self, formulario="", chave="", pedido="", placa="", selected=False):
        key = self.make_key(formulario, chave, pedido, placa)

        if selected:
            self.data.setdefault("selecionados", {})[key] = True
        else:
            self.data.setdefault("selecionados", {}).pop(key, None)

        self.save()

    def sync_from_dataframe(self, df):
        if df is None or df.empty:
            return

        for _, row in df.iterrows():
            selected = str(row.get("Selecionado", "")).strip().lower() == "sim"
            self.set_selected(
                formulario=row.get("Formulário", ""),
                chave=row.get("Chave", ""),
                pedido=row.get("Pedido Entrada", ""),
                placa=row.get("Placa", ""),
                selected=selected,
            )

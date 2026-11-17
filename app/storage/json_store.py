import json
from datetime import datetime

from app.utils.paths import get_config_path


class JsonStore:
    def __init__(self, filename="validacoes.json"):
        self.path = get_config_path(filename)
        self.data = self._load()

    def _load(self):
        if not self.path.exists():
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    @staticmethod
    def build_key(nf, pedido_compra):
        return f"{str(nf).strip()}_{str(pedido_compra).strip()}"

    def get_validacao(self, nf, pedido_compra):
        key = self.build_key(nf, pedido_compra)
        return self.data.get(key, {})

    def set_validacao(self, nf, pedido_compra, prioridade, regra_data_cd):
        key = self.build_key(nf, pedido_compra)
        self.data[key] = {
            "prioridade": prioridade,
            "regra_data_cd": regra_data_cd,
            "data_atualizacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()

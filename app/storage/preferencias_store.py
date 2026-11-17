import json

from app.utils.paths import get_config_path


class PreferenciasStore:
    def __init__(self, filename="preferencias.json"):
        self.path = get_config_path(filename)
        self.data = self._load()

    def _load(self):
        if not self.path.exists():
            return {
                "cd_nome": "CDA",
                "cd_latitude": "",
                "cd_longitude": "",
                "fator_rodoviario": 1.25
            }

        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception:
            data = {}

        data.setdefault("cd_nome", "CDA")
        data.setdefault("cd_latitude", "")
        data.setdefault("cd_longitude", "")
        data.setdefault("fator_rodoviario", 1.25)
        return data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    def get_cd(self):
        return {
            "nome": self.data.get("cd_nome", "CDA"),
            "latitude": self.data.get("cd_latitude", ""),
            "longitude": self.data.get("cd_longitude", ""),
            "fator_rodoviario": self.data.get("fator_rodoviario", 1.25),
        }

    def get_col_order(self, default_cols: list) -> list:
        saved = self.data.get("col_order", [])
        if not saved:
            return default_cols
        # Mantém só colunas que ainda existem, e adiciona novas ao final
        valid = [c for c in saved if c in default_cols]
        missing = [c for c in default_cols if c not in valid]
        return valid + missing

    def set_col_order(self, cols: list):
        self.data["col_order"] = list(cols)
        self.save()

    def get_col_widths(self, default_widths: dict) -> dict:
        saved = self.data.get("col_widths", {})
        merged = dict(default_widths)
        merged.update({k: v for k, v in saved.items() if k in default_widths})
        return merged

    def set_col_widths(self, widths: dict):
        self.data["col_widths"] = dict(widths)
        self.save()

    def set_cd(self, nome, latitude, longitude, fator_rodoviario=1.25):
        self.data["cd_nome"] = str(nome).strip() or "CDA"
        self.data["cd_latitude"] = str(latitude).strip()
        self.data["cd_longitude"] = str(longitude).strip()
        try:
            self.data["fator_rodoviario"] = float(str(fator_rodoviario).replace(",", "."))
        except Exception:
            self.data["fator_rodoviario"] = 1.25
        self.save()

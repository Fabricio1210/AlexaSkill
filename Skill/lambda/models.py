import uuid
from datetime import datetime, timedelta
from ask_sdk_core.handler_input import HandlerInput

def generar_id_unico():
    return str(uuid.uuid4())[:8]

def generar_id_prestamo():
    return str(uuid.uuid4())[:8]

class Prestamo:
    def __init__(self, libro_id, titulo, nombre_persona, dias_prestamo=7):
        self.id = generar_id_prestamo()
        self.libro_id = libro_id
        self.titulo = titulo
        self.persona = nombre_persona if nombre_persona else "un amigo"
        self.fecha_prestamo = datetime.now().isoformat()
        self.fecha_limite = (datetime.now() + timedelta(days=dias_prestamo)).isoformat()
        self.estado = "activo"
        
    def to_dict(self):
        return self.__dict__

    @property
    def fecha_limite_readable(self):
        try:
            return datetime.fromisoformat(self.fecha_limite).strftime("%d de %B")
        except:
            return "una semana"

class Libro:
    def __init__(self, titulo, autor, tipo):
        self.titulo = titulo
        self.autor = self._normalize_value(autor, "Desconocido")
        self.tipo = self._normalize_value(tipo, "Sin categoría")
        
        self.id = generar_id_unico()
        self.fecha_agregado = datetime.now().isoformat()
        self.total_prestamos = 0
        self.estado = "disponible"

    @staticmethod
    def _normalize_value(value, default):
        if value and value.lower() in ["no sé", "no se", "no lo sé"]:
            return default
        return value if value else default

    def to_dict(self):
        return self.__dict__
        
    
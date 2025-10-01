from database import DatabaseManager
from phrases import PhrasesManager 
from models import generar_id_unico, Libro, Prestamo
from datetime import datetime, timedelta
from config import LIBROS_POR_PAGINA

def buscar_libro_por_titulo(libros, titulo_buscado):
    if not titulo_buscado:
        return []
    titulo_lower = titulo_buscado.lower()
    resultados = []
    
    for libro in libros:
        libro_titulo_lower = libro.get("titulo", "").lower()
        if libro_titulo_lower == titulo_lower:
            resultados.insert(0, libro) 
        elif titulo_lower in libro_titulo_lower:
            resultados.append(libro)
    return resultados

def buscar_libro_por_titulo_exacto(libros, titulo):
    if not titulo:
        return None
    titulo_lower = titulo.lower()
    for libro in libros:
        if libro.get("titulo", "").lower() == titulo_lower:
            return libro
    return None

class BibliotecaService:
    @staticmethod
    def agregar_libro(handler_input, titulo, autor, tipo):
        
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        
        if any(libro.get("titulo", "").lower() == titulo.lower() for libro in libros):
            return False

        nuevo_libro = Libro(titulo=titulo, autor=autor, tipo=tipo)
        
        libros.append(nuevo_libro.to_dict())
        stats = user_data.setdefault("estadisticas", {})
        stats["total_libros"] = len(libros)
        
        DatabaseManager.save_user_data(handler_input, user_data)
        
        return nuevo_libro
    
    @staticmethod
    def limpiar_y_normalizar_valor(valor, esperando):
        if not valor:
            return valor

        valor = valor.lower().strip()
        no_se_options = ["no sé", "no se", "no lo sé", "no lo se"]
        
        if esperando == "autor":
            default_value = "Desconocido"
            prefijo = "el autor es"
            if valor in no_se_options or valor == "no sé el autor" or valor == "no se el autor":
                return default_value
        elif esperando == "tipo":
            default_value = "Sin categoría"
            prefijo = "el tipo es"
            if valor in no_se_options or valor == "no sé el tipo" or valor == "no se el tipo":
                return default_value
        else:
            return valor.title()
        if valor.startswith(f"{prefijo} "):
            return valor[len(f"{prefijo} "):].strip().title()
        elif valor.startswith("es "):
            return valor[3:].strip().title()
        
        return valor.title()
        
    @staticmethod
    def get_libros(handler_input):
        """Devuelve la lista completa de libros del usuario."""
        user_data = DatabaseManager.get_user_data(handler_input)
        return user_data.get("libros_disponibles", [])

    @staticmethod
    def sincronizar_y_filtrar_libros(handler_input, filtro_tipo, autor):
        """
        Sincroniza el estado de los préstamos, guarda los datos y luego filtra.
        Retorna: lista de libros filtrados, el título del filtro aplicado.
        """
        user_data = DatabaseManager.get_user_data(handler_input)

        todos_libros = user_data.get("libros_disponibles", [])
        prestamos = user_data.get("prestamos_activos", [])
        libros_filtrados = todos_libros.copy()
        titulo_filtro = ""
        
        if autor:
            libros_filtrados = [l for l in libros_filtrados if l.get("autor", "").lower() == autor.lower()]
            titulo_filtro = f" de {autor}"
        elif filtro_tipo:
            filtro_tipo_lower = filtro_tipo.lower()
            if filtro_tipo_lower in ["prestados", "prestado"]:
                ids_prestados = [p.get("libro_id") for p in prestamos]
                libros_filtrados = [l for l in libros_filtrados if l.get("id") in ids_prestados]
                titulo_filtro = " prestados"
            elif filtro_tipo_lower in ["disponibles", "disponible"]:
                ids_prestados = [p.get("libro_id") for p in prestamos]
                libros_filtrados = [l for l in libros_filtrados if l.get("id") not in ids_prestados]
                titulo_filtro = " disponibles"
                
        return libros_filtrados, titulo_filtro

    @staticmethod
    def obtener_pagina_libros(libros_filtrados, pagina_actual):
        """Calcula la paginación y devuelve los datos relevantes."""
        total_libros = len(libros_filtrados)
        inicio = pagina_actual * LIBROS_POR_PAGINA
        fin = min(inicio + LIBROS_POR_PAGINA, total_libros)
        
        libros_pagina = libros_filtrados[inicio:fin]
        
        return {
            "libros_pagina": libros_pagina,
            "inicio": inicio,
            "fin": fin,
            "total_filtrados": total_libros,
            "quedan_mas": fin < total_libros,
            "es_ultima_pagina": fin == total_libros
        }
        
    @staticmethod
    def registrar_prestamo(handler_input, titulo, nombre_persona):
        """Busca el libro, valida el estado y registra el préstamo. Retorna Prestamo, o cadena de error."""
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        prestamos_dicts = user_data.get("prestamos_activos", [])
        
        libro = buscar_libro_por_titulo_exacto(libros, titulo)
        
        if not libro:
            return "no_encontrado"

        if not libro.get("id"):
            libro["id"] = generar_id_unico()
            for l in libros:
                if l.get("titulo") == libro.get("titulo"):
                    l["id"] = libro["id"]
                    break
        prestamo_existente = next((p for p in prestamos_dicts if p.get("libro_id") == libro.get("id")), None)

        if prestamo_existente:
            return "ya_prestado"
        nuevo_prestamo = Prestamo(
            libro_id=libro["id"], 
            titulo=libro["titulo"], 
            nombre_persona=nombre_persona
        )
        prestamos_dicts.append(nuevo_prestamo.to_dict())
        for l in libros:
            if l.get("id") == libro.get("id"):
                l["estado"] = "prestado"
                l["total_prestamos"] = l.get("total_prestamos", 0) + 1
                break
                
        stats = user_data.setdefault("estadisticas", {})
        stats["total_prestamos"] = stats.get("total_prestamos", 0) + 1

        user_data["libros_disponibles"] = libros
        user_data["prestamos_activos"] = prestamos_dicts
        
        DatabaseManager.save_user_data(handler_input, user_data)
        
        return nuevo_prestamo
        
    @staticmethod
    def get_libros_disponibles_info(handler_input):
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        prestamos = user_data.get("prestamos_activos", [])
        
        ids_prestados = {p.get("libro_id") for p in prestamos}
        disponibles = [l for l in libros if l.get("id") and l.get("id") not in ids_prestados]
        
        num_disponibles = len(disponibles)
        ejemplos = [l.get("titulo") for l in disponibles[:2]]

        return num_disponibles, ejemplos
    
    @staticmethod
    def buscar_libros(handler_input, titulo):
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        libros_encontrados = buscar_libro_por_titulo(libros, titulo)
        return libros_encontrados
        
    @staticmethod
    def buscar_prestamo_activo(prestamos_dicts, titulo, id_prestamo):
        if not prestamos_dicts:
            return None, -1
        prestamo_encontrado = None
        indice = -1
        if id_prestamo:
            for i, p in enumerate(prestamos_dicts):
                if p.get("id") == id_prestamo:
                    return p, i
        if titulo:
            titulo_lower = titulo.lower()
            for i, p in enumerate(prestamos_dicts):
                if titulo_lower in p.get("titulo", "").lower():
                    return p, i
        return None, -1

    @staticmethod
    def registrar_devolucion(handler_input, titulo=None, id_prestamo=None):
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        prestamos_activos = user_data.get("prestamos_activos", [])
        historial_prestamos = user_data.get("historial_prestamos", [])
        if not prestamos_activos:
            return "no_prestamos"
        prestamo_a_devolver, indice = BibliotecaService.buscar_prestamo_activo(
            prestamos_activos, titulo, id_prestamo
        )

        if not prestamo_a_devolver:
            return "no_encontrado"
        prestamo_finalizado = prestamo_a_devolver.copy() 
        
        prestamos_activos.pop(indice) 
        
        prestamo_finalizado["fecha_devolucion"] = datetime.now().isoformat()
        prestamo_finalizado["estado"] = "devuelto"
        
        fecha_limite = datetime.fromisoformat(prestamo_finalizado.get("fecha_limite"))
        prestamo_finalizado["devuelto_a_tiempo"] = datetime.now() <= fecha_limite

        historial_prestamos.append(prestamo_finalizado)

        for l in libros:
            if l.get("id") == prestamo_finalizado.get("libro_id"):
                l["estado"] = "disponible"
                break
        stats = user_data.setdefault("estadisticas", {})
        stats["total_devoluciones"] = stats.get("total_devoluciones", 0) + 1

        user_data["prestamos_activos"] = prestamos_activos
        user_data["historial_prestamos"] = historial_prestamos
        DatabaseManager.save_user_data(handler_input, user_data)

        return prestamo_finalizado

    @staticmethod
    def get_prestamos_activos_info(handler_input):
        user_data = DatabaseManager.get_user_data(handler_input)
        prestamos = user_data.get("prestamos_activos", [])
        
        num_prestados = len(prestamos)
        ejemplos = [
            f"'{p.get('titulo')}' a {p.get('persona', 'un amigo')}" 
            for p in prestamos[:3]
        ]

        return num_prestados, ejemplos
        
    @staticmethod
    def obtener_resumen_prestamos(handler_input):
        user_data = DatabaseManager.get_user_data(handler_input)
        prestamos_activos = user_data.get("prestamos_activos", [])
        
        if not prestamos_activos:
            return {
                "total": 0,
                "detalles": [],
                "hay_vencidos": False,
                "hay_proximos": False
            }

        total_prestamos = len(prestamos_activos)
        detalles_analizados = []
        hay_vencidos = False
        hay_proximos = False
        
        fecha_actual = datetime.now()
        for p in prestamos_activos:
            detalle = f"'{p['titulo']}' está con {p.get('persona', 'alguien')}"
            
            try:
                fecha_limite = datetime.fromisoformat(p.get('fecha_limite'))
                dias_restantes = (fecha_limite - fecha_actual).days
                
                if dias_restantes < 0:
                    detalle += " (¡ya venció!)"
                    hay_vencidos = True
                elif dias_restantes == 0:
                    detalle += " (vence hoy)"
                    hay_proximos = True
                elif dias_restantes <= 2:
                    detalle += f" (vence en {dias_restantes} días)"
                    hay_proximos = True
            except:
                detalle += " (fecha límite desconocida)"
            
            detalles_analizados.append(detalle)
            
        return {
            "total": total_prestamos,
            "detalles": detalles_analizados,
            "hay_vencidos": hay_vencidos,
            "hay_proximos": hay_proximos
        }
        
    @staticmethod
    def obtener_resumen_historial(handler_input):
        user_data = DatabaseManager.get_user_data(handler_input)
        historial = user_data.get("historial_prestamos", [])
        
        total = len(historial)
        
        if total == 0:
            return {
                "total": 0,
                "detalles_voz": [],
                "es_historial_completo": True
            }

        MAX_LIBROS_VOZ = 10

        if total <= MAX_LIBROS_VOZ:
            libros_a_mostrar = historial
            es_historial_completo = True
        else:
            libros_a_mostrar = historial[-5:]
            es_historial_completo = False
        
        detalles = []
        if not es_historial_completo:
            libros_a_mostrar = reversed(libros_a_mostrar)

        for h in libros_a_mostrar:
            detalle = f"'{h.get('titulo', 'Sin título')}'"
            persona = h.get('persona', 'un amigo')
            if persona.lower() not in ['alguien', 'un amigo', 'desconocido']:
                detalle += f" que prestaste a {persona}"
            
            detalles.append(detalle)
            
        return {
            "total": total,
            "detalles_voz": detalles,
            "es_historial_completo": es_historial_completo
        }
        
    @staticmethod
    def eliminar_libro(handler_input, titulo):
        user_data = DatabaseManager.get_user_data(handler_input)
        libros = user_data.get("libros_disponibles", [])
        prestamos_activos = user_data.get("prestamos_activos", [])
        libro_a_eliminar = buscar_libro_por_titulo_exacto(libros, titulo)
        
        if not libro_a_eliminar:
            return "no_encontrado"
            
        libro_id = libro_a_eliminar.get("id")
        if any(p.get("libro_id") == libro_id for p in prestamos_activos):
            return "esta_prestado"
        try:
            libros_actualizada = [l for l in libros if l.get("id") != libro_id]
            user_data["libros_disponibles"] = libros_actualizada
            
            stats = user_data.setdefault("estadisticas", {})
            stats["total_libros"] = len(libros_actualizada)
            
            DatabaseManager.save_user_data(handler_input, user_data)
            
            return libro_a_eliminar
        except Exception as e:
            logger.error(f"Error al eliminar el libro {titulo}: {e}", exc_info=True)
            return "error_interno"
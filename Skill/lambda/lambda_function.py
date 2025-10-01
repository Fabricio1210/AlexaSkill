import os
import json
import logging
from datetime import datetime, timedelta
import random
import uuid

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_model import Response, DialogState
from ask_sdk_model.dialog import ElicitSlotDirective, DelegateDirective
from ask_sdk_s3.adapter import S3Adapter
from ask_sdk_core.handler_input import HandlerInput

import boto3
from botocore.exceptions import ClientError
import phrases
from phrases import PhrasesManager
from config import USE_FAKE_S3, S3_PERSISTENCE_BUCKET, LIBROS_POR_PAGINA
from database import DatabaseManager, FakeS3Adapter
from services import BibliotecaService
from models import Prestamo

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ==============================
# Inicializar persistence adapter
# ==============================
if USE_FAKE_S3:
    persistence_adapter = FakeS3Adapter()
else:
    s3_bucket = S3_PERSISTENCE_BUCKET
    if not s3_bucket:
        raise RuntimeError("S3_PERSISTENCE_BUCKET es requerido cuando USE_FAKE_S3=false")
    logger.info(f"🪣 Usando S3Adapter con bucket: {s3_bucket}")
    persistence_adapter = S3Adapter(bucket_name=s3_bucket)

sb = CustomSkillBuilder(persistence_adapter=persistence_adapter)

# ==============================
# Helpers
# ==============================
def generar_id_unico():
    """Genera un ID único para libros y préstamos"""
    return str(uuid.uuid4())[:8]

def sincronizar_estados_libros(user_data):
    """Sincroniza los estados de los libros basándose en los préstamos activos"""
    libros = user_data.get("libros_disponibles", [])
    prestamos = user_data.get("prestamos_activos", [])
    
    # Primero, asegurar que todos los libros tengan ID
    for libro in libros:
        if not libro.get("id"):
            libro["id"] = generar_id_unico()
    
    # Luego, actualizar estados
    ids_prestados = {p.get("libro_id") for p in prestamos if p.get("libro_id")}
    
    for libro in libros:
        if libro.get("id") in ids_prestados:
            libro["estado"] = "prestado"
        else:
            libro["estado"] = "disponible"
    
    return user_data

def buscar_libro_por_titulo(libros, titulo_busqueda):
    """Busca libros por título y devuelve una lista de coincidencias"""
    titulo_busqueda = (titulo_busqueda or "").lower().strip()
    resultados = []
    for libro in libros:
        if isinstance(libro, dict):
            titulo_libro = (libro.get("titulo") or "").lower()
            if titulo_busqueda in titulo_libro or titulo_libro in titulo_busqueda:
                resultados.append(libro)
    return resultados

def buscar_libro_por_titulo_exacto(libros, titulo_busqueda):
    """Busca un libro por título y devuelve el primero que coincida"""
    titulo_busqueda = (titulo_busqueda or "").lower().strip()
    for libro in libros:
        if isinstance(libro, dict):
            titulo_libro = (libro.get("titulo") or "").lower()
            if titulo_busqueda in titulo_libro or titulo_libro in titulo_busqueda:
                return libro
    return None

def buscar_libros_por_autor(libros, autor_busqueda):
    autor_busqueda = (autor_busqueda or "").lower().strip()
    resultados = []
    for libro in libros:
        if isinstance(libro, dict):
            autor_libro = (libro.get("autor") or "").lower()
            if autor_busqueda in autor_libro or autor_libro in autor_busqueda:
                resultados.append(libro)
    return resultados

def generar_id_prestamo():
    return f"PREST-{datetime.now().strftime('%Y%m%d')}-{generar_id_unico()}"

# ==============================
# Handlers
# ==============================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        user_data = DatabaseManager.get_user_data(handler_input)
        user_data = sincronizar_estados_libros(user_data)

        libros = user_data.get("libros_disponibles", [])
        total_libros = len(libros)
        prestamos_activos = len(user_data.get("prestamos_activos", []))
        usuario_frecuente = user_data.get("usuario_frecuente", False)
        speak_output = PhrasesManager.get_welcome_message(user_data, total_libros, prestamos_activos, usuario_frecuente)
        reprompt_output = "¿Quieres que te recuerde los comandos principales o añadir un libro?"

        if not usuario_frecuente:
            user_data["usuario_frecuente"] = True
            DatabaseManager.save_user_data(handler_input, user_data) 
            
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(reprompt_output)
                .response
        )

class AgregarLibroIntentHandler(AbstractRequestHandler):
    """Handler para agregar libros - Enfocado en el manejo manual del diálogo."""
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("AgregarLibroIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        session_attrs = handler_input.attributes_manager.session_attributes
        
        # --- Lógica de recuperación de Slots y Sesión (mantienes tu flujo) ---
        titulo = ask_utils.get_slot_value(handler_input, "titulo")
        autor = ask_utils.get_slot_value(handler_input, "autor")
        tipo = ask_utils.get_slot_value(handler_input, "tipo")
        
        if session_attrs.get("agregando_libro"):
            titulo = titulo or session_attrs.get("titulo_temp")
            autor = autor or session_attrs.get("autor_temp")
            tipo = tipo or session_attrs.get("tipo_temp")
            
        # PASO 1: Pedir título (y guardar temporalmente)
        if not titulo:
            session_attrs["agregando_libro"] = True
            session_attrs["esperando"] = "titulo"
            return (
                handler_input.response_builder
                    .speak("¡Perfecto! Vamos a agregar un libro. ¿Cuál es el título?")
                    .ask("¿Cuál es el título del libro?")
                    .response
            )
        session_attrs["titulo_temp"] = titulo
        session_attrs["agregando_libro"] = True
        
        # PASO 2: Pedir autor (y guardar temporalmente)
        if not autor:
            session_attrs["esperando"] = "autor"
            return (
                handler_input.response_builder
                    .speak(f"¡'{titulo}' suena interesante! ¿Quién es el autor? Si no lo sabes, di: no sé.")
                    .ask("¿Quién es el autor?")
                    .response
            )
        session_attrs["autor_temp"] = autor
        
        # PASO 3: Pedir tipo (y guardar temporalmente)
        if not tipo:
            session_attrs["esperando"] = "tipo"
            autor_text = f" de {autor}" if autor and autor.lower() not in ["no sé", "no se"] else ""
            return (
                handler_input.response_builder
                    .speak(f"Casi listo con '{titulo}'{autor_text}. ¿De qué tipo o género es? Si no sabes, di: no sé.")
                    .ask("¿De qué tipo es el libro?")
                    .response
            )
        session_attrs["tipo_temp"] = tipo

        nuevo_libro = BibliotecaService.agregar_libro(handler_input, titulo, autor, tipo)
        handler_input.attributes_manager.session_attributes = {}
        
        if nuevo_libro is False:
            speak_output = f"'{titulo}' ya está en tu biblioteca. {PhrasesManager.get_algo_mas()}"
            reprompt = PhrasesManager.get_preguntas_que_hacer()
        else:
            confirmacion = PhrasesManager.get_confirmaciones()
            
            autor_text = f" de {nuevo_libro.autor}" if nuevo_libro.autor != "Desconocido" else ""
            tipo_text = f", categoría {nuevo_libro.tipo}" if nuevo_libro.tipo != "Sin categoría" else ""
            
            speak_output = (
                f"{confirmacion}! He agregado '{nuevo_libro.titulo}'{autor_text}{tipo_text}. "
                f"Ahora tienes {len(BibliotecaService.get_libros(handler_input))} libros en tu biblioteca. "
                f"{PhrasesManager.get_algo_mas()}"
            )
            reprompt = PhrasesManager.get_preguntas_que_hacer()

        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(reprompt)
                .response
        )


class ContinuarAgregarHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        session_attrs = handler_input.attributes_manager.session_attributes
        return (session_attrs.get("agregando_libro") and 
                not ask_utils.is_intent_name("AgregarLibroIntent")(handler_input) and
                not ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) and
                not ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))
    
    def handle(self, handler_input: HandlerInput):
        session_attrs = handler_input.attributes_manager.session_attributes
        esperando = session_attrs.get("esperando")
        valor = None
        request = handler_input.request_envelope.request
        intent_name = request.intent.name if hasattr(request, 'intent') and request.intent else None
        
        if intent_name == "RespuestaGeneralIntent":
            valor = ask_utils.get_slot_value(handler_input, "respuesta")
        
        if not valor and intent_name and hasattr(request.intent, 'slots') and request.intent.slots:
            for slot_name, slot in request.intent.slots.items():
                if slot and hasattr(slot, 'value') and slot.value:
                    valor = slot.value
                    break
        
        # 2. Manejo de Malinterpretación de Intents (Workaround, se mantiene aquí)
        if not valor and intent_name in ["LimpiarCacheIntent", "SiguientePaginaIntent", 
                                        "ListarLibrosIntent", "BuscarLibroIntent"]:
            # Usar frases genéricas para pedir repetición
            if esperando == "autor":
                speak = "No entendí bien. Por favor di: 'el autor es' seguido del nombre. O di: no sé el autor."
                reprompt = "¿Quién es el autor? Di: 'el autor es' y el nombre."
            elif esperando == "tipo":
                speak = "No entendí bien. Por favor di: 'el tipo es' seguido del género. O di: no sé el tipo."
                reprompt = "¿De qué tipo es? Di: 'el tipo es' y el género."
            else: # Título
                speak = "No entendí bien. Por favor di: 'el título es' seguido del nombre del libro."
                reprompt = "¿Cuál es el título? Di: 'el título es' y el nombre."
            return handler_input.response_builder.speak(speak).ask(reprompt).response

        # 3. Procesar y Avanzar el Flujo (Lógica central)
        if esperando == "titulo":
            # Si el valor no es nulo, normalizar y avanzar.
            if valor:
                valor_limpio = BibliotecaService.limpiar_y_normalizar_valor(valor, "titulo")
                session_attrs["titulo_temp"] = valor_limpio
                session_attrs["esperando"] = "autor"
                speak = f"¡'{valor_limpio}' suena interesante! ¿Quién es el autor? Si no lo sabes, di: no sé el autor."
                return handler_input.response_builder.speak(speak).ask("¿Quién es el autor?").response
            else:
                # No se capturó valor
                speak = "No entendí el título. Por favor di: 'el título es' seguido del nombre del libro."
                return handler_input.response_builder.speak(speak).ask("¿Cuál es el título del libro?").response
        
        elif esperando == "autor":
            valor_limpio = BibliotecaService.limpiar_y_normalizar_valor(valor, "autor")
            session_attrs["autor_temp"] = valor_limpio
            session_attrs["esperando"] = "tipo"
            
            titulo = session_attrs.get("titulo_temp")
            autor_text = f" de {valor_limpio}" if valor_limpio != "Desconocido" else ""
            
            speak = f"Perfecto, '{titulo}'{autor_text}. ¿De qué tipo o género es? Si no sabes, di: no sé el tipo."
            return handler_input.response_builder.speak(speak).ask("¿De qué tipo es el libro?").response

        elif esperando == "tipo":
            valor_limpio = BibliotecaService.limpiar_y_normalizar_valor(valor, "tipo")
            
            # 4. FINALIZACIÓN y LLAMADA AL SERVICIO
            titulo_final = session_attrs.get("titulo_temp")
            autor_final = session_attrs.get("autor_temp", "Desconocido")
            tipo_final = valor_limpio
            
            nuevo_libro = BibliotecaService.agregar_libro(handler_input, titulo_final, autor_final, tipo_final)

            # 5. Construcción de la Respuesta Final
            handler_input.attributes_manager.session_attributes = {} # Limpiar sesión
            
            if nuevo_libro is False:
                speak_output = f"'{titulo_final}' ya está en tu biblioteca. {PhrasesManager.get_algo_mas()}"
                reprompt = PhrasesManager.get_preguntas_que_hacer()
            else:
                # Éxito (usamos el objeto Libro normalizado para la respuesta)
                autor_text = f" de {nuevo_libro.autor}" if nuevo_libro.autor != "Desconocido" else ""
                tipo_text = f", categoría {nuevo_libro.tipo}" if nuevo_libro.tipo != "Sin categoría" else ""
                
                speak_output = (
                    f"¡{PhrasesManager.get_confirmaciones()}! He agregado '{nuevo_libro.titulo}'{autor_text}{tipo_text}. "
                    f"{PhrasesManager.get_algo_mas()}"
                )
                reprompt = PhrasesManager.get_preguntas_que_hacer()

            return handler_input.response_builder.speak(speak_output).ask(reprompt).response
        
        # 6. Fallback (Si 'esperando' no está definido)
        handler_input.attributes_manager.session_attributes = {}
        return (
            handler_input.response_builder
                .speak("Hubo un problema. Empecemos de nuevo. ¿Qué libro quieres agregar?")
                .ask("¿Qué libro quieres agregar?")
                .response
        )


class ListarLibrosIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("ListarLibrosIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        session_attrs = handler_input.attributes_manager.session_attributes
        
        filtro = ask_utils.get_slot_value(handler_input, "filtro_tipo")
        autor = ask_utils.get_slot_value(handler_input, "autor")
        
        libros_filtrados, titulo_filtro = BibliotecaService.sincronizar_y_filtrar_libros(
            handler_input, filtro, autor
        )
        
        total_libros_usuario = len(BibliotecaService.get_libros(handler_input))

        if total_libros_usuario == 0:
            speak_output = "Aún no tienes libros en tu biblioteca. ¿Te gustaría agregar el primero? Solo di: agrega un libro."
            return handler_input.response_builder.speak(speak_output).ask("¿Quieres agregar tu primer libro?").response
            
        if not libros_filtrados:
            speak_output = f"No encontré libros{titulo_filtro}. {PhrasesManager.get_algo_mas()}"
            return handler_input.response_builder.speak(speak_output).ask(PhrasesManager.get_preguntas_que_hacer()).response
        
        pagina_actual = 0
        paginacion = BibliotecaService.obtener_pagina_libros(libros_filtrados, pagina_actual)
        
        libros_pagina = paginacion["libros_pagina"]
        total_filtrados = paginacion["total_filtrados"]
        inicio = paginacion["inicio"]
        fin = paginacion["fin"]
        
        if total_filtrados <= LIBROS_POR_PAGINA:
            speak_output = f"Tienes {total_filtrados} libros{titulo_filtro}: "
            titulos = [f"'{l.get('titulo', 'Sin título')}'" for l in libros_pagina]
            speak_output += ", ".join(titulos) + f". {PhrasesManager.get_algo_mas()}"
            
            session_attrs["pagina_libros"] = 0
            session_attrs["listando_libros"] = False
            ask_output = PhrasesManager.get_preguntas_que_hacer()
        else:
            speak_output = f"Tienes {total_filtrados} libros{titulo_filtro}. Te los voy a mostrar de {LIBROS_POR_PAGINA} en {LIBROS_POR_PAGINA}. "
            speak_output += f"Libros del {inicio + 1} al {fin}: "
            
            titulos = [f"'{l.get('titulo', 'Sin título')}'" for l in libros_pagina]
            speak_output += ", ".join(titulos) + ". "
            session_attrs["pagina_libros"] = pagina_actual + 1
            session_attrs["listando_libros"] = True
            session_attrs["libros_filtrados"] = libros_filtrados
            
            speak_output += f"Quedan {total_filtrados - fin} libros más. Di 'siguiente' para continuar o 'salir' para terminar."
            ask_output = "¿Quieres ver más libros? Di 'siguiente' o 'salir'."
            
        return handler_input.response_builder.speak(speak_output).ask(ask_output).response

class PrestarLibroIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("PrestarLibroIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        # 1. Obtener Slots
        titulo = ask_utils.get_slot_value(handler_input, "titulo")
        nombre_persona = ask_utils.get_slot_value(handler_input, "nombre_persona")

        # 2. Flujo: Pedir título si falta
        if not titulo:
            prompts = ["¡Claro! ¿Qué libro quieres prestar?", "Por supuesto. ¿Cuál libro vas a prestar?"]
            return handler_input.response_builder.speak(random.choice(prompts)).ask("¿Cuál es el título del libro?").response

        # 3. Lógica de Negocio: Intentar registrar el préstamo
        resultado = BibliotecaService.registrar_prestamo(handler_input, titulo, nombre_persona)

        # 4. Obtener información de disponibilidad para la respuesta
        num_disponibles, ejemplos_disponibles = BibliotecaService.get_libros_disponibles_info(handler_input)
        
        # 5. Construir Respuesta basada en el resultado
        if resultado == "no_encontrado":
            speak_output = f"Hmm, no encuentro '{titulo}' en tu biblioteca. "
            if num_disponibles > 0:
                ejemplos = ", ".join(ejemplos_disponibles)
                speak_output += f"Tienes disponibles: {ejemplos}. ¿Cuál quieres prestar?"
            elif BibliotecaService.get_libros(handler_input):
                speak_output += "Todos tus libros están prestados o no se reconoce el título exacto."
            else:
                speak_output += "De hecho, aún no tienes libros en tu biblioteca. Di 'agrega un libro' para empezar."
            return handler_input.response_builder.speak(speak_output).ask("¿Qué libro quieres prestar?").response
            
        elif resultado == "ya_prestado":
            speak_output = f"'{titulo}' ya está prestado. "
            if num_disponibles > 0:
                ejemplos = ", ".join(ejemplos_disponibles)
                speak_output += f"¿Quieres prestar otro? Tienes disponibles: {ejemplos}."
            else:
                speak_output += "No tienes más libros disponibles para prestar."
            return handler_input.response_builder.speak(speak_output).ask("¿Qué otro libro quieres prestar?").response

        # Préstamo Exitoso (resultado es el objeto Prestamo)
        elif isinstance(resultado, Prestamo):
            prestamo = resultado
            confirmacion = PhrasesManager.get_confirmaciones()
            persona_text = f" a {prestamo.persona}" if prestamo.persona != "un amigo" else "a un amigo"
            
            # Usar la propiedad 'fecha_limite_readable' del objeto Prestamo
            fecha_limite = prestamo.fecha_limite_readable 
                
            speak_output = f"{confirmacion} He registrado el préstamo de '{prestamo.titulo}'{persona_text}. "
            speak_output += f"La fecha de devolución sugerida es el {fecha_limite}. "
            
            if num_disponibles > 0:
                speak_output += f"Te quedan {num_disponibles} libros disponibles. "
            else:
                speak_output += "¡Ya no te quedan libros disponibles para prestar! "
                
            speak_output += PhrasesManager.get_algo_mas()

            return handler_input.response_builder.speak(speak_output).ask(PhrasesManager.get_preguntas_que_hacer()).response

        # Fallback de error
        else:
             # Manejo genérico de error que asume el try/except del handler padre
            logger.error(f"Resultado de préstamo inesperado: {resultado}")
            raise Exception("Error interno al registrar préstamo.")

class LimpiarCacheIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LimpiarCacheIntent")(handler_input)

    def handle(self, handler_input):
        try:
            user_id = DatabaseManager._user_id(handler_input)
            
            # Limpiar cache en memoria
            global _CACHE
            if user_id in _CACHE:
                del _CACHE[user_id]
            
            # Limpiar sesión
            handler_input.attributes_manager.session_attributes = {}
            
            # Recargar datos desde S3/FakeS3
            user_data = DatabaseManager.get_user_data(handler_input)
            
            # IMPORTANTE: Sincronizar estados
            user_data = sincronizar_estados_libros(user_data)
            
            # Guardar datos sincronizados
            DatabaseManager.save_user_data(handler_input, user_data)
            
            libros = user_data.get("libros_disponibles", [])
            prestamos = user_data.get("prestamos_activos", [])
            
            speak_output = "He limpiado el cache y sincronizado tu biblioteca. "
            speak_output += f"Tienes {len(libros)} libros en total y {len(prestamos)} préstamos activos. "
            speak_output += phrases.PhrasesManager.get_algo_mas()
            
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
        except Exception as e:
            logger.error(f"Error limpiando cache: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema limpiando el cache. Intenta de nuevo.")
                    .ask("¿Qué deseas hacer?")
                    .response
            )

# Añadir los demás handlers (los que no cambié)...
class BuscarLibroIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("BuscarLibroIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        try:
            titulo_buscado = ask_utils.get_slot_value(handler_input, "titulo")
            
            if not titulo_buscado:
                return (
                    handler_input.response_builder
                        .speak("¿Qué libro quieres buscar?")
                        .ask("Dime el título del libro que buscas.")
                        .response
                )
            libros_encontrados = BibliotecaService.buscar_libros(handler_input, titulo_buscado)
            
            speak_output = ""
            if not libros_encontrados:
                speak_output = f"No encontré ningún libro con el título '{titulo_buscado}'. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
                
            elif len(libros_encontrados) == 1:
                libro = libros_encontrados[0]
                speak_output = f"Encontré '{libro['titulo']}'. "
                speak_output += f"Autor: {libro.get('autor', 'Desconocido')}. "
                speak_output += f"Tipo: {libro.get('tipo', 'Sin categoría')}. "
                
                estado = libro.get('estado', 'disponible')
                speak_output += f"Estado: {estado}. "
                
                if libro.get('total_prestamos', 0) > 0:
                    speak_output += f"Ha sido prestado {libro['total_prestamos']} veces. "
                
                speak_output += phrases.PhrasesManager.get_algo_mas()
                
            else:
                speak_output = f"Encontré {len(libros_encontrados)} libros que coinciden con '{titulo_buscado}': "
                titulos_autores = [
                    f"'{l['titulo']}' de {l.get('autor', 'Desconocido')}" 
                    for l in libros_encontrados[:3]
                ]
                speak_output += ", ".join(titulos_autores)
                
                if len(libros_encontrados) > 3:
                    speak_output += f", y {len(libros_encontrados) - 3} más. "
                else:
                    speak_output += ". "
                    
                speak_output += phrases.PhrasesManager.get_algo_mas()
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
            
        except Exception as e:
            logger.error(f"Error en BuscarLibro: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema buscando el libro. ¿Intentamos de nuevo?")
                    .ask("¿Qué libro buscas?")
                    .response
            )

class DevolverLibroIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("DevolverLibroIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        try:
            titulo = ask_utils.get_slot_value(handler_input, "titulo")
            id_prestamo = ask_utils.get_slot_value(handler_input, "id_prestamo")
            if not titulo and not id_prestamo:
                prompts = [
                    "¡Qué bien! ¿Qué libro te devolvieron?",
                    "Perfecto, vamos a registrar la devolución. ¿Cuál libro es?",
                    "¡Excelente! ¿Qué libro estás devolviendo?"
                ]
                return (
                    handler_input.response_builder
                        .speak(random.choice(prompts))
                        .ask("¿Cuál es el título del libro?")
                        .response
                )
            resultado = BibliotecaService.registrar_devolucion(handler_input, titulo, id_prestamo)
            num_prestados, ejemplos_prestados = BibliotecaService.get_prestamos_activos_info(handler_input)

            speak_output = ""
            
            if resultado == "no_prestamos":
                speak_output = "No tienes libros prestados en este momento. Todos tus libros están en su lugar. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
            
            elif resultado == "no_encontrado":
                speak_output = f"Hmm, no encontré un préstamo activo para '{titulo or id_prestamo}'. "
                
                if num_prestados == 1:
                    speak_output += f"Solo tienes prestado {ejemplos_prestados[0]}. ¿Es ese?"
                elif num_prestados > 1:
                    speak_output += f"Tienes prestados: {', '.join(ejemplos_prestados)}. ¿Cuál de estos es?"
                else:
                    speak_output += "De hecho, ¡ya no tienes libros prestados!"
                
                return handler_input.response_builder.speak(speak_output).ask("¿Cuál libro quieres devolver?").response
            
            elif isinstance(resultado, dict):
                prestamo_finalizado = resultado
                confirmacion = phrases.PhrasesManager.get_confirmaciones()
                
                speak_output = f"{confirmacion} He registrado la devolución de '{prestamo_finalizado['titulo']}'. "
                
                if prestamo_finalizado.get("devuelto_a_tiempo", True):
                    speak_output += "¡Fue devuelto a tiempo! "
                else:
                    speak_output += "Fue devuelto un poco tarde, pero no hay problema. "
                
                speak_output += "Espero que lo hayan disfrutado. "
                
                if num_prestados > 0:
                    speak_output += f"Aún tienes {num_prestados} "
                    speak_output += "libro prestado. " if num_prestados == 1 else "libros prestados. "
                
                speak_output += phrases.PhrasesManager.get_algo_mas()
            else:
                raise Exception("Resultado de devolución inesperado.")
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
            
        except Exception as e:
            logger.error(f"Error en DevolverLibro: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Tuve un problema registrando la devolución. ¿Lo intentamos de nuevo?")
                    .ask("¿Qué libro quieres devolver?")
                    .response
            )

class ConsultarPrestamosIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("ConsultarPrestamosIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        try:
            resumen = BibliotecaService.obtener_resumen_prestamos(handler_input)
            
            total_prestamos = resumen["total"]
            
            if total_prestamos == 0:
                speak_output = "¡Excelente! No tienes ningún libro prestado en este momento. Todos están en su lugar. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
            else:
                detalles = resumen["detalles"]
                
                if total_prestamos == 1:
                    speak_output = "Déjame ver... Solo tienes un libro prestado: "
                else:
                    speak_output = f"Déjame revisar... Tienes {total_prestamos} libros prestados. Estos son los primeros: "
                
                speak_output += "; ".join(detalles[:5]) + ". "
                
                if total_prestamos > 5:
                    speak_output += f"Y {total_prestamos - 5} más. "
                
                if resumen["hay_vencidos"]:
                    speak_output += "¡ALERTA! Tienes libros vencidos. Te sugiero pedir la devolución. "
                elif resumen["hay_proximos"]:
                    speak_output += "Algunos están por vencer, ¡no lo olvides! "
                
                speak_output += phrases.PhrasesManager.get_algo_mas()
            
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
            
        except Exception as e:
            logger.error(f"Error en ConsultarPrestamos: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema consultando los préstamos. ¿Intentamos de nuevo?")
                    .ask("¿Qué más deseas hacer?")
                    .response
            )

class ConsultarDevueltosIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("ConsultarDevueltosIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        try:
            resumen = BibliotecaService.obtener_resumen_historial(handler_input)
            
            total = resumen["total"]
            
            if total == 0:
                speak_output = "Aún no has registrado devoluciones. Cuando prestes libros y te los devuelvan, aparecerán aquí. "
            else:
                speak_output = f"Has registrado {total} "
                speak_output += "devolución en total. " if total == 1 else "devoluciones en total. "
                
                detalles = resumen["detalles_voz"]
                
                if resumen["es_historial_completo"]:
                    speak_output += "Los libros devueltos son: "
                    speak_output += ", ".join(detalles) + ". "
                else:
                    speak_output += "Los 5 más recientes son: "
                    speak_output += ", ".join(detalles) + ". "
                    speak_output += f"Tienes {total - 5} devoluciones más en tu historial. "
            
            speak_output += phrases.PhrasesManager.get_algo_mas()
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
            
        except Exception as e:
            logger.error(f"Error en ConsultarDevueltos: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema consultando el historial.")
                    .ask("¿Qué más deseas hacer?")
                    .response
            )

class EliminarLibroIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return ask_utils.is_intent_name("EliminarLibroIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        try:
            titulo = ask_utils.get_slot_value(handler_input, "titulo")
            if not titulo:
                prompts = [
                    "¿Qué libro quieres eliminar de tu biblioteca?",
                    "Dime el título del libro que ya no quieres conservar.",
                ]
                return (
                    handler_input.response_builder
                        .speak(random.choice(prompts))
                        .ask("¿Cuál es el título?")
                        .response
                )
            resultado = BibliotecaService.eliminar_libro(handler_input, titulo)
            speak_output = ""
            
            if resultado == "no_encontrado":
                speak_output = f"No encontré el libro '{titulo}' en tu biblioteca. Asegúrate de que el título sea exacto. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
            
            elif resultado == "esta_prestado":
                speak_output = f"No puedo eliminar '{titulo}' porque actualmente está prestado. Primero pide que te lo devuelvan. "
                speak_output += "Di 'devolver libro' cuando lo tengas de vuelta. "
            
            elif isinstance(resultado, dict):
                libro_eliminado = resultado
                confirmacion = phrases.PhrasesManager.get_confirmaciones()
                
                speak_output = f"{confirmacion} He eliminado '{libro_eliminado['titulo']}' de tu biblioteca. "
                total_libros = BibliotecaService.get_libros(handler_input)
                speak_output += f"Ahora tienes {len(total_libros)} libros. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
            
            else:
                speak_output = "Hubo un problema al intentar eliminar el libro. ¿Intentamos de nuevo?"
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
            
        except Exception as e:
            logger.error(f"Error en EliminarLibro: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema procesando tu solicitud de eliminación. ¿Qué más deseas hacer?")
                    .ask("¿Qué más deseas hacer?")
                    .response
            )

class MostrarOpcionesIntentHandler(AbstractRequestHandler):
    """Handler para cuando el usuario pide que le repitan las opciones"""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("MostrarOpcionesIntent")(handler_input)

    def handle(self, handler_input):
        try:
            user_data = DatabaseManager.get_user_data(handler_input)
            total_libros = len(user_data.get("libros_disponibles", []))
            
            intro = "¡Por supuesto! "
            opciones = phrases.PhrasesManager.get_opciones_menu()
            
            # Agregar contexto si es útil
            if total_libros == 0:
                contexto = " Como aún no tienes libros, te sugiero empezar agregando algunos."
            elif len(user_data.get("prestamos_activos", [])) > 0:
                contexto = " Recuerda que tienes algunos libros prestados."
            else:
                contexto = ""
            
            pregunta = " " + phrases.PhrasesManager.get_preguntas_que_hacer()
            
            speak_output = intro + opciones + contexto + pregunta
            
            return (
                handler_input.response_builder
                    .speak(speak_output)
                    .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                    .response
            )
        except Exception as e:
            logger.error(f"Error mostrando opciones: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Puedo ayudarte a gestionar tu biblioteca. ¿Qué te gustaría hacer?")
                    .ask("¿En qué puedo ayudarte?")
                    .response
            )

class SiguientePaginaIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("SiguientePaginaIntent")(handler_input)

    def handle(self, handler_input):
        try:
            session_attrs = handler_input.attributes_manager.session_attributes
            
            if not session_attrs.get("listando_libros"):
                speak_output = "No estoy mostrando una lista en este momento. ¿Quieres ver tus libros?"
                return (
                    handler_input.response_builder
                        .speak(speak_output)
                        .ask("¿Quieres que liste tus libros?")
                        .response
                )
            
            # Continuar con la paginación
            handler = ListarLibrosIntentHandler()
            return handler.handle(handler_input)
            
        except Exception as e:
            logger.error(f"Error en SiguientePagina: {e}", exc_info=True)
            return (
                handler_input.response_builder
                    .speak("Hubo un problema. ¿Qué te gustaría hacer?")
                    .ask("¿En qué puedo ayudarte?")
                    .response
            )

class SalirListadoIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("SalirListadoIntent")(handler_input)

    def handle(self, handler_input):
        # Limpiar estado de paginación
        session_attrs = handler_input.attributes_manager.session_attributes
        session_attrs["pagina_libros"] = 0
        session_attrs["listando_libros"] = False
        
        speak_output = "De acuerdo, terminé de mostrar los libros. " + phrases.PhrasesManager.get_algo_mas()
        
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                .response
        )

# ==============================
# Handlers estándar
# ==============================
class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = (
            "¡Por supuesto! Te explico cómo funciona tu biblioteca. "
            "Puedes agregar libros nuevos diciendo 'agrega un libro', "
            "ver todos tus libros con 'lista mis libros', "
            "buscar un libro específico con 'busca' y el título, "
            "prestar un libro diciendo 'presta' seguido del título, "
            "registrar devoluciones con 'devuelvo' y el título, "
            "o consultar tus préstamos activos preguntando 'qué libros tengo prestados'. "
            "¿Qué te gustaría hacer primero?"
        )
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask("¿Con qué te ayudo?")
                .response
        )

class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
                ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # Limpiar sesión al salir
        handler_input.attributes_manager.session_attributes = {}
        
        despedidas = [
            "¡Hasta luego! Que disfrutes tu lectura.",
            "¡Nos vemos pronto! Espero que disfrutes tus libros.",
            "¡Adiós! Fue un gusto ayudarte con tu biblioteca.",
            "¡Hasta la próxima! Feliz lectura.",
            "¡Que tengas un excelente día! Disfruta tus libros."
        ]
        
        return (
            handler_input.response_builder
                .speak(random.choice(despedidas))
                .response
        )

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        # Limpiar sesión
        handler_input.attributes_manager.session_attributes = {}
        return handler_input.response_builder.response

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        session_attrs = handler_input.attributes_manager.session_attributes
        
        # Si estamos agregando un libro, manejar las respuestas
        if session_attrs.get("agregando_libro"):
            paso_actual = session_attrs.get("paso_actual")
            
            # Intentar obtener el texto del usuario del request
            request = handler_input.request_envelope.request
            
            # Para el fallback, Alexa a veces incluye el texto en el intent name o en slots genéricos
            # Vamos a asumir que el usuario respondió correctamente
            
            if paso_actual == "titulo":
                # El usuario probablemente dijo el título pero Alexa no lo reconoció
                return (
                    handler_input.response_builder
                        .speak("No entendí bien el título. ¿Puedes repetirlo más despacio?")
                        .ask("¿Cuál es el título del libro?")
                        .response
                )
            
            elif paso_actual == "autor":
                # Asumimos que dijo "no sé" o un nombre no reconocido
                session_attrs["autor_temp"] = "Desconocido"
                session_attrs["paso_actual"] = "tipo"
                titulo = session_attrs.get("titulo_temp")
                
                return (
                    handler_input.response_builder
                        .speak(f"De acuerdo, continuemos con '{titulo}'. ¿De qué tipo o género es? Por ejemplo: novela, fantasía, historia. Si no sabes, di: no sé.")
                        .ask("¿De qué tipo es el libro?")
                        .response
                )
            
            elif paso_actual == "tipo":
                # Asumimos que dijo "no sé" o un tipo no reconocido
                titulo_final = session_attrs.get("titulo_temp")
                autor_final = session_attrs.get("autor_temp", "Desconocido")
                tipo_final = "Sin categoría"
                
                # Guardar el libro
                user_data = DatabaseManager.get_user_data(handler_input)
                libros = user_data.get("libros_disponibles", [])
                
                # Verificar duplicado
                for libro in libros:
                    if libro.get("titulo", "").lower() == titulo_final.lower():
                        handler_input.attributes_manager.session_attributes = {}
                        return (
                            handler_input.response_builder
                                .speak(f"'{titulo_final}' ya está en tu biblioteca. " + phrases.PhrasesManager.get_algo_mas())
                                .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                                .response
                        )
                
                nuevo_libro = {
                    "id": generar_id_unico(),
                    "titulo": titulo_final,
                    "autor": autor_final,
                    "tipo": tipo_final,
                    "fecha_agregado": datetime.now().isoformat(),
                    "total_prestamos": 0,
                    "estado": "disponible"
                }
                
                libros.append(nuevo_libro)
                user_data["libros_disponibles"] = libros
                
                # Actualizar estadísticas
                stats = user_data.setdefault("estadisticas", {})
                stats["total_libros"] = len(libros)
                
                DatabaseManager.save_user_data(handler_input, user_data)
                
                # Limpiar sesión
                handler_input.attributes_manager.session_attributes = {}
                
                speak_output = f"¡Perfecto! He agregado '{titulo_final}'"
                if autor_final != "Desconocido":
                    speak_output += f" de {autor_final}"
                speak_output += f". Ahora tienes {len(libros)} libros en tu biblioteca. "
                speak_output += phrases.PhrasesManager.get_algo_mas()
                
                return (
                    handler_input.response_builder
                        .speak(speak_output)
                        .ask(phrases.PhrasesManager.get_preguntas_que_hacer())
                        .response
                )
        
        # Si estamos listando libros con paginación
        if session_attrs.get("listando_libros"):
            speak_output = "No entendí eso. ¿Quieres ver más libros? Di 'siguiente' para continuar o 'salir' para terminar."
            ask_output = "Di 'siguiente' o 'salir'."
        else:
            # Comportamiento normal del fallback
            respuestas = [
                "Disculpa, no entendí eso. ¿Podrías repetirlo de otra forma?",
                "Hmm, no estoy seguro de qué quisiste decir. ¿Me lo puedes decir de otra manera?",
                "Perdón, no comprendí. ¿Puedes intentarlo de nuevo?"
            ]
            
            speak_output = random.choice(respuestas)
            speak_output += " Recuerda que puedo ayudarte a agregar libros, listarlos, prestarlos o registrar devoluciones."
            ask_output = "¿Qué te gustaría hacer?"
        
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(ask_output)
                .response
        )

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"Exception: {exception}", exc_info=True)
        # Limpiar sesión en caso de error
        handler_input.attributes_manager.session_attributes = {}
        
        respuestas = [
            "Ups, algo no salió como esperaba. ¿Podemos intentarlo de nuevo?",
            "Perdón, tuve un pequeño problema. ¿Lo intentamos otra vez?",
            "Disculpa, hubo un inconveniente. ¿Qué querías hacer?"
        ]
        
        return (
            handler_input.response_builder
                .speak(random.choice(respuestas))
                .ask("¿En qué puedo ayudarte?")
                .response
        )

# ==============================
# Registrar handlers - ORDEN CRÍTICO
# ==============================
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(MostrarOpcionesIntentHandler())

# ContinuarAgregarHandler DEBE ir ANTES que otros handlers para interceptar respuestas
sb.add_request_handler(ContinuarAgregarHandler())

# Luego AgregarLibroIntentHandler
sb.add_request_handler(AgregarLibroIntentHandler())

# Luego los demás handlers
sb.add_request_handler(ListarLibrosIntentHandler())
sb.add_request_handler(BuscarLibroIntentHandler())
sb.add_request_handler(PrestarLibroIntentHandler())
sb.add_request_handler(DevolverLibroIntentHandler())
sb.add_request_handler(ConsultarPrestamosIntentHandler())
sb.add_request_handler(ConsultarDevueltosIntentHandler())
sb.add_request_handler(EliminarLibroIntentHandler())
sb.add_request_handler(LimpiarCacheIntentHandler())
sb.add_request_handler(SiguientePaginaIntentHandler())
sb.add_request_handler(SalirListadoIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(CatchAllExceptionHandler())
lambda_handler = sb.lambda_handler()
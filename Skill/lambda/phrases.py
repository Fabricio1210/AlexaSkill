import random

class PhrasesManager:
    # ==============================
    SALUDOS = [
        "¡Hola! ¡Qué gusto tenerte aquí!",
        "¡Bienvenido de vuelta!",
        "¡Hola! Me alegra que estés aquí.",
        "¡Qué bueno verte por aquí!",
        "¡Hola! Espero que tengas un excelente día."
    ]
    
    OPCIONES_MENU = [
        "Puedo ayudarte a gestionar tu biblioteca personal. Puedes agregar libros nuevos, ver tu lista de libros, prestar libros a tus amigos, registrar devoluciones o consultar qué libros tienes prestados.",
        "Tengo varias opciones para ti: agregar libros a tu colección, listar todos tus libros, prestar un libro a alguien, devolver un libro que te regresaron, o ver tus préstamos activos.",
        "Puedo hacer varias cosas: agregar libros nuevos a tu biblioteca, mostrarte qué libros tienes, ayudarte a prestar libros, registrar cuando te los devuelven, o decirte qué libros están prestados."
    ]
    
    PREGUNTAS_QUE_HACER = [
        "¿Qué te gustaría hacer hoy?",
        "¿En qué puedo ayudarte?",
        "¿Qué necesitas?",
        "¿Cómo puedo ayudarte con tu biblioteca?",
        "¿Qué quieres hacer?"
    ]
    
    ALGO_MAS = [
        "¿Hay algo más en lo que pueda ayudarte?",
        "¿Necesitas algo más?",
        "¿Qué más puedo hacer por ti?",
        "¿Te ayudo con algo más?",
        "¿Hay algo más que quieras hacer?"
    ]
    
    CONFIRMACIONES = [
        "¡Perfecto!",
        "¡Excelente!",
        "¡Genial!",
        "¡Muy bien!",
        "¡Estupendo!"
    ]
    
    @staticmethod
    def get_random_phrase(phrase_list):
        """Selecciona una frase aleatoria de una lista"""
        return random.choice(phrase_list)
    
    @classmethod
    def get_saludo(cls): 
        saludo = cls.get_random_phrase(cls.SALUDOS) 
        return saludo
        
    @classmethod
    def get_opciones_menu(cls):
        opcion = cls.get_random_phrase(cls.OPCIONES_MENU)
        return opcion
        
    @classmethod
    def get_preguntas_que_hacer(cls):
        pregunta_que_hacer = cls.get_random_phrase(cls.PREGUNTAS_QUE_HACER)
        return pregunta_que_hacer
    
    @classmethod
    def get_algo_mas(cls):
        algo_mas = cls.get_random_phrase(cls.ALGO_MAS)
        return algo_mas
    
    @classmethod
    def get_confirmaciones(cls):
        confirmacion = cls.get_random_phrase(cls.CONFIRMACIONES)
        return confirmacion

    @classmethod
    def get_welcome_message(cls, user_data, total_libros, prestamos_activos, usuario_frecuente):
        if usuario_frecuente and total_libros > 0:
            saludo = "¡Hola de nuevo! ¡Qué bueno verte por aquí!"
            estado = f"Veo que tienes {total_libros} libros en tu biblioteca"
            if prestamos_activos > 0:
                estado += f" y {prestamos_activos} préstamos activos."
            else:
                estado += "."
        else:
            saludo = cls.get_saludo() 
            if total_libros == 0:
                estado = "Veo que es tu primera vez aquí. ¡Empecemos a construir tu colección!"
            else:
                estado = f"Tienes {total_libros} libros en tu colección. ¿Qué quieres hacer?"
                
        opciones = cls.get_opciones_menu()
        pregunta = cls.get_preguntas_que_hacer()   
                
        return f"{saludo} {estado} {opciones} {pregunta}"
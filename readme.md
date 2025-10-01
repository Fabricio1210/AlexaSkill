# Mi Biblioteca Personal - Alexa Skill

Este es el código *backend* (función AWS Lambda) para una skill de Alexa diseñada para ayudarte a llevar un registro de tu biblioteca personal, centrándose en el control de préstamos.

---

## Conceptos Básicos

El diseño de este proyecto sigue una arquitectura limpia (inspirada en Patrones de Diseño) donde las responsabilidades están estrictamente separadas:

1.  **Handlers (`lambda_function.py`)**: Gestionan la interacción de voz de Alexa. Su único trabajo es obtener los *slots* (títulos, nombres) y delegar la lógica de negocio.
2.  **Lógica de Negocio (`services.py`)**: Contiene la clase `BibliotecaService`, donde reside toda la validación, búsqueda, registro de préstamos, y actualización de datos.
3.  **Modelos (`models.py`)**: Define las entidades básicas de la aplicación (`Libro`, `Prestamo`).
4.  **Persistencia (`database.py`)**: Aísla la aplicación de la base de datos (AWS S3, en este caso), proporcionando métodos simples de lectura y escritura (`get_user_data`, `save_user_data`).


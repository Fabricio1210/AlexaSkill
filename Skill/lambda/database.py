import logging
import os
from config import USE_FAKE_S3, ENABLE_DDB_CACHE, CACHE_TTL_SECONDS
import boto3
from datetime import datetime, timedelta

# ==============================
# Adaptador de "Fake S3" (memoria)
# ==============================
_FAKE_STORE = {}
logger = logging.getLogger(__name__)

class FakeS3Adapter:
    def __init__(self):
        logger.info("🧪 Usando FakeS3Adapter (memoria)")

    @staticmethod
    def _user_id_from_envelope(request_envelope):
        return request_envelope.context.system.user.user_id

    def get_attributes(self, request_envelope):
        uid = self._user_id_from_envelope(request_envelope)
        return _FAKE_STORE.get(uid, {})

    def save_attributes(self, request_envelope, attributes):
        uid = self._user_id_from_envelope(request_envelope)
        _FAKE_STORE[uid] = attributes or {}
        logger.info(f"FakeS3Adapter: guardados atributos para {uid}")

    def delete_attributes(self, request_envelope):
        uid = self._user_id_from_envelope(request_envelope)
        if uid in _FAKE_STORE:
            del _FAKE_STORE[uid]
            logger.info(f"FakeS3Adapter: atributos borrados para {uid}")


# ==============================
# Cache en memoria con TTL
# ==============================
_CACHE = {}

def _cache_get(user_id):
    item = _CACHE.get(user_id)
    if not item:
        return None
    if datetime.now().timestamp() > item["expire_at"]:
        _CACHE.pop(user_id, None)
        return None
    return item["data"]

def _cache_put(user_id, data):
    _CACHE[user_id] = {
        "data": data,
        "expire_at": (datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS)).timestamp()
    }

dynamodb = boto3.resource("dynamodb", region_name="us-east-1") if ENABLE_DDB_CACHE else None

class DatabaseManager:
    DDB_TABLE = "BibliotecaSkillCache"

    @staticmethod
    def _user_id(handler_input):
        return handler_input.request_envelope.context.system.user.user_id

    @staticmethod
    def _get_ddb_table():
        if not ENABLE_DDB_CACHE:
            return None
        try:
            table = dynamodb.Table(DatabaseManager.DDB_TABLE)
            table.load()
            return table
        except Exception as e:
            logger.warning(f"DDB deshabilitado o sin permisos: {e}")
            return None

    @staticmethod
    def get_user_data(handler_input):
        user_id = DatabaseManager._user_id(handler_input)

        # 1) Cache en memoria
        data = _cache_get(user_id)
        if data is not None:
            logger.info("⚡ Cache hit (memoria)")
            return data

        # 2) Cache en DDB (opcional)
        if ENABLE_DDB_CACHE:
            try:
                table = DatabaseManager._get_ddb_table()
                if table:
                    resp = table.get_item(Key={"user_id": user_id})
                    if "Item" in resp:
                        data = resp["Item"].get("data", {})
                        logger.info("⚡ Cache hit (DynamoDB)")
                        _cache_put(user_id, data)
                        return data
            except Exception as e:
                logger.warning(f"DDB get_item error: {e}")

        # 3) Persistencia principal
        attr_mgr = handler_input.attributes_manager
        persistent = attr_mgr.persistent_attributes
        if not persistent:
            persistent = DatabaseManager.initial_data()
            attr_mgr.persistent_attributes = persistent
            attr_mgr.save_persistent_attributes()

        # 4) Actualizar cachés
        _cache_put(user_id, persistent)
        if ENABLE_DDB_CACHE:
            try:
                table = DatabaseManager._get_ddb_table()
                if table:
                    table.put_item(Item={
                        "user_id": user_id,
                        "data": persistent,
                        "ttl": int((datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS)).timestamp())
                    })
            except Exception as e:
                logger.warning(f"DDB put_item error: {e}")

        return persistent

    @staticmethod
    def save_user_data(handler_input, data):
        user_id = DatabaseManager._user_id(handler_input)

        # Persistencia principal
        attr_mgr = handler_input.attributes_manager
        attr_mgr.persistent_attributes = data
        attr_mgr.save_persistent_attributes()

        # Actualizar cachés
        _cache_put(user_id, data)

        if ENABLE_DDB_CACHE:
            try:
                table = DatabaseManager._get_ddb_table()
                if table:
                    table.put_item(Item={
                        "user_id": user_id,
                        "data": data,
                        "ttl": int((datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS)).timestamp())
                    })
            except Exception as e:
                logger.warning(f"DDB put_item error: {e}")

    @staticmethod
    def initial_data():
        return {
            "libros_disponibles": [],
            "prestamos_activos": [],
            "historial_prestamos": [],
            "estadisticas": {
                "total_libros": 0,
                "total_prestamos": 0,
                "total_devoluciones": 0
            },
            "historial_conversaciones": [],
            "configuracion": {"limite_prestamos": 10, "dias_prestamo": 7},  # Aumentado el límite
            "usuario_frecuente": False
        }
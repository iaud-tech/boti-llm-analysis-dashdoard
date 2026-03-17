from fastapi import FastAPI, UploadFile, File, HTTPException
import httpx
import json
import traceback
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from fastapi.middleware.cors import CORSMiddleware

# =========================
# CARGA DE VARIABLES .env
# =========================
BASE_DIR = Path(__file__).resolve().parent
env_path = find_dotenv(str(BASE_DIR / ".env"))
load_dotenv(env_path)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # React/Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# CONFIGURACIÓN
# =========================
OPEN_WEBUI_URL = os.getenv("OPEN_WEBUI_URL")
API_KEY = os.getenv("OPEN_WEBUI_API_KEY")
MODEL_ID = os.getenv("MODEL_ID")
PORT = int(os.getenv("PORT", 8000))

# =========================
# VALIDACIÓN AL ARRANCAR
# =========================
missing_vars = []
if not OPEN_WEBUI_URL:
    missing_vars.append("OPEN_WEBUI_URL")
if not API_KEY:
    missing_vars.append("OPEN_WEBUI_API_KEY")
if not MODEL_ID:
    missing_vars.append("MODEL_ID")

if missing_vars:
    print("\n" + "=" * 60)
    print("❌ CONFIGURACIÓN INCOMPLETA")
    print("Faltan las siguientes variables en el .env:")
    for var in missing_vars:
        print(f" - {var}")
    print("=" * 60 + "\n")
    raise RuntimeError("Faltan variables de entorno obligatorias.")

# =========================
# ENDPOINT DE SALUD
# =========================
@app.get("/health")
async def health():
    return {"status": "ok"}

# =========================
# HELPERS
# =========================
def extraer_texto_content(content):
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        partes = []
        for item in content:
            if isinstance(item, str) and item.strip():
                partes.append(item.strip())
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content") or item.get("value") or ""
                if isinstance(txt, str) and txt.strip():
                    partes.append(txt.strip())
        return "\n".join(partes).strip()

    if isinstance(content, dict):
        txt = content.get("text") or content.get("content") or content.get("value") or ""
        if isinstance(txt, str):
            return txt.strip()

    return ""


def ordenar_mensajes(mensajes):
    return sorted(
        mensajes,
        key=lambda x: (
            x.get("timestamp", 0) if x.get("timestamp") is not None else 0,
            x.get("create_time", 0) if x.get("create_time") is not None else 0,
            str(x.get("id", "")),
            str(x.get("role", "")),
        )
    )


def extraer_mensajes_de_chat(chat):
    mensajes = chat.get("messages", [])
    if isinstance(mensajes, list) and mensajes:
        return ordenar_mensajes(mensajes)

    mensajes_dict = chat.get("chat", {}).get("history", {}).get("messages", {})
    if isinstance(mensajes_dict, dict) and mensajes_dict:
        return ordenar_mensajes(list(mensajes_dict.values()))

    return []


# =========================
# FILTRO: EXTRACCIÓN LIMPIA
# =========================
def extraer_conversaciones_limpias(raw_data):
    chats_limpios = []

    if isinstance(raw_data, list):
        for i, chat in enumerate(raw_data, start=1):
            if not isinstance(chat, dict):
                continue

            titulo = chat.get("title", f"Chat {i}")
            mensajes = extraer_mensajes_de_chat(chat)

            dialogo = []
            for msg in mensajes:
                rol = msg.get("role")
                texto = extraer_texto_content(msg.get("content"))
                if rol and texto:
                    dialogo.append(f"[{str(rol).upper()}]: {texto}")

            if dialogo:
                texto_chat = f"--- {str(titulo).upper()} ---\n" + "\n".join(dialogo)
                chats_limpios.append(texto_chat)

    return "\n\n".join(chats_limpios)


# =========================
# MÉTRICAS CALCULADAS EN PYTHON
# =========================
def calcular_metricas_generales(raw_data):
    total_conversaciones = 0
    total_interacciones = 0

    if isinstance(raw_data, list):
        for chat in raw_data:
            if not isinstance(chat, dict):
                continue

            mensajes = extraer_mensajes_de_chat(chat)

            interacciones_validas = 0
            for msg in mensajes:
                rol = msg.get("role")
                texto = extraer_texto_content(msg.get("content"))
                if rol and texto:
                    interacciones_validas += 1

            if interacciones_validas > 0:
                total_conversaciones += 1
                total_interacciones += interacciones_validas

    promedio = 0
    if total_conversaciones > 0:
        promedio = round(total_interacciones / total_conversaciones, 2)

    return {
        "total_conversaciones_analizadas": total_conversaciones,
        "promedio_interacciones_por_chat": promedio
    }


# =========================
# PARSEO DE JSON DE LA IA
# =========================
def intentar_parsear_json(texto):
    texto = texto.strip()

    try:
        data = json.loads(texto)
        if not isinstance(data, dict):
            raise ValueError("La respuesta JSON no es un objeto")
        return data
    except Exception:
        pass

    first = texto.find("{")
    last = texto.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = texto[first:last + 1]
        data = json.loads(candidate)
        if not isinstance(data, dict):
            raise ValueError("La respuesta JSON rescatada no es un objeto")
        return data

    raise ValueError("No se pudo parsear la respuesta como JSON")


# =========================
# PROCESAMIENTO DEL JSON
# =========================
@app.post("/process-conversations")
async def process_json(file: UploadFile = File(...)):
    print(f"\nArchivo recibido: {file.filename}")

    try:
        contents = await file.read()
        raw_data = json.loads(contents)

        # 1. Python calcula métricas generales
        metricas_generales = calcular_metricas_generales(raw_data)

        # 2. Filtramos la "basura" y nos quedamos solo con los guiones de chat
        texto_limpio = extraer_conversaciones_limpias(raw_data)


        if not texto_limpio.strip():
            raise HTTPException(
                status_code=400,
                detail="No se encontró texto de chat válido en el archivo. ¿Estás seguro de que es una exportación de chats?"
            )

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        mensaje_sistema = (
            "Actúa como un Analista de Datos Educativos. "
            "Tu tarea es analizar un historial de conversaciones entre estudiantes y un asistente virtual. "
            "Debes devolver ESTRICTAMENTE un JSON válido. "
            "NO incluyas saludos, explicaciones, ni markdown. "
            "La respuesta debe ser únicamente un objeto JSON parseable. "
            "NO calcules 'metricas_generales'. "
            "Devuelve igualmente la clave 'metricas_generales', pero con este contenido exacto:\n"
            '{'
            '"total_conversaciones_analizadas": 0,'
            '"promedio_interacciones_por_chat": 0'
            '}'
        )

        mensaje_usuario = (
            "Analiza este historial y devuelve el JSON con la estructura esperada. "
            "Recuerda: no calcules las metricas_generales reales; deja esos dos valores a 0 "
            "porque se sustituirán en backend.\n\n"
            f"<historial>\n{texto_limpio}\n</historial>"
        )

        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": mensaje_sistema},
                {"role": "user", "content": mensaje_usuario}
            ],
            "stream": False,
            "temperature": 0.0,
            "top_p": 1
        }

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                OPEN_WEBUI_URL,
                json=payload,
                headers=headers
            )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error del servidor de IA: {response.text}"
            )

        result = response.json()
        respuesta_ia = result["choices"][0]["message"]["content"].strip()

        # 4. Parseamos el JSON devuelto por la IA
        respuesta_json = intentar_parsear_json(respuesta_ia)

        # 5. Sobrescribimos las métricas generales con las calculadas en Python
        respuesta_json["metricas_generales"] = metricas_generales

        # 6. Lo devolvemos en el formato que espera tu frontend
        return {"content": json.dumps(respuesta_json, ensure_ascii=False)}

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="El archivo no es un JSON válido")

    except Exception as e:
        print("❌ ERROR CRÍTICO:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
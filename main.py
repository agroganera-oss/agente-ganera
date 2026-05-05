from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright
from groq import Groq
from supabase import create_client
import base64
import os
import uuid
from datetime import datetime

app = FastAPI(
    title="Agente IA",
    docs_url="/docs",
    openapi_url="/openapi.json"
)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key="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")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ultima_pantalla = ""
historial_pantallas = []

async def capturar_pantalla(page):
    screenshot = await page.screenshot(type="jpeg", quality=60)
    return base64.b64encode(screenshot).decode()

async def preguntarle_a_groq(tarea, pantalla_base64, pasos_anteriores=[], prompt_extra=""):
    pasos_str = "\n".join([f"- {p}" for p in pasos_anteriores]) if pasos_anteriores else "Ninguno todavía"
    try:
        respuesta = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""Eres un agente que controla un navegador web de gestión ganadera llamado OVZnet.
Tarea: {tarea}

Pasos que ya has ejecutado:
{pasos_str}

{prompt_extra}

REGLAS ESTRICTAS:
- Responde SOLO con UNA línea
- Sin explicaciones, sin puntos, sin comillas
- NO repitas un paso que ya hayas hecho
- Usa EXACTAMENTE uno de estos formatos:

escribir_campo SELECTOR:::TEXTO
click en TEXTO_DEL_BOTON
navegar a URL
tarea completada RESULTADO

¿Qué acción hacer AHORA?"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{pantalla_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=80
        )
        respuesta_texto = respuesta.choices[0].message.content.strip()
        primera_linea = respuesta_texto.split('\n')[0].strip()
        return primera_linea
    except Exception as e:
        print(f"Error en Groq: {e}", flush=True)
        return "error"

async def crear_contexto_navegador(p, carpeta_descargas="/tmp/descargas"):
    os.makedirs(carpeta_descargas, exist_ok=True)
    navegador = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
            "--disable-blink-features=AutomationControlled"
        ]
    )
    contexto = await navegador.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        java_script_enabled=True,
        locale="es-ES",
        timezone_id="Europe/Madrid",
        accept_downloads=True
    )
    await contexto.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es'] });
        window.chrome = { runtime: {} };
    """)
    return navegador, contexto

async def subir_a_supabase(ruta_archivo, nombre_archivo, usuario):
    try:
        with open(ruta_archivo, "rb") as f:
            contenido = f.read()
        
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_storage = f"{usuario}/{fecha}_{nombre_archivo}"
        
        supabase.storage.from_("documentos-ovznet").upload(
            ruta_storage,
            contenido,
            {"content-type": "application/pdf"}
        )
        
        url_firmada = supabase.storage.from_("documentos-ovznet").create_signed_url(
            ruta_storage,
            3600
        )
        
        print(f"Archivo subido: {ruta_storage}", flush=True)
        return url_firmada.get("signedURL", "")
    except Exception as e:
        print(f"Error subiendo a Supabase: {e}", flush=True)
        return ""

async def ejecutar_agente(pagina, tarea, max_pasos=15, prompt_extra=""):
    pasos = []
    for intento in range(max_pasos):
        print(f"--- Intento {intento + 1} ---", flush=True)

        pantalla = await capturar_pantalla(pagina)
        global ultima_pantalla
        ultima_pantalla = pantalla
        accion = await preguntarle_a_groq(tarea, pantalla, pasos, prompt_extra)
        print(f"Acción: {accion}", flush=True)
        pasos.append(accion)
        historial_pantallas.append((accion, pantalla))

        if "tarea completada" in accion.lower() or "error" in accion.lower():
            pantalla_final = await capturar_pantalla(pagina)
            historial_pantallas.append(("pantalla final", pantalla_final))
            break

        elif accion.lower().startswith("escribir_campo"):
            try:
                contenido = accion.replace("escribir_campo", "").strip()
                partes = contenido.split(":::")
                selector = partes[0].strip()
                texto = partes[1].strip() if len(partes) > 1 else ""
                campo = await pagina.wait_for_selector(selector, timeout=5000)
                await campo.click()
                await campo.fill("")
                await campo.type(texto, delay=50)
                print(f"Escrito '{texto}' en '{selector}'", flush=True)
                await pagina.wait_for_timeout(500)
            except Exception as e:
                print(f"Fallo escribir_campo: {e}", flush=True)

        elif accion.lower().startswith("escribir"):
            texto_escribir = accion.lower().replace("escribir", "").strip()
            try:
                selector = "input[type='text'], input[type='search'], textarea"
                campo = await pagina.wait_for_selector(selector, timeout=5000)
                await campo.click()
                await campo.fill("")
                await campo.type(texto_escribir, delay=50)
                await pagina.wait_for_timeout(500)
                await campo.press("Enter")
                print(f"Escrito: {texto_escribir}", flush=True)
                await pagina.wait_for_load_state("domcontentloaded")
                await pagina.wait_for_timeout(3000)
            except Exception as e:
                print(f"Fallo al escribir: {e}", flush=True)

        elif accion.lower().startswith("click en"):
            texto = accion.lower().replace("click en", "").strip()
            try:
                await pagina.get_by_text(texto, exact=False).first.click(timeout=5000)
                await pagina.wait_for_load_state("domcontentloaded")
                await pagina.wait_for_timeout(2000)
                print(f"Click en: {texto}", flush=True)
            except:
                try:
                    await pagina.locator(f"button:has-text('{texto}')").first.click(timeout=3000)
                except:
                    try:
                        await pagina.locator(f"input[value*='{texto}']").first.click(timeout=3000)
                    except:
                        print(f"No se encontró el botón: {texto}", flush=True)

        elif accion.lower().startswith("navegar a"):
            url = accion.lower().replace("navegar a", "").strip()
            if not url.startswith("http"):
                url = f"https://{url}"
            await pagina.goto(url)
            await pagina.wait_for_load_state("domcontentloaded")
            await pagina.wait_for_timeout(2000)

        await pagina.wait_for_timeout(1500)

    return pasos

@app.get("/")
def root():
    return {"estado": "agente funcionando"}

@app.get("/pantalla", response_class=HTMLResponse)
def ver_pantalla():
    global ultima_pantalla, historial_pantallas
    if not historial_pantallas:
        return "<h2>No hay pantalla guardada todavía</h2>"

    html = """
    <html>
    <head>
        <title>Pantallas del agente</title>
        <style>
            body { font-family: Arial; background: #1a1a1a; color: white; padding: 20px; }
            h2 { color: #4CAF50; }
            .pantalla { margin: 20px 0; border: 2px solid #4CAF50; padding: 10px; border-radius: 8px; }
            .pantalla h3 { color: #aaa; font-size: 14px; }
            img { max-width: 100%; border-radius: 4px; }
            .ultima { border-color: #ff9800; }
        </style>
    </head>
    <body>
        <h2>Historial de pantallas del agente</h2>
    """

    for i, (accion, img) in enumerate(historial_pantallas):
        clase = "ultima" if i == len(historial_pantallas) - 1 else ""
        html += f"""
        <div class='pantalla {clase}'>
            <h3>Paso {i+1}: {accion}</h3>
            <img src='data:image/jpeg;base64,{img}'/>
        </div>
        """

    html += "</body></html>"
    return html

@app.post("/ejecutar")
async def ejecutar_tarea(request: Request):
    global historial_pantallas
    historial_pantallas = []

    datos = await request.json()
    tarea = datos.get("tarea")
    url_inicio = datos.get("url", "https://www.google.com")
    usuario = datos.get("usuario", "")
    password = datos.get("password", "")

    if usuario and password:
        tarea = f"{tarea}. Usuario: '{usuario}', Contraseña: '{password}'"

    prompt_extra = """
FLUJO DE LOGIN:
1. Primero escribe el usuario en input[type='text']
2. Luego escribe la contraseña en input[type='password']
3. Luego haz click en el botón Entrar
4. Si ya estás dentro responde tarea completada
"""

    async with async_playwright() as p:
        navegador, contexto = await crear_contexto_navegador(p)
        pagina = await contexto.new_page()
        await pagina.goto(url_inicio)
        await pagina.wait_for_load_state("domcontentloaded")
        await pagina.wait_for_timeout(3000)

        pasos = await ejecutar_agente(pagina, tarea, prompt_extra=prompt_extra)

        await navegador.close()

        return {
            "estado": "proceso terminado",
            "pasos": pasos,
            "ver_pantallas": "https://agente-ganera.onrender.com/pantalla"
        }

@app.post("/importardocs")
async def importar_documentos(request: Request):
    global historial_pantallas
    historial_pantallas = []

    datos = await request.json()
    usuario = datos.get("usuario", "")
    password = datos.get("password", "")
    tipo_tramite = datos.get("tipo_tramite", "")

    tarea = f"""Entra en OVZnet con usuario '{usuario}' y contraseña '{password}'.
Una vez dentro ve al menú Documentos en la barra superior y haz click en el submenú Documentos.
Pulsa el botón Buscar para ver todos los documentos disponibles.
Cuando veas la lista de documentos haz click en el número del primer documento para descargarlo."""

    prompt_extra = """
FLUJO PARA DESCARGAR DOCUMENTOS EN OVZNET:
1. Escribe el usuario en input[type='text']
2. Escribe la contraseña en input[type='password']
3. Haz click en el botón Entrar
4. Una vez dentro haz click en el menú Documentos
5. Haz click en el submenú Documentos
6. Pulsa el botón Buscar
7. Haz click en el número del primer documento de la lista
8. Espera a que se descargue y responde tarea completada
"""

    urls_documentos = []

    async with async_playwright() as p:
        carpeta_descargas = f"/tmp/descargas_{uuid.uuid4().hex}"
        os.makedirs(carpeta_descargas, exist_ok=True)

        navegador, contexto = await crear_contexto_navegador(p, carpeta_descargas)

        pagina = await contexto.new_page()

        async def manejar_descarga(download):
            nombre = download.suggested_filename or f"documento_{uuid.uuid4().hex}.pdf"
            ruta = f"{carpeta_descargas}/{nombre}"
            await download.save_as(ruta)
            print(f"Descargado: {nombre}", flush=True)
            url = await subir_a_supabase(ruta, nombre, usuario)
            if url:
                urls_documentos.append({
                    "nombre": nombre,
                    "url": url
                })

        pagina.on("download", manejar_descarga)

        await pagina.goto("https://ovznet.juntaex.es/")
        await pagina.wait_for_load_state("domcontentloaded")
        await pagina.wait_for_timeout(3000)

        pasos = await ejecutar_agente(pagina, tarea, max_pasos=20, prompt_extra=prompt_extra)

        await pagina.wait_for_timeout(5000)
        await navegador.close()

        return {
            "estado": "proceso terminado",
            "pasos": pasos,
            "documentos_descargados": urls_documentos,
            "ver_pantallas": "https://agente-ganera.onrender.com/pantalla"
        }

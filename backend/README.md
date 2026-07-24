# Backend

Backend de Azure Functions para crear agentes de Microsoft Foundry, adjuntar conocimiento con File Search, adjuntar archivos de analisis con Code Interpreter y entregar respuestas por streaming SSE al frontend.

En palabras simples: este backend es el puente entre la pantalla web y Azure AI Foundry. El frontend le pide crear agentes, subir documentos, hacer preguntas y descargar archivos. El backend se encarga de hablar con Foundry, guardar los identificadores importantes y devolver la respuesta al usuario.

El archivo central es:

```text
function_app.py
```

## Stack

- Azure Functions Python
- `azure-ai-projects`
- `azure-identity`
- `azurefunctions-extensions-http-fastapi`
- `python-multipart`, con fallback manual si no esta disponible
- Persistencia local en `agents_db.json`

Cada elemento cumple un papel:

- Azure Functions expone las URLs del backend.
- `azure-ai-projects` permite crear agentes y conectarse a Foundry.
- `azure-identity` permite autenticarse contra Azure.
- `agents_db.json` guarda la relacion entre lo que ve el usuario y lo que existe en Foundry.

## Resumen para Personas no Tecnicas

El sistema funciona con tres ideas principales:

1. **Agente**: es el asistente de IA que responde preguntas. Cada agente tiene un nombre y una version en Foundry.
2. **Conocimiento**: son documentos que el agente puede consultar, como PDF, Word, texto o presentaciones. Esto usa File Search.
3. **Analisis**: son archivos que el agente debe procesar con Python, como Excel o CSV. Esto usa Code Interpreter.

La razon de separar conocimiento y analisis es que Azure Foundry usa herramientas diferentes para cada caso. Un PDF de politicas debe ir a File Search. Un Excel que se quiere calcular o graficar debe ir a Code Interpreter.

El backend tambien guarda un registro local para saber:

- que agente pertenece a cada usuario o pantalla;
- que version del agente debe usarse;
- que vector store contiene sus documentos;
- que archivos estan disponibles para analisis con Python.

## Componentes Internos

### Azure Functions App

```python
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
```

Define todos los endpoints HTTP:

```text
GET  /agents
POST /agents
GET  /chat-stream
POST /upload-and-ask
POST /agents/{agent_id}/files
POST /agents/{agent_id}/code-files
GET  /generated-files/{container_id}/{file_id}
```

El backend no usa `routePrefix` porque `host.json` tiene:

```json
{
  "extensions": {
    "http": {
      "routePrefix": ""
    }
  }
}
```

Por eso las rutas son directas, no `/api/agents`.

Para personas no tecnicas: cada endpoint es una puerta de entrada. Por ejemplo, `/agents` sirve para listar o crear agentes, mientras que `/chat-stream` sirve para conversar con uno.

### Credenciales

La funcion `_get_credential()` crea una credencial por request:

```python
DefaultAzureCredential()
```

En local puedes autenticarte con:

```bash
az login
```

Cada request crea su credencial y la cierra al final para evitar fugas de recursos async.

Para personas no tecnicas: las credenciales son la forma en la que el backend demuestra a Azure que tiene permiso para usar el proyecto Foundry. En local `DefaultAzureCredential` puede usar tu sesion de `az login`; en nube usa identidad administrada si esta habilitada.

### Cliente Foundry

El backend crea un cliente por request:

```python
project_client = AIProjectClient(
    endpoint=FOUNDRY_ENDPOINT,
    credential=credential
)
```

Para operaciones OpenAI dentro del proyecto usa:

```python
openai_client = project_client.get_openai_client()
```

Para personas no tecnicas: `AIProjectClient` administra recursos del proyecto, como agentes. `openai_client` ejecuta operaciones del modelo, sube archivos, crea conversaciones y recibe respuestas.

### CORS

La funcion `_cors_headers()` agrega headers para permitir llamadas desde el frontend:

```text
Access-Control-Allow-Origin
Access-Control-Allow-Methods
Access-Control-Allow-Headers
```

Por defecto:

```text
ALLOWED_ORIGIN=http://localhost:5173
```

Para personas no tecnicas: CORS es la regla que permite que el frontend, que corre en otra direccion, pueda llamar al backend. En despliegue se debe cambiar para permitir el dominio real del frontend.

### SSE

La funcion `_sse()` convierte texto en Server-Sent Events:

```python
data: texto

```

Preserva saltos de linea para que Markdown y tablas no se rompan.

Eventos usados:

```text
message      Texto normal del agente
metadata     Nombre/version del agente usado
artifact     Archivo generado por Code Interpreter
agent-error  Error del backend o Foundry
```

Para personas no tecnicas: SSE permite que la respuesta aparezca poco a poco en pantalla, como en ChatGPT, sin esperar a que termine todo el texto.

## Configuracion

Valores principales en `function_app.py`:

```python
ENTORNO = os.getenv("ENTORNO", "local")
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "https://...")
```

Variables opcionales:

```text
ENTORNO=local
FOUNDRY_ENDPOINT=https://...services.ai.azure.com/api/projects/...
ALLOWED_ORIGIN=http://localhost:5173
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5-mini
MODEL_DEPLOYMENT_NAME=gpt-5-mini
AGENTS_DB_PATH=agents_db.json
AZURE_AI_AGENT_NAME=...
AZURE_AI_AGENT_VERSION=...
AZURE_AI_AUTO_RESOLVE_AGENT_VERSION=true|false
```

### Que Cambiar entre Local y Nube

Para trabajar localmente normalmente basta con:

```bash
az login
func start
```

Y configurar:

```text
ALLOWED_ORIGIN=http://localhost:5173
```

Para desplegar en Azure:

```text
ENTORNO=nube
```

Y configurar:

```text
ALLOWED_ORIGIN=https://<dominio-del-frontend>
AZURE_AI_MODEL_DEPLOYMENT_NAME=<nombre-del-deployment-del-modelo>
AGENTS_DB_PATH=<ruta-o-reemplazo-por-base-de-datos>
```

Tambien se debe revisar `FOUNDRY_ENDPOINT`, que debe apuntar al proyecto correcto de Azure AI Foundry.

## Despliegue

Esta seccion resume que hay que cambiar antes de publicar el backend.

### 1. Configurar Autenticacion

El backend usa:

```python
DefaultAzureCredential()
```

Para produccion, habilitar Managed Identity en la Azure Function y darle permisos sobre el proyecto Foundry. En local, `az login` es suficiente si tu usuario tiene acceso al proyecto.

### 2. Cambiar CORS

En local el frontend usa:

```text
http://localhost:5173
```

En produccion debe ser el dominio real:

```text
ALLOWED_ORIGIN=https://mi-frontend.com
```

Si esto queda mal, el navegador mostrara errores como `Failed to fetch` o bloqueos CORS.

### 3. Revisar el Endpoint de Foundry

El valor:

```python
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "https://...")
```

debe apuntar al proyecto correcto. En produccion, configura `FOUNDRY_ENDPOINT` como variable de entorno para evitar depender del fallback local.

### 4. Configurar Modelo

El backend necesita saber que deployment de modelo usar al crear agentes.

Configurar:

```text
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5-mini
```

El nombre debe coincidir con el deployment configurado en Azure AI Foundry.

### 5. Reemplazar `agents_db.json`

`agents_db.json` sirve para desarrollo, pero no es ideal para produccion porque:

- puede perderse al redesplegar;
- no maneja concurrencia avanzada;
- no es compartido si hay varias instancias de Functions;
- no tiene controles de auditoria.

Opciones recomendadas:

- Azure Cosmos DB
- Azure SQL
- Azure Table Storage
- Blob Storage con control de concurrencia

El cambio se debe hacer reemplazando las funciones:

```python
_load_db()
_save_db()
_save_agent_record()
_get_agent_record()
_list_agent_records()
```

### 6. Seguridad

Actualmente:

```python
http_auth_level=func.AuthLevel.ANONYMOUS
```

Esto permite llamadas sin autenticacion. Para produccion se debe evaluar:

- Azure Function auth;
- Easy Auth con Entra ID;
- API Management;
- tokens propios de aplicacion;
- validacion de usuario por agente.

### 7. Archivos y Tamanos

El backend lee archivos en memoria antes de subirlos a Foundry. Para produccion se debe:

- limitar tamano maximo;
- validar extensiones;
- validar tipo MIME;
- registrar errores de subida;
- considerar subida previa a Blob Storage si los archivos son grandes.

### 8. Logs y Observabilidad

Configurar Application Insights para:

- errores de creacion de agentes;
- errores de subida de archivos;
- latencia de streaming;
- uso de Code Interpreter;
- fallos de descarga.

### 9. Costos

Code Interpreter puede generar costos adicionales por sesiones. Revisar:

- cantidad de usuarios concurrentes;
- cantidad de conversaciones activas;
- frecuencia de analisis con Python;
- cantidad de archivos procesados.

## Persistencia Local

El backend guarda metadatos en:

```text
backend/agents_db.json
```

La ruta puede cambiarse con:

```text
AGENTS_DB_PATH
```

Funciones relacionadas:

```python
_load_db()
_save_db()
_save_agent_record()
_get_agent_record()
_list_agent_records()
_append_file_record()
```

La escritura usa archivo temporal y `os.replace()` para reducir riesgo de corrupcion:

```python
agents_db.json.<uuid>.tmp
```

### Estructura de un agente

```json
{
  "agent_id": "uuid-interno",
  "display_name": "Agente contratos",
  "agent_name": "Agente-contratos-a1b2c3",
  "agent_version": "1",
  "vector_store_id": "vs_...",
  "vector_store_ids": ["vs_..."],
  "model": "gpt-5-mini",
  "instructions": "...",
  "description": "...",
  "tools": {
    "code_interpreter": true,
    "file_search": true
  },
  "files": [
    {
      "file_id": "file-...",
      "vector_store_id": "vs_...",
      "filename": "politica.pdf",
      "content_type": "application/pdf",
      "bytes": 12345,
      "status": "completed",
      "created_at": "..."
    }
  ],
  "code_files": [
    {
      "file_id": "file-...",
      "filename": "datos.xlsx",
      "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "bytes": 12345,
      "purpose": "assistants",
      "created_at": "..."
    }
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

Para produccion, reemplazar `agents_db.json` por Cosmos DB, SQL, Table Storage u otro almacenamiento persistente.

Para personas no tecnicas: Foundry tiene sus propios identificadores, pero el frontend necesita un identificador mas simple para trabajar. `agents_db.json` es la libreta donde el backend anota esa relacion.

## Lectura de Archivos

Los endpoints de subida reciben:

```text
multipart/form-data
```

Funcion principal:

```python
_read_uploaded_files(req)
```

Primero intenta:

```python
await req.form()
```

Si el runtime no tiene `python-multipart`, usa fallback con librerias estandar:

```python
_read_multipart_without_dependency(req)
```

Ese fallback:

1. Lee `Content-Type`.
2. Lee bytes del body con `_request_body_bytes()`.
3. Construye un mensaje MIME.
4. Usa `BytesParser`.
5. Extrae partes con `filename`.
6. Devuelve una lista:

```python
[
    {
        "filename": "archivo.xlsx",
        "content_type": "...",
        "content": b"..."
    }
]
```

Los archivos no se guardan fisicamente en disco. El backend lee bytes en memoria y los sube a Foundry.

Para personas no tecnicas: cuando un usuario sube un archivo, el backend lo toma temporalmente, lo envia a Azure Foundry y guarda solo los identificadores. No queda una copia permanente del archivo en la carpeta del backend.

## File Search vs Code Interpreter

### File Search

Se usa para conocimiento documental/RAG.

En palabras simples: File Search es como una biblioteca del agente. El agente consulta documentos para responder con base en contenido cargado.

Los archivos van al vector store:

```python
openai_client.vector_stores.files.upload_and_poll(
    vector_store_id=vector_store_id,
    file=(filename, file_content, content_type)
)
```

El vector store queda asociado al agente mediante:

```python
FileSearchTool(vector_store_ids=[vector_store_id])
```

No usar para Excel. `.xlsx` no esta soportado por retrieval/File Search.

### Code Interpreter

Se usa para analisis con Python:

```text
Excel, CSV, graficos, calculos, transformaciones, archivos generados
```

Los archivos se suben a Foundry con:

```python
openai_client.files.create(
    purpose="assistants",
    file=file_content
)
```

Luego el backend crea una nueva version del agente con:

```python
CodeInterpreterTool(
    container=AutoCodeInterpreterToolParam(file_ids=[...])
)
```

Esto es importante: los archivos de Code Interpreter no se agregan al vector store. Se agregan al contenedor automatico de Code Interpreter.

En palabras simples: Code Interpreter es como darle al agente una computadora temporal con Python. Sirve para abrir Excel, calcular, transformar datos y generar archivos como graficos o scripts.

## Endpoints

### `GET /agents`

Lista agentes de Foundry y los cruza con `agents_db.json`.

Este endpoint alimenta la lista de agentes que aparece en el frontend.

Proceso:

1. Crea credencial.
2. Crea `AIProjectClient`.
3. Lista agentes con:

```python
async for agent in project_client.agents.list():
```

4. Busca metadatos locales por `(agent_name, agent_version)`.
5. Devuelve agentes enriquecidos.

Respuesta:

```json
{
  "agents": [
    {
      "agent_id": "uuid",
      "display_name": "Agente",
      "name": "Agente-a1b2c3",
      "version": "2",
      "vector_store_id": "vs_...",
      "files": [],
      "code_files": []
    }
  ]
}
```

### `POST /agents`

Crea un agente nuevo.

Este endpoint se usa cuando una persona presiona "Crear agente" en el frontend.

Body:

```json
{
  "name": "Agente de ejemplo",
  "instructions": "Eres un asistente util...",
  "model": "gpt-5-mini"
}
```

Proceso:

1. Lee JSON.
2. Resuelve modelo desde body o variables de entorno.
3. Genera `agent_id` local con UUID.
4. Genera `agent_name` unico.
5. Crea vector store vacio si File Search esta activo:

```python
openai_client.vector_stores.create(name=f"{agent_name}-files")
```

6. Construye herramientas con `_build_agent_tools()`:

```python
CodeInterpreterTool(container=AutoCodeInterpreterToolParam())
FileSearchTool(vector_store_ids=[...])
```

7. Crea version del agente:

```python
project_client.agents.create_version(
    agent_name=agent_name,
    definition=PromptAgentDefinition(...),
    description=description
)
```

8. Guarda registro en `agents_db.json`.

Respuesta:

```json
{
  "agent": {
    "agent_id": "uuid",
    "agent_name": "...",
    "agent_version": "1",
    "vector_store_id": "vs_..."
  },
  "tools": {
    "code_interpreter": true,
    "file_search": true
  }
}
```

### `GET /chat-stream`

Ejecuta chat asincrono por SSE.

Este endpoint se usa cuando una persona escribe una pregunta y espera respuesta del agente.

Parametros:

```text
message=...
agent_id=...
```

Tambien acepta:

```text
agent_name=...
agent_version=...
```

Proceso:

1. Si llega `agent_id`, busca el agente en `agents_db.json`.
2. Obtiene `agent_name`, `agent_version` y `code_files`.
3. Llama `_stream_agent_response()`.
4. Crea `openai_client`.
5. Crea conversation si el SDK lo soporta:

```python
openai_client.conversations.create()
```

6. Llama:

```python
openai_client.responses.create(
    input=[{"role": "user", "content": message}],
    stream=True,
    extra_body={
        "agent_reference": {
            "name": agent_name,
            "version": agent_version,
            "type": "agent_reference"
        }
    }
)
```

7. Por cada evento:

```text
response.output_text.delta       -> envia texto al frontend
response.output_text.annotation  -> intenta detectar archivos generados
response.completed               -> revisa anotaciones finales
response.failed                  -> envia agent-error
```

### `POST /agents/{agent_id}/files`

Sube documentos a File Search.

Este endpoint corresponde al boton "Subir a File Search" del frontend.

Formato:

```text
multipart/form-data
files=<archivo>
```

Proceso:

1. Busca `agent_id` en `agents_db.json`.
2. Obtiene `vector_store_id`.
3. Lee archivos multipart.
4. Sube cada archivo al vector store.
5. Guarda cada `file_id` en `files`.

Usar para:

```text
.pdf, .docx, .txt, .md, .json, .pptx, codigo, etc.
```

No usar para:

```text
.xlsx
```

### `POST /agents/{agent_id}/code-files`

Sube archivos para Code Interpreter.

Este endpoint corresponde al boton "Subir a Code Interpreter" del frontend.

Formato:

```text
multipart/form-data
files=<archivo>
```

Proceso:

1. Busca `agent_id` en `agents_db.json`.
2. Lee archivos multipart.
3. Sube cada archivo con `purpose="assistants"`.
4. Agrega el registro a `code_files`.
5. Construye lista de `file_id`.
6. Crea nueva version del mismo agente con:

```python
CodeInterpreterTool(
    container=AutoCodeInterpreterToolParam(file_ids=code_file_ids)
)
```

7. Mantiene File Search si el agente tiene `vector_store_ids`.
8. Actualiza `agent_version` en `agents_db.json`.

Usar para:

```text
.xlsx, .csv, .json, .txt, .zip, imagenes, archivos de datos
```

### `GET /generated-files/{container_id}/{file_id}`

Descarga archivos generados por Code Interpreter.

Este endpoint no se llama manualmente normalmente. El frontend lo usa cuando el backend detecta que Code Interpreter genero un archivo descargable.

Ejemplo:

```http
GET /generated-files/container_x/file-y?filename=chart.png
```

Proceso:

1. Recibe `container_id` y `file_id`.
2. Llama:

```python
openai_client.containers.files.content.retrieve(
    container_id=container_id,
    file_id=file_id
)
```

3. Lee bytes.
4. Devuelve:

```text
Content-Disposition: attachment
```

## Comunicacion Frontend-Backend

### Crear agente

```text
Frontend -> POST /agents -> Backend -> Foundry create_version
```

El frontend guarda el agente seleccionado con:

```text
agent_id
name
version
vector_store_id
files
code_files
```

### Subir conocimiento

```text
Frontend -> POST /agents/{agent_id}/files
Backend -> vector_stores.files.upload_and_poll
Backend -> agents_db.json files[]
Frontend <- agent actualizado
```

### Subir archivo de analisis

```text
Frontend -> POST /agents/{agent_id}/code-files
Backend -> openai.files.create(purpose="assistants")
Backend -> agents.create_version(...CodeInterpreterTool(file_ids=[...]))
Backend -> agents_db.json code_files[] + agent_version nueva
Frontend <- agent actualizado
```

### Chatear

```text
Frontend -> EventSource /chat-stream?agent_id=...
Backend -> agents_db.json
Backend -> responses.create(stream=True, agent_reference=name/version)
Backend -> SSE deltas
Frontend <- texto, metadata, artifacts, errores
```

### `POST /upload-and-ask`

Subida unificada tipo ChatGPT: el frontend envia la pregunta y cero, uno o varios archivos en un solo `multipart/form-data`. El backend decide automaticamente el destino:

- Imagenes (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`): no se suben a tools; se convierten a data URI base64 y se envian como `input_image`.
- Datos tabulares (`.csv`, `.xlsx`, `.json`, `.tsv`): se suben con Files API (`purpose="assistants"`) y se pasan a Code Interpreter con `structured_inputs`.
- Documentos (`.pdf`, `.docx`, `.txt`, `.md`, `.pptx`): se suben al vector store asociado al `thread_id` local para File Search.
- Otros tipos devuelven `415`.

Campos:

```text
message=<pregunta>       requerido
file=<archivo>           opcional, repetible
thread_id=<id>           opcional; si falta, el backend crea conversacion y lo devuelve
agent_id=<id local>      recomendado para que el backend pueda refrescar el agente con placeholders
agent_name=<nombre>      opcional si no usas agent_id
agent_version=<version>  opcional si no usas agent_id
```

Respuesta del `POST`: JSON `202` con `invocation_id`, `thread_id`, contexto de archivos y `stream_url`.

```json
{
  "invocation_id": "uuid",
  "thread_id": "conv_...",
  "stream_url": "http://localhost:7071/upload-and-ask-stream/uuid",
  "archivos_subidos": [],
  "archivos_thread": []
}
```

Luego el frontend abre `GET /upload-and-ask-stream/{invocation_id}` con `EventSource`.
Respuesta del stream: `text/event-stream`. Eventos principales:

```text
event: metadata
data: {"thread_id":"conv_...","agent":"...","version":"...","archivos_subidos":[],"archivos_thread":[]}

data: delta de texto del agente

event: uploaded-files
data: {"archivos_subidos":[...],"archivos_thread":[...]}

event: artifact
data: {"filename":"grafica.png","download_url":"..."}

event: done
data: {"thread_id":"conv_...","run_id":"resp_...","archivos_subidos":[],"archivos_thread":[]}

data: [FIN]
```

`archivos_subidos` contiene solo los archivos enviados en esa llamada. `archivos_thread` contiene todos los recursos acumulados en la sesion. Para imagenes, `file_id` es `null` porque no se suben a ninguna herramienta; el frontend puede usar `upload_id` como identificador local.

La ruta guarda en `agents_db.json` una seccion `threads` con el vector store, archivos de Code Interpreter e imagenes asociadas al thread para que las siguientes preguntas puedan reutilizarlos.

### Descargar archivo generado

```text
Backend detecta annotation container_file_citation
Backend emite event: artifact
Frontend muestra download_url
Usuario abre download_url
Backend -> containers.files.content.retrieve
Frontend descarga archivo
```

## Flujo Recomendado

1. Crear agente con `POST /agents`.
2. Subir documentos de conocimiento a `/agents/{agent_id}/files`.
3. Subir Excel/CSV/datos a `/agents/{agent_id}/code-files`.
4. Chatear con `/chat-stream?agent_id=...&message=...`.
5. Si Code Interpreter genera archivos, usar los links del evento `artifact`.

## Limitaciones Actuales

- `agents_db.json` no es una base de datos de produccion.
- Los archivos se leen en memoria antes de subirlos.
- El limite local para archivos Code Interpreter asociados al agente es `CODE_INTERPRETER_FILE_INPUT_COUNT = 10`.
- Subir archivos a Code Interpreter crea una nueva version del agente.
- `sandbox:/mnt/data/...` no es una URL descargable. Solo se puede descargar si Foundry devuelve `container_id` y `file_id`.
- Si no aparece evento `artifact`, Foundry no genero anotacion descargable.

## Errores Comunes

### `Failed to fetch`

Normalmente significa:

- backend apagado;
- `BASE_URL` del frontend incorrecto;
- proceso viejo de Azure Functions;
- error de import al iniciar Functions;
- CORS/preflight sin respuesta.

### `.xlsx` falla al subir

Si se sube por File Search, falla porque retrieval no soporta `.xlsx`.

Solucion:

```text
Subir .xlsx por /agents/{agent_id}/code-files
```

### Mensajes antiguos siguen apareciendo

Si el frontend muestra un error que ya no existe en el codigo, el backend que responde es una version vieja. Reiniciar Functions o redesplegar.

### No hay link descargable real

El modelo puede escribir texto como:

```text
sandbox:/mnt/data/archivo.py
```

Eso no basta. Para descargar se requiere anotacion Foundry:

```text
container_file_citation
```

con:

```text
container_id
file_id
filename
```

## Notas de Produccion

- Reemplazar `agents_db.json` por base persistente.
- Controlar permisos y autenticacion; actualmente `AuthLevel.ANONYMOUS`.
- Validar tamanos maximos de archivo.
- Registrar logs en Application Insights.
- Manejar borrado de agentes, versiones, vector stores y archivos.
- Revisar costos de Code Interpreter, ya que cada sesion puede generar cargos adicionales.

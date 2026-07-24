# Frontend

Frontend Vue 3 para administrar agentes Foundry, subir archivos y chatear por streaming SSE con el backend.

En palabras simples: esta carpeta contiene la pantalla que usa la persona final. Desde aqui se crean agentes, se selecciona con que agente hablar, se suben documentos o archivos de analisis, se envia la pregunta y se muestran las respuestas del backend en formato tipo chat.

El frontend no crea agentes directamente en Azure. Siempre llama al backend. Esto es importante porque las credenciales, los permisos y la comunicacion con Foundry deben quedarse en el servidor.

## Stack

- Vue 3
- TypeScript
- Vite
- CSS global en `src/style.css`

Cada elemento tiene un papel:

- Vue 3 construye la interfaz visible.
- TypeScript ayuda a detectar errores de datos durante desarrollo.
- Vite levanta el proyecto local y genera los archivos finales para publicar.
- `src/style.css` contiene la apariencia del chat, paneles, tablas Markdown, botones y mensajes.

Archivo principal de la experiencia:

```text
src/components/HelloWorld.vue
```

## Distribucion de Archivos

La carpeta `frontend` esta organizada asi:

```text
frontend/
  index.html
  package.json
  vite.config.ts
  tsconfig.json
  tsconfig.app.json
  tsconfig.node.json
  pnpm-lock.yaml
  public/
    favicon.svg
    icons.svg
  src/
    main.ts
    App.vue
    style.css
    components/
      HelloWorld.vue
    assets/
      hero.png
      vite.svg
      vue.svg
```

### Archivos de Entrada

`index.html` es el archivo HTML base que carga la aplicacion en el navegador. Contiene el contenedor donde Vue monta toda la experiencia.

`src/main.ts` es el punto de arranque tecnico. Importa Vue, importa los estilos globales y monta `App.vue` en el HTML.

```ts
createApp(App).mount('#app')
```

Para personas no tecnicas: `main.ts` es como el interruptor que enciende la pantalla.

### Componente Raiz

`src/App.vue` es el componente principal de Vue. Actualmente su unica responsabilidad es mostrar:

```vue
<HelloWorld />
```

Para personas no tecnicas: `App.vue` es la primera pieza visual de la aplicacion. En este proyecto delega casi todo a `HelloWorld.vue`.

### Componente Principal

`src/components/HelloWorld.vue` contiene la experiencia completa:

- formulario para crear agentes;
- lista de agentes disponibles;
- subida de archivos para File Search;
- subida de archivos para Code Interpreter;
- chat por streaming;
- renderizado Markdown;
- links de descarga de archivos generados.

Para personas no tecnicas: este archivo es donde vive la pantalla principal. Si se quiere cambiar el comportamiento del chat, la carga de archivos o la forma de mostrar respuestas, normalmente se revisa aqui.

### Estilos

`src/style.css` define la apariencia visual:

- layout general;
- paneles;
- botones;
- estados de carga;
- tablas Markdown;
- bloques de codigo;
- links de descarga;
- mensajes de error y exito.

Para personas no tecnicas: este archivo no cambia la logica, cambia como se ve la aplicacion.

### Archivos Publicos y Assets

`public/` contiene archivos servidos directamente por el navegador, como iconos.

`src/assets/` contiene imagenes o recursos que pueden importarse desde componentes Vue.

Actualmente la experiencia principal no depende de muchas imagenes; la mayor parte de la interfaz esta construida con HTML, Vue y CSS.

## Como Interactuan los Archivos

El flujo de ejecucion es:

```text
index.html
  -> src/main.ts
    -> src/App.vue
      -> src/components/HelloWorld.vue
        -> backend Azure Functions
```

Explicado paso a paso:

1. El navegador abre `index.html`.
2. `index.html` carga la aplicacion generada por Vite.
3. `main.ts` inicia Vue.
4. `App.vue` renderiza `HelloWorld.vue`.
5. `HelloWorld.vue` llama al backend usando `fetch` y `EventSource`.
6. El backend responde con agentes, estados, texto por streaming y archivos generados.
7. `HelloWorld.vue` actualiza la pantalla con la informacion recibida.

Para personas no tecnicas: el frontend no hace inteligencia artificial por si mismo. Su trabajo es mostrar botones, formularios y respuestas. La parte de IA ocurre en el backend y en Azure Foundry.

## Estructura de `HelloWorld.vue`

`HelloWorld.vue` usa el formato Single File Component de Vue:

```vue
<script setup lang="ts">
  Logica TypeScript
</script>

<template>
  Pantalla HTML/Vue
</template>
```

En este proyecto no hay estilos dentro de `HelloWorld.vue`; los estilos estan en `src/style.css`.

### 1. Interfaces TypeScript

Al inicio se definen estructuras de datos:

```ts
interface AgentInfo
interface AgentFile
interface GeneratedArtifact
interface MarkdownBlock
```

Estas interfaces explican que forma tienen los datos que llegan del backend.

Para personas no tecnicas: son como formularios internos que dicen que campos debe tener un agente, un archivo o una respuesta.

### 2. Estados Reactivos

Luego se crean variables con `ref`:

```ts
question
answer
status
agents
selectedAgent
selectedFiles
selectedCodeFiles
generatedArtifacts
```

Cuando estos valores cambian, Vue actualiza la pantalla automaticamente.

Ejemplos:

- si cambia `answer`, se actualiza la respuesta del chat;
- si cambia `agents`, se actualiza la lista de agentes;
- si cambia `selectedAgent`, se actualiza el agente activo;
- si cambia `generatedArtifacts`, aparecen links de descarga.

### 3. Propiedades Calculadas

Las propiedades `computed` controlan si una accion esta disponible:

```ts
canAsk
canCreateAgent
canUploadFiles
canUploadCodeFiles
renderedAnswer
```

Ejemplo: `canAsk` solo permite preguntar si hay texto, hay agente seleccionado y no hay una respuesta en curso.

Para personas no tecnicas: estas reglas evitan que el usuario presione botones en momentos incorrectos.

### 4. Configuracion del Backend

La constante:

```ts
const BASE_URL = 'http://localhost:7071'
```

define donde esta el backend.

Todo lo que implique agentes, archivos o chat depende de esta URL.

### 5. Normalizacion de Agentes

La funcion:

```ts
normalizeAgent()
```

convierte la respuesta del backend a una forma estable para el frontend.

Esto ayuda porque el backend puede devolver campos como `agent_name`, `name`, `agent_version` o `version`, y el frontend necesita usarlos de forma consistente.

### 6. Markdown y Formato de Respuestas

El bloque de funciones:

```ts
escapeHtml()
renderFileCitations()
renderInlineMarkdown()
normalizeCodeFences()
restoreCollapsedTableRows()
normalizeMarkdown()
parseMarkdown()
```

prepara la respuesta del agente para que se vea correctamente.

Sirve para:

- mostrar tablas;
- mostrar codigo;
- respetar saltos de linea;
- mostrar fuentes de Foundry;
- evitar que rutas `sandbox:/mnt/data/...` parezcan links descargables reales;
- convertir links reales en enlaces clicables.

Para personas no tecnicas: esta parte transforma la respuesta cruda del agente en una respuesta legible, parecida a una conversacion moderna.

### 7. Carga y Seleccion de Agentes

Funciones principales:

```ts
fetchAgents()
selectAgent()
updateSelectedAgent()
agentKey()
```

`fetchAgents()` llama:

```http
GET /agents
```

`selectAgent()` guarda el agente activo para las siguientes acciones.

Para personas no tecnicas: esta seccion controla que agente esta disponible y con cual se va a conversar.

### 8. Creacion de Agentes

Funcion:

```ts
createAgent()
```

Llama:

```http
POST /agents
```

Envia:

- nombre;
- instrucciones;
- modelo opcional.

Cuando el backend responde, el nuevo agente se agrega a la lista y queda seleccionado.

### 9. Subida de Archivos

Funciones para seleccionar archivos:

```ts
onFilesChange()
onCodeFilesChange()
```

Funciones para subirlos:

```ts
uploadFiles()
uploadCodeFiles()
```

`uploadFiles()` llama:

```http
POST /agents/{agent_id}/files
```

`uploadCodeFiles()` llama:

```http
POST /agents/{agent_id}/code-files
```

Para personas no tecnicas: hay dos botones porque Azure usa caminos distintos. File Search es para consultar documentos. Code Interpreter es para analizar datos con Python.

### 10. Chat por Streaming

Funcion:

```ts
askAgent()
```

Crea una conexion:

```ts
new EventSource(`${BASE_URL}/chat-stream?...`)
```

Escucha cuatro tipos de eventos:

- `message`: texto de la respuesta;
- `metadata`: agente y version usados;
- `artifact`: archivo generado para descargar;
- `agent-error`: error del backend o Foundry.

Funcion relacionada:

```ts
closeStream()
```

Sirve para cerrar la conexion cuando termina, falla o el usuario detiene la respuesta.

### 11. Ciclo de Vida

Al montar la pantalla:

```ts
onMounted(() => {
  fetchAgents()
})
```

Esto carga automaticamente la lista de agentes.

Antes de destruir la pantalla:

```ts
onBeforeUnmount(() => {
  source?.close()
})
```

Esto cierra la conexion SSE si estaba abierta.

Para personas no tecnicas: al entrar a la pantalla se cargan los agentes, y al salir se limpian conexiones abiertas.

## Secciones Visuales del Template

El `<template>` de `HelloWorld.vue` esta dividido en bloques visibles.

### 1. Barra Superior

Muestra:

- titulo de la aplicacion;
- estado actual: listo, pensando, finalizado o error.

### 2. Panel Crear Agente

Permite ingresar:

- nombre;
- modelo;
- instrucciones.

Boton principal:

```text
Crear agente
```

### 3. Panel Agentes

Muestra la lista de agentes disponibles y permite seleccionar uno.

Cada agente muestra:

- nombre visible;
- version.

### 4. Panel Conocimiento

Permite subir documentos a File Search y ver los documentos ya asociados.

### 5. Panel Analisis

Permite subir archivos a Code Interpreter y ver los archivos disponibles para analisis.

### 6. Compositor de Preguntas

Incluye:

- caja de texto para la pregunta;
- boton `Preguntar`;
- boton `Detener`.

### 7. Panel de Respuesta

Muestra:

- respuesta renderizada en Markdown;
- errores;
- nombre/version del agente;
- links de archivos generados.

## Configuracion

El backend se configura en `HelloWorld.vue`:

```ts
const BASE_URL = 'http://localhost:7071'
```

Para personas no tecnicas: `BASE_URL` es la direccion a la que la pantalla llama para pedir informacion. En local apunta a tu computador. En produccion debe apuntar a la URL real del backend publicado.

Para nube, cambiarlo por la URL de Azure Functions.

Ejemplo:

```ts
const BASE_URL = 'https://<function-app>.azurewebsites.net'
```

El backend actual no usa `routePrefix`, por eso las rutas son:

```text
/agents
/chat-stream
/agents/{agent_id}/files
/agents/{agent_id}/code-files
```

Si el backend cambia su `routePrefix` o se publica detras de API Management, tambien se debe actualizar `BASE_URL` o las rutas usadas en `HelloWorld.vue`.

## Que Cambiar para Despliegue

Antes de publicar el frontend se debe revisar lo siguiente.

### 1. URL del Backend

Cambiar:

```ts
const BASE_URL = 'http://localhost:7071'
```

por:

```ts
const BASE_URL = 'https://<backend-publicado>'
```

Ejemplo con Azure Functions:

```ts
const BASE_URL = 'https://mi-function-app.azurewebsites.net'
```

Si no se cambia esto, el frontend publicado intentara llamar a `localhost`, que en el navegador del usuario significa "la maquina del usuario", no tu backend real.

### 2. CORS en el Backend

El dominio donde se publique el frontend debe estar permitido en el backend.

Ejemplo:

```text
ALLOWED_ORIGIN=https://mi-frontend.com
```

Si esto no coincide, el navegador puede bloquear las llamadas y mostrar errores como:

```text
Failed to fetch
```

### 3. Construccion del Frontend

Para generar los archivos finales:

```bash
npm run build
```

Esto crea una carpeta:

```text
dist
```

Esa carpeta es la que normalmente se publica en Azure Static Web Apps, App Service, Storage Static Website u otro hosting web.

### 4. Variables por Ambiente

Actualmente `BASE_URL` esta escrito directamente en `HelloWorld.vue`. Para produccion seria mejor moverlo a variables de entorno de Vite.

Ejemplo recomendado:

```ts
const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:7071'
```

Y configurar:

```text
VITE_BACKEND_URL=https://<backend-publicado>
```

Esto permite tener valores diferentes para local, pruebas y produccion sin modificar codigo cada vez.

### 5. Seguridad Visible para el Usuario

El frontend no debe guardar secretos, llaves de Azure ni tokens privados dentro del codigo. Todo eso debe vivir en el backend.

El frontend solamente deberia manejar:

- URL publica del backend;
- datos de pantalla;
- archivos seleccionados por el usuario;
- identificador del agente seleccionado.

### 6. Validaciones de Archivos

El frontend ayuda a separar:

- documentos para File Search;
- archivos de datos para Code Interpreter.

Pero la validacion fuerte debe estar en el backend. El frontend solo guia al usuario para evitar errores comunes.

## Pantallas y funciones

### Crear agente

Formulario superior:

- `Nombre`
- `Modelo`
- `Instrucciones`

Llama:

```http
POST /agents
```

El backend devuelve:

```json
{
  "agent": {
    "agent_id": "uuid",
    "agent_name": "...",
    "agent_version": "...",
    "vector_store_id": "vs_..."
  }
}
```

El frontend normaliza el agente para usar:

```ts
agent.agent_id
agent.name
agent.version
agent.vector_store_id
agent.files
agent.code_files
```

Para personas no tecnicas: esta pantalla crea un nuevo asistente preparado con dos capacidades. File Search le permite consultar documentos. Code Interpreter le permite analizar archivos con Python.

### Selector de agentes

Llama:

```http
GET /agents
```

El agente seleccionado se guarda en:

```ts
selectedAgent
```

Para chatear se prefiere enviar:

```text
agent_id
```

El backend resuelve `agent_name` y `agent_version`.

Para personas no tecnicas: el usuario ve una lista sencilla de agentes. Internamente, el backend traduce esa seleccion al nombre y version que Foundry necesita.

## Subida de archivos

El frontend separa dos flujos porque Foundry los trata distinto.

### Conocimiento

Panel:

```text
Conocimiento
Subir a File Search
```

Llama:

```http
POST /agents/{agent_id}/files
```

Uso:

```text
Documentos que el agente debe consultar como conocimiento.
```

Tipos sugeridos:

```text
.pdf, .docx, .txt, .md, .json, .pptx, codigo
```

No usar para Excel.

Para personas no tecnicas: esta opcion es para documentos que el agente debe leer como fuente de informacion. Por ejemplo: politicas, manuales, matrices, normas, contratos o presentaciones.

### Analisis

Panel:

```text
Analisis
Subir a Code Interpreter
```

Llama:

```http
POST /agents/{agent_id}/code-files
```

Uso:

```text
Archivos que el agente debe procesar con Python.
```

Tipos sugeridos:

```text
.xlsx, .csv, .json, .txt, .zip, imagenes, .py
```

Cuando se suben archivos de analisis, el backend crea una nueva version del agente. El frontend recibe el agente actualizado y refresca `selectedAgent`.

Para personas no tecnicas: esta opcion es para archivos que requieren calculos o procesamiento. Por ejemplo: Excel, CSV, datos tabulares, scripts o archivos que el agente debe transformar.

## Chat SSE

El chat usa `EventSource`:

```ts
source = new EventSource(`${BASE_URL}/chat-stream?${params.toString()}`)
```

Parametros:

```text
message=<pregunta>
agent_id=<agent_id>
```

Eventos escuchados:

```ts
source.onmessage
source.addEventListener('metadata', ...)
source.addEventListener('artifact', ...)
source.addEventListener('agent-error', ...)
```

Para personas no tecnicas: SSE permite que el texto aparezca de forma progresiva, parecido a ChatGPT. No se espera toda la respuesta completa para mostrarla.

### `message`

Recibe deltas de texto y los acumula en:

```ts
answer
```

### `metadata`

Actualiza la etiqueta de agente:

```ts
agentLabel
```

### `artifact`

Recibe archivos generados por Code Interpreter:

```ts
interface GeneratedArtifact {
  type: string
  container_id: string
  file_id: string
  filename: string
  download_url: string
}
```

Los links se muestran en la seccion:

```text
Archivos generados
```

Para personas no tecnicas: si Code Interpreter crea un archivo real, el backend envia un evento especial y el frontend muestra un enlace de descarga. Si el modelo solo escribe una ruta `sandbox:/mnt/data/...` como texto, eso no siempre significa que exista una descarga valida.

## Markdown

El frontend incluye un parser Markdown ligero sin dependencias externas.

Soporta:

- encabezados;
- listas;
- tablas;
- bloques de codigo con triple o cuadruple backtick;
- inline code;
- enlaces HTTP;
- rutas `sandbox:/mnt/data/...` como referencia visual;
- citas internas Foundry tipo `filecite` convertidas a texto visible.

Nota: `sandbox:/mnt/data/...` no es descargable desde el navegador. Solo `download_url` del evento `artifact` es descargable.

Para personas no tecnicas: Markdown es el formato que permite ver respuestas con tablas, titulos, listas, codigo y enlaces. Sin esta conversion, el usuario veria texto plano dificil de leer.

## Estados importantes

```ts
agents
selectedAgent
question
answer
generatedArtifacts
selectedFiles
selectedCodeFiles
isStreaming
isUploadingFiles
isUploadingCodeFiles
```

Estos estados controlan lo que se ve en pantalla. Por ejemplo, `isStreaming` indica que el agente sigue respondiendo, `selectedAgent` indica con que agente se esta hablando y `generatedArtifacts` guarda los archivos descargables generados por Code Interpreter.

## Errores comunes

### `Failed to fetch`

Suele indicar:

- backend apagado;
- `BASE_URL` incorrecto;
- backend viejo ejecutandose;
- CORS/preflight sin respuesta;
- Azure Functions no cargo por error de import.

Para personas no tecnicas: este error significa que la pantalla no pudo comunicarse con el backend. La causa puede estar en la URL, en permisos del navegador, en el backend apagado o en un despliegue incompleto.

### Excel falla en File Search

Correcto: `.xlsx` no va a File Search. Debe subirse en **Analisis / Code Interpreter**.

### No aparecen links reales de descarga

Code Interpreter debe generar una anotacion `container_file_citation`. Si el modelo solo escribe `sandbox:/mnt/data/...` como texto, no hay URL real para descargar.

## Checklist de Despliegue

Antes de entregar a usuarios:

- confirmar que `BASE_URL` apunta al backend publicado;
- confirmar que `ALLOWED_ORIGIN` del backend permite el dominio del frontend;
- ejecutar `npm run build`;
- publicar la carpeta `dist`;
- probar crear agente desde la pantalla;
- probar subir un PDF a File Search;
- probar subir un Excel a Code Interpreter;
- probar una pregunta por chat;
- probar que una respuesta con tabla Markdown se vea correctamente;
- probar descarga de archivos generados si Code Interpreter produce uno.

## Scripts

Instalar dependencias:

```bash
npm install
```

Desarrollo:

```bash
npm run dev
```

Build:

```bash
npm run build
```

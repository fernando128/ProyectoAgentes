<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

interface AgentFile {
  file_id: string | null
  filename: string
  status?: string | null
  bytes?: number
  content_type?: string
}

interface AgentInfo {
  id?: string | null
  agent_id?: string | null
  display_name?: string | null
  name: string
  version: string | null
  vector_store_id?: string | null
  vector_store_ids?: string[]
  files?: AgentFile[]
  code_files?: AgentFile[]
  status?: string
}

interface GeneratedArtifact {
  type: string
  container_id: string
  file_id: string
  filename: string
  download_url: string
}

interface MarkdownListItem {
  content: string
  level: number
}

interface MarkdownTable {
  headers: string[]
  rows: string[][]
}

interface MarkdownBlock {
  type: 'paragraph' | 'heading' | 'list' | 'code' | 'blockquote' | 'table'
  content?: string
  html?: string
  level?: number
  ordered?: boolean
  language?: string
  items?: MarkdownListItem[]
  table?: MarkdownTable
}

const question = ref('Resume que puedes hacer con Code Interpreter.')
const answer = ref('')
const status = ref('Listo')
const errorMessage = ref('')
const agentLabel = ref('agente-code-interpreter')
const isStreaming = ref(false)
const agents = ref<AgentInfo[]>([])
const selectedAgent = ref<AgentInfo | null>(null)
const isLoadingAgents = ref(false)
const agentsError = ref('')
const newAgentName = ref('Nuevo agente')
const newAgentInstructions = ref(
  'Eres un asistente util. Usa los archivos cargados cuando la pregunta dependa de documentos y usa Python cuando necesites calcular o analizar datos.'
)
const newAgentModel = ref('')
const isCreatingAgent = ref(false)
const createAgentError = ref('')
const createAgentSuccess = ref('')
const selectedFiles = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const isUploadingFiles = ref(false)
const uploadError = ref('')
const uploadSuccess = ref('')
const selectedCodeFiles = ref<File[]>([])
const codeFileInput = ref<HTMLInputElement | null>(null)
const isUploadingCodeFiles = ref(false)
const codeUploadError = ref('')
const codeUploadSuccess = ref('')
const generatedArtifacts = ref<GeneratedArtifact[]>([])

let source: EventSource | null = null

const canAsk = computed(
  () => question.value.trim().length > 0 && !isStreaming.value && !!selectedAgent.value
)
const canCreateAgent = computed(
  () => newAgentName.value.trim().length > 0 && !isCreatingAgent.value
)
const canUploadFiles = computed(
  () =>
    !!selectedAgent.value?.agent_id &&
    selectedFiles.value.length > 0 &&
    !isUploadingFiles.value &&
    !isStreaming.value
)
const canUploadCodeFiles = computed(
  () =>
    !!selectedAgent.value?.agent_id &&
    selectedCodeFiles.value.length > 0 &&
    !isUploadingCodeFiles.value &&
    !isStreaming.value
)
const renderedAnswer = computed(() => parseMarkdown(answer.value))

const BASE_URL = 'http://localhost:7071'
// const BASE_URL = 'https://audibotfunctions-aubaarg4h0gyf3hd.eastus2-01.azurewebsites.net/api'

function normalizeAgent(raw: Record<string, unknown>): AgentInfo | null {
  const name = String(raw.name ?? raw.agent_name ?? '').trim()
  const versionValue = raw.version ?? raw.agent_version

  if (!name) {
    return null
  }

  return {
    id: raw.id ? String(raw.id) : null,
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    display_name: raw.display_name ? String(raw.display_name) : null,
    name,
    version: versionValue ? String(versionValue) : null,
    vector_store_id: raw.vector_store_id ? String(raw.vector_store_id) : null,
    vector_store_ids: Array.isArray(raw.vector_store_ids)
      ? raw.vector_store_ids.map(String)
      : undefined,
    files: Array.isArray(raw.files) ? (raw.files as AgentFile[]) : [],
    code_files: Array.isArray(raw.code_files) ? (raw.code_files as AgentFile[]) : [],
    status: raw.status ? String(raw.status) : undefined,
  }
}

function agentKey(agent: AgentInfo) {
  return agent.agent_id || `${agent.name}:${agent.version ?? ''}`
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function renderFileCitations(value: string) {
  return value.replace(/([^]+)/g, (_, citation: string) => {
    const sourceIds = citation
      .split('')
      .map((ref) => ref.trim())
      .filter((ref) => ref && ref !== 'filecite')

    if (!sourceIds.length) {
      return ''
    }

    return ` [Fuente: ${sourceIds.join(', ')}]`
  })
}

function renderInlineMarkdown(value: string) {
  const placeholders: string[] = []
  const protect = (html: string) => {
    const token = `@@MD_TOKEN_${placeholders.length}@@`
    placeholders.push(html)
    return token
  }

  let text = renderFileCitations(value).replace(/\\([_*\[\]()])/g, '$1')

  text = text.replace(/`([^`]+)`/g, (_, code: string) =>
    protect(`<code>${escapeHtml(code)}</code>`)
  )
  text = text.replace(
    /\[([^\]]+)\]\((sandbox:[^)]+)\)/g,
    (_, label: string, href: string) =>
      protect(
        `<span class="sandbox-link">${escapeHtml(label)} <code>${escapeHtml(
          href
        )}</code></span>`
      )
  )
  text = text.replace(/\b(sandbox:\/[^\s)]+)/g, (_, href: string) =>
    protect(`<span class="sandbox-link"><code>${escapeHtml(href)}</code></span>`)
  )
  text = text.replace(
    /\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)\s]+)\)/g,
    (_, label: string, href: string) =>
      protect(
        `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(
          label
        )}</a>`
      )
  )

  let html = escapeHtml(text)

  html = html.replace(/@@MD_TOKEN_(\d+)@@/g, (_, index: string) => placeholders[Number(index)] ?? '')
  html = html.replace(/``([^`]+)``/g, '<code>$1</code>')
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')

  return html
}

function normalizeCodeFences(markdown: string) {
  const languages =
    'python|py|javascript|js|typescript|ts|json|bash|sh|shell|powershell|ps1|html|css|sql|yaml|yml|xml|txt|text'

  return markdown
    .replace(/([^\n`])(`{3,})(?=[a-zA-Z]|$)/g, '$1\n$2')
    .replace(
      new RegExp('(^|\\n)``(' + languages + ')([^`\\n]+)``(?=\\n|$)', 'gi'),
      (_, prefix: string, language: string, code: string) =>
        `${prefix}\`\`\`${language}\n${code}\n\`\`\``
    )
    .replace(
      new RegExp('(^|\\n)(`{3,})(' + languages + ')(\\S)', 'gi'),
      (_, prefix: string, fence: string, language: string, nextChar: string) =>
        `${prefix}${fence}${language}\n${nextChar}`
    )
    .replace(
      /([^\n`])(`{3,})(?=\n|$)/g,
      '$1\n$2'
    )
}

function restoreCollapsedTableRows(line: string) {
  if (!line.trim().startsWith('|') || !line.includes('||')) {
    return [line]
  }

  return line
    .split('||')
    .map((row) => {
      const trimmed = row.trim()

      if (!trimmed) {
        return ''
      }

      const left = trimmed.startsWith('|') ? trimmed : `| ${trimmed}`
      return left.endsWith('|') ? left : `${left} |`
    })
    .filter(Boolean)
}

function normalizeMarkdown(markdown: string) {
  return normalizeCodeFences(markdown)
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/([^\n])(\s*)(#{1,6}\s+)/g, '$1\n\n$3')
    .replace(/\n{3,}/g, '\n\n')
    .split('\n')
    .flatMap(restoreCollapsedTableRows)
    .join('\n')
}

function isTableSeparator(line: string) {
  if (!isTableRow(line)) {
    return false
  }

  const cells = line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())

  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
}

function isTableRow(line: string) {
  return line.trim().startsWith('|') && line.includes('|')
}

function startsTableAt(lines: string[], index: number) {
  return (
    index + 1 < lines.length &&
    isTableRow(lines[index]) &&
    isTableSeparator(lines[index + 1])
  )
}

function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => renderInlineMarkdown(cell.trim()))
}

function parseMarkdown(markdown: string): MarkdownBlock[] {
  const lines = normalizeMarkdown(markdown).split('\n')
  const blocks: MarkdownBlock[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    const fenceMatch = line.match(/^(`{3,})([\w-]*)\s*$/)

    if (fenceMatch) {
      const fence = fenceMatch[1]
      const codeLines: string[] = []
      index += 1

      while (index < lines.length && !lines[index].startsWith(fence)) {
        codeLines.push(lines[index])
        index += 1
      }

      if (index < lines.length) {
        index += 1
      }

      blocks.push({
        type: 'code',
        language: fenceMatch[2],
        content: codeLines.join('\n'),
      })
      continue
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.+)$/)

    if (headingMatch) {
      blocks.push({
        type: 'heading',
        level: headingMatch[1].length,
        html: renderInlineMarkdown(headingMatch[2]),
      })
      index += 1
      continue
    }

    if (startsTableAt(lines, index)) {
      const headers = parseTableRow(line)
      const rows: string[][] = []
      index += 2

      while (index < lines.length && isTableRow(lines[index])) {
        rows.push(parseTableRow(lines[index]))
        index += 1
      }

      blocks.push({
        type: 'table',
        table: { headers, rows },
      })
      continue
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = []

      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }

      blocks.push({
        type: 'blockquote',
        html: quoteLines.map(renderInlineMarkdown).join('<br />'),
      })
      continue
    }

    if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(line)) {
      const items: MarkdownListItem[] = []
      const ordered = /^\s*\d+[.)]\s+/.test(line)

      while (index < lines.length && /^\s*(?:[-*+]|\d+[.)])\s+/.test(lines[index])) {
        const itemMatch = lines[index].match(/^(\s*)(?:[-*+]|\d+[.)])\s+(.+)$/)

        if (itemMatch) {
          items.push({
            content: renderInlineMarkdown(itemMatch[2]),
            level: Math.floor(itemMatch[1].length / 2),
          })
        }

        index += 1
      }

      blocks.push({ type: 'list', ordered, items })
      continue
    }

    const paragraphLines = [line]
    index += 1

    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].match(/^```/) &&
      !lines[index].match(/^(#{1,4})\s+/) &&
      !lines[index].match(/^>\s?/) &&
      !lines[index].match(/^\s*(?:[-*+]|\d+[.)])\s+/) &&
      !startsTableAt(lines, index)
    ) {
      paragraphLines.push(lines[index])
      index += 1
    }

    blocks.push({
      type: 'paragraph',
      html: paragraphLines.map(renderInlineMarkdown).join('<br />'),
    })
  }

  return blocks
}

async function fetchAgents() {
  isLoadingAgents.value = true
  agentsError.value = ''

  try {
    const res = await fetch(`${BASE_URL}/agents`)

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const data = await res.json()
    const previousKey = selectedAgent.value ? agentKey(selectedAgent.value) : ''
    agents.value = (Array.isArray(data.agents) ? data.agents : [])
      .map((agent: Record<string, unknown>) => normalizeAgent(agent))
      .filter((agent: AgentInfo | null): agent is AgentInfo => Boolean(agent))

    const nextSelected =
      agents.value.find((agent) => agentKey(agent) === previousKey) ?? agents.value[0]

    if (nextSelected) {
      selectAgent(nextSelected)
    }
  } catch (err) {
    agentsError.value = 'No se pudieron cargar los agentes.'
    console.error(err)
  } finally {
    isLoadingAgents.value = false
  }
}

function selectAgent(agent: AgentInfo) {
  selectedAgent.value = agent
  agentLabel.value = `${agent.display_name || agent.name}:${agent.version ?? 's/v'}`
  uploadError.value = ''
  uploadSuccess.value = ''
  codeUploadError.value = ''
  codeUploadSuccess.value = ''
}

function closeStream(finalStatus = 'Finalizado') {
  source?.close()
  source = null
  isStreaming.value = false
  status.value = finalStatus
}

async function createAgent() {
  if (!canCreateAgent.value) {
    return
  }

  isCreatingAgent.value = true
  createAgentError.value = ''
  createAgentSuccess.value = ''

  const body: Record<string, string> = {
    name: newAgentName.value.trim(),
    instructions: newAgentInstructions.value.trim(),
  }

  if (newAgentModel.value.trim()) {
    body.model = newAgentModel.value.trim()
  }

  try {
    const res = await fetch(`${BASE_URL}/agents`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })

    const data = await res.json()

    if (!res.ok) {
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }

    const createdAgent = normalizeAgent(data.agent as Record<string, unknown>)

    if (!createdAgent) {
      throw new Error('El backend no devolvió un agente válido.')
    }

    agents.value = [
      createdAgent,
      ...agents.value.filter((agent) => agentKey(agent) !== agentKey(createdAgent)),
    ]
    selectAgent(createdAgent)
    createAgentSuccess.value = 'Agente creado con File Search y Code Interpreter.'
    newAgentName.value = 'Nuevo agente'
  } catch (err) {
    createAgentError.value =
      err instanceof Error ? err.message : 'No se pudo crear el agente.'
  } finally {
    isCreatingAgent.value = false
  }
}

function onFilesChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files ?? [])
  uploadError.value = ''
  uploadSuccess.value = ''
}

function onCodeFilesChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedCodeFiles.value = Array.from(input.files ?? [])
  codeUploadError.value = ''
  codeUploadSuccess.value = ''
}

function updateSelectedAgent(agent: AgentInfo) {
  agents.value = agents.value.map((item) =>
    agentKey(item) === agentKey(agent) ? agent : item
  )
  selectAgent(agent)
}

async function uploadFiles() {
  if (!canUploadFiles.value || !selectedAgent.value?.agent_id) {
    return
  }

  isUploadingFiles.value = true
  uploadError.value = ''
  uploadSuccess.value = ''

  const formData = new FormData()
  selectedFiles.value.forEach((file) => {
    formData.append('files', file)
  })

  try {
    const res = await fetch(`${BASE_URL}/agents/${selectedAgent.value.agent_id}/files`, {
      method: 'POST',
      body: formData,
    })
    const data = await res.json()

    if (!res.ok) {
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }

    const updatedAgent = normalizeAgent(data.agent as Record<string, unknown>)

    if (updatedAgent) {
      updateSelectedAgent(updatedAgent)
    }

    selectedFiles.value = []

    if (fileInput.value) {
      fileInput.value.value = ''
    }

    uploadSuccess.value = 'Archivos subidos e indexados en el vector store.'
  } catch (err) {
    uploadError.value =
      err instanceof Error ? err.message : 'No se pudieron subir los archivos.'
  } finally {
    isUploadingFiles.value = false
  }
}

async function uploadCodeFiles() {
  if (!canUploadCodeFiles.value || !selectedAgent.value?.agent_id) {
    return
  }

  isUploadingCodeFiles.value = true
  codeUploadError.value = ''
  codeUploadSuccess.value = ''

  const formData = new FormData()
  selectedCodeFiles.value.forEach((file) => {
    formData.append('files', file)
  })

  try {
    const res = await fetch(`${BASE_URL}/agents/${selectedAgent.value.agent_id}/code-files`, {
      method: 'POST',
      body: formData,
    })
    const data = await res.json()

    if (!res.ok) {
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }

    const updatedAgent = normalizeAgent(data.agent as Record<string, unknown>)

    if (updatedAgent) {
      updateSelectedAgent(updatedAgent)
    }

    selectedCodeFiles.value = []

    if (codeFileInput.value) {
      codeFileInput.value.value = ''
    }

    codeUploadSuccess.value = 'Archivos listos para Code Interpreter.'
  } catch (err) {
    codeUploadError.value =
      err instanceof Error ? err.message : 'No se pudieron subir los archivos de analisis.'
  } finally {
    isUploadingCodeFiles.value = false
  }
}

function askAgent() {
  const message = question.value.trim()

  if (!message || isStreaming.value || !selectedAgent.value) {
    return
  }

  answer.value = ''
  errorMessage.value = ''
  generatedArtifacts.value = []
  status.value = 'Conectando'
  isStreaming.value = true

  source?.close()

  const params = new URLSearchParams({ message })
  const activeAgent = selectedAgent.value

  if (activeAgent.agent_id) {
    params.set('agent_id', activeAgent.agent_id)
  } else {
    params.set('agent_name', activeAgent.name)

    if (activeAgent.version) {
      params.set('agent_version', activeAgent.version)
    }
  }

  // prueba en local
  source = new EventSource(`${BASE_URL}/chat-stream?${params.toString()}`)
  // pruba en la nube
  //source = new EventSource(
  //`https://audibotfunctions-aubaarg4h0gyf3hd.eastus2-01.azurewebsites.net/chat-stream?${params.toString()}`
  //)
  source.onopen = () => {
    status.value = 'Pensando'
  }

  source.onmessage = (event) => {
    if (event.data === '[FIN]') {
      closeStream('Finalizado')
      return
    }

    answer.value += event.data
  }

  source.addEventListener('metadata', (event) => {
    try {
      const metadata = JSON.parse((event as MessageEvent).data)
      agentLabel.value = `${metadata.agent}:${metadata.version}`
    } catch {
      agentLabel.value = 'agente-code-interpreter'
    }
  })

  source.addEventListener('artifact', (event) => {
    try {
      const artifact = JSON.parse((event as MessageEvent).data) as GeneratedArtifact
      const exists = generatedArtifacts.value.some(
        (item) =>
          item.container_id === artifact.container_id && item.file_id === artifact.file_id
      )

      if (!exists) {
        generatedArtifacts.value.push(artifact)
      }
    } catch {
      console.warn('No se pudo leer el artifact del stream.')
    }
  })

  source.addEventListener('agent-error', (event) => {
    errorMessage.value = (event as MessageEvent).data
    closeStream('Error')
  })

  source.onerror = () => {
    if (isStreaming.value) {
      errorMessage.value = 'Se cerró la conexión con Azure Functions.'
      closeStream('Error de conexión')
    }
  }
}

onMounted(() => {
  fetchAgents()
})

onBeforeUnmount(() => {
  source?.close()
})
</script>

<template>
  <main class="agent-console">
    <section class="workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">Azure AI Foundry</p>
          <h1>Chat asíncrono con agente</h1>
        </div>
        <div class="status" :data-active="isStreaming">
          <span aria-hidden="true"></span>
          {{ status }}
        </div>
      </header>

      <section class="settings-panel">
        <div class="panel-header">
          <h2>Crear agente</h2>
          <button type="button" class="secondary" @click="fetchAgents" :disabled="isLoadingAgents">
            {{ isLoadingAgents ? 'Cargando...' : 'Recargar' }}
          </button>
        </div>

        <form class="field-grid" @submit.prevent="createAgent">
          <label>
            Nombre
            <input v-model="newAgentName" :disabled="isCreatingAgent" />
          </label>
          <label>
            Modelo
            <input
              v-model="newAgentModel"
              :disabled="isCreatingAgent"
              placeholder="Usar variable del backend"
            />
          </label>
          <label class="wide">
            Instrucciones
            <textarea v-model="newAgentInstructions" :disabled="isCreatingAgent" rows="3" />
          </label>
          <div class="actions wide">
            <button type="submit" :disabled="!canCreateAgent">
              {{ isCreatingAgent ? 'Creando...' : 'Crear agente' }}
            </button>
          </div>
        </form>

        <p v-if="createAgentError" class="error">{{ createAgentError }}</p>
        <p v-if="createAgentSuccess" class="success">{{ createAgentSuccess }}</p>
      </section>

      <section class="layout-grid">
        <section class="panel">
          <div class="panel-header">
            <h2>Agentes</h2>
            <span class="hint">{{ agents.length }} disponibles</span>
          </div>

          <p v-if="agentsError" class="error">{{ agentsError }}</p>

          <div v-else class="agent-list">
            <button
              v-for="agent in agents"
              :key="agentKey(agent)"
              type="button"
              class="agent-item"
              :class="{ active: selectedAgent && agentKey(selectedAgent) === agentKey(agent) }"
              :disabled="isStreaming"
              @click="selectAgent(agent)"
            >
              <span>{{ agent.display_name || agent.name }}</span>
              <code>v{{ agent.version ?? '?' }}</code>
            </button>

            <p v-if="!isLoadingAgents && agents.length === 0" class="empty compact">
              No hay agentes disponibles.
            </p>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <h2>Conocimiento</h2>
            <code>{{ selectedAgent?.vector_store_id ?? 'sin vector store' }}</code>
          </div>

          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".c,.cs,.cpp,.css,.doc,.docx,.html,.java,.js,.json,.md,.pdf,.php,.pptx,.py,.rb,.sh,.tex,.ts,.txt"
            :disabled="!selectedAgent?.agent_id || isUploadingFiles || isStreaming"
            @change="onFilesChange"
          />

          <div class="actions">
            <button type="button" :disabled="!canUploadFiles" @click="uploadFiles">
              {{ isUploadingFiles ? 'Subiendo...' : 'Subir a File Search' }}
            </button>
          </div>

          <p v-if="uploadError" class="error">{{ uploadError }}</p>
          <p v-if="uploadSuccess" class="success">{{ uploadSuccess }}</p>

          <div class="artifact-list">
            <p v-if="!selectedAgent?.files?.length" class="empty compact">
              Este agente todavía no tiene documentos de conocimiento.
            </p>
            <div
              v-for="file in selectedAgent?.files ?? []"
              :key="file.file_id ?? file.filename"
              class="file-row"
            >
              <span>{{ file.filename }}</span>
              <code>{{ file.status ?? 'procesado' }}</code>
            </div>
          </div>

          <div class="upload-divider"></div>

          <div class="panel-header">
            <h2>Análisis</h2>
            <span class="hint">Excel, CSV y datos</span>
          </div>

          <input
            ref="codeFileInput"
            type="file"
            multiple
            accept=".xlsx,.csv,.json,.txt,.py,.zip,.png,.jpg,.jpeg,.gif,.pdf,.docx,.pptx"
            :disabled="!selectedAgent?.agent_id || isUploadingCodeFiles || isStreaming"
            @change="onCodeFilesChange"
          />

          <div class="actions">
            <button type="button" :disabled="!canUploadCodeFiles" @click="uploadCodeFiles">
              {{ isUploadingCodeFiles ? 'Subiendo...' : 'Subir a Code Interpreter' }}
            </button>
          </div>

          <p v-if="codeUploadError" class="error">{{ codeUploadError }}</p>
          <p v-if="codeUploadSuccess" class="success">{{ codeUploadSuccess }}</p>

          <div class="artifact-list">
            <p v-if="!selectedAgent?.code_files?.length" class="empty compact">
              Este agente todavía no tiene archivos para análisis.
            </p>
            <div
              v-for="file in selectedAgent?.code_files ?? []"
              :key="file.file_id ?? file.filename"
              class="file-row"
            >
              <span>{{ file.filename }}</span>
              <code>python</code>
            </div>
          </div>
        </section>
      </section>

      <form class="composer" @submit.prevent="askAgent">
        <label for="question">Pregunta</label>
        <textarea
          id="question"
          v-model="question"
          :disabled="isStreaming"
          rows="4"
          placeholder="Escribe tu pregunta para el agente..."
        />
        <div class="actions">
          <button type="submit" :disabled="!canAsk">
            Preguntar
          </button>
          <button
            type="button"
            class="secondary"
            :disabled="!isStreaming"
            @click="closeStream('Detenido')"
          >
            Detener
          </button>
        </div>
      </form>

      <section class="response-panel" aria-live="polite">
        <div class="response-header">
          <span>Respuesta</span>
          <code>{{ agentLabel }}</code>
        </div>
        <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
        <template v-else>
          <div v-if="answer" class="answer markdown-answer">
            <template v-for="(block, index) in renderedAnswer" :key="index">
              <h2
                v-if="block.type === 'heading' && block.level === 1"
                v-html="block.html"
              ></h2>
              <h3
                v-else-if="block.type === 'heading'"
                v-html="block.html"
              ></h3>
              <pre
                v-else-if="block.type === 'code'"
                :data-language="block.language || undefined"
              ><code>{{ block.content }}</code></pre>
              <blockquote v-else-if="block.type === 'blockquote'" v-html="block.html"></blockquote>
              <div v-else-if="block.type === 'table' && block.table" class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th
                        v-for="(header, headerIndex) in block.table.headers"
                        :key="headerIndex"
                        v-html="header"
                      ></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, rowIndex) in block.table.rows" :key="rowIndex">
                      <td
                        v-for="(_, cellIndex) in block.table.headers"
                        :key="cellIndex"
                        v-html="row[cellIndex] ?? ''"
                      ></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ol v-else-if="block.type === 'list' && block.ordered">
                <li
                  v-for="(item, itemIndex) in block.items"
                  :key="itemIndex"
                  :style="{ marginLeft: `${item.level * 18}px` }"
                  v-html="item.content"
                ></li>
              </ol>
              <ul v-else-if="block.type === 'list'">
                <li
                  v-for="(item, itemIndex) in block.items"
                  :key="itemIndex"
                  :style="{ marginLeft: `${item.level * 18}px` }"
                  v-html="item.content"
                ></li>
              </ul>
              <p v-else v-html="block.html"></p>
            </template>
          </div>
          <div v-if="generatedArtifacts.length" class="generated-files">
            <span>Archivos generados</span>
            <a
              v-for="artifact in generatedArtifacts"
              :key="`${artifact.container_id}:${artifact.file_id}`"
              :href="artifact.download_url"
              target="_blank"
              rel="noreferrer"
            >
              {{ artifact.filename }}
            </a>
          </div>
          <p v-if="!answer && !generatedArtifacts.length" class="empty">
            La respuesta del agente aparecerá aquí.
          </p>
        </template>
      </section>
    </section>
  </main>
</template>

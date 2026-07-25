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

interface UploadedResource {
  id: string
  upload_id?: string | null
  file_id?: string | null
  filename: string
  content_type?: string | null
  bytes?: number | null
  destination: 'file_search' | 'code_interpreter' | 'vision' | string
  tool?: string | null
  vector_store_id?: string | null
  status?: string | null
  purpose?: string | null
  created_at?: string | null
}

interface ChatMessage {
  role: 'user' | 'assistant' | string
  content: string
  created_at?: string | null
  metadata?: Record<string, unknown>
}

interface ChatThread {
  thread_id: string
  agent_id?: string | null
  agent_name?: string | null
  agent_version?: string | null
  title: string
  vector_store_id?: string | null
  files?: UploadedResource[]
  code_files?: UploadedResource[]
  images?: UploadedResource[]
  messages?: ChatMessage[]
  message_count?: number
  created_at?: string | null
  updated_at?: string | null
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
const selectedUploadFiles = ref<File[]>([])
const uploadFileInput = ref<HTMLInputElement | null>(null)
const threadId = ref('')
const threads = ref<ChatThread[]>([])
const selectedThread = ref<ChatThread | null>(null)
const isLoadingThreads = ref(false)
const isCreatingThread = ref(false)
const threadsError = ref('')
const chatMessages = ref<ChatMessage[]>([])
const sessionResources = ref<UploadedResource[]>([])
const generatedArtifacts = ref<GeneratedArtifact[]>([])
const activeAssistantMessageIndex = ref<number | null>(null)

let requestController: AbortController | null = null
let source: EventSource | null = null

const canAsk = computed(
  () => question.value.trim().length > 0 && !isStreaming.value && !!selectedAgent.value
)
const canCreateAgent = computed(
  () => newAgentName.value.trim().length > 0 && !isCreatingAgent.value
)
const canCreateThread = computed(
  () => !!selectedAgent.value?.agent_id && !isCreatingThread.value && !isStreaming.value
)

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

function normalizeUploadedResource(raw: Record<string, unknown>): UploadedResource | null {
  const filename = String(raw.filename ?? '').trim()
  const destination = String(raw.destination ?? raw.route ?? raw.tool ?? '').trim()
  const id = String(raw.id ?? raw.file_id ?? raw.upload_id ?? '').trim()

  if (!filename || !destination || !id) {
    return null
  }

  return {
    id,
    upload_id: raw.upload_id ? String(raw.upload_id) : null,
    file_id: raw.file_id ? String(raw.file_id) : null,
    filename,
    content_type: raw.content_type ? String(raw.content_type) : null,
    bytes: typeof raw.bytes === 'number' ? raw.bytes : null,
    destination,
    tool: raw.tool ? String(raw.tool) : null,
    vector_store_id: raw.vector_store_id ? String(raw.vector_store_id) : null,
    status: raw.status ? String(raw.status) : null,
    purpose: raw.purpose ? String(raw.purpose) : null,
    created_at: raw.created_at ? String(raw.created_at) : null,
  }
}

function normalizeUploadedResources(value: unknown): UploadedResource[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .map((resource) => normalizeUploadedResource(resource as Record<string, unknown>))
    .filter((resource: UploadedResource | null): resource is UploadedResource =>
      Boolean(resource)
    )
}

function normalizeChatMessage(raw: Record<string, unknown>): ChatMessage | null {
  const role = String(raw.role ?? '').trim()
  const content = String(raw.content ?? '')

  if (!role) {
    return null
  }

  return {
    role,
    content,
    created_at: raw.created_at ? String(raw.created_at) : null,
    metadata:
      raw.metadata && typeof raw.metadata === 'object'
        ? (raw.metadata as Record<string, unknown>)
        : undefined,
  }
}

function normalizeThread(raw: Record<string, unknown>): ChatThread | null {
  const id = String(raw.thread_id ?? '').trim()

  if (!id) {
    return null
  }

  return {
    thread_id: id,
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    agent_name: raw.agent_name ? String(raw.agent_name) : null,
    agent_version: raw.agent_version ? String(raw.agent_version) : null,
    title: String(raw.title ?? 'Nuevo chat').trim() || 'Nuevo chat',
    vector_store_id: raw.vector_store_id ? String(raw.vector_store_id) : null,
    files: normalizeUploadedResources(raw.files),
    code_files: normalizeUploadedResources(raw.code_files),
    images: normalizeUploadedResources(raw.images),
    messages: Array.isArray(raw.messages)
      ? raw.messages
          .map((message) => normalizeChatMessage(message as Record<string, unknown>))
          .filter((message: ChatMessage | null): message is ChatMessage =>
            Boolean(message)
          )
      : [],
    message_count: typeof raw.message_count === 'number' ? raw.message_count : 0,
    created_at: raw.created_at ? String(raw.created_at) : null,
    updated_at: raw.updated_at ? String(raw.updated_at) : null,
  }
}

function threadResources(thread: ChatThread | null) {
  if (!thread) {
    return []
  }

  return [
    ...(thread.files ?? []),
    ...(thread.code_files ?? []),
    ...(thread.images ?? []),
  ]
}

function upsertThread(thread: ChatThread) {
  threads.value = [
    thread,
    ...threads.value.filter((item) => item.thread_id !== thread.thread_id),
  ]
}

function destinationLabel(destination: string) {
  if (destination === 'file_search') {
    return 'File Search'
  }

  if (destination === 'code_interpreter') {
    return 'Code Interpreter'
  }

  if (destination === 'vision') {
    return 'Visión'
  }

  return destination
}

function formatBytes(bytes?: number | null) {
  if (!bytes) {
    return '0 KB'
  }

  if (bytes < 1024 * 1024) {
    return `${Math.ceil(bytes / 1024)} KB`
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function applyUploadMetadata(data: Record<string, unknown>) {
  if (data.thread_id) {
    threadId.value = String(data.thread_id)
    const existingThread = threads.value.find((thread) => thread.thread_id === threadId.value)
    const title = existingThread?.title || question.value.trim().slice(0, 80) || 'Nuevo chat'
    const metadataThread: ChatThread = {
      ...(existingThread ?? {
        thread_id: threadId.value,
        agent_id: selectedAgent.value?.agent_id ?? null,
        agent_name: selectedAgent.value?.name ?? null,
        agent_version: selectedAgent.value?.version ?? null,
        title,
        files: [],
        code_files: [],
        images: [],
        messages: [],
        message_count: 0,
      }),
      thread_id: threadId.value,
      title,
      updated_at: new Date().toISOString(),
    }

    upsertThread(metadataThread)
    selectedThread.value = metadataThread
  }

  if (data.agent || data.version) {
    agentLabel.value = `${String(data.agent ?? 'agente')}:${String(data.version ?? 's/v')}`
  }

  sessionResources.value = normalizeUploadedResources(
    data.archivos_thread ?? data.archivos_subidos
  )
}

function addGeneratedArtifact(artifact: GeneratedArtifact) {
  const exists = generatedArtifacts.value.some(
    (item) =>
      item.container_id === artifact.container_id && item.file_id === artifact.file_id
  )

  if (!exists) {
    generatedArtifacts.value.push(artifact)
  }
}

function handleUploadSseEvent(eventName: string, data: string) {
  if (data === '[FIN]') {
    status.value = errorMessage.value ? 'Error' : 'Finalizado'
    return
  }

  if (!eventName || eventName === 'message') {
    answer.value += data
    if (activeAssistantMessageIndex.value === null) {
      chatMessages.value.push({ role: 'assistant', content: '' })
      activeAssistantMessageIndex.value = chatMessages.value.length - 1
    }

    chatMessages.value[activeAssistantMessageIndex.value].content += data
    status.value = 'Respondiendo'
    return
  }

  if (eventName === 'metadata' || eventName === 'uploaded-files' || eventName === 'done') {
    try {
      applyUploadMetadata(JSON.parse(data) as Record<string, unknown>)
    } catch {
      console.warn(`No se pudo leer el evento ${eventName}.`)
    }

    if (eventName === 'metadata') {
      status.value = 'Pensando'
    }

    if (eventName === 'done') {
      status.value = 'Finalizado'
      activeAssistantMessageIndex.value = null

      if (selectedAgent.value?.agent_id) {
        void fetchThreads(selectedAgent.value, false)
      }
    }

    return
  }

  if (eventName === 'artifact') {
    try {
      addGeneratedArtifact(JSON.parse(data) as GeneratedArtifact)
    } catch {
      console.warn('No se pudo leer el artifact del stream.')
    }

    return
  }

  if (eventName === 'agent-error') {
    errorMessage.value = data
    status.value = 'Error'
  }
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

function renderMessageHtml(content: string) {
  return parseMarkdown(content)
    .map((block) => {
      if (block.type === 'heading') {
        const tag = block.level === 1 ? 'h2' : 'h3'
        return `<${tag}>${block.html ?? ''}</${tag}>`
      }

      if (block.type === 'code') {
        const language = block.language
          ? ` data-language="${escapeHtml(block.language)}"`
          : ''
        return `<pre${language}><code>${escapeHtml(block.content ?? '')}</code></pre>`
      }

      if (block.type === 'blockquote') {
        return `<blockquote>${block.html ?? ''}</blockquote>`
      }

      if (block.type === 'table' && block.table) {
        const headers = block.table.headers
          .map((header) => `<th>${header}</th>`)
          .join('')
        const rows = block.table.rows
          .map(
            (row) =>
              `<tr>${block.table?.headers
                .map((_, index) => `<td>${row[index] ?? ''}</td>`)
                .join('')}</tr>`
          )
          .join('')

        return `<div class="table-wrap"><table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`
      }

      if (block.type === 'list') {
        const tag = block.ordered ? 'ol' : 'ul'
        const items = (block.items ?? [])
          .map(
            (item) =>
              `<li style="margin-left: ${item.level * 18}px">${item.content}</li>`
          )
          .join('')
        return `<${tag}>${items}</${tag}>`
      }

      return `<p>${block.html ?? ''}</p>`
    })
    .join('')
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

async function fetchThreads(agent: AgentInfo, selectFirst = true) {
  const agentId = agent.agent_id

  threadsError.value = ''

  if (!agentId) {
    threads.value = []
    selectedThread.value = null
    threadId.value = ''
    return
  }

  isLoadingThreads.value = true

  try {
    const res = await fetch(`${BASE_URL}/agents/${encodeURIComponent(agentId)}/threads`)

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const data = await res.json()
    const loadedThreads = (Array.isArray(data.threads) ? data.threads : [])
      .map((thread: Record<string, unknown>) => normalizeThread(thread))
      .filter((thread: ChatThread | null): thread is ChatThread => Boolean(thread))

    threads.value = loadedThreads

    if (!selectFirst) {
      const refreshedThread = loadedThreads.find((thread) => thread.thread_id === threadId.value)

      if (refreshedThread) {
        selectedThread.value = refreshedThread
      }

      return
    }

    const nextThread =
      loadedThreads.find((thread) => thread.thread_id === threadId.value) ??
      loadedThreads[0] ??
      null

    if (nextThread) {
      await selectThread(nextThread)
    } else {
      selectedThread.value = null
      threadId.value = ''
      chatMessages.value = []
      answer.value = ''
      sessionResources.value = []
    }
  } catch (err) {
    threadsError.value = 'No se pudieron cargar los chats del agente.'
    console.error(err)
  } finally {
    isLoadingThreads.value = false
  }
}

async function createThread() {
  const agent = selectedAgent.value

  if (!agent?.agent_id || isCreatingThread.value) {
    return
  }

  isCreatingThread.value = true
  threadsError.value = ''

  try {
    const res = await fetch(`${BASE_URL}/agents/${encodeURIComponent(agent.agent_id)}/threads`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title: 'Nuevo chat' }),
    })
    const data = await res.json()

    if (!res.ok) {
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }

    const thread = normalizeThread(data.thread as Record<string, unknown>)

    if (!thread) {
      throw new Error('El backend no devolvió un chat válido.')
    }

    upsertThread(thread)
    await selectThread(thread)
  } catch (err) {
    threadsError.value = err instanceof Error ? err.message : 'No se pudo crear el chat.'
  } finally {
    isCreatingThread.value = false
  }
}

async function selectThread(thread: ChatThread) {
  if (isStreaming.value) {
    return
  }

  selectedThread.value = thread
  threadId.value = thread.thread_id
  answer.value = ''
  errorMessage.value = ''
  generatedArtifacts.value = []
  sessionResources.value = threadResources(thread)

  try {
    const res = await fetch(`${BASE_URL}/threads/${encodeURIComponent(thread.thread_id)}`)

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const data = await res.json()
    const detailedThread = normalizeThread(data.thread as Record<string, unknown>)

    if (!detailedThread) {
      throw new Error('El backend no devolvió un chat válido.')
    }

    selectedThread.value = detailedThread
    upsertThread(detailedThread)
    chatMessages.value = detailedThread.messages ?? []
    sessionResources.value = threadResources(detailedThread)
  } catch (err) {
    threadsError.value = 'No se pudo cargar el detalle del chat.'
    chatMessages.value = thread.messages ?? []
    console.error(err)
  }
}

function selectAgent(agent: AgentInfo) {
  const previousKey = selectedAgent.value ? agentKey(selectedAgent.value) : ''
  const nextKey = agentKey(agent)
  selectedAgent.value = agent
  agentLabel.value = `${agent.display_name || agent.name}:${agent.version ?? 's/v'}`

  if (previousKey && previousKey !== nextKey) {
    threadId.value = ''
    answer.value = ''
    chatMessages.value = []
    selectedThread.value = null
    sessionResources.value = []
    generatedArtifacts.value = []
  }

  void fetchThreads(agent)
}

function closeStream(finalStatus = 'Finalizado') {
  source?.close()
  source = null
  requestController?.abort()
  requestController = null
  isStreaming.value = false
  status.value = finalStatus
  activeAssistantMessageIndex.value = null
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

function onUnifiedFilesChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedUploadFiles.value = Array.from(input.files ?? [])
  errorMessage.value = ''
}

async function askAgent() {
  const message = question.value.trim()

  if (!message || isStreaming.value || !selectedAgent.value) {
    return
  }

  answer.value = ''
  errorMessage.value = ''
  generatedArtifacts.value = []
  chatMessages.value.push({
    role: 'user',
    content: message,
    created_at: new Date().toISOString(),
  })
  activeAssistantMessageIndex.value = null
  status.value = selectedUploadFiles.value.length ? 'Subiendo' : 'Pensando'
  isStreaming.value = true

  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  const formData = new FormData()
  const activeAgent = selectedAgent.value

  formData.append('message', message)

  if (threadId.value) {
    formData.append('thread_id', threadId.value)
  }

  if (activeAgent.agent_id) {
    formData.append('agent_id', activeAgent.agent_id)
  } else {
    formData.append('agent_name', activeAgent.name)

    if (activeAgent.version) {
      formData.append('agent_version', activeAgent.version)
    }
  }

  selectedUploadFiles.value.forEach((file) => {
    formData.append('file', file)
  })

  try {
    const res = await fetch(`${BASE_URL}/upload-and-ask`, {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    })

    if (!res.ok) {
      const contentType = res.headers.get('content-type') ?? ''
      const data = contentType.includes('application/json')
        ? await res.json()
        : { error: await res.text() }
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }

    const data = await res.json()
    applyUploadMetadata(data as Record<string, unknown>)
    selectedUploadFiles.value = []

    if (uploadFileInput.value) {
      uploadFileInput.value.value = ''
    }

    const invocationId = String(data.invocation_id ?? '').trim()
    const streamUrl = String(
      data.stream_url ??
        (invocationId ? `${BASE_URL}/upload-and-ask-stream/${invocationId}` : '')
    ).trim()

    if (!streamUrl) {
      if (data.respuesta) {
        answer.value = String(data.respuesta)
        generatedArtifacts.value = Array.isArray(data.archivos_generados)
          ? (data.archivos_generados as GeneratedArtifact[])
          : []
        status.value = 'Finalizado sin streaming'
        throw new Error(
          'El backend respondió con el contrato anterior sin stream_url. Reinicia Azure Functions para cargar la versión nueva.'
        )
      }

      throw new Error(
        `El backend no devolvió stream_url ni invocation_id. Campos recibidos: ${Object.keys(
          data as Record<string, unknown>
        ).join(', ') || 'ninguno'}`
      )
    }

    status.value = 'Pensando'
    source?.close()
    source = new EventSource(streamUrl)

    source.onmessage = (event) => {
      if (event.data === '[FIN]') {
        closeStream(errorMessage.value ? 'Error' : 'Finalizado')
        return
      }

      handleUploadSseEvent('message', event.data)
    }

    source.addEventListener('metadata', (event) => {
      handleUploadSseEvent('metadata', (event as MessageEvent).data)
    })

    source.addEventListener('uploaded-files', (event) => {
      handleUploadSseEvent('uploaded-files', (event as MessageEvent).data)
    })

    source.addEventListener('artifact', (event) => {
      handleUploadSseEvent('artifact', (event as MessageEvent).data)
    })

    source.addEventListener('done', (event) => {
      handleUploadSseEvent('done', (event as MessageEvent).data)
    })

    source.addEventListener('agent-error', (event) => {
      handleUploadSseEvent('agent-error', (event as MessageEvent).data)
      closeStream('Error')
    })

    source.onerror = () => {
      if (isStreaming.value) {
        errorMessage.value = 'Se cerró la conexión con Azure Functions.'
        closeStream('Error de conexión')
      }
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      return
    }

    errorMessage.value =
      err instanceof Error ? err.message : 'No se pudo consultar el agente.'
    status.value = 'Error'
    isStreaming.value = false
  } finally {
    if (requestController === controller) {
      requestController = null
    }
  }
}

onMounted(() => {
  fetchAgents()
})

onBeforeUnmount(() => {
  source?.close()
  requestController?.abort()
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

          <div class="upload-divider"></div>

          <div class="panel-header">
            <h2>Chats</h2>
            <button
              type="button"
              class="secondary"
              :disabled="!canCreateThread"
              @click="createThread"
            >
              {{ isCreatingThread ? 'Creando...' : 'Nuevo chat' }}
            </button>
          </div>

          <p v-if="threadsError" class="error">{{ threadsError }}</p>

          <div class="chat-list">
            <button
              v-for="thread in threads"
              :key="thread.thread_id"
              type="button"
              class="chat-item"
              :class="{ active: selectedThread?.thread_id === thread.thread_id }"
              :disabled="isStreaming"
              @click="selectThread(thread)"
            >
              <span>{{ thread.title }}</span>
              <small>{{ thread.message_count ?? 0 }} mensajes</small>
            </button>

            <p v-if="!isLoadingThreads && threads.length === 0" class="empty compact">
              No hay chats para este agente.
            </p>
            <p v-if="isLoadingThreads" class="empty compact">Cargando chats...</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <h2>Sesión</h2>
            <code>{{ selectedThread?.title || threadId || 'thread nuevo' }}</code>
          </div>

          <div class="artifact-list">
            <p v-if="!sessionResources.length" class="empty compact">
              Los archivos enviados aparecerán aquí con su destino.
            </p>
            <div
              v-for="resource in sessionResources"
              :key="resource.id"
              class="file-row resource-row"
            >
              <span>{{ resource.filename }}</span>
              <code>{{ destinationLabel(resource.destination) }}</code>
              <small>{{ resource.file_id || resource.upload_id }}</small>
              <small>{{ resource.content_type || 'sin MIME' }}</small>
              <small v-if="resource.vector_store_id">VS {{ resource.vector_store_id }}</small>
              <small>{{ formatBytes(resource.bytes) }}</small>
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
        <label for="unified-files">Archivos</label>
        <input
          id="unified-files"
          ref="uploadFileInput"
          type="file"
          multiple
          accept=".png,.jpg,.jpeg,.webp,.gif,.csv,.xlsx,.json,.tsv,.pdf,.docx,.txt,.md,.pptx"
          :disabled="isStreaming"
          @change="onUnifiedFilesChange"
        />
        <div v-if="selectedUploadFiles.length" class="artifact-list selected-files">
          <div
            v-for="file in selectedUploadFiles"
            :key="`${file.name}:${file.size}:${file.lastModified}`"
            class="file-row"
          >
            <span>{{ file.name }}</span>
            <code>{{ Math.ceil(file.size / 1024) }} KB</code>
          </div>
        </div>
        <div class="actions">
          <button type="submit" :disabled="!canAsk">
            {{ isStreaming ? 'Enviando...' : 'Preguntar' }}
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
          <span>Conversación</span>
          <code>{{ agentLabel }}</code>
        </div>
        <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
        <template v-else>
          <div v-if="chatMessages.length" class="chat-transcript">
            <article
              v-for="(message, index) in chatMessages"
              :key="`${message.role}:${message.created_at ?? index}`"
              class="chat-message"
              :class="message.role === 'user' ? 'from-user' : 'from-agent'"
            >
              <div class="message-meta">
                <span>{{ message.role === 'user' ? 'Tú' : 'Agente' }}</span>
              </div>
              <div class="message-bubble" v-html="renderMessageHtml(message.content)"></div>
            </article>
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
          <p v-if="!chatMessages.length && !generatedArtifacts.length" class="empty">
            La conversación del chat seleccionado aparecerá aquí.
          </p>
        </template>
      </section>
    </section>
  </main>
</template>

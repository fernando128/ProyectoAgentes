<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

interface AgentInfo {
  name: string
  version: string | null
}

const question = ref('Resume que puedes hacer con Code Interpreter.')
const answer = ref('')
const status = ref('Listo')
const errorMessage = ref('')
const agentLabel = ref('agente-code-interpreter')
const isStreaming = ref(false)
// --- Nuevo: manejo de agentes ---
const agents = ref<AgentInfo[]>([])
const selectedAgent = ref<AgentInfo | null>(null)
const isLoadingAgents = ref(false)
const agentsError = ref('')


let source: EventSource | null = null


const canAsk = computed(
  () => question.value.trim().length > 0 && !isStreaming.value && !!selectedAgent.value
)

const BASE_URL = 'http://localhost:7071'
// const BASE_URL = 'https://audibotfunctions-aubaarg4h0gyf3hd.eastus2-01.azurewebsites.net/api'

async function fetchAgents() {
  isLoadingAgents.value = true
  agentsError.value = ''

  try {
    const res = await fetch(`${BASE_URL}/agents`)

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const data = await res.json()
    agents.value = data.agents ?? []

    if (agents.value.length > 0) {
      selectAgent(agents.value[0])
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
  agentLabel.value = `${agent.name}:${agent.version ?? 's/v'}`
}



function closeStream(finalStatus = 'Finalizado') {
  source?.close()
  source = null
  isStreaming.value = false
  status.value = finalStatus
}




function askAgent() {
  const message = question.value.trim()

  if (!message || isStreaming.value) {
    return
  }

  answer.value = ''
  errorMessage.value = ''
  status.value = 'Conectando'
  isStreaming.value = true

  source?.close()

  const params = new URLSearchParams({ message })
  // prueba en local
  source = new EventSource(`http://localhost:7071/chat-stream?${params.toString()}`)
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

      <!-- Nuevo: selector de agentes -->
      <section class="agent-picker">
        <div class="agent-picker-header">
          <span>Agente activo</span>
          <button type="button" class="secondary small" @click="fetchAgents" :disabled="isLoadingAgents">
            {{ isLoadingAgents ? 'Cargando...' : 'Recargar agentes' }}
          </button>
        </div>

        <p v-if="agentsError" class="error">{{ agentsError }}</p>

        <div v-else class="agent-buttons">
          <button
            v-for="agent in agents"
            :key="agent.name"
            type="button"
            class="agent-chip"
            :class="{ active: selectedAgent?.name === agent.name }"
            :disabled="isStreaming"
            @click="selectAgent(agent)"
          >
            {{ agent.name }} <small>v{{ agent.version ?? '?' }}</small>
          </button>

          <p v-if="!isLoadingAgents && agents.length === 0" class="empty">
            No hay agentes disponibles.
          </p>
        </div>
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
        <div v-else-if="answer" class="answer">{{ answer }}</div>
        <p v-else class="empty">La respuesta del agente aparecerá aquí.</p>
      </section>
    </section>
  </main>
</template>
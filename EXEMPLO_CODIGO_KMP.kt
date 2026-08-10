// ============================================================================
// EXEMPLO COMPLETO DE CÓDIGO PARA KOTLIN MULTIPLATFORM
// Copie e adapte para seu projeto
// ============================================================================

// ============================================================================
// 1. MODELS (commonMain/kotlin/data/models/)
// ============================================================================

// ChatRequest.kt
package data.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ChatRequest(
    val message: String,
    @SerialName("restaurant_id")
    val restaurantId: Int? = null,
    @SerialName("session_id")
    val sessionId: String? = null
)

// ChatResponse.kt
package data.models

import kotlinx.serialization.Serializable

@Serializable
data class ChatResponse(
    val response: String,
    val products: List<Product>,
    val intent: String
)

// Product.kt (se ainda não tiver)
package data.models

import kotlinx.serialization.Serializable

@Serializable
data class Product(
    val id: Int,
    val name: String,
    val price: Double,
    val description: String? = null,
    val category: String? = null,
    val quantity: Int = 1
)

// ============================================================================
// 2. API CLIENT (commonMain/kotlin/data/api/)
// ============================================================================

// ApiConfig.kt
package data.api

object ApiConfig {
    // TODO: SUBSTITUIR PELO SEU IP LOCAL
    const val BASE_URL = "http://192.168.1.100:8000"

    const val ENDPOINT_CHAT_SALES = "$BASE_URL/chat/sales"
    const val ENDPOINT_CHAT_STATUS = "$BASE_URL/chat/status"
}

// HttpClientFactory.kt
package data.api

import io.ktor.client.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.logging.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.serialization.json.Json

object HttpClientFactory {
    fun create(): HttpClient {
        return HttpClient {
            install(ContentNegotiation) {
                json(Json {
                    prettyPrint = true
                    isLenient = true
                    ignoreUnknownKeys = true
                })
            }

            install(Logging) {
                logger = Logger.DEFAULT
                level = LogLevel.INFO
            }

            // Timeout para IA (pode demorar)
            engine {
                requestTimeout = 60_000  // 60 segundos
                connectTimeout = 30_000  // 30 segundos
            }
        }
    }
}

// ============================================================================
// 3. REPOSITORY (commonMain/kotlin/data/repository/)
// ============================================================================

// ChatRepository.kt
package data.repository

import data.api.ApiConfig
import data.api.HttpClientFactory
import data.models.ChatRequest
import data.models.ChatResponse
import io.ktor.client.call.*
import io.ktor.client.request.*
import io.ktor.http.*

class ChatRepository {
    private val client = HttpClientFactory.create()

    /**
     * Envia mensagem para o chat com IA conversacional
     */
    suspend fun sendMessage(
        message: String,
        restaurantId: Int? = null,
        sessionId: String? = null
    ): Result<ChatResponse> {
        return try {
            println("📤 Enviando mensagem: $message")

            val request = ChatRequest(
                message = message,
                restaurantId = restaurantId,
                sessionId = sessionId
            )

            val response: ChatResponse = client.post(ApiConfig.ENDPOINT_CHAT_SALES) {
                contentType(ContentType.Application.Json)
                setBody(request)
            }.body()

            println("✅ Resposta da IA: ${response.response}")
            println("📦 Produtos encontrados: ${response.products.size}")

            Result.success(response)

        } catch (e: Exception) {
            println("❌ Erro ao enviar mensagem: ${e.message}")
            Result.failure(e)
        }
    }

    /**
     * Verifica se o servidor está pronto
     */
    suspend fun checkServerStatus(): Result<Boolean> {
        return try {
            val response: Map<String, Any> = client.get(ApiConfig.ENDPOINT_CHAT_STATUS).body()
            val isReady = response["status"] == "ready"

            println("🔍 Status do servidor: ${if (isReady) "PRONTO" else "NÃO PRONTO"}")

            Result.success(isReady)

        } catch (e: Exception) {
            println("❌ Erro ao verificar servidor: ${e.message}")
            Result.failure(e)
        }
    }
}

// ============================================================================
// 4. VIEWMODEL (commonMain/kotlin/presentation/chat/)
// ============================================================================

// ChatViewModel.kt
package presentation.chat

import data.models.ChatResponse
import data.models.Product
import data.repository.ChatRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class ChatViewModel {
    private val repository = ChatRepository()
    private val viewModelScope = CoroutineScope(Dispatchers.Main)

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private var sessionId: String? = null

    init {
        sessionId = generateSessionId()
        addWelcomeMessage()
    }

    private fun generateSessionId(): String {
        return "session_${System.currentTimeMillis()}"
    }

    private fun addWelcomeMessage() {
        val welcomeMessage = ChatMessage(
            id = generateMessageId(),
            text = "Olá! Como posso te ajudar hoje? 😊",
            isUser = false,
            timestamp = System.currentTimeMillis()
        )
        _messages.value = listOf(welcomeMessage)
    }

    fun sendMessage(text: String, restaurantId: Int? = null) {
        if (text.isBlank()) return

        // Adiciona mensagem do usuário
        val userMessage = ChatMessage(
            id = generateMessageId(),
            text = text,
            isUser = true,
            timestamp = System.currentTimeMillis()
        )
        _messages.value = _messages.value + userMessage

        // Mostra loading
        _isLoading.value = true

        // Envia para API
        viewModelScope.launch {
            repository.sendMessage(
                message = text,
                restaurantId = restaurantId,
                sessionId = sessionId
            ).fold(
                onSuccess = { response ->
                    handleSuccess(response)
                },
                onFailure = { error ->
                    handleError(error)
                }
            )
            _isLoading.value = false
        }
    }

    private fun handleSuccess(response: ChatResponse) {
        val aiMessage = ChatMessage(
            id = generateMessageId(),
            text = response.response,
            isUser = false,
            timestamp = System.currentTimeMillis(),
            products = response.products
        )
        _messages.value = _messages.value + aiMessage
    }

    private fun handleError(error: Throwable) {
        val errorMessage = ChatMessage(
            id = generateMessageId(),
            text = "Desculpe, ocorreu um erro. Tente novamente.",
            isUser = false,
            timestamp = System.currentTimeMillis(),
            isError = true
        )
        _messages.value = _messages.value + errorMessage
    }

    fun checkServerStatus() {
        viewModelScope.launch {
            repository.checkServerStatus()
        }
    }

    private fun generateMessageId(): String {
        return "msg_${System.currentTimeMillis()}_${(0..999).random()}"
    }
}

// ChatMessage.kt
package presentation.chat

import data.models.Product

data class ChatMessage(
    val id: String,
    val text: String,
    val isUser: Boolean,
    val timestamp: Long,
    val products: List<Product>? = null,
    val isError: Boolean = false
)

// ============================================================================
// 5. UI ANDROID COMPOSE (androidMain/kotlin/presentation/chat/)
// ============================================================================

// ChatScreen.kt
package presentation.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

@Composable
fun ChatScreen(
    viewModel: ChatViewModel = remember { ChatViewModel() },
    restaurantId: Int? = null
) {
    val messages by viewModel.messages.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val listState = rememberLazyListState()
    var messageText by remember { mutableStateOf("") }

    // Auto-scroll
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFF5F5F5))
    ) {
        // Lista de mensagens
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            items(messages, key = { it.id }) { message ->
                ChatBubble(message = message)
            }

            if (isLoading) {
                item {
                    LoadingIndicator()
                }
            }
        }

        // Input
        ChatInput(
            text = messageText,
            onTextChange = { messageText = it },
            onSend = {
                viewModel.sendMessage(messageText, restaurantId)
                messageText = ""
            },
            enabled = !isLoading
        )
    }
}

@Composable
fun ChatBubble(message: ChatMessage) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (message.isUser) {
            Alignment.End
        } else {
            Alignment.Start
        }
    ) {
        Surface(
            color = when {
                message.isUser -> MaterialTheme.colorScheme.primary
                message.isError -> MaterialTheme.colorScheme.error
                else -> Color(0xFFE0E0E0)
            },
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.widthIn(max = 280.dp)
        ) {
            Text(
                text = message.text,
                modifier = Modifier.padding(12.dp),
                color = if (message.isUser) Color.White else Color.Black
            )
        }

        // Produtos
        message.products?.let { products ->
            if (products.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                products.forEach { product ->
                    ProductCard(product = product)
                }
            }
        }
    }
}

@Composable
fun ProductCard(product: Product) {
    Card(
        modifier = Modifier
            .widthIn(max = 300.dp)
            .padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = product.name,
                    style = MaterialTheme.typography.titleSmall
                )
                product.description?.let {
                    Text(
                        text = it,
                        style = MaterialTheme.typography.bodySmall,
                        color = Color.Gray,
                        maxLines = 2
                    )
                }
            }
            Text(
                text = "R$ ${String.format("%.2f", product.price)}",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.primary
            )
        }
    }
}

@Composable
fun ChatInput(
    text: String,
    onTextChange: (String) -> Unit,
    onSend: () -> Unit,
    enabled: Boolean
) {
    Surface(shadowElevation = 4.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = onTextChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("Digite sua mensagem...") },
                maxLines = 3,
                enabled = enabled
            )

            Spacer(modifier = Modifier.width(8.dp))

            IconButton(
                onClick = { if (text.isNotBlank()) onSend() },
                enabled = enabled && text.isNotBlank()
            ) {
                Icon(
                    imageVector = Icons.Default.Send,
                    contentDescription = "Enviar",
                    tint = MaterialTheme.colorScheme.primary
                )
            }
        }
    }
}

@Composable
fun LoadingIndicator() {
    Row(
        modifier = Modifier.padding(start = 16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(20.dp),
            strokeWidth = 2.dp
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text("IA está pensando...")
    }
}

// ============================================================================
// 6. EXEMPLO DE USO
// ============================================================================

// Em alguma Activity ou tela:
/*
@Composable
fun RestaurantDetailScreen(restaurantId: Int) {
    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Restaurante") })
        }
    ) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            // ... outras informações do restaurante

            // Chat com IA
            ChatScreen(restaurantId = restaurantId)
        }
    }
}
*/

// ============================================================================
// 7. TESTE RÁPIDO
// ============================================================================

// Para testar rapidamente, adicione um botão:
/*
Button(onClick = {
    val viewModel = ChatViewModel()
    viewModel.checkServerStatus()
}) {
    Text("Testar Servidor")
}

Button(onClick = {
    val viewModel = ChatViewModel()
    viewModel.sendMessage("Quero uma pizza", restaurantId = 1)
}) {
    Text("Testar Chat")
}
*/


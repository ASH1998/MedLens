package com.medlens.android.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.util.UUID

private val Context.dataStore by preferencesDataStore(name = "medlens_android")
private val CONVERSATIONS_KEY = stringPreferencesKey("conversations")
private val ACTIVE_ID_KEY = stringPreferencesKey("active_conversation_id")
private val LITERT_BACKEND_KEY = stringPreferencesKey("litert_backend")

enum class LiteRtBackendPref { CPU, GPU }

@Serializable
data class UiMessage(
    val id: String,
    val role: String,
    val content: String,
    val pending: Boolean = false,
    val imagePath: String? = null,
    val imagePaths: List<String> = emptyList(),
)

@Serializable
data class PersistedConversation(
    val id: String,
    val title: String,
    val messages: List<UiMessage>,
    val medications: List<String>,
    val updatedAt: Long,
)

@Serializable
data class PersistedState(
    val conversations: List<PersistedConversation>,
    val activeId: String,
)

class ConversationStore(
    context: Context,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private val dataStore = context.dataStore

    val state: Flow<PersistedState> = dataStore.data.map { prefs ->
        val raw = prefs[CONVERSATIONS_KEY]
        val conversations = runCatching {
            if (raw.isNullOrBlank()) listOf(newConversation()) else json.decodeFromString<List<PersistedConversation>>(raw)
        }.getOrElse { listOf(newConversation()) }
        val safeConversations = if (conversations.isEmpty()) listOf(newConversation()) else conversations
        val activeId = prefs[ACTIVE_ID_KEY] ?: safeConversations.first().id
        PersistedState(
            conversations = safeConversations,
            activeId = activeId.ifBlank { safeConversations.first().id },
        )
    }

    suspend fun save(conversations: List<PersistedConversation>, activeId: String) {
        dataStore.edit { prefs ->
            prefs[CONVERSATIONS_KEY] = json.encodeToString(conversations.take(30))
            prefs[ACTIVE_ID_KEY] = activeId
        }
    }

    val backendPref: Flow<LiteRtBackendPref> = dataStore.data.map { prefs ->
        when (prefs[LITERT_BACKEND_KEY]) {
            "GPU" -> LiteRtBackendPref.GPU
            else -> LiteRtBackendPref.CPU
        }
    }

    suspend fun saveBackendPref(pref: LiteRtBackendPref) {
        dataStore.edit { prefs ->
            prefs[LITERT_BACKEND_KEY] = pref.name
        }
    }

    companion object {
        fun newConversation(): PersistedConversation {
            val now = System.currentTimeMillis()
            return PersistedConversation(
                id = UUID.randomUUID().toString(),
                title = "New chat",
                messages = emptyList(),
                medications = emptyList(),
                updatedAt = now,
            )
        }

        fun titleFromMessage(message: String): String {
            val compact = message.replace(Regex("\\s+"), " ").trim()
            return when {
                compact.isBlank() -> "New chat"
                compact.length > 36 -> compact.take(35) + "..."
                else -> compact
            }
        }
    }
}

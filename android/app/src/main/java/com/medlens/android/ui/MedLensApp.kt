package com.medlens.android.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Medication
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.medlens.android.data.PersistedConversation
import com.medlens.android.model.GEMMA_4_E4B_DESCRIPTOR
import com.medlens.android.model.ModelState

@Composable
fun MedLensApp(viewModel: MedLensViewModel) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    when (val setup = state.setupStage) {
        SetupStage.Checking, SetupStage.Copying -> FirstRunScreen(
            message = if (setup == SetupStage.Copying) {
                "Copying bundled SQLite artifacts into app storage..."
            } else {
                "Checking local data..."
            },
        )
        is SetupStage.Error -> FirstRunScreen(message = setup.message)
        SetupStage.Ready -> ChatShell(state = state, viewModel = viewModel)
    }
}

@Composable
private fun FirstRunScreen(message: String) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceContainerLowest),
        contentAlignment = Alignment.Center,
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp),
            shape = RoundedCornerShape(16.dp),
        ) {
            Column(modifier = Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("MedLens", style = MaterialTheme.typography.labelLarge)
                Text("Preparing local safety data", style = MaterialTheme.typography.headlineSmall)
                Text(message, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatShell(
    state: MedLensUiState,
    viewModel: MedLensViewModel,
) {
    var draft by rememberSaveable { mutableStateOf("") }
    var settingsOpen by remember { mutableStateOf(false) }

    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val compact = maxWidth < 720.dp
        var sidebarOpen by rememberSaveable { mutableStateOf(!compact) }

        LaunchedEffect(compact) {
            sidebarOpen = !compact
        }

        Row(modifier = Modifier.fillMaxSize()) {
            if (sidebarOpen) {
                Sidebar(
                    conversations = state.conversations,
                    activeId = state.activeConversationId,
                    modifier = if (compact) Modifier.fillMaxSize() else Modifier.width(280.dp).fillMaxHeight(),
                    onSelect = {
                        viewModel.selectConversation(it)
                        if (compact) sidebarOpen = false
                    },
                    onNew = viewModel::createConversation,
                    onDelete = viewModel::deleteConversation,
                    onToggle = { sidebarOpen = false },
                )
            }

            if (!compact || !sidebarOpen) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(MaterialTheme.colorScheme.surface),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 20.dp, vertical = 16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (!sidebarOpen) {
                                IconButton(onClick = { sidebarOpen = true }) {
                                    Icon(Icons.Outlined.Menu, contentDescription = "Show conversations")
                                }
                                Spacer(modifier = Modifier.width(4.dp))
                            }
                            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Text("MedLens", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                                Text(
                                    "${state.providerLabel} · ${state.modelState.toLabel()} · ${activeConversation(state)?.medications?.size ?: 0} meds in session",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        IconButton(onClick = { settingsOpen = true }) {
                            Icon(Icons.Outlined.Settings, contentDescription = "Settings")
                        }
                    }

                    Divider()

                    LazyColumn(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .padding(horizontal = 20.dp, vertical = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        item {
                            ModelStatusCard(
                                modelState = state.modelState,
                                onDownload = viewModel::enqueueModelDownload,
                            )
                        }
                        items(activeConversation(state)?.messages ?: emptyList(), key = { it.id }) { message ->
                            MessageBubble(role = message.role, content = message.content, pending = message.pending)
                        }
                        if (state.trace.isNotEmpty()) {
                            item {
                                ToolTraceCard(traceLines = state.trace.map { "${it.name} · ${it.duration_ms ?: 0} ms" })
                            }
                        }
                    }

                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        val meds = activeConversation(state)?.medications.orEmpty()
                        if (meds.isNotEmpty()) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                meds.take(4).forEach { med ->
                                    AssistChip(onClick = {}, label = { Text(med) })
                                }
                            }
                        }
                        Row(verticalAlignment = Alignment.Bottom) {
                            IconButton(onClick = viewModel::acknowledgeOcrScaffold) {
                                Icon(Icons.Outlined.Search, contentDescription = "Scan")
                            }
                            Spacer(modifier = Modifier.width(8.dp))
                            OutlinedTextField(
                                value = draft,
                                onValueChange = { draft = it },
                                modifier = Modifier.weight(1f),
                                placeholder = { Text("Ask about medications...") },
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            IconButton(
                                onClick = {
                                    val text = draft
                                    draft = ""
                                    viewModel.sendMessage(text)
                                },
                                enabled = !state.busy && draft.isNotBlank(),
                            ) {
                                Icon(Icons.Outlined.Send, contentDescription = "Send")
                            }
                        }
                    }
                }
            }
        }

        if (settingsOpen) {
            ModalBottomSheet(onDismissRequest = { settingsOpen = false }) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text("Settings", style = MaterialTheme.typography.titleLarge)
                    Text("Model repo: ${GEMMA_4_E4B_DESCRIPTOR.repoId}", style = MaterialTheme.typography.bodyMedium)
                    Text("Model file: ${GEMMA_4_E4B_DESCRIPTOR.fileName}", style = MaterialTheme.typography.bodyMedium)
                    Text("Model state: ${state.modelState.toLabel()}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (state.modelState is ModelState.Error) {
                        Text(
                            state.modelState.message,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                    Button(onClick = viewModel::enqueueModelDownload, enabled = state.modelState !is ModelState.Downloading) {
                        Text("Download Gemma Model")
                    }
                    Text(
                        "Bundled artifacts: normalization.sqlite + evidence.mobile.sqlite from data/artifacts.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        "The LiteRT-LM model manager is wired for download and local storage. Inference integration still needs Android SDK validation on a real build machine.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                }
            }
        }
    }
}

@Composable
private fun Sidebar(
    conversations: List<PersistedConversation>,
    activeId: String,
    modifier: Modifier,
    onSelect: (String) -> Unit,
    onNew: () -> Unit,
    onDelete: (String) -> Unit,
    onToggle: () -> Unit,
) {
    Column(
        modifier = modifier
            .background(MaterialTheme.colorScheme.surfaceContainerLowest)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onToggle) {
                    Icon(Icons.Outlined.Menu, contentDescription = "Hide conversations")
                }
                Spacer(modifier = Modifier.width(8.dp))
                Text("Conversations", style = MaterialTheme.typography.titleMedium)
            }
            IconButton(onClick = onNew) {
                Icon(Icons.Outlined.Add, contentDescription = "New")
            }
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(conversations, key = { it.id }) { conversation ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onSelect(conversation.id) },
                    colors = CardDefaults.cardColors(
                        containerColor = if (conversation.id == activeId) {
                            MaterialTheme.colorScheme.secondaryContainer
                        } else {
                            MaterialTheme.colorScheme.surface
                        },
                    ),
                ) {
                    Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            conversation.title,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleSmall,
                        )
                        Text(
                            "${conversation.medications.size} meds · ${conversation.messages.size} messages",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        TextButton(
                            onClick = { onDelete(conversation.id) },
                            modifier = Modifier.align(Alignment.End),
                        ) {
                            Text("Delete")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ModelStatusCard(
    modelState: ModelState,
    onDownload: () -> Unit,
) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow)) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Gemma model", style = MaterialTheme.typography.titleSmall)
            Text(modelState.toLabel(), style = MaterialTheme.typography.bodyMedium)
            if (modelState is ModelState.Error) {
                Text(
                    modelState.message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (modelState is ModelState.Downloading) {
                LinearProgressIndicator(
                    progress = { modelState.progress },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "${(modelState.progress * 100).toInt()}%",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (modelState !is ModelState.Ready) {
                Button(onClick = onDownload, enabled = modelState !is ModelState.Downloading) {
                    Text(if (modelState is ModelState.Error) "Retry Download" else "Download Gemma Model")
                }
            }
        }
    }
}

@Composable
private fun MessageBubble(
    role: String,
    content: String,
    pending: Boolean,
) {
    val isUser = role == "user"
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(0.82f),
            colors = CardDefaults.cardColors(
                containerColor = if (isUser) {
                    MaterialTheme.colorScheme.primaryContainer
                } else {
                    MaterialTheme.colorScheme.surfaceContainerHigh
                },
            ),
        ) {
            Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(
                        Icons.Outlined.Medication,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Text(if (isUser) "You" else "MedLens", style = MaterialTheme.typography.labelMedium)
                }
                Text(
                    if (pending && content.isBlank()) "Thinking..." else content,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun ToolTraceCard(traceLines: List<String>) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow)) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Tool Trace", style = MaterialTheme.typography.titleSmall)
            traceLines.forEach { line ->
                Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun activeConversation(state: MedLensUiState): PersistedConversation? =
    state.conversations.firstOrNull { it.id == state.activeConversationId }

private fun ModelState.toLabel(): String = when (this) {
    ModelState.NotDownloaded -> "model not downloaded"
    is ModelState.Downloading -> "downloading model"
    is ModelState.Ready -> "model ready"
    is ModelState.Error -> "model error"
}

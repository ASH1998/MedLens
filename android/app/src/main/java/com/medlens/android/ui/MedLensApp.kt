package com.medlens.android.ui

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Medication
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.medlens.android.data.LiteRtBackendPref
import com.medlens.android.data.PersistedConversation
import com.medlens.android.model.GEMMA_4_E4B_DESCRIPTOR
import com.medlens.android.model.ModelState
import java.io.File

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
            .background(MedLensBackgroundBrush),
        contentAlignment = Alignment.Center,
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp),
            shape = RoundedCornerShape(8.dp),
            colors = CardDefaults.cardColors(containerColor = Color.White.copy(alpha = 0.92f)),
            elevation = CardDefaults.cardElevation(defaultElevation = 8.dp),
        ) {
            Column(modifier = Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                MedLensBrandHeader(logoSize = 42.dp, titleStyle = MaterialTheme.typography.titleLarge)
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
    var pendingImagePaths by rememberSaveable { mutableStateOf(emptyList<String>()) }
    var settingsOpen by remember { mutableStateOf(false) }
    var cameraOpen by rememberSaveable { mutableStateOf(false) }

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
                        .background(MedLensBackgroundBrush),
                ) {
                    MedLensTopBar(
                        state = state,
                        showMenu = !sidebarOpen,
                        onMenu = { sidebarOpen = true },
                        onSettings = { settingsOpen = true },
                    )

                    LazyColumn(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .padding(horizontal = 18.dp, vertical = 14.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        if (state.modelState !is ModelState.Ready) {
                            item {
                                ModelStatusCard(
                                    modelState = state.modelState,
                                    onDownload = viewModel::enqueueModelDownload,
                                )
                            }
                        }
                        items(activeConversation(state)?.messages ?: emptyList(), key = { it.id }) { message ->
                            MessageBubble(
                                role = message.role,
                                content = message.content,
                                pending = message.pending,
                                imagePath = message.imagePath,
                                imagePaths = message.imagePaths,
                            )
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
                            .background(Color.White.copy(alpha = 0.88f))
                            .padding(horizontal = 18.dp, vertical = 12.dp)
                            .navigationBarsPadding()
                            .imePadding(),
                    ) {
                        if (pendingImagePaths.isNotEmpty()) {
                            PendingImageAttachments(
                                imagePaths = pendingImagePaths,
                                onRemove = {
                                    runCatching { File(it).delete() }
                                    pendingImagePaths = pendingImagePaths.filterNot { path -> path == it }
                                },
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                        }
                        Row(verticalAlignment = Alignment.Bottom) {
                            val canSend = !state.busy &&
                                state.modelState is ModelState.Ready &&
                                (draft.isNotBlank() || pendingImagePaths.isNotEmpty())
                            IconButton(
                                onClick = { cameraOpen = true },
                                enabled = !state.busy && state.modelState is ModelState.Ready,
                                modifier = Modifier
                                    .size(48.dp)
                                    .clip(CircleShape)
                                    .background(MedLensMint),
                            ) {
                                Icon(Icons.Outlined.PhotoCamera, contentDescription = "Use camera", tint = MedLensNavy)
                            }
                            Spacer(modifier = Modifier.width(8.dp))
                            OutlinedTextField(
                                value = draft,
                                onValueChange = { draft = it },
                                modifier = Modifier.weight(1f),
                                placeholder = { Text(if (state.modelState is ModelState.Ready) "Ask about medications..." else "Download Gemma to chat") },
                                shape = RoundedCornerShape(8.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedBorderColor = MedLensTeal,
                                    unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.65f),
                                    focusedContainerColor = Color.White,
                                    unfocusedContainerColor = Color.White,
                                ),
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            IconButton(
                                onClick = {
                                    val text = draft
                                    draft = ""
                                    val imagePaths = pendingImagePaths
                                    pendingImagePaths = emptyList()
                                    if (imagePaths.isNotEmpty()) {
                                        viewModel.sendImageMessage(imagePaths, text.trim())
                                    } else {
                                        viewModel.sendMessage(text)
                                    }
                                },
                                enabled = canSend,
                                modifier = Modifier
                                    .size(48.dp)
                                    .clip(CircleShape)
                                    .background(if (canSend) MedLensTeal else MaterialTheme.colorScheme.surfaceVariant),
                            ) {
                                Icon(
                                    Icons.AutoMirrored.Outlined.Send,
                                    contentDescription = "Send",
                                    tint = if (canSend) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            "MedLens is not a replacement for advice from a doctor or pharmacist.",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 56.dp, end = 48.dp),
                        )
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
                    HorizontalDivider()
                    Text("How technical?", style = MaterialTheme.typography.titleSmall)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = state.audienceStyle == AudienceStyle.Regular,
                            onClick = { viewModel.setAudienceStyle(AudienceStyle.Regular) },
                        )
                        Text("Regular person")
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = state.audienceStyle == AudienceStyle.Clinician,
                            onClick = { viewModel.setAudienceStyle(AudienceStyle.Clinician) },
                        )
                        Text("Doctor / clinician")
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = state.audienceStyle == AudienceStyle.Simple,
                            onClick = { viewModel.setAudienceStyle(AudienceStyle.Simple) },
                        )
                        Text("Simple language")
                    }
                    HorizontalDivider()
                    Text("LiteRT-LM backend", style = MaterialTheme.typography.titleSmall)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = state.backendPref == LiteRtBackendPref.CPU,
                            onClick = { viewModel.setBackendPref(LiteRtBackendPref.CPU) },
                        )
                        Text("CPU")
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(
                            selected = state.backendPref == LiteRtBackendPref.GPU,
                            onClick = { viewModel.setBackendPref(LiteRtBackendPref.GPU) },
                        )
                        Text("GPU")
                    }
                    Spacer(modifier = Modifier.height(12.dp))
                }
            }
        }

        if (cameraOpen) {
            CameraCaptureScreen(
                onDismiss = { cameraOpen = false },
                onUsePhoto = { imagePath ->
                    if (pendingImagePaths.size >= MAX_PENDING_IMAGES) {
                        runCatching { File(imagePath).delete() }
                    } else {
                        pendingImagePaths = pendingImagePaths + imagePath
                    }
                    cameraOpen = false
                },
            )
        }
    }
}

@Composable
private fun MedLensTopBar(
    state: MedLensUiState,
    showMenu: Boolean,
    onMenu: () -> Unit,
    onSettings: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .background(Color.White.copy(alpha = 0.86f))
            .padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
            if (showMenu) {
                IconButton(onClick = onMenu) {
                    Icon(Icons.Outlined.Menu, contentDescription = "Show conversations", tint = MedLensNavy)
                }
                Spacer(modifier = Modifier.width(4.dp))
            }
            MedLensBrandHeader(logoSize = 38.dp, titleStyle = MaterialTheme.typography.headlineSmall)
            Spacer(modifier = Modifier.width(12.dp))
            Text(
                "${state.modelState.toLabel()} · ${activeConversation(state)?.medications?.size ?: 0} meds",
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
        }
        IconButton(
            onClick = onSettings,
            modifier = Modifier
                .size(44.dp)
                .clip(CircleShape)
                .background(MedLensMint),
        ) {
            Icon(Icons.Outlined.Settings, contentDescription = "Settings", tint = MedLensNavy)
        }
    }
}

@Composable
private fun MedLensBrandHeader(
    logoSize: Dp,
    titleStyle: TextStyle,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        MedLensLogo(size = logoSize)
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            buildAnnotatedString {
                pushStyle(SpanStyle(color = MedLensNavy, fontWeight = FontWeight.Bold))
                append("Med")
                pop()
                pushStyle(SpanStyle(color = MedLensTeal, fontWeight = FontWeight.Bold))
                append("Lens")
                pop()
            },
            style = titleStyle,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun MedLensLogo(size: Dp) {
    val context = LocalContext.current
    val logo = remember(context) {
        runCatching {
            context.assets.open("logo.png").use { stream ->
                BitmapFactory.decodeStream(stream)?.asImageBitmap()
            }
        }.getOrNull()
    }
    if (logo != null) {
        Image(
            bitmap = logo,
            contentDescription = "MedLens logo",
            modifier = Modifier
                .size(size)
                .clip(RoundedCornerShape(8.dp)),
            contentScale = ContentScale.Crop,
        )
    } else {
        Box(
            modifier = Modifier
                .size(size)
                .clip(RoundedCornerShape(8.dp))
                .background(MedLensMint),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Outlined.Medication,
                contentDescription = "MedLens logo",
                tint = MedLensTeal,
                modifier = Modifier.size(size * 0.58f),
            )
        }
    }
}

@Composable
private fun PendingImageAttachments(
    imagePaths: List<String>,
    onRemove: (String) -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color.White.copy(alpha = 0.94f)),
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MedLensTeal.copy(alpha = 0.12f)),
    ) {
        Column(modifier = Modifier.padding(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    if (imagePaths.size == 1) "Image attached" else "${imagePaths.size} images attached",
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    "${imagePaths.size}/$MAX_PENDING_IMAGES",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                imagePaths.forEach { imagePath ->
                    PendingImageThumb(imagePath = imagePath, onRemove = { onRemove(imagePath) })
                }
            }
        }
    }
}

@Composable
private fun PendingImageThumb(
    imagePath: String,
    onRemove: () -> Unit,
) {
    val bitmap = remember(imagePath) { BitmapFactory.decodeFile(imagePath)?.asImageBitmap() }
    Box {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = "Attached medicine image",
                modifier = Modifier
                    .size(56.dp)
                    .clip(RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Crop,
            )
        } else {
            Icon(Icons.Outlined.PhotoCamera, contentDescription = null, modifier = Modifier.size(48.dp))
        }
        IconButton(
            onClick = onRemove,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .size(24.dp),
        ) {
            Icon(Icons.Outlined.Close, contentDescription = "Remove image", modifier = Modifier.size(16.dp))
        }
    }
}

@Composable
private fun CameraCaptureScreen(
    onDismiss: () -> Unit,
    onUsePhoto: (String) -> Unit,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var hasPermission by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    var imageCapture by remember { mutableStateOf<ImageCapture?>(null) }
    var capturedPath by rememberSaveable { mutableStateOf<String?>(null) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        hasPermission = granted
        if (!granted) error = "Camera permission is required to scan medicine images."
    }

    LaunchedEffect(Unit) {
        if (!hasPermission) permissionLauncher.launch(Manifest.permission.CAMERA)
    }

    DisposableEffect(Unit) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        onDispose {
            cameraProviderFuture.addListener(
                { runCatching { cameraProviderFuture.get().unbindAll() } },
                ContextCompat.getMainExecutor(context),
            )
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface),
    ) {
        if (hasPermission) {
            val captured = capturedPath
            if (captured == null) {
                AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { viewContext ->
                        PreviewView(viewContext).also { previewView ->
                            val cameraProviderFuture = ProcessCameraProvider.getInstance(viewContext)
                            cameraProviderFuture.addListener(
                                {
                                    val cameraProvider = cameraProviderFuture.get()
                                    val preview = Preview.Builder().build().also {
                                        it.setSurfaceProvider(previewView.surfaceProvider)
                                    }
                                    val capture = ImageCapture.Builder()
                                        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                                        .build()
                                    imageCapture = capture
                                    cameraProvider.unbindAll()
                                    cameraProvider.bindToLifecycle(
                                        lifecycleOwner,
                                        CameraSelector.DEFAULT_BACK_CAMERA,
                                        preview,
                                        capture,
                                    )
                                },
                                ContextCompat.getMainExecutor(viewContext),
                            )
                        }
                    },
                )
            } else {
                val bitmap = remember(captured) { BitmapFactory.decodeFile(captured)?.asImageBitmap() }
                if (bitmap != null) {
                    Image(
                        bitmap = bitmap,
                        contentDescription = "Captured medicine image",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                    )
                }
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp)
                .align(Alignment.TopCenter),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = {
                    capturedPath?.let { runCatching { File(it).delete() } }
                    onDismiss()
                },
            ) {
                Icon(Icons.Outlined.Close, contentDescription = "Close camera")
            }
            Text("Medicine camera", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.size(48.dp))
        }

        error?.let {
            Card(
                modifier = Modifier
                    .align(Alignment.Center)
                    .padding(24.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
            ) {
                Text(it, modifier = Modifier.padding(18.dp), style = MaterialTheme.typography.bodyMedium)
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp)
                .align(Alignment.BottomCenter),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val captured = capturedPath
            if (captured == null) {
                Button(
                    onClick = {
                        val capture = imageCapture ?: return@Button
                        val file = File(context.cacheDir, "medlens-camera-${System.currentTimeMillis()}.jpg")
                        val output = ImageCapture.OutputFileOptions.Builder(file).build()
                        capture.takePicture(
                            output,
                            ContextCompat.getMainExecutor(context),
                            object : ImageCapture.OnImageSavedCallback {
                                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                                    capturedPath = file.absolutePath
                                    error = null
                                }

                                override fun onError(exception: ImageCaptureException) {
                                    error = exception.message ?: "Could not capture image."
                                }
                            },
                        )
                    },
                    enabled = hasPermission,
                ) {
                    Icon(Icons.Outlined.PhotoCamera, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Capture")
                }
            } else {
                Button(
                    onClick = {
                        runCatching { File(captured).delete() }
                        capturedPath = null
                    },
                ) {
                    Icon(Icons.Outlined.Refresh, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Retake")
                }
                Spacer(modifier = Modifier.width(12.dp))
                Button(onClick = { onUsePhoto(captured) }) {
                    Icon(Icons.Outlined.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Use photo")
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
            .background(MedLensSidebarBrush)
            .statusBarsPadding()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                IconButton(onClick = onToggle) {
                    Icon(Icons.Outlined.Menu, contentDescription = "Hide conversations", tint = MedLensNavy)
                }
                Spacer(modifier = Modifier.width(4.dp))
                MedLensBrandHeader(logoSize = 32.dp, titleStyle = MaterialTheme.typography.titleLarge)
            }
            IconButton(onClick = onNew) {
                Icon(Icons.Outlined.Add, contentDescription = "New", tint = MedLensNavy)
            }
        }
        Button(
            onClick = onNew,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = MedLensPurple),
            elevation = ButtonDefaults.buttonElevation(defaultElevation = 2.dp),
        ) {
            Icon(Icons.Outlined.Add, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(modifier = Modifier.width(8.dp))
            Text("New chat")
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(conversations, key = { it.id }) { conversation ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onSelect(conversation.id) },
                    shape = RoundedCornerShape(8.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = if (conversation.id == activeId) {
                            Color(0xFFEDE6FF)
                        } else {
                            Color.White.copy(alpha = 0.88f)
                        },
                    ),
                    border = BorderStroke(
                        1.dp,
                        if (conversation.id == activeId) MedLensPurple.copy(alpha = 0.30f) else Color.White.copy(alpha = 0.70f),
                    ),
                    elevation = CardDefaults.cardElevation(defaultElevation = if (conversation.id == activeId) 4.dp else 1.dp),
                ) {
                    Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                        Text(
                            conversation.title,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleSmall,
                            color = MedLensNavy,
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
                            Text("Delete", color = MedLensPurple)
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
    Card(
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White.copy(alpha = 0.94f)),
        border = BorderStroke(1.dp, MedLensTeal.copy(alpha = 0.14f)),
        elevation = CardDefaults.cardElevation(defaultElevation = 3.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Gemma model", style = MaterialTheme.typography.titleSmall, color = MedLensNavy)
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
                Button(
                    onClick = onDownload,
                    enabled = modelState !is ModelState.Downloading,
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = MedLensTeal),
                ) {
                    Text(if (modelState is ModelState.Error) "Retry Download" else "Download Gemma Model")
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MessageBubble(
    role: String,
    content: String,
    pending: Boolean,
    imagePath: String?,
    imagePaths: List<String>,
) {
    val isUser = role == "user"
    val uriHandler = LocalUriHandler.current
    val messageText = if (pending && content.isBlank()) "Thinking..." else content
    val parsed = remember(messageText, isUser) {
        if (isUser) ParsedAssistantMessage(body = messageText, sources = emptyList()) else parseAssistantMessage(messageText)
    }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(0.84f),
            shape = RoundedCornerShape(
                topStart = 8.dp,
                topEnd = 8.dp,
                bottomStart = if (isUser) 8.dp else 2.dp,
                bottomEnd = if (isUser) 2.dp else 8.dp,
            ),
            colors = CardDefaults.cardColors(
                containerColor = if (isUser) {
                    Color(0xFFE5D9FF)
                } else {
                    Color.White.copy(alpha = 0.95f)
                },
            ),
            border = BorderStroke(
                1.dp,
                if (isUser) MedLensPurple.copy(alpha = 0.12f) else MedLensTeal.copy(alpha = 0.12f),
            ),
            elevation = CardDefaults.cardElevation(defaultElevation = if (isUser) 0.dp else 2.dp),
        ) {
            Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (isUser) {
                        Icon(
                            Icons.Outlined.Medication,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp),
                            tint = MedLensPurple,
                        )
                    } else {
                        MedLensLogo(size = 18.dp)
                    }
                    Text(
                        if (isUser) "You" else "MedLens",
                        style = MaterialTheme.typography.labelMedium,
                        color = if (isUser) MaterialTheme.colorScheme.onSecondaryContainer else MedLensNavy,
                    )
                }
                val displayImagePaths = imagePaths.ifEmpty { listOfNotNull(imagePath) }
                if (isUser && displayImagePaths.isNotEmpty()) {
                    MessageImageThumbnails(imagePaths = displayImagePaths)
                }
                if (parsed.body.isNotBlank()) {
                    Text(
                        markdownBoldText(parsed.body),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
                if (!isUser && parsed.sources.isNotEmpty()) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        Text("Sources:", style = MaterialTheme.typography.labelMedium)
                        parsed.sources.forEach { source ->
                            SourceReferenceText(source = source, onClick = { uriHandler.openUri(source.url) })
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MessageImageThumbnails(imagePaths: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        imagePaths.take(MAX_PENDING_IMAGES).chunked(2).forEach { rowImages ->
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                rowImages.forEach { imagePath ->
                    MessageImageTile(
                        imagePath = imagePath,
                        modifier = Modifier.weight(1f),
                    )
                }
                if (rowImages.size == 1) {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun MessageImageTile(
    imagePath: String,
    modifier: Modifier = Modifier,
) {
    val bitmap = remember(imagePath) { BitmapFactory.decodeFile(imagePath)?.asImageBitmap() }
    if (bitmap == null) {
        Row(
            modifier = modifier
                .height(118.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(MedLensMint)
                .padding(10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(Icons.Outlined.PhotoCamera, contentDescription = null, modifier = Modifier.size(18.dp), tint = MedLensNavy)
            Text("Image attached", style = MaterialTheme.typography.bodySmall, color = MedLensNavy)
        }
    } else {
        Image(
            bitmap = bitmap,
            contentDescription = "Attached medicine image",
            modifier = modifier
                .height(118.dp)
                .clip(RoundedCornerShape(8.dp)),
            contentScale = ContentScale.Crop,
        )
    }
}

@Composable
private fun SourceReferenceText(
    source: SourceReference,
    onClick: () -> Unit,
) {
    Text(
        text = source.label,
        modifier = Modifier
            .clickable(onClick = onClick)
            .clip(RoundedCornerShape(8.dp))
            .background(MedLensMint)
            .padding(horizontal = 8.dp, vertical = 3.dp),
        style = MaterialTheme.typography.bodyMedium,
        color = MedLensNavy,
        fontWeight = FontWeight.SemiBold,
    )
}

@Composable
private fun ToolTraceCard(traceLines: List<String>) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            modifier = Modifier
                .clickable { expanded = !expanded }
                .padding(horizontal = 2.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                "Tool trace",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "${traceLines.size}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Icon(
                if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                contentDescription = if (expanded) "Hide tool trace" else "Show tool trace",
                modifier = Modifier.size(16.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (expanded) {
            Column(
                modifier = Modifier.padding(start = 2.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                traceLines.forEach { line ->
                    Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
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

private data class ParsedAssistantMessage(
    val body: String,
    val sources: List<SourceReference>,
)

private data class SourceReference(
    val label: String,
    val url: String,
)

private fun parseAssistantMessage(content: String): ParsedAssistantMessage {
    val lines = content.lines()
    val sourceStart = lines.indexOfFirst { line ->
        line.trim().replace("*", "").equals("Sources:", ignoreCase = true) ||
            line.trim().replace("*", "").startsWith("Sources:", ignoreCase = true)
    }
    if (sourceStart < 0) return ParsedAssistantMessage(content, emptyList())

    val sourceText = lines.drop(sourceStart).joinToString("\n")
    val urls = URL_REGEX.findAll(sourceText)
        .map { match -> match.value.trimEnd('.', ',', ';', ')') }
        .distinct()
        .take(9)
        .mapIndexed { index, url -> SourceReference(label = "[${index + 1}]", url = url) }
        .toList()
    if (urls.isEmpty()) return ParsedAssistantMessage(content, emptyList())

    val body = lines.take(sourceStart).joinToString("\n").trimEnd()
    return ParsedAssistantMessage(body = body, sources = urls)
}

private fun markdownBoldText(value: String): AnnotatedString = buildAnnotatedString {
    var index = 0
    while (index < value.length) {
        val start = value.indexOf("**", index)
        if (start < 0) {
            append(value.substring(index))
            break
        }
        append(value.substring(index, start))
        val end = value.indexOf("**", start + 2)
        if (end < 0) {
            append(value.substring(start))
            break
        }
        pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
        append(value.substring(start + 2, end))
        pop()
        index = end + 2
    }
}

private val MedLensNavy = Color(0xFF071F49)
private val MedLensTeal = Color(0xFF009F8B)
private val MedLensPurple = Color(0xFF7052B7)
private val MedLensMint = Color(0xFFE4F7F1)
private val MedLensBackgroundBrush = Brush.verticalGradient(
    colors = listOf(
        Color(0xFFFFFBFF),
        Color(0xFFF7FFFC),
        Color(0xFFFFF8FD),
    ),
)
private val MedLensSidebarBrush = Brush.verticalGradient(
    colors = listOf(
        Color(0xFFFAFDFF),
        Color(0xFFF3FFF9),
        Color(0xFFFFF7FE),
    ),
)
private val URL_REGEX = Regex("""https?://[^\s)]+""")
private const val MAX_PENDING_IMAGES = 3

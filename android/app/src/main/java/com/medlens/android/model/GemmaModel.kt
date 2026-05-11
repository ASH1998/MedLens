package com.medlens.android.model

data class GemmaModelDescriptor(
    val repoId: String,
    val fileName: String,
    val downloadUrl: String,
    val sha256: String,
    val sizeBytes: Long,
)

val GEMMA_4_E4B_DESCRIPTOR = GemmaModelDescriptor(
    repoId = "litert-community/gemma-4-E4B-it-litert-lm",
    fileName = "gemma-4-E4B-it.litertlm",
    downloadUrl = "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm?download=true",
    sha256 = "0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0",
    sizeBytes = 3659530240L,
)

sealed interface ModelState {
    data object NotDownloaded : ModelState
    data class Downloading(val progress: Float) : ModelState
    data class Ready(val path: String) : ModelState
    data class Error(val message: String) : ModelState
}

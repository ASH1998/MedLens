package com.medlens.android.ocr

import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.tasks.await

class MlKitOcrManager {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    suspend fun recognizeCandidates(image: InputImage): List<String> {
        val text = recognizer.process(image).await().text
        return extractCandidates(text)
    }

    fun extractCandidates(text: String): List<String> {
        return text.lines()
            .map { it.replace(Regex("[^A-Za-z0-9 /+-]"), " ").trim() }
            .filter { it.length >= 3 }
            .distinct()
            .take(12)
    }
}

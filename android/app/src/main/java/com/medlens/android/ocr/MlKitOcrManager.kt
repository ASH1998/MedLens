package com.medlens.android.ocr

import android.content.Context
import android.net.Uri
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.tasks.await
import java.io.File

/**
 * ML Kit OCR wrapper. Runs on-device text recognition to extract candidate
 * medicine names from images. This is the first-pass OCR — deterministic,
 * offline, no model download required.
 *
 * Use Gemma vision only as a fallback when ML Kit produces no useful candidates.
 */
class MlKitOcrManager {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    /**
     * Recognize text from an [InputImage] and extract candidate medicine names.
     */
    suspend fun recognizeCandidates(image: InputImage): List<String> {
        val text = recognizer.process(image).await().text
        return extractCandidates(text)
    }

    /**
     * Recognize text from a file path and extract candidate medicine names.
     * Returns empty list if the file cannot be loaded.
     */
    suspend fun recognizeCandidatesFromFile(context: Context, filePath: String): List<String> {
        return try {
            val uri = Uri.fromFile(File(filePath))
            val image = InputImage.fromFilePath(context, uri)
            recognizeCandidates(image)
        } catch (e: Exception) {
            android.util.Log.w("MlKitOcr", "Failed to process image file: $filePath", e)
            emptyList()
        }
    }

    /**
     * Extract candidate medicine names from raw OCR text.
     * Filters lines to plausible medicine names, strips non-alphanumeric noise.
     */
    fun extractCandidates(text: String): List<String> {
        return text.lines()
            .map { it.replace(Regex("[^A-Za-z0-9 /+-]"), " ").trim() }
            .filter { it.length >= 3 }
            .distinct()
            .take(12)
    }

    /**
     * Get raw OCR text from an image file (for debugging/display).
     */
    suspend fun recognizeRawText(context: Context, filePath: String): String {
        return try {
            val uri = Uri.fromFile(File(filePath))
            val image = InputImage.fromFilePath(context, uri)
            recognizer.process(image).await().text
        } catch (e: Exception) {
            ""
        }
    }
}

package com.medlens.android.model

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.medlens.android.BuildConfig
import java.io.File
import java.security.MessageDigest
import okhttp3.OkHttpClient
import okhttp3.Request

class GemmaModelDownloadWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    private val client = OkHttpClient()

    override suspend fun doWork(): Result {
        val descriptor = GEMMA_4_E4B_DESCRIPTOR
        val modelsDir = File(applicationContext.filesDir, "models").apply { mkdirs() }
        val target = File(modelsDir, descriptor.fileName)
        val partial = File(modelsDir, descriptor.fileName + ".part")
        Log.i(TAG, "Starting model download check for ${descriptor.fileName}")

        if (target.exists() && target.length() == descriptor.sizeBytes && sha256(target) == descriptor.sha256) {
            Log.i(TAG, "Model already present at ${target.absolutePath}")
            return Result.success(outputData(target))
        }

        val requestBuilder = Request.Builder().url(descriptor.downloadUrl)
        if (BuildConfig.HF_ACCESS_TOKEN.isNotBlank()) {
            requestBuilder.header("Authorization", "Bearer ${BuildConfig.HF_ACCESS_TOKEN}")
            Log.i(TAG, "Using authenticated Hugging Face download for debug build")
        }
        val request = requestBuilder.build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                Log.e(TAG, "Model download failed with HTTP ${response.code}")
                return Result.failure(Data.Builder().putString("error", "HTTP ${response.code}").build())
            }
            val body = response.body ?: return Result.failure(Data.Builder().putString("error", "Empty response body").build())
            val contentLength = body.contentLength()
            Log.i(TAG, "Downloading model payload, expectedBytes=${descriptor.sizeBytes}, responseBytes=$contentLength")
            body.byteStream().use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    var downloadedBytes = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read <= 0) break
                        output.write(buffer, 0, read)
                        downloadedBytes += read
                        reportProgress(downloadedBytes, descriptor.sizeBytes, contentLength)
                    }
                }
            }
        }

        if (partial.length() != descriptor.sizeBytes) {
            Log.e(TAG, "Model size mismatch, actual=${partial.length()}, expected=${descriptor.sizeBytes}")
            partial.delete()
            return Result.failure(Data.Builder().putString("error", "Model size mismatch").build())
        }
        val actualSha = sha256(partial)
        if (actualSha != descriptor.sha256) {
            Log.e(TAG, "Model checksum mismatch")
            partial.delete()
            return Result.failure(Data.Builder().putString("error", "Model checksum mismatch").build())
        }

        if (target.exists()) target.delete()
        partial.renameTo(target)
        Log.i(TAG, "Model download complete: ${target.absolutePath}")
        return Result.success(outputData(target))
    }

    private fun outputData(target: File): Data = Data.Builder()
        .putString("path", target.absolutePath)
        .build()

    private suspend fun reportProgress(
        downloadedBytes: Long,
        expectedSizeBytes: Long,
        responseSizeBytes: Long,
    ) {
        val totalBytes = when {
            expectedSizeBytes > 0L -> expectedSizeBytes
            responseSizeBytes > 0L -> responseSizeBytes
            else -> return
        }
        setProgress(workDataOf(GemmaModelManager.KEY_PROGRESS to (downloadedBytes.toFloat() / totalBytes.toFloat()).coerceIn(0f, 1f)))
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val TAG = "GemmaDownloadWorker"
    }
}

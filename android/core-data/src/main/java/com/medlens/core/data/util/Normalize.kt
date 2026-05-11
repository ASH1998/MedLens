package com.medlens.core.data.util

private val NON_ALNUM_RE = Regex("[^a-z0-9]+")

fun normalizeLookupText(value: String): String {
    val folded = value.lowercase().trim()
    val stripped = folded.replace(NON_ALNUM_RE, " ")
    return stripped.split(Regex("\\s+")).filter { it.isNotBlank() }.joinToString(" ")
}

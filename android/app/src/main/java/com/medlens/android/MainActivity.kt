package com.medlens.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.viewmodel.compose.viewModel
import com.medlens.android.ui.MedLensApp
import com.medlens.android.ui.MedLensViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = MedLensColorScheme) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    val viewModel: MedLensViewModel = viewModel()
                    MedLensApp(viewModel)
                }
            }
        }
    }
}

private val MedLensColorScheme = lightColorScheme(
    primary = Color(0xFF008E7E),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDDF7F0),
    onPrimaryContainer = Color(0xFF063B39),
    secondary = Color(0xFF6E55B7),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE9E1FF),
    onSecondaryContainer = Color(0xFF251154),
    tertiary = Color(0xFF0A2A57),
    onTertiary = Color.White,
    background = Color(0xFFFCF9FD),
    onBackground = Color(0xFF172033),
    surface = Color(0xFFFFFBFF),
    onSurface = Color(0xFF172033),
    surfaceVariant = Color(0xFFE8E1EC),
    onSurfaceVariant = Color(0xFF5D6070),
    outline = Color(0xFFC7BFCC),
    error = Color(0xFFBA1A1A),
)
